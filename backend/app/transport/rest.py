"""
transport/rest.py — drive the gateway over its HTTP REST API.

No MQTT broker required: each frame is POSTed to the gateway's raw-bus endpoint
``/api/rs485/send`` ({"data": "m05-A\\n"}). A persistent keep-alive client keeps
per-frame overhead low, but note that animated styles issue one request per
module — MQTT is smoother for heavy animation. For instant/`sync`-style full
rows the gateway also offers ``/api/flap/text`` (used by ``send_text``).
"""

from __future__ import annotations

import logging

from .base import DisplayTransport, frame_for

log = logging.getLogger("companion.transport.rest")


class RestTransport(DisplayTransport):
    type_name = "rest"

    def __init__(self, gateway_url: str, timeout: float = 5.0):
        if not gateway_url:
            raise ValueError("REST transport requires a gateway_url")
        self.base = gateway_url.rstrip("/")
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
                "/api/rs485/send", json={"data": frame_for(module_id, char)}
            )
            r.raise_for_status()
            self._connected = True
            self._last_error = None
        except Exception as e:
            self._connected = False
            self._last_error = str(e)
            raise

    async def send_text(self, start: int, text: str) -> None:
        """Optional bulk fast-path for instant, non-animated row updates."""
        if self._client is None:
            raise RuntimeError("REST transport not connected")
        r = await self._client.post("/api/flap/text", json={"text": text, "start": start})
        r.raise_for_status()
