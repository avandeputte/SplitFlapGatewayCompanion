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

from . import canvas, device, gateway, renderer
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
        # Set = no interrupt in progress. The app/playlist loops await it rather than
        # polling a boolean every 0.1 s.
        self._interrupt_over = asyncio.Event()
        self._interrupt_over.set()
        self._temp_task: asyncio.Task | None = None   # a timed show_temporary() message
        # The page an app loop last put on the wall, for unchanged-page suppression.
        # Valid only while the wall actually still shows it: _emit_page invalidates it
        # on EVERY paint (an interrupt's text, a manual message, a failed send), and
        # only a send that reached the wall re-validates it (record_as=). Without that,
        # a static app was suppressed forever behind a trigger's text.
        self._app_last_sent: str | None = None
        # Bumped by every manual takeover (compose / home). fire_interrupt compares it
        # so a message sent DURING a timed interrupt is not blanked when it ends.
        self._takeovers = 0
        # True while a CANVAS app owns the Matrix panel (it drew straight to the
        # framebuffer, bypassing the flaps). The next driver to take over must hand
        # the panel back to the reel wall first — see _cancel_task.
        self._canvas_active = False

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
        # A true shutdown: the transport is about to close, so nothing follows to auto-stop the
        # panel's canvas mode — hand it back explicitly here.
        await self._cancel_task(network_release=True)
        await self._cancel_temp()
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
    async def _cancel_task(self, *, network_release: bool = False) -> None:
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self._release_canvas(network=network_release)

    async def _release_canvas(self, *, network: bool = False) -> None:
        """Leave canvas mode if a canvas app had the Matrix panel. The single choke point every
        driver switch passes through, so a canvas app is always let go before flaps (or another
        canvas app) take the panel.

        It does NOT eagerly hand the panel back to the reel wall (``POST /api/canvas {active:
        false}``). That would make the firmware repaint the wall from the modules' retained state
        — the flaps from BEFORE the canvas — and hold it there through the render + round-trip
        gap until the replacement page lands: the "old flaps flash" on a canvas→flap switch. It is
        also unnecessary, because whatever comes next auto-stops the panel's canvas mode itself:
        the firmware drops raw-canvas on the first flap command (``-``/``+``/``h``), and every
        switch here IS followed by a page — a flap app's page, ``clear()``'s blank on stop, a
        playlist's next entry, or a new canvas app's own takeover. So the transition happens in
        one step, with no window on the stale wall.

        ``network=True`` is the exception — a true shutdown (``stop()``), where nothing follows to
        auto-stop the canvas, so the panel is handed back explicitly."""
        if not self._canvas_active:
            return
        self._canvas_active = False
        if network:
            url = str(self.config.transport.get("gateway_url") or "").strip()
            if url:
                try:
                    await asyncio.to_thread(canvas.release, url)
                except Exception as e:
                    log.debug("canvas release failed: %s", e)
        # The canvas app drew straight to the framebuffer, bypassing the flap transport's
        # shown-cell cache — so it's now stale. Forget it, or the next flap page's unchanged
        # cells would be skipped (and never repaint, so the canvas mode would never auto-stop).
        try:
            self.transport.forget()
        except Exception as e:
            log.debug("transport.forget failed: %s", e)

    async def _cancel_temp(self) -> None:
        """Cancel a timed show_temporary() message, if one is up. Without this, stop()
        left the temp task alive to blank (or repaint) the wall seconds later."""
        task, self._temp_task = self._temp_task, None
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
        self._takeovers += 1
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
        self._takeovers += 1
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

        Then DEGRADE, which is later still, and has to be: it asks "can this wall show this
        exact character", and until the case is settled that question has no answer. A reel
        with `É` on it and no `é` would reject the lowercase form and accept the uppercase,
        and asking before the fold would degrade a letter that was about to become showable.
        """
        clean = renderer.normalize(text, self.config.module_count(), frame=frame)
        if not self.shows_lowercase:
            clean = renderer.fold(clean)
        # What the wall cannot show becomes the nearest thing it can, rather than a homed
        # module and a hole in the middle of a word. A gateway that has not told us its
        # charset is left exactly as it was.
        return renderer.degrade(clean, self.caps)

    async def _run_manual(self, clean: str, *, style: str | None, speed: int | None) -> None:
        disp = self.config.display
        style = style or disp.get("transition_style", "ltr")
        if speed is None:
            speed = disp.get("slot_speed", 80) if style == "slot" else disp.get("transition_speed", 15)
        await self._emit_page(clean, style=style, speed=int(speed))

    async def _emit_page(self, clean: str, *, style: str, speed: int,
                         unless=None, record_as: str | None = None) -> bool | None:
        """Render ``clean`` to frames and push them over the transport.

        Returns True when the page reached the wall, False when the send failed (the
        wall's contents are then unknown), None when ``unless`` said to stand down
        before anything was sent. ``unless`` is re-checked UNDER the send lock: it
        closes the gap between a driver loop's interrupt check and its send, so an
        interrupt that began in between is never painted over by a stale app page.

        Every paint invalidates the app loops' unchanged-page record (whatever was on
        the wall is about to stop being there); ``record_as`` re-validates it — set by
        the page cycle, under the same lock, only when the send succeeded.
        """
        grid = self.config.grid
        base = int(grid.get("module_id_base", 0))
        rows, cols = int(grid["rows"]), int(grid["cols"])
        speed = int(speed)
        if style == "slot" and speed <= 0:
            speed = 80   # _plan_slot spins at its own 80 ms default; keep step_ms honest
        plan = renderer.build_send_plan(
            clean, style=style, speed_ms=speed, rows=rows, cols=cols,
        )
        await self._yield_to_settings_transfer()
        async with self._send_lock:
            if unless is not None and unless():
                return None
            # About to paint flaps. If a canvas app still holds the panel (a path that did not go
            # through _cancel_task, e.g. a background send_text_bg), this page auto-stops its
            # canvas mode on the firmware — so just drop the bypassed shown-cell cache and let it
            # repaint whole. No eager POST /api/canvas {active:false}, which would flash the wall's
            # stale pre-canvas flaps in the gap before this page lands.
            if self._canvas_active:
                self._canvas_active = False
                try:
                    self.transport.forget()
                except Exception:
                    pass
            self.state.set_target(clean)
            self._app_last_sent = None
            ok = True
            try:
                # Batch path (REST): draw the whole page in one request per uniformly
                # paced run of steps; the gateway (3.0+) paces the cascade. Sim/MQTT
                # send per frame.
                if getattr(self.transport, "batch_capable", False):
                    await self._send_plan_batched(plan, base, speed)
                else:
                    for step in plan:
                        for grid_index, char in step.frames:
                            await self.transport.send_frame(base + grid_index, renderer.for_legacy(char))
                            self.state.set_module(grid_index, char)
                        if step.delay_after > 0:
                            await asyncio.sleep(step.delay_after)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                ok = False
                self.state.last_error = str(e)
                log.warning("send failed: %s", e)
            finally:
                self._sync_transport_state()
            if ok and record_as is not None:
                self._app_last_sent = record_as
            return ok

    async def _send_plan_batched(self, plan, base: int, speed: int) -> None:
        """One request per run of uniformly paced steps. ``step_ms`` paces the frames
        device-side, so a step whose ``delay_after`` IS that pace folds into the run;
        a step that holds for anything else (the slot style's 1.5 s spin-hold) ends
        the run: flush, hold, continue. Ordered styles still collapse to exactly one
        request — but the slot spin no longer vanishes and its hold is no longer
        discarded, which is what ``dict()``-flattening the plan used to do."""
        step_delay = max(0, speed) / 1000.0
        pending: list[tuple[int, str, int]] = []      # (module_id, char, grid_index)

        async def flush() -> None:
            if not pending:
                return
            await self.transport.send_batch([(m, c) for m, c, _ in pending], speed)
            for _, char, gi in pending:
                self.state.set_module(gi, char)
            pending.clear()

        for step in plan:
            pending.extend((base + gi, ch, gi) for gi, ch in step.frames)
            if abs(step.delay_after - step_delay) > 1e-9:
                await flush()
                if step.delay_after > 0:
                    await asyncio.sleep(step.delay_after)
        await flush()

    async def _yield_to_settings_transfer(self) -> None:
        """Silently yield to a settings upload/download so we don't flood the gateway
        with frame traffic while it's busy storing/serving the settings blob. Only
        THIS display's gateway matters — another wall's transfer is none of our
        business. The transfer is small and quick; the wait is capped so a stuck
        transfer never wedges the display."""
        url = (self.config.transport.get("gateway_url") or "").strip()
        if not url or not gateway.settings_active(url):
            return
        waited = await asyncio.get_running_loop().run_in_executor(
            None, gateway.wait_settings_idle, url, 5.0)
        log.debug("held display send %.2fs for an in-flight settings transfer", waited)

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
        self._takeovers += 1          # a manual takeover, like compose/clear
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
        """Start continuously running an app. A flap app fetches pages and the
        engine rotates them; a CANVAS app draws straight to the Matrix panel."""
        if self.plugins is None or self.plugins.manifest(app_id) is None:
            raise KeyError(app_id)
        is_canvas = getattr(self.plugins, "is_canvas_app", None)
        canvas_app = bool(is_canvas and is_canvas(app_id))
        if canvas_app and not self._caps().has_canvas:
            # A canvas app on a wall with no framebuffer has nothing to draw on.
            raise KeyError(f"{app_id} needs a canvas the wall does not have")
        await self._cancel_task()
        self._clear_driver_flags()
        self.active_app = app_id
        self.state.active_app = app_id
        self._remember({"kind": "app", "app": app_id})
        loop = self._canvas_loop if canvas_app else self._app_loop
        self._task = asyncio.create_task(loop(app_id))

    def _caps(self):
        prov = getattr(self.plugins, "_caps", None)
        return prov() if callable(prov) else device.SPLIT_FLAP

    def has_canvas_preview(self) -> bool:
        """True when the live preview / HA image should show the LED panel instead of the
        (bypassed, stale) flap grid: a canvas app is drawing here and either its last frame is
        cached OR the wall can be read back. Readback (firmware 1.19) is what covers an on-device
        effect, ticker or animation — content the companion never rendered a frame for."""
        if not self._canvas_active:
            return False
        url = str(self.config.transport.get("gateway_url") or "").strip()
        return canvas.has_frame(url) or self.caps.canvas_readback

    def canvas_preview_png(self, scale: int = 1):
        """PNG of what this display's panel is showing, or None. The cached frame when a frame-push
        app drew one (no gateway round-trip); otherwise the panel read back, so an on-device effect
        or ticker previews too. May make a network call — call it off the event loop."""
        if not self._canvas_active:
            return None
        url = str(self.config.transport.get("gateway_url") or "").strip()
        png = canvas.last_frame_png(url, scale=scale)
        if png is None and url and self.caps.canvas_readback:
            png = canvas.readback_png(url, scale=scale)
        return png

    async def _canvas_loop(self, app_id: str) -> None:
        """Drive a canvas app: take the panel over, then re-run its draw on a timer.
        The app draws through the injected ``canvas`` helper (an effect, ops, or a
        raw frame); its return value, if a number, is the seconds to hold before
        the next redraw — an effect sets once and holds, a clock redraws each tick."""
        self.state.current_app = app_id
        url = str(self.config.transport.get("gateway_url") or "").strip()
        if url:
            await asyncio.to_thread(canvas.set_active, url, True)
            self._canvas_active = True
        while self.active_app == app_id:
            try:
                hold = await asyncio.to_thread(self.plugins.render_canvas, app_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("canvas app %s draw error: %s", app_id, e)
                hold = None
            delay = hold if hold else self.plugins.loop_delay(app_id)
            # A canvas app can animate: a low floor lets it pick its own frame rate
            # (the HTTP ops path tops out around 8 fps anyway). A still app returns a
            # long hold and just sits there.
            await asyncio.sleep(max(0.05, float(delay or 5)))

    async def _play_canvas_entry(self, app_id: str, deadline: float, want) -> None:
        """Drive a canvas app for one playlist entry — take the panel over (once)
        and redraw on its own timer until the entry's ``deadline``. The caller
        releases the panel afterwards, so an effect/frame never outlives its slot.

        The per-frame sleep is capped at the time left in the slot: a canvas app
        that asks to hold a long time (an on-device effect returns its whole
        loop_delay) must not sleep past its turn in the playlist."""
        rt_loop = asyncio.get_running_loop()
        url = str(self.config.transport.get("gateway_url") or "").strip()
        if url and not self._canvas_active:
            await asyncio.to_thread(canvas.set_active, url, True)
            self._canvas_active = True
        render = getattr(self.plugins, "render_canvas", None)
        while rt_loop.time() < deadline and self.active_playlist == want:
            try:
                hold = await asyncio.to_thread(render, app_id) if render else None
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("canvas app %s draw error: %s", app_id, e)
                hold = None
            remaining = deadline - rt_loop.time()
            if remaining <= 0:
                break
            delay = hold if hold else self.plugins.loop_delay(app_id)
            await asyncio.sleep(max(0.05, min(float(delay or 5), remaining)))

    async def stop_app(self) -> None:
        """Stop whatever is running — and BLANK the wall.

        A display with nothing running should show nothing. Leaving the last page an app
        happened to draw is worse than blank: it is a lie that goes stale, and the longer it
        sits there the more it looks like the thing is still working.

        Blanking IS homing here: flap 0 is the blank flap, so driving every module to a space
        returns it home. (The Home button is a separate, physical re-home; this is the
        ordinary "nothing is on" state.)

        Only an EXPLICIT stop comes through here — starting another app cancels the loop
        directly (run_app), so switching apps does not flash a blank wall in between.
        """
        await self._cancel_task()
        self._clear_driver_flags()
        self._remember(None)
        await self.clear()

    async def _app_loop(self, app_id: str) -> None:
        # A standalone app is the app on screen the whole time it runs.
        self.state.current_app = app_id
        self._app_last_sent = None
        while self.active_app == app_id:
            await self._play_app_pages(app_id, None,
                                       lambda: self.active_app == app_id)

    async def _play_app_pages(self, app_id: str, ov: dict | None, keep_going) -> None:
        """ONE fetch → page-cycle pass of an app: the loop body shared by _app_loop and
        a playlist's app entries. (It used to be duplicated in both, which meant the
        stale-suppression bug had to be fixed twice.)

        Suppression: a non-anim app skips re-sending an unchanged page — but only
        while ``_app_last_sent`` is valid, i.e. the wall really still shows it. Every
        paint through _emit_page (an interrupt's text, a failed send) invalidates it,
        and only a send that reached the wall re-validates it (``record_as``), so the
        driver repaints the moment an interrupt is over instead of leaving its text
        up indefinitely.
        """
        rt_loop = asyncio.get_running_loop()
        try:
            pages = await rt_loop.run_in_executor(None, self.plugins.get_pages, app_id, ov)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("app %s get_pages error: %s", app_id, e)
            pages = []
        if not pages:
            await asyncio.sleep(1)
            return
        t = self.plugins.page_timing(app_id, ov)
        for page in pages:
            if not keep_going():
                return
            text = page if isinstance(page, str) else str(page.get("text", ""))
            if t["is_anim"] or text != self._app_last_sent:
                clean = self._normalize(text, frame=t["is_anim"])
                await self._emit_page_from_loop(clean, style=t["style"], speed=t["speed"],
                                                record_as=text)
            await asyncio.sleep(max(0.0, float(t["loop_delay"])))

    async def _emit_page_from_loop(self, clean: str, *, style: str, speed: int,
                                   record_as: str | None = None) -> bool:
        """Emit on behalf of a driver loop — never over a live interrupt.

        The old park-then-send was check-then-act: an interrupt that began between
        ``_wait_if_interrupted`` and the send could be painted over by a stale app
        page. _emit_page re-checks under the send lock and stands down (None); we
        park again and retry."""
        while True:
            await self._wait_if_interrupted()
            sent = await self._emit_page(clean, style=style, speed=speed,
                                         unless=lambda: self._interrupting,
                                         record_as=record_as)
            if sent is not None:
                return sent

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
                    clean = self._normalize(entry.get("text", ""))
                    await self._emit_page_from_loop(clean, style=entry.get("style", "ltr"),
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

                    def keep_going() -> bool:
                        return rt_loop.time() < deadline and self.active_playlist == want

                    is_canvas = getattr(self.plugins, "is_canvas_app", None)
                    if is_canvas and is_canvas(app_id):
                        # A canvas app draws on the Matrix panel, not the flaps. Drive
                        # it like the standalone canvas loop — take the panel over, redraw
                        # until the entry's deadline — then HAND THE PANEL BACK before the
                        # next entry. Without that release, an on-device effect in a
                        # playlist stayed lit forever (it never went through _cancel_task,
                        # and _canvas_active was never set, so release was a no-op).
                        await self._play_canvas_entry(app_id, deadline, want)
                        await self._release_canvas()
                    else:
                        while keep_going():
                            await self._play_app_pages(app_id, ov, keep_going)
            if not loop:
                # A playlist that has run out is a display with nothing running, and it
                # should show nothing — not the last page it happened to stop on.
                #
                # NOT clear(): that cancels the current task, and the current task is THIS
                # one. Emit the blank page directly.
                self._clear_driver_flags()
                self._remember(None)
                await self._emit_page(self._normalize(" " * self.config.module_count()),
                                      style="ltr",
                                      speed=int(self.config.display.get("transition_speed", 15)))
                return

    # -- trigger interrupts + quiet hours -----------------------------------
    @property
    def _interrupting(self) -> bool:
        return not self._interrupt_over.is_set()

    async def _wait_if_interrupted(self) -> None:
        await self._interrupt_over.wait()

    async def fire_interrupt(self, text: str, seconds: float, *, style: str = "ltr",
                             frame: bool = False, blank_if_idle: bool = False) -> None:
        """Briefly show ``text`` over whatever is running, then let it resume.

        `frame` defaults to FALSE — words. It used to default to "raw", i.e. "a lowercase
        letter is a colour flap", which was harmless only while every app uppercased its own
        output. They stopped, so a trigger on any app whose page contained a lowercase r, o,
        y, g, b, p or w was quietly sprinkling COLOUR FLAPS through it: "Partly cloudy" came
        out with an orange, a white and a yellow flap in the middle of the words.

        While interrupting, the app/playlist loops park on ``_wait_if_interrupted`` and pick
        back up the moment it clears. Our paint invalidates their unchanged-page record
        (see _emit_page), so a static app really does redraw — the wall does not keep the
        trigger's text just because the app's next page equals its last one.

        ``blank_if_idle`` covers the case where nothing was running to redraw: the message
        would otherwise just linger, so blank the board instead — UNLESS a manual message
        took the board over while we were up (``_takeovers`` moved): that message is the
        newest intent, and blanking it would destroy it."""
        async with self._interrupt_lock:
            takeovers = self._takeovers
            self._interrupt_over.clear()
            try:
                clean = self._normalize(text, frame=frame)
                await self._emit_page(clean, style=style,
                                      speed=int(self.config.display.get("transition_speed", 15)))
                await asyncio.sleep(max(0.0, seconds))
            finally:
                self._interrupt_over.set()
        # A running app/playlist redraws itself now the interrupt is over; if there is none,
        # the message would just sit there, so revert to blank.
        if blank_if_idle and takeovers == self._takeovers \
                and not (self.active_app or self.active_playlist):
            await self.clear()

    def show_temporary(self, text: str, seconds: float, *, style: str = "ltr",
                       frame: bool = False) -> bool:
        """Show ``text`` for ``seconds``, then revert — to whatever was running, or to
        blank if nothing was. Runs in the background (a message can last minutes; the
        caller shouldn't block on it) and returns whether something was playing to come
        back to.

        A second message REPLACES a live one rather than queueing behind it on the
        interrupt lock: "Dinner!" superseded by "Dinner NOW" should not wait out the
        first message's full timer before appearing."""
        running = bool(self.active_app or self.active_playlist)
        old = self._temp_task
        if old and not old.done():
            old.cancel()   # its finally re-opens the interrupt gate; we take over cleanly
        # Keep a reference: a bare create_task() can be garbage-collected mid-sleep.
        self._temp_task = asyncio.create_task(
            self.fire_interrupt(text, seconds, style=style, frame=frame, blank_if_idle=True))
        return running
