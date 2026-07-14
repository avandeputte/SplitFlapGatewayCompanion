"""
engine.py — the display controller.

Owns the active transport and turns "show this text" requests into timed frame
sends, updating live state as it goes. One send runs at a time (an asyncio lock
serializes bus access). The manual compose path, the app play-loop, playlists,
and trigger interrupts all drive this same controller.
"""

from __future__ import annotations

import asyncio
import logging

from . import device, gateway, renderer
from .config import Config
from .state import DisplayState
from .transport import DisplayTransport, SimTransport, build_transport

log = logging.getLogger("companion.engine")


def _entry_label(entry: dict) -> str:
    """A playlist entry as one word for the running-order view: the app id, or
    "(message)" for a composed entry. Mirrors the id normalisation in the loop."""
    if entry.get("type") == "compose":
        return "(message)"
    app_id = entry.get("app", "") or "(unknown)"
    return app_id[7:] if app_id.startswith("plugin_") else app_id


class DisplayController:
    def __init__(self, config: Config, state: DisplayState):
        self.config = config
        self.state = state
        self.transport: DisplayTransport = SimTransport()
        self._send_lock = asyncio.Lock()
        # A single "driver" task at a time: manual compose, an app play-loop, or
        # an app-playlist loop. Starting one cancels the others (exclusive).
        self._task: asyncio.Task | None = None
        self.active_app: str | None = None
        self.active_playlist: str | None = None
        self.plugins = None  # set via attach_plugins()
        # Told what is driving the display whenever that changes, so it can outlive the
        # process (see attach_persist). Set via attach_persist().
        self._persist = None
        # Trigger interrupts briefly take over the display, then the driver resumes.
        self._interrupt_lock = asyncio.Lock()
        self._interrupting = False
        self._temp_task: asyncio.Task | None = None   # a timed show_temporary() message

    def attach_plugins(self, plugins) -> None:
        self.plugins = plugins

    def attach_persist(self, cb) -> None:
        """Register a callback told what is now driving the display — an app, a playlist,
        or nothing. It is what lets a restart pick up where it left off (a container
        updating itself used to silently stop whatever was playing).

        It lives here rather than in the endpoints because the display gets driven from
        four places — the API, the scheduler, triggers and MCP — and only the engine sees
        all of them.
        """
        self._persist = cb

    def _remember(self, doc: dict | None) -> None:
        """What is driving the display now. None = nothing."""
        if not self._persist:
            return
        try:
            self._persist(doc)
        except Exception as e:                    # never let bookkeeping break playback
            log.debug("could not record what is running: %s", e)

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        await self._open_transport()

    async def stop(self) -> None:
        await self._cancel_task()
        await self._safe_close(self.transport)

    async def _open_transport(self) -> None:
        build_error: str | None = None
        if self.config.sim_mode:
            # Developer simulation mode: nothing is sent to the display.
            self.transport = SimTransport()
            self._sync_transport_state()
            return
        cfg = self.config.transport
        try:
            transport = build_transport(cfg)   # always REST
        except Exception as e:
            # Only reachable if no gateway_url is configured. Fall back to a sim
            # no-op so the app still serves the preview, with *why* in the pill.
            log.warning("REST transport unavailable (%s); using sim no-op", e)
            build_error = f"rest: {e}"
            transport = SimTransport()
        try:
            await transport.connect()
        except Exception as e:
            # A network failure does NOT downgrade to sim — we keep the selected
            # transport so the UI honestly shows it offline (and it can recover).
            log.warning("transport connect error (%s)", e)
        self.transport = transport
        self._sync_transport_state()
        if build_error:
            self.state.last_error = build_error

    async def reload_transport(self) -> None:
        """Swap the transport after a config change."""
        old = self.transport
        await self._open_transport()
        if old is not self.transport:
            await self._safe_close(old)

    @staticmethod
    async def _safe_close(transport: DisplayTransport | None) -> None:
        if transport is None:
            return
        try:
            await transport.close()
        except Exception:
            pass

    def _sync_transport_state(self) -> None:
        self.state.transport_type = self.transport.type_name
        self.state.transport_connected = self.transport.connected
        self.state.last_error = self.transport.last_error

    def resize_grid(self) -> None:
        self.state.resize(self.config.module_count())

    # -- task control -------------------------------------------------------
    async def _cancel_task(self) -> None:
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # -- manual compose -----------------------------------------------------
    def _clear_driver_flags(self) -> None:
        self.active_app = None
        self.active_playlist = None
        self.state.active_app = None
        self.state.active_playlist = None
        # Nothing is on the flaps that belongs to an app any more.
        self.state.current_app = None
        self.state.playlist_index = None
        self.state.playlist_entries = None

    def send_text_bg(self, text: str, style: str | None = None,
                     speed: int | None = None, frame: bool = False) -> str:
        """Stop any app/playlist and show ``text`` (background). Returns target.

        ``frame``: the text is a FRAME, not words — its lowercase r/o/y/g/b/p/w are the
        COLOUR FLAPS. That is what an animation and a raw colour grid are."""
        clean = self._normalize(text, frame=frame)
        self._clear_driver_flags()
        # A manual message replaces whatever was playing, so there is nothing to come
        # back to — a restart should leave the board alone, not resurrect the old app.
        self._remember(None)
        old = self._task
        if old and not old.done():
            old.cancel()  # releases the lock at its next await
        self._task = asyncio.create_task(self._run_manual(clean, style=style, speed=speed))
        return clean

    async def send_text(self, text: str, style: str | None = None,
                        speed: int | None = None, frame: bool = False) -> str:
        """Send and await completion (used by tests / synchronous callers)."""
        await self._cancel_task()
        self._clear_driver_flags()
        self._remember(None)                       # as send_text_bg — a manual takeover
        clean = self._normalize(text, frame=frame)
        await self._run_manual(clean, style=style, speed=speed)
        return clean

    @property
    def caps(self) -> device.Capabilities:
        """What THIS wall can show. A property of the gateway on the other end, so with
        several displays the answer differs per wall."""
        return getattr(self.transport, "caps", device.SPLIT_FLAP)

    def _forced_uppercase(self) -> bool:
        """Has the user asked this wall to shout, even though it need not?"""
        plugins = getattr(self, "plugins", None)
        if plugins is None:
            return False
        try:
            v = plugins.settings.get("force_uppercase", "no")
        except Exception:
            return False
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    @property
    def shows_lowercase(self) -> bool:
        """Whether this wall will actually SHOW lowercase.

        Two different questions, and keeping them apart is the point:

          caps.lowercase   — what the wall CAN do. A property of the hardware.
          shows_lowercase  — what it WILL do. The hardware, AND the user's preference.

        Only the second one decides the fold. The first still decides the WIRE protocol: a
        Matrix Portal asked to shout is still driven by the index-addressed API, and still
        shows its pictographs and named colours. It is just in capitals.
        """
        return self.caps.lowercase and not self._forced_uppercase()

    @property
    def rich(self) -> bool:
        """What the UI and the API mean by "rich": what the wall will actually show."""
        return self.shows_lowercase

    def _normalize(self, text: str, *, frame: bool = False) -> str:
        """A page, ready for the wire.

        `frame` is the ONE question the caller has to answer: is a lowercase letter in this
        text a COLOUR (an animation, a raw colour grid) or a LETTER (words)? It used to be
        two flags — `raw` and `keep_case` — which were the same axis inverted, and whose
        fourth combination silently destroyed an animation's colours.

        Folding is not the caller's business. A wall with no lowercase flaps gets uppercase;
        a Matrix Portal does not. That decision belongs to the wall, it is made here, once,
        and it is made LAST — after the colours are explicit, so folding can never eat one.
        """
        clean = renderer.normalize(text, self.config.module_count(), frame=frame)
        return clean if self.shows_lowercase else renderer.fold(clean)

    async def _run_manual(self, clean: str, *, style: str | None, speed: int | None) -> None:
        disp = self.config.display
        style = style or disp.get("transition_style", "ltr")
        if speed is None:
            speed = disp.get("slot_speed", 80) if style == "slot" else disp.get("transition_speed", 15)
        await self._emit_page(clean, style=style, speed=int(speed))

    async def _emit_page(self, clean: str, *, style: str, speed: int) -> None:
        """Render ``clean`` to frames and push them over the transport."""
        grid = self.config.grid
        base = int(grid.get("module_id_base", 0))
        rows, cols = int(grid["rows"]), int(grid["cols"])
        plan = renderer.build_send_plan(
            clean, style=style, speed_ms=int(speed), rows=rows, cols=cols,
        )
        self.state.set_target(clean)
        # Silently yield to a settings upload/download so we don't flood the gateway
        # with frame traffic while it's busy storing/serving the settings blob. The
        # transfer is small and quick; cap the wait so a stuck flag never wedges the
        # display.
        waited = 0.0
        while gateway.settings_active() and waited < 5.0:
            await asyncio.sleep(0.05)
            waited += 0.05
        if waited:
            log.debug("held display send %.2fs for an in-flight settings transfer", waited)
        async with self._send_lock:
            try:
                # Batch path (REST): draw the whole page in one request; the
                # gateway (3.0+) paces the cascade. MQTT/sim send per frame.
                if getattr(self.transport, "batch_capable", False):
                    ordered = [(base + gi, ch, gi) for step in plan for (gi, ch) in step.frames]
                    await self.transport.send_batch([(m, c) for m, c, _ in ordered], int(speed))
                    for _, char, gi in ordered:
                        self.state.set_module(gi, char)
                    return
                for step in plan:
                    for grid_index, char in step.frames:
                        await self.transport.send_frame(base + grid_index, renderer.for_legacy(char))
                        self.state.set_module(grid_index, char)
                    if step.delay_after > 0:
                        await asyncio.sleep(step.delay_after)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.state.last_error = str(e)
                log.warning("send failed: %s", e)
            finally:
                self._sync_transport_state()

    async def clear(self) -> str:
        """Blank the whole display."""
        return await self.send_text(" " * self.config.module_count(), style="ltr")

    async def home_all(self) -> bool:
        """Physically home every module and blank the preview.

        Stops any running app/playlist first — homing is a manual takeover of the
        wall, like compose/clear — then broadcasts a Home to the gateway, which
        returns every module to flap 0 (the blank home flap). In simulation mode
        nothing is sent to the gateway, but the preview is still blanked so the
        effect is visible. Returns whether the gateway accepted the command (True
        in sim). Raises if no gateway is configured or the gateway call fails.
        """
        await self._cancel_task()
        self._clear_driver_flags()
        ok = True
        async with self._send_lock:   # serialize with any in-flight/interrupt send
            if not self.config.sim_mode:
                url = (self.config.transport.get("gateway_url") or "").strip()
                if not url:
                    raise RuntimeError("no gateway_url configured")
                ok = await gateway.home_all(url)
            self.state.blank()
        self._sync_transport_state()
        return ok

    # -- app play-loop ------------------------------------------------------
    async def run_app(self, app_id: str) -> None:
        """Start continuously running an app (fetch → page cycle)."""
        if self.plugins is None or self.plugins.manifest(app_id) is None:
            raise KeyError(app_id)
        await self._cancel_task()
        self._clear_driver_flags()
        self.active_app = app_id
        self.state.active_app = app_id
        self._remember({"kind": "app", "app": app_id})
        self._task = asyncio.create_task(self._app_loop(app_id))

    async def stop_app(self) -> None:
        await self._cancel_task()
        self._clear_driver_flags()
        self._remember(None)

    async def _app_loop(self, app_id: str) -> None:
        loop = asyncio.get_running_loop()
        last_sent: str | None = None
        # A standalone app is the app on screen the whole time it runs.
        self.state.current_app = app_id
        while self.active_app == app_id:
            try:
                pages = await loop.run_in_executor(None, self.plugins.get_pages, app_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("app %s get_pages error: %s", app_id, e)
                pages = []
            if not pages:
                await asyncio.sleep(1)
                continue
            t = self.plugins.page_timing(app_id)
            for page in pages:
                if self.active_app != app_id:
                    return
                await self._wait_if_interrupted()
                text = page if isinstance(page, str) else str(page.get("text", ""))
                # Non-anim apps skip re-sending an unchanged page.
                if t["is_anim"] or text != last_sent:
                    clean = self._normalize(text, frame=t["is_anim"])
                    await self._emit_page(clean, style=t["style"], speed=t["speed"])
                    last_sent = text
                await asyncio.sleep(max(0.0, float(t["loop_delay"])))

    # -- app-playlist loop --------------------------------------------------
    async def run_playlist(self, entries: list[dict], loop: bool = True,
                           name: str | None = None) -> None:
        """Cycle a list of playlist entries (apps and/or composed messages)."""
        await self._cancel_task()
        self._clear_driver_flags()
        self.active_playlist = name or "(unsaved)"
        self.state.active_playlist = self.active_playlist
        # The rotation, as one label per entry (an app id, or "(message)" for a composed
        # entry), so a reader can see the whole running order and where it is in it —
        # which the API could not say before, so a client had to guess from the flaps.
        self.state.playlist_entries = [_entry_label(e) for e in entries]
        self.state.playlist_index = None
        # Keep the entries, not just the name: a playlist can be run unsaved, and that
        # one has no name to look up on the way back.
        self._remember({"kind": "playlist", "name": name or "",
                        "entries": entries, "loop": bool(loop)})
        self._task = asyncio.create_task(self._playlist_loop(entries, loop))

    async def _playlist_loop(self, entries: list[dict], loop: bool) -> None:
        rt_loop = asyncio.get_running_loop()
        want = self.active_playlist
        while self.active_playlist == want:
            for i, entry in enumerate(entries):
                if self.active_playlist != want:
                    return
                self.state.playlist_index = i
                etype = entry.get("type", "app")
                duration = float(entry.get("duration", entry.get("delay", 30)))
                if etype == "compose":
                    self.state.current_app = None      # a composed entry, not an app
                    await self._wait_if_interrupted()
                    clean = self._normalize(entry.get("text", ""))
                    await self._emit_page(clean, style=entry.get("style", "ltr"),
                                          speed=int(entry.get("speed", 15)))
                    await asyncio.sleep(duration)
                else:  # app entry — run the app's pages until the deadline
                    app_id = entry.get("app", "")
                    if app_id.startswith("plugin_"):
                        app_id = app_id[7:]
                    if not app_id or self.plugins.manifest(app_id) is None:
                        continue
                    self.state.current_app = app_id    # this app is on the flaps now
                    # Per-entry setting overrides (own location/language/config), so
                    # the same app can appear twice with different configuration.
                    ov = entry.get("overrides") or None
                    deadline = rt_loop.time() + duration
                    last_sent = None
                    while rt_loop.time() < deadline and self.active_playlist == want:
                        try:
                            pages = await rt_loop.run_in_executor(None, self.plugins.get_pages, app_id, ov)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pages = []
                        if not pages:
                            await asyncio.sleep(1)
                            continue
                        t = self.plugins.page_timing(app_id, ov)
                        for page in pages:
                            if rt_loop.time() >= deadline or self.active_playlist != want:
                                break
                            await self._wait_if_interrupted()
                            text = page if isinstance(page, str) else str(page.get("text", ""))
                            if t["is_anim"] or text != last_sent:
                                clean = self._normalize(text, frame=t["is_anim"])
                                await self._emit_page(clean, style=t["style"], speed=t["speed"])
                                last_sent = text
                            await asyncio.sleep(max(0.0, float(t["loop_delay"])))
            if not loop:
                self._clear_driver_flags()
                return

    # -- trigger interrupts + quiet hours -----------------------------------
    async def _wait_if_interrupted(self) -> None:
        while self._interrupting:
            await asyncio.sleep(0.1)

    async def fire_interrupt(self, text: str, seconds: float, *, style: str = "ltr",
                             frame: bool = False, blank_if_idle: bool = False) -> None:
        """Briefly show ``text`` over whatever is running, then let it resume.

        `frame` defaults to FALSE — words. It used to default to "raw", i.e. "a lowercase
        letter is a colour flap", which was harmless only while every app uppercased its own
        output. They stopped, so a trigger on any app whose page contained a lowercase r, o,
        y, g, b, p or w was quietly sprinkling COLOUR FLAPS through it: "Partly cloudy" came
        out with an orange, a white and a yellow flap in the middle of the words.

        While interrupting, the app/playlist loops park on ``_wait_if_interrupted`` and pick
        back up the moment it clears — that is what makes this a *temporary* takeover rather
        than a permanent one. ``blank_if_idle`` covers the case where nothing was running to
        redraw: the message would otherwise just linger, so blank the board instead."""
        async with self._interrupt_lock:
            self._interrupting = True
            try:
                clean = self._normalize(text, frame=frame)
                await self._emit_page(clean, style=style,
                                      speed=int(self.config.display.get("transition_speed", 15)))
                await asyncio.sleep(max(0.0, seconds))
            finally:
                self._interrupting = False
        # A running app/playlist redraws itself now the interrupt is over; if there is none,
        # the message would just sit there, so revert to blank.
        if blank_if_idle and not (self.active_app or self.active_playlist):
            await self.clear()

    def show_temporary(self, text: str, seconds: float, *, style: str = "ltr",
                       frame: bool = False) -> bool:
        """Show ``text`` for ``seconds``, then revert — to whatever was running, or to
        blank if nothing was. Runs in the background (a message can last minutes; the
        caller shouldn't block on it) and returns whether something was playing to come
        back to."""
        running = bool(self.active_app or self.active_playlist)
        # Keep a reference: a bare create_task() can be garbage-collected mid-sleep.
        self._temp_task = asyncio.create_task(
            self.fire_interrupt(text, seconds, style=style, frame=frame, blank_if_idle=True))
        return running
