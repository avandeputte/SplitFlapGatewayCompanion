"""
main.py — FastAPI application (Phase 1 slice).

Serves the SPA, the companion API (compose/send, live state, config), and a
best-effort gateway status probe. Later phases add the plugin runtime,
playlists/schedules/triggers, and the gateway reverse-proxy.
"""

from __future__ import annotations

import copy
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, helpers, renderer
from .config import Config
from .engine import DisplayController
from .gateway import build_sync_patch, fetch_gateway_config, register_companion
from .plugin_settings import PluginSettings
from .plugins import PluginRuntime
from .scheduler import Scheduler
from .state import DisplayState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("companion")

STATIC_DIR = Path(__file__).resolve().parent / "static"
APPS_DIR = Path(__file__).resolve().parents[2] / "apps"

config = Config()
state = DisplayState(config.module_count())
controller = DisplayController(config, state)
plugin_settings = PluginSettings(config.data_dir)
plugins = PluginRuntime(config, plugin_settings, APPS_DIR)
controller.attach_plugins(plugins)
scheduler = Scheduler(controller, plugins)


def _redact(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    mqtt = cfg.get("transport", {}).get("mqtt", {})
    if mqtt.get("password"):
        mqtt["password"] = "********"
    return cfg


async def do_gateway_sync() -> dict:
    """Pull grid + MQTT settings from the gateway and apply them.

    The gateway is the source of truth for hardware config; the companion keeps
    only what the gateway can't give it (transport choice, MQTT password).
    """
    url = (config.transport.get("gateway_url") or "").strip()
    if not url:
        return {"ok": False, "error": "no gateway_url configured"}
    try:
        gw = await fetch_gateway_config(url)
    except Exception as e:
        log.warning("gateway sync failed: %s", e)
        return {"ok": False, "error": str(e)}
    patch = build_sync_patch(gw)
    if patch:
        config.update(patch)
        if "grid" in patch:
            controller.resize_grid()
        if "transport" in patch and config.transport.get("type") == "mqtt":
            await controller.reload_transport()
    return {
        "ok": True,
        "applied": patch,
        "gateway": {k: gw.get(k) for k in
                    ("gridRows", "gridCols", "mqHost", "mqPort", "mqUser", "mqPfx")},
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("SplitFlapGatewayCompanion v%s starting (transport=%s, grid=%sx%s)",
             __version__, config.transport.get("type"), config.grid["rows"], config.grid["cols"])
    await controller.start()
    if config.effective.get("sync_from_gateway") and config.transport.get("gateway_url"):
        res = await do_gateway_sync()
        if res.get("ok"):
            log.info("synced config from gateway: %s", res.get("applied"))
        else:
            log.info("gateway sync skipped at startup: %s", res.get("error"))
    plugins.load()
    log.info("loaded %d app plugins", len(plugins.app_list()))
    scheduler.start()
    # Register with the gateway (v3.0) so it can show a "Companion" tab.
    companion_url = config.effective.get("companion_url")
    if companion_url and config.transport.get("gateway_url"):
        ok = await register_companion(config.transport["gateway_url"], companion_url)
        log.info("companion registration %s", "ok" if ok else "skipped")
    yield
    await scheduler.stop()
    await controller.stop()


app = FastAPI(title="SplitFlapGatewayCompanion", version=__version__, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ComposeRequest(BaseModel):
    text: str
    style: str | None = None
    speed: int | None = None
    raw: bool = False


class ConfigPatch(BaseModel):
    grid: dict | None = None
    transport: dict | None = None
    display: dict | None = None
    sync_from_gateway: bool | None = None


class RunAppRequest(BaseModel):
    app: str


class AppSettingsPatch(BaseModel):
    values: dict


class InstallRequest(BaseModel):
    installed: bool


class PlaylistSave(BaseModel):
    name: str
    entries: list = []
    loop: bool = True


class RunPlaylist(BaseModel):
    entries: list = []
    loop: bool = True
    name: str | None = None


class TriggersPatch(BaseModel):
    triggers: list | None = None
    triggers_enabled: bool | None = None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__}


@app.get("/api/current_state")
async def current_state():
    return state.snapshot()


@app.get("/api/grid")
async def grid():
    g = config.grid
    return {
        "rows": int(g["rows"]),
        "cols": int(g["cols"]),
        "module_count": config.module_count(),
        "module_id_base": int(g.get("module_id_base", 0)),
        "flap_chars": renderer.FLAP_CHARS,
        "styles": list(renderer.ALL_STYLES),
        "color_map": renderer.COLOR_MAP,
        "display": config.display,
    }


@app.get("/api/config")
async def get_config():
    return _redact(config.effective)


@app.post("/api/config")
async def update_config(patch: ConfigPatch):
    body = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not body:
        raise HTTPException(400, "empty config patch")
    old_url = config.transport.get("gateway_url")
    config.update(body)
    if "grid" in body:
        controller.resize_grid()
    if "transport" in body:
        await controller.reload_transport()
    # If the gateway URL just changed and auto-sync is on, pull its config now.
    new_url = config.transport.get("gateway_url")
    if new_url and new_url != old_url and config.effective.get("sync_from_gateway"):
        await do_gateway_sync()
    return _redact(config.effective)


@app.post("/api/gateway/sync")
async def gateway_sync():
    """Pull grid geometry + MQTT settings from the gateway on demand."""
    return await do_gateway_sync()


# ---------------------------------------------------------------------------
# Apps / plugins
# ---------------------------------------------------------------------------
@app.get("/api/apps")
async def apps_list():
    return {"apps": plugins.app_list(), "active_app": controller.active_app}


@app.get("/api/apps/available")
async def apps_available():
    return {"apps": plugins.available_list()}


@app.post("/api/apps/run")
async def apps_run(req: RunAppRequest):
    app_id = req.app[7:] if req.app.startswith("plugin_") else req.app
    try:
        await controller.run_app(app_id)
    except KeyError:
        raise HTTPException(404, f"app not installed: {app_id}")
    return {"ok": True, "active_app": app_id}


@app.post("/api/apps/stop")
async def apps_stop():
    await controller.stop_app()
    return {"ok": True}


@app.get("/api/apps/{app_id}/settings")
async def apps_get_settings(app_id: str):
    try:
        return plugins.settings_schema(app_id)
    except KeyError:
        raise HTTPException(404, f"app not installed: {app_id}")


@app.post("/api/apps/{app_id}/settings")
async def apps_save_settings(app_id: str, patch: AppSettingsPatch):
    try:
        plugins.save_settings(app_id, patch.values)
    except KeyError:
        raise HTTPException(404, f"app not installed: {app_id}")
    return {"ok": True}


@app.get("/api/apps/{app_id}/preview")
async def apps_preview(app_id: str):
    if plugins.manifest(app_id) is None:
        raise HTTPException(404, f"app not installed: {app_id}")
    import asyncio
    pages = await asyncio.get_running_loop().run_in_executor(None, plugins.get_pages, app_id)
    return {"pages": pages, "rows": plugins.get_rows(), "cols": plugins.get_cols()}


@app.post("/api/apps/{app_id}/install")
async def apps_install(app_id: str, req: InstallRequest):
    if app_id not in plugins.discover():
        raise HTTPException(404, f"unknown app: {app_id}")
    if not req.installed and controller.active_app == app_id:
        await controller.stop_app()
    plugins.set_installed(app_id, req.installed)
    return {"ok": True, "installed": req.installed}


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------
@app.get("/api/playlists")
async def playlists_list():
    return {"playlists": plugin_settings.get("saved_app_playlists", {})}


@app.post("/api/playlists")
async def playlists_save(req: PlaylistSave):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    saved = dict(plugin_settings.get("saved_app_playlists", {}))
    saved[name] = {"entries": req.entries, "loop": req.loop}
    plugin_settings.set("saved_app_playlists", saved)
    return {"ok": True, "name": name}


@app.delete("/api/playlists/{name}")
async def playlists_delete(name: str):
    saved = dict(plugin_settings.get("saved_app_playlists", {}))
    saved.pop(name, None)
    plugin_settings.set("saved_app_playlists", saved)
    return {"ok": True}


@app.post("/api/playlists/run")
async def playlists_run(req: RunPlaylist):
    if not req.entries:
        raise HTTPException(400, "playlist has no entries")
    await controller.run_playlist(req.entries, req.loop, req.name)
    return {"ok": True, "active_playlist": controller.active_playlist}


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------
@app.get("/api/triggers")
async def triggers_get():
    trigs = []
    for t in plugin_settings.get("triggers", []):
        e = dict(t)
        e["last_fired"] = scheduler.last_fired(t.get("id", ""))
        trigs.append(e)
    return {
        "triggers": trigs,
        "triggers_enabled": plugin_settings.get("triggers_enabled", True),
        "trigger_apps": plugins.trigger_apps(),
    }


@app.post("/api/triggers")
async def triggers_save(patch: TriggersPatch):
    body = {k: v for k, v in patch.model_dump().items() if v is not None}
    if body:
        plugin_settings.update(body)
    return {"ok": True}


# ---------------------------------------------------------------------------
# App-data helper endpoints — served at the SAME root paths splitflap-os uses
# so dropped-in app manifests' searchUrl / resultKey work unchanged.
# ---------------------------------------------------------------------------
class SportsFollow(BaseModel):
    league: str
    teams: str = ""


@app.get("/location_search")
async def h_location_search(q: str = ""):
    return await helpers.location_search(q)


@app.get("/location_timezone")
async def h_location_timezone(lat: str = "", lon: str = ""):
    return await helpers.location_timezone(lat, lon)


@app.get("/timezones")
async def h_timezones(q: str = ""):
    return helpers.timezones(q)


@app.get("/stocks_search")
async def h_stocks_search(q: str = ""):
    return await helpers.stocks_search(q)


@app.get("/crypto_search")
async def h_crypto_search(q: str = ""):
    return await helpers.crypto_search(q)


@app.get("/sports_leagues")
async def h_sports_leagues():
    return helpers.sports_leagues(plugin_settings)


@app.get("/sports_teams/{league_key}")
async def h_sports_teams(league_key: str, q: str = ""):
    return await helpers.sports_teams(league_key, q)


@app.post("/sports_follow")
async def h_sports_follow(req: SportsFollow):
    return helpers.sports_follow(plugin_settings, req.league, req.teams)


@app.post("/api/compose/send")
async def compose_send(req: ComposeRequest):
    if req.style and req.style not in renderer.ALL_STYLES:
        raise HTTPException(400, f"unknown style: {req.style}")
    target = controller.send_text_bg(req.text, style=req.style, speed=req.speed, raw=req.raw)
    return {"ok": True, "target": target}


@app.post("/api/display/clear")
async def display_clear():
    await controller.clear()
    return {"ok": True}


@app.get("/api/gateway/status")
async def gateway_status():
    """Probe the gateway's /api/status and return its URL (for the Display tab)."""
    import httpx

    url = config.transport.get("gateway_url", "").rstrip("/")
    if not url:
        return {"ok": False, "url": "", "error": "no gateway_url configured"}
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{url}/api/status")
            return {"ok": r.status_code < 400, "url": url, "status_code": r.status_code, "data": r.json()}
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}


# ---------------------------------------------------------------------------
# Static SPA (mounted last so /api/* wins)
# ---------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="spa")
