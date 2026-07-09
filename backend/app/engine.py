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

from . import renderer
from .config import Config
from .state import DisplayState
from .transport import DisplayTransport, SimTransport, build_transport

log = logging.getLogger("companion.engine")


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
        # Trigger interrupts briefly take over the display, then the driver resumes.
        self._interrupt_lock = asyncio.Lock()
        self._interrupting = False

    def attach_plugins(self, plugins) -> None:
        self.plugins = plugins

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

    def send_text_bg(self, text: str, style: str | None = None,
                     speed: int | None = None, raw: bool = False) -> str:
        """Stop any app/playlist and show ``text`` (background). Returns target."""
        clean = self._normalize(text, raw=raw)
        self._clear_driver_flags()
        old = self._task
        if old and not old.done():
            old.cancel()  # releases the lock at its next await
        self._task = asyncio.create_task(self._run_manual(clean, style=style, speed=speed))
        return clean

    async def send_text(self, text: str, style: str | None = None,
                        speed: int | None = None, raw: bool = False) -> str:
        """Send and await completion (used by tests / synchronous callers)."""
        await self._cancel_task()
        self._clear_driver_flags()
        clean = self._normalize(text, raw=raw)
        await self._run_manual(clean, style=style, speed=speed)
        return clean

    def _normalize(self, text: str, *, raw: bool) -> str:
        return renderer.normalize(text, self.config.module_count(), raw=raw)

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
                        await self.transport.send_frame(base + grid_index, char)
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
        return await self.send_text(" " * self.config.module_count(), style="ltr", raw=True)

    # -- app play-loop ------------------------------------------------------
    async def run_app(self, app_id: str) -> None:
        """Start continuously running an app (fetch → page cycle)."""
        if self.plugins is None or self.plugins.manifest(app_id) is None:
            raise KeyError(app_id)
        await self._cancel_task()
        self._clear_driver_flags()
        self.active_app = app_id
        self.state.active_app = app_id
        self._task = asyncio.create_task(self._app_loop(app_id))

    async def stop_app(self) -> None:
        await self._cancel_task()
        self._clear_driver_flags()

    async def _app_loop(self, app_id: str) -> None:
        loop = asyncio.get_running_loop()
        last_sent: str | None = None
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
                    clean = self._normalize(text, raw=t["is_anim"])
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
        self._task = asyncio.create_task(self._playlist_loop(entries, loop))

    async def _playlist_loop(self, entries: list[dict], loop: bool) -> None:
        rt_loop = asyncio.get_running_loop()
        want = self.active_playlist
        while self.active_playlist == want:
            for entry in entries:
                if self.active_playlist != want:
                    return
                etype = entry.get("type", "app")
                duration = float(entry.get("duration", entry.get("delay", 30)))
                if etype == "compose":
                    await self._wait_if_interrupted()
                    clean = self._normalize(entry.get("text", ""), raw=False)
                    await self._emit_page(clean, style=entry.get("style", "ltr"),
                                          speed=int(entry.get("speed", 15)))
                    await asyncio.sleep(duration)
                else:  # app entry — run the app's pages until the deadline
                    app_id = entry.get("app", "")
                    if app_id.startswith("plugin_"):
                        app_id = app_id[7:]
                    if not app_id or self.plugins.manifest(app_id) is None:
                        continue
                    deadline = rt_loop.time() + duration
                    last_sent = None
                    while rt_loop.time() < deadline and self.active_playlist == want:
                        try:
                            pages = await rt_loop.run_in_executor(None, self.plugins.get_pages, app_id)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            pages = []
                        if not pages:
                            await asyncio.sleep(1)
                            continue
                        t = self.plugins.page_timing(app_id)
                        for page in pages:
                            if rt_loop.time() >= deadline or self.active_playlist != want:
                                break
                            await self._wait_if_interrupted()
                            text = page if isinstance(page, str) else str(page.get("text", ""))
                            if t["is_anim"] or text != last_sent:
                                clean = self._normalize(text, raw=t["is_anim"])
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

    async def fire_interrupt(self, text: str, seconds: float) -> None:
        """Briefly show ``text`` over whatever is running, then let it resume."""
        async with self._interrupt_lock:
            self._interrupting = True
            try:
                clean = self._normalize(text, raw=True)  # already a grid string
                await self._emit_page(clean, style="ltr",
                                      speed=int(self.config.display.get("transition_speed", 15)))
                await asyncio.sleep(max(0.0, seconds))
            finally:
                self._interrupting = False
