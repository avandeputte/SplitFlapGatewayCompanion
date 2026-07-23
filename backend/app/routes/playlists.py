"""Playlists and triggers routes.

``deps`` is the app.main module — see routes/__init__.py.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger("companion")


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


def build(deps) -> APIRouter:
    # dependency_overrides_provider is what @app.<method> bakes into an APIRoute;
    # these routes join app.routes FLAT (see main._include_flat), so they carry it
    # themselves. deps.app exists by the time main calls build().
    router = APIRouter(dependency_overrides_provider=deps.app)

    # -----------------------------------------------------------------------
    # Playlists
    # -----------------------------------------------------------------------
    @router.get("/api/playlists")
    async def playlists_list(request: Request):
        d = deps.display_for(request)
        return {"playlists": d.settings.get("saved_app_playlists", {})}

    @router.post("/api/playlists")
    async def playlists_save(request: Request, req: PlaylistSave):
        d = deps.display_for(request)
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "name required")
        saved = dict(d.settings.get("saved_app_playlists", {}))
        saved[name] = {"entries": req.entries, "loop": req.loop}
        d.settings.set("saved_app_playlists", saved)
        d.ha.refresh_discovery()  # playlist option list changed
        return {"ok": True, "name": name}

    @router.delete("/api/playlists/{name}")
    async def playlists_delete(request: Request, name: str):
        d = deps.display_for(request)
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
