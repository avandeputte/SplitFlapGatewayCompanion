"""Routes for the SET of displays (the registry, Phase 1) and the per-display
basics every surface reads — live state, grid geometry, config.

Split out of main.py (audit E1); the bodies, docstrings and behaviour are
main.py's, verbatim. ``deps`` is the app.main module — see routes/__init__.py.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import discovery, renderer
from ..catalog import GLOBAL_STORAGE_KEYS
from ..gateway import fetch_gateway_settings

log = logging.getLogger("companion")


class GridPatch(BaseModel):
    rows: int = Field(ge=1, le=64)
    cols: int = Field(ge=1, le=128)


class ConfigPatch(BaseModel):
    # grid is typed: a non-numeric rows/cols used to slip straight into the
    # in-memory config and 500 /api/grid until restart.
    grid: GridPatch | None = None
    transport: dict | None = None
    display: dict | None = None
    sync_from_gateway: bool | None = None


class DisplayCreate(BaseModel):
    name: str
    gateway_url: str
    id: str | None = None
    # Start this wall from another one's global settings (location, timezone, language,
    # API keys, provider). A one-time COPY, not a link: the values then belong to the new
    # display and mirror to its own gateway, like everything else it has. It exists only
    # so that adding a second wall does not mean retyping an API key by hand.
    copy_settings_from: str | None = None      # display id; None = the current default
    copy_settings: bool = True


class DisplayPatch(BaseModel):
    name: str | None = None
    gateway_url: str | None = None
    enabled: bool | None = None
    order: int | None = None


def build(deps) -> APIRouter:
    # dependency_overrides_provider is what @app.<method> bakes into an APIRoute;
    # these routes join app.routes FLAT (see main._include_flat), so they carry it
    # themselves. deps.app exists by the time main calls build().
    router = APIRouter(dependency_overrides_provider=deps.app)

    # -----------------------------------------------------------------------
    # Displays — the registry (Phase 1). These are the only routes that are ABOUT the
    # set of displays rather than about one of them, so they talk to the manager and
    # the registry directly instead of resolving through display_for().
    # -----------------------------------------------------------------------
    @router.get("/api/displays")
    async def list_displays():
        """Every wall we drive, plus which one is the default. The UI's switcher reads this;
        so does anything that wants to address a display explicitly."""
        out = deps.displays.status()
        known = {d["id"] for d in out["displays"]}
        # Registered but disabled displays have no runtime object — say so rather than
        # hiding them, or the UI could not offer to turn one back on.
        for rec in deps.registry.all():
            if rec.id not in known:
                out["displays"].append({
                    "id": rec.id, "name": rec.name, "gateway_url": rec.gateway_url,
                    "enabled": False, "grid": None, "module_count": 0,
                    "active_app": None, "active_playlist": None,
                })
        for d in out["displays"]:
            d.setdefault("enabled", True)
            # Whether THIS wall has a framebuffer a canvas app can draw to (a Matrix
            # panel does; a physical reel does not). Sits beside `rich` so the UI can
            # gate canvas apps exactly as it gates lowercase — read per wall, since one
            # companion may drive both kinds. A disabled/registry-only entry has no live
            # controller, so it is simply not a canvas wall.
            live = deps.displays.get(d["id"])
            d["canvas"] = bool(live and live.controller.caps.has_canvas)
        return out

    @router.post("/api/displays")
    async def add_display(body: DisplayCreate):
        """Register a second gateway — and bring it up now, not on the next restart.

        Adding a wall you then cannot use until you restart the add-on is a poor trade, and
        everything a display needs to start is already per-display (Phase 0). If its gateway
        happens to be unreachable, it comes up anyway and its heartbeat keeps trying, which
        is exactly what the first display does.
        """
        url = (body.gateway_url or "").strip()
        if not url:
            raise HTTPException(400, "gateway_url is required")
        if any(r.gateway_url == url for r in deps.registry.all()):
            raise HTTPException(409, f"a display already points at {url}")

        rec = deps.registry.add(name=body.name, gateway_url=url, display_id=body.id or "")
        d = deps.displays.build_from(rec)

        # If the new gateway already holds a settings blob (it was driven by a companion
        # before), that blob wins — start_display restores it, and we must not scribble over
        # it first. Only a gateway with nothing to say gets seeded from an existing wall.
        remote = await asyncio.to_thread(fetch_gateway_settings, url)
        if body.copy_settings and remote is None:
            src = deps.displays.get(body.copy_settings_from) if body.copy_settings_from \
                else deps.displays.default
            if src is not None and src is not d:
                seed = {k: v for k, v in src.settings.all().items() if k in GLOBAL_STORAGE_KEYS}
                d.settings.update(seed)      # a copy — it is this display's own from here on
                log.info("display %r seeded its global settings from %r", d.id, src.id)

        deps._display_tasks[d.id] = await deps.start_display(d, deps._companion_url)
        return {"ok": True, "display": rec.to_dict(), "status": d.status()}

    @router.patch("/api/displays/{display_id}")
    async def patch_display(display_id: str, body: DisplayPatch):
        try:
            rec = deps.registry.update(display_id, **body.model_dump(exclude_none=True))
        except KeyError:
            raise HTTPException(404, f"no such display: {display_id}")

        live = deps.displays.get(display_id)
        if live is not None and body.name:
            live.name = rec.name

        # Enabling/disabling starts and stops the wall's whole runtime — its app loop,
        # settings mirror, HA device and heartbeat.
        if body.enabled is True and live is None:
            d = deps.displays.build_from(rec)
            deps._display_tasks[d.id] = await deps.start_display(d, deps._companion_url)
        elif body.enabled is False and live is not None:
            await deps.stop_display(live, deps._display_tasks.pop(display_id, []))
            deps.displays.remove(display_id)

        # Re-pointing a running display at a different gateway is the one thing we don't do
        # in place: the settings mirror, the HA device and the heartbeat are all bound to the
        # old URL, and swapping it underneath them is how you end up mirroring one wall's
        # playlists onto another's box.
        restart = body.gateway_url is not None and live is not None
        return {"ok": True, "display": rec.to_dict(), "restart_required": restart}

    @router.delete("/api/displays/{display_id}")
    async def remove_display(display_id: str):
        """Deregister a wall. Its settings directory is deliberately left on disk: removing
        a display should not silently destroy the playlists and triggers built for it."""
        try:
            rec = deps.registry.remove(display_id)
        except KeyError:
            raise HTTPException(404, f"no such display: {display_id}")
        except ValueError as e:
            raise HTTPException(409, str(e))

        live = deps.displays.get(display_id)
        if live is not None:
            await deps.stop_display(live, deps._display_tasks.pop(display_id, []))
            deps.displays.remove(display_id)
        # The registry decides which display is the default; keep the runtime in step.
        deps.displays.adopt_default(deps.registry.default_id)
        return {"ok": True, "removed": rec.to_dict()}

    @router.get("/api/displays/discover")
    async def discover_displays():
        """Scan the LAN for gateways — the Displays dialog's scan, and nothing else.

        On demand only: a scan probes neighbours (and opens an mDNS socket where
        multicast works at all), which is dialog behaviour, not background
        behaviour. See discovery module docstring for why this is an HTTP sweep
        first and mDNS second."""
        known = [r.gateway_url for r in deps.registry.all()]
        found = await discovery.discover(known)
        return {"ok": True, "found": found}

    @router.post("/api/displays/{display_id}/default")
    async def make_default(display_id: str):
        """Choose which display the display-less surfaces mean: the bare /api/... routes,
        /local-api/message (a Vestaboard client sends no display id), an MCP call with no
        display argument, an existing HACS entry. A choice, never an inference — and it is
        persisted, so a restart does not quietly hand it back."""
        try:
            deps.displays.set_default(display_id)
        except KeyError:
            raise HTTPException(404, f"no such display: {display_id}")
        return {"ok": True, "default": deps.displays.default_id}

    @router.get("/api/current_state")
    async def current_state(request: Request):
        d = deps.display_for(request)
        snap = d.state.snapshot()
        # A canvas app draws on the Matrix panel, not the flaps — flag it so the
        # live preview shows the panel (via /api/current_state/canvas.png) instead
        # of the stale flap grid.
        snap["canvas"] = d.controller.has_canvas_preview()
        return snap

    @router.get("/api/current_state/canvas.png")
    async def current_canvas_png(request: Request):
        """The frame a canvas app is currently drawing on this display's Matrix
        panel, as a PNG — for the live preview and the HA board image. 404 when no
        canvas app is drawing (or it's an on-device effect, which has no frame)."""
        d = deps.display_for(request)
        png = d.controller.canvas_preview_png()
        if png is None:
            raise HTTPException(404, "no canvas frame")
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @router.get("/api/grid")
    async def grid(request: Request):
        d = deps.display_for(request)
        g = d.config.grid
        return {
            "rows": int(g["rows"]),
            "cols": int(g["cols"]),
            "module_count": d.config.module_count(),
            "module_id_base": int(g.get("module_id_base", 0)),
            "styles": list(renderer.ALL_STYLES),
            "color_map": renderer.COLOR_MAP,
            "display": d.config.display,
        }

    @router.get("/api/config")
    async def get_config(request: Request):
        d = deps.display_for(request)
        return deps._redact(d.config.effective)

    @router.post("/api/config")
    async def update_config(request: Request, patch: ConfigPatch):
        d = deps.display_for(request)
        body = {k: v for k, v in patch.model_dump().items() if v is not None}
        if not body:
            raise HTTPException(400, "empty config patch")
        old_url = d.config.transport.get("gateway_url")
        d.config.update(body)
        if "grid" in body:
            d.grid_changed()   # cached/channel pages were sized for the old grid
        if "transport" in body:
            await d.controller.reload_transport()
        # If the gateway URL just changed and auto-sync is on, pull its config now.
        new_url = d.config.transport.get("gateway_url")
        if new_url and new_url != old_url and d.config.effective.get("sync_from_gateway"):
            await deps.do_gateway_sync(d)
        return deps._redact(d.config.effective)

    return router
