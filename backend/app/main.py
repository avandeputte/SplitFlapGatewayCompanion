"""
main.py — FastAPI application (Phase 1 slice).

Serves the SPA, the companion API (compose/send, live state, config), and a
best-effort gateway status probe. Later phases add the plugin runtime,
playlists/schedules/triggers, and the gateway reverse-proxy.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, helpers, renderer
from .config import Config
from .engine import DisplayController
from .gateway import build_sync_patch, detect_local_ip, fetch_gateway_config, post_companion
from .homeassistant import HomeAssistant
from .plugin_settings import PluginSettings
from .plugins import PluginRuntime
from .scheduler import Scheduler
from .state import DisplayState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("companion")


class _SuppressStatePolling(logging.Filter):
    """The preview polls /api/current_state a few times a second — drop just
    those access-log lines so they don't flood the console (all other requests
    still log). uvicorn configures its access logger before importing this app,
    so attaching the filter here is reliable."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/current_state" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_SuppressStatePolling())

STATIC_DIR = Path(__file__).resolve().parent / "static"
APPS_DIR = Path(__file__).resolve().parents[2] / "apps"

config = Config()
state = DisplayState(config.module_count())
controller = DisplayController(config, state)
plugin_settings = PluginSettings(config.data_dir)
# User-uploaded apps live in the persistent data volume, not the image's apps/.
plugins = PluginRuntime(config, plugin_settings, APPS_DIR, config.data_dir / "apps")
controller.attach_plugins(plugins)
scheduler = Scheduler(controller, plugins)
ha = HomeAssistant(config, plugins, controller)


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
        # A sync only touches grid (resized above) and the HA MQTT broker; the
        # REST display transport depends only on gateway_url, which sync never
        # changes, so there's nothing to reload here.
    return {
        "ok": True,
        "applied": patch,
        "gateway": {k: gw.get(k) for k in
                    ("gridRows", "gridCols", "mqHost", "mqPort", "mqUser", "mqPfx", "haEnabled")},
    }


def resolve_companion_url() -> str:
    """The URL to register with the gateway: explicit COMPANION_PUBLIC_URL, else
    this host's detected LAN IP + port."""
    explicit = (config.effective.get("companion_url") or "").strip()
    if explicit:
        return explicit
    ip = detect_local_ip(config.transport.get("gateway_url", ""))
    return f"http://{ip}:{int(config.effective.get('port', 8000))}" if ip else ""


def companion_status_string() -> str:
    """Short human status for the gateway's status page."""
    if controller.active_app:
        m = plugins.manifest(controller.active_app)
        return f"App: {m['name']}" if m and m.get("name") else f"App: {controller.active_app}"
    if controller.active_playlist:
        return f"Playlist: {controller.active_playlist}"
    return "Idle"


async def _companion_heartbeat(gateway_url: str, companion_url: str):
    """Register immediately, then keep the gateway posted on our status.

    Runs entirely in the background so an unreachable gateway never delays
    startup (a POST to an unreachable host can take several seconds)."""
    try:
        ok = await post_companion(gateway_url, url=companion_url, status=companion_status_string())
        log.info("companion registered as %s (%s)", companion_url,
                 "ok" if ok else "gateway unreachable — will retry")
    except Exception as e:
        log.debug("companion registration error: %s", e)
    while True:
        await asyncio.sleep(30)
        try:
            await post_companion(gateway_url, url=companion_url, status=companion_status_string())
        except Exception as e:
            log.debug("companion heartbeat error: %s", e)


