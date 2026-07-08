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

    async def connect(self) -> None:
        import httpx

        self._client = httpx.AsyncClient(
            base_url=self.base,
            timeout=self.timeout,
            headers={"Content-Type": "application/json"},
        )
        # Probe the gateway so the UI can show a truthful status pill.
        try:
            r = await self._client.get("/api/status")
            self._connected = r.status_code < 500
            self._last_error = None if self._connected else f"status {r.status_code}"
        except Exception as e:
            self._connected = False
            self._last_error = f"gateway unreachable: {e}"
            log.warning("REST %s", self._last_error)

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

    async def send_batch(self, frames: list[tuple[int, str]], step_ms: int) -> None:
        """Draw a whole page in one request via /api/rs485/batch (Gateway 3.0+).

        step_ms paces the cascade device-side (the gateway sleeps between frames),
        so this call blocks for roughly the page's animation duration — one
        round-trip for the whole page. Raises on failure (the caller logs it)."""
        if self._client is None:
            raise RuntimeError("REST transport not connected")
        payload = {"frames": [frame_for(mid, ch) for mid, ch in frames],
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
