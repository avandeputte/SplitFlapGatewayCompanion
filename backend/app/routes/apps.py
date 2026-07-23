"""Apps / plugins routes — list, run, settings, preview, install/uninstall,
upload and delete — plus the shared global settings apps rely on.

Split out of main.py (audit E1); the bodies, docstrings and behaviour are
main.py's, verbatim. ``deps`` is the app.main module — see routes/__init__.py.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

log = logging.getLogger("companion")


class RunAppRequest(BaseModel):
    app: str


class AppSettingsPatch(BaseModel):
    values: dict


class InstallRequest(BaseModel):
    installed: bool


def build(deps) -> APIRouter:
    # dependency_overrides_provider is what @app.<method> bakes into an APIRoute;
    # these routes join app.routes FLAT (see main._include_flat), so they carry it
    # themselves. deps.app exists by the time main calls build().
    router = APIRouter(dependency_overrides_provider=deps.app)

    @router.get("/api/apps")
    async def apps_list(request: Request):
        d = deps.display_for(request)
        return {"apps": d.plugins.app_list(lang=deps._ui_lang(request)),
                "active_app": d.controller.active_app}

    @router.get("/api/apps/available")
    async def apps_available(request: Request):
        d = deps.display_for(request)
        return {"apps": d.plugins.available_list(lang=deps._ui_lang(request))}

    @router.post("/api/apps/run")
    async def apps_run(request: Request, req: RunAppRequest):
        d = deps.display_for(request)
        app_id = req.app[7:] if req.app.startswith("plugin_") else req.app
        try:
            await d.controller.run_app(app_id)
        except KeyError as e:
            if "needs a Matrix panel" in str(e):   # a Matrix-panel app on a wall with no framebuffer
                raise HTTPException(409, "Matrix-panel app: this wall has no framebuffer to draw on.")
            raise HTTPException(404, f"app not installed: {app_id}")
        d.ha.publish_state()
        return {"ok": True, "active_app": app_id}

    @router.post("/api/apps/stop")
    async def apps_stop(request: Request):
        d = deps.display_for(request)
        await d.controller.stop_app()
        d.ha.publish_state()
        return {"ok": True}

    @router.get("/api/apps/{app_id}/settings")
    async def apps_get_settings(app_id: str, request: Request):
        d = deps.display_for(request)
        try:
            return d.plugins.settings_schema(app_id, lang=deps._ui_lang(request))
        except KeyError:
            raise HTTPException(404, f"app not installed: {app_id}")

    @router.post("/api/apps/{app_id}/settings")
    async def apps_save_settings(request: Request, app_id: str, patch: AppSettingsPatch):
        d = deps.display_for(request)
        try:
            d.plugins.save_settings(app_id, patch.values)
        except KeyError:
            raise HTTPException(404, f"app not installed: {app_id}")
        # If this app is on the display right now, restart it so the new settings
        # (page dwell, refresh cadence, content options) take effect immediately.
        if d.controller.active_app == app_id:
            await d.controller.run_app(app_id)
        return {"ok": True}

    @router.get("/api/global-settings")
    async def global_settings_get(request: Request):
        """Shared settings apps rely on (weather_api_key, timezone, location, …)."""
        d = deps.display_for(request)
        return d.plugins.global_settings_schema(lang=deps._ui_lang(request))

    @router.post("/api/global-settings")
    async def global_settings_save(request: Request, patch: AppSettingsPatch):
        # A person changing the Language control marks it explicit, which is what
        # lets it beat the browser's language in the UI-chrome chain (uilang.py).
        # Compared against the stored value, not just presence: the form posts every
        # field, so an untouched seeded en-US must not count as a choice.
        d = deps.display_for(request)
        if "language" in patch.values and \
                str(patch.values["language"]) != str(d.plugins.settings.get("language")):
            d.plugins.settings.set("language_explicit", True)
        d.plugins.save_global_settings(patch.values)
        # Globals (location, provider, page dwell, …) can change what the running app
        # shows or how fast it cycles — restart it so the change is visible at once.
        if d.controller.active_app:
            await d.controller.run_app(d.controller.active_app)
        return {"ok": True}

    @router.get("/api/apps/{app_id}/preview")
    async def apps_preview(request: Request, app_id: str):
        d = deps.display_for(request)
        if d.plugins.manifest(app_id) is None:
            raise HTTPException(404, f"app not installed: {app_id}")
        pages = await asyncio.get_running_loop().run_in_executor(None, d.plugins.get_pages, app_id)
        return {"pages": pages, "rows": d.plugins.get_rows(), "cols": d.plugins.get_cols()}

    @router.post("/api/apps/{app_id}/install")
    async def apps_install(request: Request, app_id: str, req: InstallRequest):
        d = deps.display_for(request)
        if app_id not in d.plugins.installable_ids():
            raise HTTPException(404, f"unknown app: {app_id}")
        if not req.installed and d.controller.active_app == app_id:
            await d.controller.stop_app()
        # set_installed() reloads all plugins (re-executes each app.py), so run it off
        # the event loop to avoid freezing the display loop and other requests.
        await asyncio.get_running_loop().run_in_executor(
            None, d.plugins.set_installed, app_id, req.installed)
        d.ha.refresh_discovery()  # app option list changed
        d.ha.publish_state()
        return {"ok": True, "installed": req.installed}

    @router.post("/api/apps/upload")
    async def apps_upload(request: Request, file: UploadFile = File(...)):
        """Upload + register a new app from a .zip (manifest.json + app.py/data.json).

        Note: a functional app's app.py is executed to validate it — only upload
        apps you trust."""
        d = deps.display_for(request)
        data = await file.read()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(413, "app too large (max 8 MB)")
        try:
            info = await asyncio.get_running_loop().run_in_executor(None, d.plugins.install_zip, data)
        except ValueError as e:
            raise HTTPException(400, str(e))
        d.ha.refresh_discovery()
        log.info("uploaded app: %s (%s)", info["id"], info["type"])
        return {"ok": True, **info}

    @router.delete("/api/apps/{app_id}")
    async def apps_delete(request: Request, app_id: str):
        """Delete a user-uploaded app entirely (built-ins can't be deleted)."""
        d = deps.display_for(request)
        if d.controller.active_app == app_id:
            await d.controller.stop_app()
        try:
            # delete_app() reloads all plugins — run it off the event loop.
            await asyncio.get_running_loop().run_in_executor(None, d.plugins.delete_app, app_id)
        except KeyError:
            raise HTTPException(404, f"unknown app: {app_id}")
        except ValueError as e:
            raise HTTPException(400, str(e))
        d.ha.refresh_discovery()
        return {"ok": True}

    return router