async def _verify_reachable(companion_url: str):
    """Confirm the URL we registered is actually reachable. If the server was
    launched with `uvicorn ...` (binds 127.0.0.1) instead of `python -m app`
    (binds 0.0.0.0), the LAN URL we register won't be reachable — warn loudly."""
    import httpx

    await asyncio.sleep(4)  # let the server start accepting connections
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(companion_url.rstrip("/") + "/api/health")
            if r.status_code < 500:
                return  # reachable — all good
    except Exception:
        pass
    log.warning(
        "⚠ Registered with the gateway as %s, but that address is NOT reachable "
        "— the server looks bound to localhost. Launch with `python -m app` "
        "(binds 0.0.0.0), or if you use uvicorn directly add `--host 0.0.0.0`.",
        companion_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Hard requirement: without a gateway there is nothing to drive. Refuse to
    # start (rather than silently retrying a phantom host). __main__.py catches
    # this earlier with a friendlier message; this also covers `uvicorn app.main:app`.
    if not (config.transport.get("gateway_url") or "").strip():
        raise RuntimeError(
            "GATEWAY_URL is not set. Set it to your SplitFlapGateway's URL "
            "(e.g. GATEWAY_URL=http://192.168.1.50) and restart."
        )
    log.info("SplitFlapGatewayCompanion v%s starting (transport=REST %s, grid=%sx%s)",
             __version__, config.transport.get("gateway_url"),
             config.grid["rows"], config.grid["cols"])
    await controller.start()
    gw_ha = None
    if config.effective.get("sync_from_gateway") and config.transport.get("gateway_url"):
        res = await do_gateway_sync()
        if res.get("ok"):
            log.info("synced config from gateway: %s", res.get("applied"))
            gw_ha = res.get("gateway", {}).get("haEnabled")
        else:
            log.info("gateway sync skipped at startup: %s", res.get("error"))
    plugins.load()
    log.info("loaded %d app plugins", len(plugins.app_list()))
    scheduler.start()

    # Home Assistant: "auto" follows the gateway's haEnabled; true/false force it.
    # Started in the background so a slow/unreachable broker never delays startup.
    ha_mode = config.effective.get("ha", {}).get("enabled", "auto")
    ha_on = ha_mode is True if isinstance(ha_mode, bool) else bool(gw_ha)
    if ha_on:
        log.info("Home Assistant integration enabled")
        asyncio.create_task(ha.start())

    # Register with the gateway (v3.0) + status heartbeat, all in the background
    # so an unreachable gateway never delays startup.
    gw = config.transport.get("gateway_url", "")
    companion_url = resolve_companion_url()
    tasks = []
    if gw and companion_url:
        tasks.append(asyncio.create_task(_companion_heartbeat(gw, companion_url)))
        # If we auto-detected our URL, verify it's actually reachable.
        if not (config.effective.get("companion_url") or "").strip():
            tasks.append(asyncio.create_task(_verify_reachable(companion_url)))

    yield

    # Deregister on shutdown.
    for t in tasks:
        t.cancel()
    if gw and companion_url:
        try:
            await post_companion(gw, url="")
            log.info("companion deregistered from gateway")
        except Exception:
            pass
    await ha.stop()
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
    ha.publish_state()
    return {"ok": True, "active_app": app_id}


@app.post("/api/apps/stop")
async def apps_stop():
    await controller.stop_app()
    ha.publish_state()
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
    ha.refresh_discovery()  # app option list changed
    ha.publish_state()
    return {"ok": True, "installed": req.installed}


@app.post("/api/apps/upload")
async def apps_upload(file: UploadFile = File(...)):
    """Upload + register a new app from a .zip (manifest.json + app.py/data.json).

    Note: a functional app's app.py is executed to validate it — only upload
    apps you trust."""
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "app too large (max 8 MB)")
    try:
        info = await asyncio.get_running_loop().run_in_executor(None, plugins.install_zip, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    ha.refresh_discovery()
    log.info("uploaded app: %s (%s)", info["id"], info["type"])
    return {"ok": True, **info}


@app.delete("/api/apps/{app_id}")
async def apps_delete(app_id: str):
    """Delete a user-uploaded app entirely (built-ins can't be deleted)."""
    if controller.active_app == app_id:
        await controller.stop_app()
    try:
        plugins.delete_app(app_id)
    except KeyError:
        raise HTTPException(404, f"unknown app: {app_id}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    ha.refresh_discovery()
    return {"ok": True}


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
    ha.refresh_discovery()  # playlist option list changed
    return {"ok": True, "name": name}


@app.delete("/api/playlists/{name}")
async def playlists_delete(name: str):
    saved = dict(plugin_settings.get("saved_app_playlists", {}))
    saved.pop(name, None)
    plugin_settings.set("saved_app_playlists", saved)
    ha.refresh_discovery()
    return {"ok": True}


@app.post("/api/playlists/run")
async def playlists_run(req: RunPlaylist):
    if not req.entries:
        raise HTTPException(400, "playlist has no entries")
    await controller.run_playlist(req.entries, req.loop, req.name)
    ha.publish_state()
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
