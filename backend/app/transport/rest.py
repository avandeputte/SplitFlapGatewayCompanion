"""
transport/rest.py — drive the gateway over its HTTP REST API.

No MQTT broker required. A whole page is drawn in ONE request via the gateway's
batch endpoint ``/api/rs485/batch`` ({"frames":[...], "step_ms":N}) — this is what
closes the animation gap vs MQTT (each module would otherwise be its own HTTP
round-trip). The companion targets Gateway 3.0+, so the batch endpoint is always
available; there's no per-frame legacy fallback.
"""

from __future__ import annotations

import logging

from .. import renderer
from .base import DisplayTransport, frame_for

log = logging.getLogger("companion.transport.rest")

# The gateway and split-flap modules render the Windows-1252 code page, so the
# request body is serialized as cp1252 (not httpx's default UTF-8). That keeps
# every accented character a single byte — the byte the module expects — instead
# of a UTF-8 multibyte sequence. JSON punctuation is ASCII, so only the frame
# string values carry the high bytes.
_JSON_1252_HEADERS = {"Content-Type": "application/json; charset=windows-1252"}


def _win1252_body(payload: dict) -> bytes:
    import json
    return json.dumps(payload, ensure_ascii=False).encode("cp1252", "replace")


class RestTransport(DisplayTransport):
    type_name = "rest"
    batch_capable = True   # the engine will hand us whole pages to batch

    def __init__(self, gateway_url: str, timeout: float = 5.0):
        if not gateway_url:
            raise ValueError("REST transport requires a gateway_url")
        self.base = gateway_url.rstrip("/")
        # Batching can block the gateway for the page's cascade duration, so use
        # a longer timeout than the single-frame path.
        self.timeout = timeout
        self._client = None
        self._connected = False
        self._last_error: str | None = None
        # Does this wall have the index-addressed API (lowercase, pictographs, named
        # colours)? Probed on connect — it is a property of the gateway on the other end,
        # not a setting, and with several displays the answer differs per wall.
        self.cells = False
        # What we last put in each module. Lets us send `skip` for the cells that did not
        # change: a clock repainting 75 modules every second to move one digit is traffic
        # the wall does not need, and flaps that should not be moving.
        self._shown: dict[int, str] = {}

    async def connect(self) -> None:
        import httpx

        self._client = httpx.AsyncClient(
            base_url=self.base,
            timeout=self.timeout,
            headers={"Content-Type": "application/json"},
        )
        # We do not know what is on the wall until we have put it there.
        self._shown.clear()
        # Probe the gateway so the UI can show a truthful status pill.
        try:
            r = await self._client.get("/api/status")
            self._connected = r.status_code < 500
            self._last_error = None if self._connected else f"status {r.status_code}"
        except Exception as e:
            self._connected = False
            self._last_error = f"gateway unreachable: {e}"
            log.warning("REST %s", self._last_error)
        # …and ask what it CAN do.
        try:
            from ..gateway import supports_cells
            cfg = (await self._client.get("/api/config")).json()
            self.cells = supports_cells(cfg)
            if self.cells:
                log.info("gateway %s: index-addressed display API — lowercase, "
                         "pictographs and named colours are available", self.base)
        except Exception:
            self.cells = False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str | None:
        return self._last_error

    async def send_frame(self, module_id: int, char: str) -> None:
        if self._client is None:
            raise RuntimeError("REST transport not connected")
        try:
            r = await self._client.post(
                "/api/rs485/send",
                content=_win1252_body({"data": frame_for(module_id, char)}),
                headers=_JSON_1252_HEADERS,
            )
            r.raise_for_status()
            self._connected = True
            self._last_error = None
        except Exception as e:
            self._connected = False
            self._last_error = str(e)
            raise

    # -- the index-addressed path (Matrix Portal) -----------------------------
    def _cell(self, ch: str):
        """One character as a cell for POST /api/display/cells.

        The firmware REJECTS the whole page if any character has no flap ("a half-written
        wall is worse than a rejected request"), which is the right call for it and a trap
        for us: one stray glyph from an app would blank the wall. So anything the reel
        cannot show is turned into something it can, HERE, before it is sent.
        """
        # A colour is a colour because the PAGE said so (renderer.colorize), never because
        # the character happens to be one of the seven letters. Guessing here is how "Hello"
        # comes out as "Hell<orange>".
        if renderer.is_color(ch):
            return {"color": renderer.PUA_TO_NAME[ch]}
        if ch in renderer.PICTOGRAPHS or renderer.in_cp1252(ch):
            return {"ch": ch}
        return {"ch": " "}                      # no flap for it: a blank, not a 400

    async def _send_cells(self, frames: list[tuple[int, str]], step_ms: int) -> None:
        base = min(m for m, _ in frames)
        by_id = dict(frames)
        cells, sent = [], 0
        for mid in range(base, max(by_id) + 1):
            ch = by_id.get(mid)
            if ch is None:
                cells.append({"skip": True})            # not ours to touch
            elif self._shown.get(mid) == ch:
                cells.append({"skip": True})            # already showing it — leave it alone
            else:
                cells.append(self._cell(ch))
                sent += 1
        if not sent:
            return                                       # the wall already says this
        payload = {"start": base, "step_ms": int(step_ms), "cells": cells}
        import json
        r = await self._client.post(
            "/api/display/cells",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30.0)
        r.raise_for_status()
        self._shown.update(by_id)

    async def send_batch(self, frames: list[tuple[int, str]], step_ms: int) -> None:
        """Draw a whole page in one request.

        On a Matrix Portal this goes to /api/display/cells, which addresses flaps by INDEX:
        lowercase and accents survive, pictographs are reachable at all, colours are named
        rather than stealing seven letters — and unchanged cells are skipped, so moving one
        digit of a clock no longer repaints seventy-five modules.

        Everywhere else it is /api/rs485/batch exactly as before (Gateway 3.0+). step_ms
        paces the cascade device-side, so the call blocks for roughly the page's animation
        duration — one round-trip for the whole page. Raises on failure (the caller logs it).
        """
        if self._client is None:
            raise RuntimeError("REST transport not connected")
        if self.cells:
            try:
                await self._send_cells(frames, step_ms)
                self._connected = True
                self._last_error = None
                return
            except Exception as e:
                # The wall is now in an unknown state, so the next page must be sent whole.
                self._shown.clear()
                self._connected = False
                self._last_error = str(e)
                raise
        payload = {"frames": [frame_for(mid, renderer.for_legacy(ch)) for mid, ch in frames],
                   "step_ms": int(step_ms)}
        try:
            # allow the gateway to pace a long page without a client timeout
            r = await self._client.post("/api/rs485/batch",
                                        content=_win1252_body(payload),
                                        headers=_JSON_1252_HEADERS, timeout=30.0)
            r.raise_for_status()
            self._connected = True
            self._last_error = None
        except Exception as e:
            self._connected = False
            self._last_error = str(e)
            raise
