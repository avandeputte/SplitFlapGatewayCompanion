"""
engine.py — the display controller.

Owns the active transport and turns "show this text" requests into timed frame
sends, updating live state as it goes. One send runs at a time (an asyncio lock
stands in for splitflap-os's serial_lock). Later phases (app play-loop,
playlists, schedules, triggers) will drive this same controller.
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
        self._current_send: asyncio.Task | None = None

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        await self._open_transport()

    async def stop(self) -> None:
        if self._current_send and not self._current_send.done():
            self._current_send.cancel()
        await self._safe_close(self.transport)

    async def _open_transport(self) -> None:
        cfg = self.config.transport
        build_error: str | None = None
        try:
            transport = build_transport(cfg)
        except Exception as e:
            # Invalid config (e.g. mqtt with no broker). Fall back to a sim
            # no-op so the app still runs, but surface *why* in the status pill.
            log.warning("transport build failed (%s); using sim", e)
            build_error = f"{cfg.get('type')}: {e}"
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

    # -- sending ------------------------------------------------------------
    def send_text_bg(self, text: str, style: str | None = None,
                     speed: int | None = None, raw: bool = False) -> str:
        """Kick off a send in the background; return the normalized target.

        A new request cancels an in-flight animation so the display always
        tracks the latest intent.
        """
        clean = self._normalize(text, raw=raw)
        if self._current_send and not self._current_send.done():
            self._current_send.cancel()
        self._current_send = asyncio.create_task(
            self._run_send(clean, style=style, speed=speed)
        )
        return clean

    async def send_text(self, text: str, style: str | None = None,
                        speed: int | None = None, raw: bool = False) -> str:
        """Send and await completion (used by tests / synchronous callers)."""
        clean = self._normalize(text, raw=raw)
        await self._run_send(clean, style=style, speed=speed)
        return clean

    def _normalize(self, text: str, *, raw: bool) -> str:
        return renderer.normalize(
            text,
            self.config.module_count(),
            raw=raw,
            currency=self.config.display.get("currency_symbol", "$"),
        )

    async def _run_send(self, clean: str, *, style: str | None, speed: int | None) -> None:
        disp = self.config.display
        style = style or disp.get("transition_style", "ltr")
        if speed is None:
            speed = disp.get("slot_speed", 80) if style == "slot" else disp.get("transition_speed", 15)

        grid = self.config.grid
        base = int(grid.get("module_id_base", 0))
        rows, cols = int(grid["rows"]), int(grid["cols"])

        plan = renderer.build_send_plan(
            clean, style=style, speed_ms=int(speed), rows=rows, cols=cols,
            current_indices=self.state.current_indices,
        )
        # Show the target immediately (indices then animate toward it).
        self.state.set_target(clean)

        async with self._send_lock:
            try:
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
