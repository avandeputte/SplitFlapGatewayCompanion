"""Playlists and triggers routes.

``deps`` is the app.main module — see routes/__init__.py.
"""

from __future__ import annotations


from fastapi import APIRouter, HTTPException, Request

from ..engine import NeedsCanvasError
from pydantic import BaseModel


class PlaylistSave(BaseModel):
    name: str
    # list[dict], not list: a non-dict entry would 500 deep in the engine's
    # _entry_label — and PERSIST, so the playlist would crash on every run.
    entries: list[dict] = []
    loop: bool = True


class RunPlaylist(BaseModel):
    entries: list[dict] = []
    loop: bool = True
    name: str | None = None


class TriggersPatch(BaseModel):
    triggers: list | None = None
    triggers_enabled: bool | None = None


class ZoneLayoutSave(BaseModel):
    """POST /api/zones/layouts — a named 2-3 zone layout: [{app, width?, overrides?}]."""
    name: str
    zones: list


class RunZones(BaseModel):
    """POST /api/zones/run — ad-hoc zones, or a saved layout by name."""
    zones: list | None = None
    layout: str | None = None


def build(deps) -> APIRouter:
    # Flat-mounted (see main._include_flat and routes/__init__.py for why).
    router = APIRouter(dependency_overrides_provider=deps.app)

    # -----------------------------------------------------------------------
    # Playlists
    # -----------------------------------------------------------------------
    @router.get("/api/playlists")
    async def playlists_list(request: Request):
        # The built-in "All apps" (computed fresh from the Apps screen) rides first,
        # then the user's saved playlists — one dict, so every client shows both.
        d = deps.display_for(request)
        return {"playlists": {**d.plugins.builtin_playlists(),
                              **d.settings.get("saved_app_playlists", {})}}

    @router.post("/api/playlists")
    async def playlists_save(request: Request, req: PlaylistSave):
        d = deps.display_for(request)
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "name required")
        if name in d.plugins.builtin_playlists():
            raise HTTPException(400, f"{name!r} is the built-in playlist — pick another name")
        saved = dict(d.settings.get("saved_app_playlists", {}))
        saved[name] = {"entries": req.entries, "loop": req.loop}
        d.settings.set("saved_app_playlists", saved)
        d.ha.refresh_discovery()  # playlist option list changed
        return {"ok": True, "name": name}

    @router.delete("/api/playlists/{name}")
    async def playlists_delete(request: Request, name: str):
        d = deps.display_for(request)
        if name in d.plugins.builtin_playlists():
            raise HTTPException(400, f"{name!r} is built in — it cannot be deleted")
        saved = dict(d.settings.get("saved_app_playlists", {}))
        saved.pop(name, None)
        d.settings.set("saved_app_playlists", saved)
        d.ha.refresh_discovery()
        return {"ok": True}

    @router.post("/api/playlists/run")
    async def playlists_run(request: Request, req: RunPlaylist):
        d = deps.display_for(request)
        if not req.entries:
            raise HTTPException(400, "playlist has no entries")
        await d.controller.run_playlist(req.entries, req.loop, req.name)
        d.ha.publish_state()
        return {"ok": True, "active_playlist": d.controller.active_playlist}

    # -----------------------------------------------------------------------
    # Zones — 2-3 apps side by side on the Matrix panel (saved layouts + run)
    # -----------------------------------------------------------------------
    @router.get("/api/zones/layouts")
    async def zones_layouts(request: Request):
        d = deps.display_for(request)
        return {"layouts": d.settings.get("saved_zone_layouts", {})}

    @router.post("/api/zones/layouts")
    async def zones_layout_save(request: Request, req: ZoneLayoutSave):
        d = deps.display_for(request)
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "name required")
        try:
            spec = d.controller.validate_zones(req.zones)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e))
        saved = dict(d.settings.get("saved_zone_layouts", {}))
        saved[name] = {"zones": spec}
        d.settings.set("saved_zone_layouts", saved)
        return {"ok": True, "name": name, "zones": spec}

    @router.delete("/api/zones/layouts/{name}")
    async def zones_layout_delete(request: Request, name: str):
        d = deps.display_for(request)
        saved = dict(d.settings.get("saved_zone_layouts", {}))
        saved.pop(name, None)
        d.settings.set("saved_zone_layouts", saved)
        return {"ok": True}

    @router.post("/api/zones/run")
    async def zones_run(request: Request, req: RunZones):
        d = deps.display_for(request)
        zones, name = req.zones, ""
        if req.layout:
            saved = d.settings.get("saved_zone_layouts", {}).get(req.layout)
            if not saved:
                raise HTTPException(404, f"no such layout: {req.layout}")
            zones, name = saved.get("zones") or [], req.layout
        if not zones:
            raise HTTPException(400, "zones or layout required")
        try:
            await d.controller.run_zones(zones, name)
        except NeedsCanvasError as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        d.ha.publish_state()
        return {"ok": True, "active": d.controller.active_app}

    # -----------------------------------------------------------------------
    # Triggers
    # -----------------------------------------------------------------------
    @router.get("/api/triggers")
    async def triggers_get(request: Request):
        d = deps.display_for(request)
        trigs = []
        for t in d.settings.get("triggers", []):
            e = dict(t)
            e["last_fired"] = d.scheduler.last_fired(t.get("id", ""))
            trigs.append(e)
        return {
            "triggers": trigs,
            "triggers_enabled": d.settings.get("triggers_enabled", True),
            "trigger_apps": d.plugins.trigger_apps(),
        }

    @router.post("/api/triggers")
    async def triggers_save(request: Request, patch: TriggersPatch):
        d = deps.display_for(request)
        body = {k: v for k, v in patch.model_dump().items() if v is not None}
        if body:
            d.settings.update(body)
        return {"ok": True}

    return router
