"""Developer-menu routes. The GET is always safe to call (the UI uses `enabled` to
decide whether to show the dev menu); the actions gated by COMPANION_DEV_MODE say so.

Split out of main.py (audit E1); the bodies, docstrings and behaviour are
main.py's, verbatim. ``deps`` is the app.main module — see routes/__init__.py.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..gateway import (fetch_gateway_config, fetch_gateway_settings,
                       push_gateway_settings, supports_settings)

log = logging.getLogger("companion")


class DevSim(BaseModel):
    on: bool


class DevVestaboard(BaseModel):
    on: bool


class DevMCP(BaseModel):
    on: bool


class DevGrid(BaseModel):
    rows: int
    cols: int


async def _gateway_settings_ready(d) -> tuple[str, dict] | dict:
    """(url, gateway-config) if THIS DISPLAY's gateway is reachable and supports
    settings storage (3.1+); otherwise an error dict to return to the caller.
    Scoped to the display: reading the module-level config here once made
    /api/dev/settings/pull?display=X pull wall 1's blob into wall X's store."""
    url = (d.config.transport.get("gateway_url") or "").strip()
    if not url:
        return {"ok": False, "error": "no gateway_url configured"}
    try:
        gw = await fetch_gateway_config(url)
    except Exception as e:
        return {"ok": False, "error": f"gateway unreachable: {e}"}
    if not supports_settings(gw):
        return {"ok": False, "error": "this gateway does not store settings (needs Gateway 3.1+)"}
    return url, gw


def build(deps) -> APIRouter:
    # dependency_overrides_provider is what @app.<method> bakes into an APIRoute;
    # these routes join app.routes FLAT (see main._include_flat), so they carry it
    # themselves. deps.app exists by the time main calls build().
    router = APIRouter(dependency_overrides_provider=deps.app)

    def _require_dev():
        """Gate for SIMULATION MODE only. The ⚙ tools menu itself is permanent — the
        Vestaboard/MCP switches, resync and settings sync are ordinary controls on an API
        that is unauthenticated on the LAN anyway, so hiding them behind an env var never
        added protection, just friction. Simulation stays dev-gated: silently not driving
        the wall is a developer's tool, and a trap for anyone else."""
        if not deps.config.dev_mode:
            raise HTTPException(404, "developer mode is off (set COMPANION_DEV_MODE=1)")

    async def _external_url(path: str) -> str:
        """The address a client OUTSIDE the browser has to use — an MCP client, a Home
        Assistant rest_command.

        The UI can't work this out for itself. As an add-on it is served through ingress, so
        its own `location.origin` is Home Assistant's host and its path is
        /api/hassio_ingress/<token>/ — neither of which reaches /mcp. The way in is the host
        address and the published port, which only Supervisor knows (resolve_companion_url).
        Empty if we can't tell, and the UI falls back to its own origin.
        """
        try:
            base = (await deps.resolve_companion_url()).rstrip("/")
        except Exception:
            base = ""
        return f"{base}{path}" if base else ""

    @router.get("/api/dev")
    async def dev_state(request: Request):
        d = deps.display_for(request)
        state = d.config.dev_state()
        # Sim/grid are per-display; the Vestaboard/MCP switches are process-wide
        # (one HTTP surface) and live on the default config — report those, or the
        # menu shows a stale switch while a second wall is selected.
        state["vestaboard"] = deps.displays.default.config.vestaboard_enabled
        state["mcp"] = deps.displays.default.config.mcp_enabled
        return state

    @router.post("/api/dev/sim")
    async def dev_sim(request: Request, req: DevSim):
        """Toggle simulation mode: on = nothing is sent to the display."""
        d = deps.display_for(request)
        _require_dev()
        d.config.set_sim_mode(req.on)          # turning off also clears any grid override
        await d.controller.reload_transport()  # swap REST <-> sim
        d.grid_changed()                       # geometry may have reverted
        return d.config.dev_state()

    @router.post("/api/dev/vestaboard")
    async def dev_vestaboard(req: DevVestaboard):
        """Turn the Vestaboard-compatible Local API on/off at runtime (see
        routes/local_api.py). COMPANION_VESTABOARD sets where it starts. The layer is
        ONE process-wide HTTP surface, so the toggle deliberately ignores ?display= —
        the guards read the default config, and writing another display's copy used to
        make this a silent no-op while switched to a second wall."""
        deps.displays.default.config.set_vestaboard(req.on)
        if req.on:
            deps.vestaboard_key()   # mint + persist the key now, so the menu can show it
        log.info("Vestaboard API %s (dev menu)", "enabled" if req.on else "disabled")
        return deps.displays.default.config.dev_state()

    @router.get("/api/dev/vestaboard")
    async def dev_vestaboard_state():
        """The Vestaboard connection details, for the dev menu to display: the key a
        client must send, and the endpoint it posts to. (Not gated: the key guards the
        Vestaboard routes from OUTSIDE clients; anyone who can read this endpoint already
        has the whole unauthenticated companion API.) Process-wide, like the toggle."""
        on = deps.displays.default.config.vestaboard_enabled
        return {
            "enabled": on,
            "key": deps.vestaboard_key() if on else "",
            "path": "/local-api/message",
            # Same as MCP: a rest_command is not the browser, and under ingress the browser's
            # own origin is Home Assistant's, which does not reach this endpoint.
            "url": await _external_url("/local-api/message"),
            "env_key": bool(deps.displays.default.config.vestaboard.get("api_key")),   # pinned via env
        }

    @router.post("/api/dev/mcp")
    async def dev_mcp(req: DevMCP):
        """Turn the MCP server on/off at runtime (see the MCP block in main.py).
        COMPANION_MCP sets where it starts. Process-wide, same reasoning as the
        Vestaboard toggle above."""
        deps.displays.default.config.set_mcp(req.on)
        if req.on:
            deps.mcp_token()        # mint + persist the token now, so the menu can show it
        log.info("MCP server %s (dev menu)", "enabled" if req.on else "disabled")
        return deps.displays.default.config.dev_state()

    @router.get("/api/dev/mcp")
    async def dev_mcp_state():
        """The MCP connection details for the dev menu: the endpoint an LLM client
        points at and the bearer token it must send. (Not gated — same reasoning as the
        Vestaboard key above.) Process-wide, like the toggle."""
        on = deps.displays.default.config.mcp_enabled
        return {
            "enabled": on,
            "token": deps.mcp_token() if on else "",
            "path": "/mcp",
            "url": await _external_url("/mcp"),
            "env_token": bool(deps.displays.default.config.mcp.get("token")),          # pinned via env
        }

    @router.post("/api/dev/resync")
    async def dev_resync(request: Request):
        """Force a settings resync with the gateway."""
        return await deps.do_gateway_sync(deps.display_for(request))

    @router.post("/api/dev/grid")
    async def dev_grid(request: Request, req: DevGrid):
        """Override the grid geometry — only while simulating (so the real display's
        gateway-derived geometry is never touched)."""
        d = deps.display_for(request)
        _require_dev()
        if not d.config.sim_mode:
            raise HTTPException(400, "turn simulation mode on before overriding the grid")
        d.config.set_grid_override(req.rows, req.cols)
        d.grid_changed()
        return d.config.dev_state()

    @router.post("/api/dev/settings/pull")
    async def dev_settings_pull(request: Request):
        """Force-retrieve the settings blob from the gateway and apply it."""
        d = deps.display_for(request)
        ready = await _gateway_settings_ready(d)
        if isinstance(ready, dict):
            return ready
        url, _ = ready
        doc = await asyncio.to_thread(fetch_gateway_settings, url)
        if doc is None:
            return {"ok": False, "error": "no settings are stored on the gateway yet"}
        d.settings.restore_from_doc(doc)
        # load() re-executes every installed app.py — that belongs in an executor,
        # not on the event loop while other walls are animating.
        await asyncio.to_thread(d.plugins.load)   # apply the restored installed-apps list + settings
        d.ha.refresh_discovery()         # the app/playlist option lists may have changed
        log.info("dev: retrieved settings from the gateway (%d apps installed)",
                 len(d.settings.installed_apps))
        return {"ok": True, "applied": True, "installed": len(d.settings.installed_apps)}

    @router.post("/api/dev/settings/push")
    async def dev_settings_push(request: Request):
        """Force-write the current settings to the gateway now."""
        d = deps.display_for(request)
        ready = await _gateway_settings_ready(d)
        if isinstance(ready, dict):
            return ready
        url, _ = ready
        # Match the debounced _pusher: the registry backup rides along. The PUT is a
        # full replace, so omitting it here erased the displays list from the gateway
        # until the next ordinary sync.
        doc = d.settings.snapshot()
        doc["displays"] = deps.registry.snapshot()
        ok = await asyncio.to_thread(push_gateway_settings, url, doc)
        log.info("dev: pushed settings to the gateway (ok=%s)", ok)
        return {"ok": ok, "error": None if ok else "push failed"}

    return router
