"""Thin async client for the SplitFlap Gateway Companion REST API.

The companion's API is unauthenticated on the local network (only its /mcp and Vestaboard
surfaces carry keys), so this needs a base URL and nothing more. Every call is a short
JSON request against the endpoints the web UI itself uses.
"""

from __future__ import annotations

from typing import Any

import aiohttp


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

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        if self._display:
            kw.setdefault("params", {})["display"] = self._display
        try:
            async with self._session.request(method, f"{self._base}{path}",
                                              raise_for_status=True, **kw) as r:
                if r.content_type == "application/json":
                    return await r.json()
                return await r.text()
        except aiohttp.ClientError as err:
            raise SplitFlapError(f"{method} {path} failed: {err}") from err

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
