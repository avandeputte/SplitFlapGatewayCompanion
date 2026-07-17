"""Thin async client for the SplitFlap Gateway Companion REST API.

The companion's API is unauthenticated on the local network (only its /mcp and Vestaboard
surfaces carry keys), so this needs a base URL and nothing more. Every call is a short
JSON request against the endpoints the web UI itself uses.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

# One knob for every call. The companion answers in milliseconds on a LAN, so 10 s
# total is generous — and without it aiohttp's 5-minute default would hang the
# config flow and stack polls behind an unplugged box.
_TIMEOUT = aiohttp.ClientTimeout(total=10)


class SplitFlapError(Exception):
    """A request to the companion failed."""


class SplitFlapClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, display: str = "") -> None:
        self._session = session
        self._base = url.rstrip("/")
        # Which wall. A companion can drive several displays; every /api/... call
        # accepts ?display=<id>, and without it the companion means its default
        # display — which is also what keeps entries from older versions working.
        self._display = display

    @property
    def base_url(self) -> str:
        """The companion's base URL (no trailing slash) — what a browser should open."""
        return self._base

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        if self._display:
            kw.setdefault("params", {})["display"] = self._display
        kw.setdefault("timeout", _TIMEOUT)
        try:
            async with self._session.request(method, f"{self._base}{path}",
                                              raise_for_status=True, **kw) as r:
                if r.content_type == "application/json":
                    return await r.json()
                return await r.text()
        # TimeoutError alongside ClientError so an unreachable box reads as
        # "cannot connect" in the config flow, not "unknown error".
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            detail = str(err) or type(err).__name__   # TimeoutError stringifies empty
            raise SplitFlapError(f"{method} {path} failed: {detail}") from err

    # --- reads (the coordinator polls these) ---------------------------------
    async def health(self) -> dict:
        """{"ok": true, "version": "..."} — also the connectivity/validation check."""
        return await self._request("GET", "/api/health")

    async def displays(self) -> dict:
        """{"displays": [{id, name, ...}], "default": id} — every wall this companion
        drives. Older companions (pre-2.0) don't have the route; the caller treats
        that as a single-display box."""
        return await self._request("GET", "/api/displays")

    async def state(self) -> dict:
        return await self._request("GET", "/api/current_state")

    async def grid(self) -> dict:
        return await self._request("GET", "/api/grid")

    async def apps(self) -> dict:
        return await self._request("GET", "/api/apps")

    async def playlists(self) -> dict:
        return await self._request("GET", "/api/playlists")

    async def canvas_png(self) -> bytes | None:
        """The PNG a canvas app is currently drawing on the Matrix panel, or None
        when there is none (a flap app, or an on-device effect with no frame). Raw
        bytes, not JSON — so the board image can show the panel, not stale flaps."""
        kw: dict = {"timeout": _TIMEOUT}
        if self._display:
            kw["params"] = {"display": self._display}
        try:
            async with self._session.request(
                    "GET", f"{self._base}/api/current_state/canvas.png", **kw) as r:
                if r.status == 404:
                    return None
                r.raise_for_status()
                return await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    # --- writes --------------------------------------------------------------
    async def run_app(self, app_id: str) -> None:
        await self._request("POST", "/api/apps/run", json={"app": app_id})

    async def stop_app(self) -> None:
        await self._request("POST", "/api/apps/stop")

    async def run_playlist(self, name: str, entries: list, loop: bool) -> None:
        await self._request("POST", "/api/playlists/run",
                            json={"name": name, "entries": entries, "loop": loop})

    async def message(self, text: str, style: str | None = None,
                      seconds: int | None = None) -> None:
        body: dict[str, Any] = {"text": text}
        if style:
            body["style"] = style
        if seconds:
            body["seconds"] = seconds
        await self._request("POST", "/api/message", json=body)

    async def clear(self) -> None:
        await self._request("POST", "/api/display/clear")

    async def home(self) -> None:
        await self._request("POST", "/api/display/home")
