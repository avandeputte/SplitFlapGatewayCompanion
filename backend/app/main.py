"""
main.py — FastAPI application.

Serves the SPA, the companion API (compose/send, live state, config), the plugin
runtime (apps, settings, library, playlists, triggers), the app-data helper
endpoints, the gateway reverse-proxy, and a best-effort gateway status probe.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, helpers, mcp_server, renderer, vestaboard, weather
from .config import Config, addon_option
from .engine import DisplayController
from .gateway import (addon_public_url, build_sync_patch, detect_local_ip,
                      fetch_gateway_config, fetch_gateway_settings, gateway_tabs,
                      post_companion, push_gateway_settings, supports_settings)
from .homeassistant import HomeAssistant
from .plugin_settings import PluginSettings
from .plugins import PluginRuntime
from .scheduler import Scheduler
from .state import DisplayState

# Log level from COMPANION_LOG_LEVEL (DEBUG/INFO/WARNING/ERROR/CRITICAL); default INFO.
# Configured at import, before Config exists, so the add-on's own `log_level` option
# is read straight from /data/options.json rather than through the config merge.
_LEVEL_NAME = os.environ.get("COMPANION_LOG_LEVEL") or addon_option("log_level", "INFO")
_LEVEL = getattr(logging, _LEVEL_NAME.strip().upper(), None)
if not isinstance(_LEVEL, int):
    _LEVEL = logging.INFO
logging.basicConfig(level=_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Keep chatty third-party libraries from flooding the log at DEBUG — we want the
# companion's own DEBUG lines, not httpcore/asyncio wire-level noise.
for _noisy in ("httpx", "httpcore", "asyncio", "urllib3", "paho", "python_multipart", "multipart"):
    logging.getLogger(_noisy).setLevel(max(_LEVEL, logging.INFO))
log = logging.getLogger("companion")
log.debug("log level set to %s", logging.getLevelName(_LEVEL))


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

# The MCP server is built unconditionally, even when the layer is off: the Dev-menu
# switch has to be able to turn it on without a restart, and an ASGI app can't be
# mounted after startup. _MCPGuard below is what makes the surface exist or 404.
mcp = mcp_server.build(config, state, controller, plugins, plugin_settings, ha)


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
    # The gateway is a single-threaded ESP32 that may be briefly busy (driving a
    # flap cascade, an in-flight settings transfer) when we ask, so a one-shot fetch
    # can transiently time out and make a resync a silent no-op. Retry a few times.
    gw, last_err = None, None
    for attempt in range(3):
        try:
            gw = await fetch_gateway_config(url)
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(0.4)
    if gw is None:
        log.warning("gateway sync failed after retries: %s", last_err)
        return {"ok": False, "error": str(last_err)}
    patch = build_sync_patch(gw)
    log.debug("gateway sync: config=%s -> patch=%s", {k: gw.get(k) for k in ("gridRows", "gridCols", "version")}, patch)
    if patch:
        config.update(patch)
        if "grid" in patch:
            controller.resize_grid()
            plugins.on_grid_changed()   # cached/channel pages were sized for the old grid
        # A sync only touches grid (resized above) and the HA MQTT broker; the
        # REST display transport depends only on gateway_url, which sync never
        # changes, so there's nothing to reload here.
    return {
        "ok": True,
        "applied": patch,
        "gateway": {k: gw.get(k) for k in
                    ("gridRows", "gridCols", "mqHost", "mqPort", "mqUser", "mqPfx", "haEnabled")},
    }


async def resolve_companion_url() -> str:
    """The URL to register with the gateway.

    In order: an explicit COMPANION_PUBLIC_URL (or the add-on's Companion URL option);
    then, as an add-on, the host address + published port that Supervisor reports; then
    this host's detected LAN IP + port.

    The add-on step is not an optimisation — it is the only correct answer there. The
    socket-based detection below sees the container's own address on Home Assistant's
    internal bridge (172.30.33.x), which no device on the LAN can reach, so the gateway
    was being handed a URL that could never work.
    """
    explicit = (config.effective.get("companion_url") or "").strip()
    if explicit:
        return explicit
    port = int(config.effective.get("port", 8000))
    if (url := await addon_public_url(port)):
        return url
    ip = detect_local_ip(config.transport.get("gateway_url", ""))
    return f"http://{ip}:{port}" if ip else ""


async def setup_settings_sync() -> None:
    """Wire settings storage per COMPANION_SETTINGS_STORE (needs Gateway 3.1+):
      * local   — nothing to do (the default local file is authoritative).
      * mirror  — mirror local settings onto the gateway; a fresh host with no local
                  file restores from the gateway, and an empty gateway is seeded.
      * gateway — the gateway is authoritative: load from it, stop writing locally.
    On a pre-3.1 (or unreachable) gateway this degrades to local, so the companion
    stays backward compatible with Gateway 3.0."""
    mode = config.effective.get("settings_store", "mirror")
    url = (config.transport.get("gateway_url") or "").strip()
    if mode == "local" or not url:
        return
    try:
        gw = await fetch_gateway_config(url)
    except Exception as e:
        gw = None
        log.info("settings sync: could not reach gateway to check version: %s", e)
    if not (gw and supports_settings(gw)):
        if mode == "gateway":
            log.warning("COMPANION_SETTINGS_STORE=gateway needs Gateway 3.1+; this gateway is "
                        "older or unreachable — falling back to LOCAL settings.")
        else:
            log.info("settings sync: gateway is pre-3.1 (or unreachable) — settings stay local.")
        return

    def _pusher(doc: dict) -> bool:
        return push_gateway_settings(url, doc)

    debounce = float(config.effective.get("settings_debounce", 3.0))
    if mode == "gateway":
        remote = await asyncio.to_thread(fetch_gateway_settings, url)
        if remote is not None:
            plugin_settings.restore_from_doc(remote)
        plugin_settings.set_gateway_only()               # nothing local
        plugin_settings.attach_gateway_sync(_pusher, debounce)
        if remote is None:
            plugin_settings.sync_now()                   # seed the empty gateway
        log.info("settings: stored ONLY on the gateway (3.1+)")
    else:  # mirror
        plugin_settings.attach_gateway_sync(_pusher, debounce)
        has_local = plugin_settings.has_local()
        remote = await asyncio.to_thread(fetch_gateway_settings, url)
        if not has_local and remote is not None:
            plugin_settings.restore_from_doc(remote)
            log.info("settings: restored from gateway (fresh host); mirroring enabled")
        elif has_local and remote is None:
            plugin_settings.sync_now()                   # seed the empty gateway from local
            log.info("settings: mirroring to gateway (seeding it from local)")
        else:
            log.info("settings: local primary, mirroring to gateway enabled")


async def _settings_flush_loop() -> None:
    """Periodically retry a pending settings push (covers a gateway that was briefly
    unreachable when a change happened)."""
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(plugin_settings.flush)
        except Exception:
            pass


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
    # Settings storage (mirror / gateway-only) — before plugins.load() so a list of
    # installed apps restored from the gateway drives what gets loaded.
    await setup_settings_sync()
    plugins.load()
    log.info("loaded %d app plugins", len(plugins.app_list()))
    scheduler.start()

    # Home Assistant: "auto" follows the gateway's haEnabled; true/false force it.
    # Started in the background so a slow/unreachable broker never delays startup.
    ha_mode = config.effective.get("ha", {}).get("enabled", "auto")
    ha_on = ha_mode is True if isinstance(ha_mode, bool) else bool(gw_ha)
    # Register with the gateway (v3.0) + status heartbeat, all in the background
    # so an unreachable gateway never delays startup.
    gw = config.transport.get("gateway_url", "")
    companion_url = await resolve_companion_url()
    tasks = []
    if ha_on:
        log.info("Home Assistant integration enabled")
        # Keep a strong reference (a bare create_task() can be GC'd mid-await,
        # and its exception would go unretrieved) and cancel it at shutdown.
        tasks.append(asyncio.create_task(ha.start()))
    if gw and companion_url:
        tasks.append(asyncio.create_task(_companion_heartbeat(gw, companion_url)))
        # If we auto-detected our URL, verify it's actually reachable. Skipped as an
        # add-on: Supervisor already told us the host's address and our published port,
        # and probing it from inside the container proves nothing about the LAN (and its
        # "launch with python -m app" advice would be nonsense there).
        if not (config.effective.get("companion_url") or "").strip() \
                and not os.environ.get("SUPERVISOR_TOKEN"):
            tasks.append(asyncio.create_task(_verify_reachable(companion_url)))
    # Retry pending settings pushes if the gateway was briefly unreachable.
    tasks.append(asyncio.create_task(_settings_flush_loop()))

    # Mounting an MCP app disables its own lifespan, so the host owns its session
    # manager — without this the first /mcp request fails. Cheap, and harmless when
    # the layer is off (the guard 404s before anything reaches it).
    async with mcp.session_manager.run():
        yield

    # Flush any pending settings to the gateway before shutting down.
    try:
        await asyncio.to_thread(plugin_settings.flush)
    except Exception:
        pass
    # Deregister on shutdown; cancel background tasks and let them unwind.
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
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


class DevSim(BaseModel):
    on: bool


class DevVestaboard(BaseModel):
    on: bool


class DevMCP(BaseModel):
    on: bool


class DevGrid(BaseModel):
    rows: int
    cols: int


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
        plugins.on_grid_changed()   # cached/channel pages were sized for the old grid
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
# Developer mode (gated by COMPANION_DEV_MODE). The GET is always safe to call
# (the UI uses `enabled` to decide whether to show the dev menu); the actions
# require dev mode to be on.
# ---------------------------------------------------------------------------
def _require_dev():
    if not config.dev_mode:
        raise HTTPException(404, "developer mode is off (set COMPANION_DEV_MODE=1)")


@app.get("/api/dev")
async def dev_state():
    return config.dev_state()


@app.post("/api/dev/sim")
async def dev_sim(req: DevSim):
    """Toggle simulation mode: on = nothing is sent to the display."""
    _require_dev()
    config.set_sim_mode(req.on)          # turning off also clears any grid override
    await controller.reload_transport()  # swap REST <-> sim
    controller.resize_grid()             # geometry may have reverted
    plugins.on_grid_changed()
    return config.dev_state()


@app.post("/api/dev/vestaboard")
async def dev_vestaboard(req: DevVestaboard):
    """Turn the Vestaboard-compatible Local API on/off at runtime (see the block at
    the bottom of this file). COMPANION_VESTABOARD sets where it starts."""
    _require_dev()
    config.set_vestaboard(req.on)
    if req.on:
        vestaboard_key()   # mint + persist the key now, so the menu can show it
    log.info("Vestaboard API %s (dev menu)", "enabled" if req.on else "disabled")
    return config.dev_state()


@app.get("/api/dev/vestaboard")
async def dev_vestaboard_state():
    """The Vestaboard connection details, for the dev menu to display: the key a
    client must send, and the endpoint it posts to. Dev-gated because it hands out
    the key."""
    _require_dev()
    on = config.vestaboard_enabled
    return {
        "enabled": on,
        "key": vestaboard_key() if on else "",
        "path": "/local-api/message",
        "env_key": bool(config.vestaboard.get("api_key")),   # pinned via env, not generated
    }


@app.post("/api/dev/mcp")
async def dev_mcp(req: DevMCP):
    """Turn the MCP server on/off at runtime (see the block further down).
    COMPANION_MCP sets where it starts."""
    _require_dev()
    config.set_mcp(req.on)
    if req.on:
        mcp_token()        # mint + persist the token now, so the menu can show it
    log.info("MCP server %s (dev menu)", "enabled" if req.on else "disabled")
    return config.dev_state()


@app.get("/api/dev/mcp")
async def dev_mcp_state():
    """The MCP connection details for the dev menu: the endpoint an LLM client
    points at and the bearer token it must send. Dev-gated because it hands out
    the token."""
    _require_dev()
    on = config.mcp_enabled
    return {
        "enabled": on,
        "token": mcp_token() if on else "",
        "path": "/mcp",
        "env_token": bool(config.mcp.get("token")),          # pinned via env, not generated
    }


@app.post("/api/dev/resync")
async def dev_resync():
    """Force a settings resync with the gateway."""
    _require_dev()
    return await do_gateway_sync()


@app.post("/api/dev/grid")
async def dev_grid(req: DevGrid):
    """Override the grid geometry — only while simulating (so the real display's
    gateway-derived geometry is never touched)."""
    _require_dev()
    if not config.sim_mode:
        raise HTTPException(400, "turn simulation mode on before overriding the grid")
    config.set_grid_override(req.rows, req.cols)
    controller.resize_grid()
    plugins.on_grid_changed()
    return config.dev_state()


async def _gateway_settings_ready() -> tuple[str, dict] | dict:
    """(url, gateway-config) if the gateway is reachable and supports settings storage
    (3.1+); otherwise an error dict to return to the caller."""
    url = (config.transport.get("gateway_url") or "").strip()
    if not url:
        return {"ok": False, "error": "no gateway_url configured"}
    try:
        gw = await fetch_gateway_config(url)
    except Exception as e:
        return {"ok": False, "error": f"gateway unreachable: {e}"}
    if not supports_settings(gw):
        return {"ok": False, "error": "this gateway does not store settings (needs Gateway 3.1+)"}
    return url, gw


@app.post("/api/dev/settings/pull")
async def dev_settings_pull():
    """Force-retrieve the settings blob from the gateway and apply it."""
    _require_dev()
    ready = await _gateway_settings_ready()
    if isinstance(ready, dict):
        return ready
    url, _ = ready
    doc = await asyncio.to_thread(fetch_gateway_settings, url)
    if doc is None:
        return {"ok": False, "error": "no settings are stored on the gateway yet"}
    plugin_settings.restore_from_doc(doc)
    plugins.load()                 # apply the restored installed-apps list + settings
    ha.refresh_discovery()         # the app/playlist option lists may have changed
    log.info("dev: retrieved settings from the gateway (%d apps installed)", len(plugin_settings.installed_apps))
    return {"ok": True, "applied": True, "installed": len(plugin_settings.installed_apps)}


@app.post("/api/dev/settings/push")
async def dev_settings_push():
    """Force-write the current settings to the gateway now."""
    _require_dev()
    ready = await _gateway_settings_ready()
    if isinstance(ready, dict):
        return ready
    url, _ = ready
    ok = await asyncio.to_thread(push_gateway_settings, url, plugin_settings.snapshot())
    log.info("dev: pushed settings to the gateway (ok=%s)", ok)
    return {"ok": ok, "error": None if ok else "push failed"}


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
    # If this app is on the display right now, restart it so the new settings
    # (page dwell, refresh cadence, content options) take effect immediately.
    if controller.active_app == app_id:
        await controller.run_app(app_id)
    return {"ok": True}


@app.get("/api/global-settings")
async def global_settings_get():
    """Shared settings apps rely on (weather_api_key, timezone, location, …)."""
    return plugins.global_settings_schema()


@app.post("/api/global-settings")
async def global_settings_save(patch: AppSettingsPatch):
    plugins.save_global_settings(patch.values)
    # Globals (location, provider, page dwell, …) can change what the running app
    # shows or how fast it cycles — restart it so the change is visible at once.
    if controller.active_app:
        await controller.run_app(controller.active_app)
    return {"ok": True}


@app.get("/api/apps/{app_id}/preview")
async def apps_preview(app_id: str):
    if plugins.manifest(app_id) is None:
        raise HTTPException(404, f"app not installed: {app_id}")
    pages = await asyncio.get_running_loop().run_in_executor(None, plugins.get_pages, app_id)
    return {"pages": pages, "rows": plugins.get_rows(), "cols": plugins.get_cols()}


@app.post("/api/apps/{app_id}/install")
async def apps_install(app_id: str, req: InstallRequest):
    if app_id not in plugins.discover():
        raise HTTPException(404, f"unknown app: {app_id}")
    if not req.installed and controller.active_app == app_id:
        await controller.stop_app()
    # set_installed() reloads all plugins (re-executes each app.py), so run it off
    # the event loop to avoid freezing the display loop and other requests.
    await asyncio.get_running_loop().run_in_executor(
        None, plugins.set_installed, app_id, req.installed)
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
        # delete_app() reloads all plugins — run it off the event loop.
        await asyncio.get_running_loop().run_in_executor(None, plugins.delete_app, app_id)
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
# App-data helper endpoints — served at the fixed root paths that dropped-in app
# manifests' searchUrl / resultKey point at, so they work unchanged.
# ---------------------------------------------------------------------------
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


@app.get("/sports_search")
async def h_sports_search(q: str = ""):
    return await helpers.sports_search(q)


@app.get("/weather")
async def h_weather():
    """Current conditions via the global provider/key/location — the same shared
    helper apps get injected as get_weather()."""
    return await asyncio.to_thread(weather.fetch_current, plugin_settings)


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


@app.post("/api/display/home")
async def display_home():
    """Physically home every module (gateway broadcast), stop any running
    app/playlist, and blank the live preview. Best-effort: reports the reason on
    failure rather than raising, so the UI can surface it inline."""
    try:
        ok = await controller.home_all()
        return {"ok": ok, "error": None if ok else "gateway rejected the home command"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/gateway/status")
async def gateway_status():
    """Probe the gateway's /api/status and return its URL (for the Display tab).

    ``tabs`` is the gateway's own tab list as it advertised it when we registered
    (Gateway 3.4+); empty means it never did — an older firmware, or we haven't
    reached it yet — and the UI falls back to its built-in list. See tabs.py.
    """
    import httpx

    tabs = gateway_tabs()
    url = config.transport.get("gateway_url", "").rstrip("/")
    if not url:
        return {"ok": False, "url": "", "tabs": tabs, "error": "no gateway_url configured"}
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{url}/api/status")
            return {"ok": r.status_code < 400, "url": url, "tabs": tabs,
                    "status_code": r.status_code, "data": r.json()}
    except Exception as e:
        return {"ok": False, "url": url, "tabs": tabs, "error": str(e)}


# ---------------------------------------------------------------------------
# Vestaboard-compatible Local API (off unless COMPANION_VESTABOARD=1, or the dev
# menu turns it on). Anything that speaks to a Vestaboard — a Home Assistant
# rest_command, the HACS integration, a script — can then drive this display by
# URL alone. The codec is in vestaboard.py; these are the endpoints.
#
# The paths are Vestaboard's, so they sit at the root rather than under /api/*,
# like the app-data helpers above — and so they must be declared BEFORE the SPA
# is mounted at "/". A real board answers on port 7000; publish the container as
# `-p 7000:8000` and clients that hard-code that port are satisfied too.
#
# NOTE: this key guards these routes ONLY. The rest of the companion's API is
# unauthenticated, as it always was — the key is Vestaboard compatibility, not a
# security boundary for the host.
# ---------------------------------------------------------------------------
VESTABOARD_KEY_SETTING = "vestaboard_api_key"


def _require_vestaboard() -> dict:
    """The Vestaboard config, or 404 when the layer is off — so the whole surface
    genuinely vanishes rather than answering 401s nobody can satisfy."""
    if not config.vestaboard_enabled:
        raise HTTPException(404, "Vestaboard API is off (set COMPANION_VESTABOARD=1)")
    return config.vestaboard


def vestaboard_key() -> str:
    """The Local API key: the env value if set, else one generated once and kept in
    the settings store (which persists to /data and mirrors to the gateway). A key
    that changed on every restart would silently break an already-configured client,
    so it must outlive the process."""
    env_key = config.vestaboard.get("api_key") or ""
    if env_key:
        return env_key
    stored = plugin_settings.get(VESTABOARD_KEY_SETTING) or ""
    if not stored:
        stored = secrets.token_urlsafe(24)
        plugin_settings.set(VESTABOARD_KEY_SETTING, stored)
        log.info("Vestaboard API: generated an API key (see the Dev menu, or set "
                 "COMPANION_VESTABOARD_KEY to pin your own)")
    return stored


def _check_key(request: Request) -> None:
    key = request.headers.get("X-Vestaboard-Local-Api-Key", "")
    if not key or not secrets.compare_digest(key, vestaboard_key()):
        raise HTTPException(401, "invalid or missing X-Vestaboard-Local-Api-Key")


@app.post("/local-api/enablement")
async def vb_enablement(request: Request):
    """Vestaboard's enablement handshake: present the token, get the API key back.
    On a real board the token is emailed to the owner; here it is whatever you set
    in COMPANION_VESTABOARD_ENABLEMENT_TOKEN. With no token set there is nothing to
    verify, so the exchange is refused (the key is in the Dev menu instead)."""
    vb = _require_vestaboard()
    token = vb.get("enablement_token") or ""
    if not token:
        raise HTTPException(403, "no enablement token configured "
                                 "(set COMPANION_VESTABOARD_ENABLEMENT_TOKEN)")
    sent = request.headers.get("X-Vestaboard-Local-Api-Enablement-Token", "")
    if not sent or not secrets.compare_digest(sent, token):
        raise HTTPException(403, "invalid enablement token")
    return {"message": "Local API enabled", "apiKey": vestaboard_key()}


@app.get("/local-api/message")
async def vb_read_message(request: Request):
    """The board as it stands, as a character-code matrix — which is what this
    endpoint means on real hardware: whatever the flaps are showing, including a
    running app's output, not merely the last message someone posted."""
    _require_vestaboard()
    _check_key(request)
    g = config.grid
    rows, cols = int(g["rows"]), int(g["cols"])
    return vestaboard.encode(state.current_chars, rows, cols)


@app.post("/local-api/message")
async def vb_send_message(request: Request):
    """Post a message. Takes every shape a Vestaboard client sends:

        [[0,8,5,...], ...]                        a bare character-code matrix
        {"characters": [[...]], "strategy": ...}  ...with an animation
        {"text": "HELLO"}                         an extension of ours, because most
                                                  Home Assistant setups send text

    Like a compose push, this takes the display over: any running app or playlist is
    cancelled (send_text_bg), which is what posting to a Vestaboard implies.
    """
    _require_vestaboard()
    _check_key(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")

    g = config.grid
    rows, cols = int(g["rows"]), int(g["cols"])
    strategy = None

    try:
        if isinstance(body, list):                       # the bare-matrix form
            page = vestaboard.fit(vestaboard.decode(body), rows, cols)
        elif isinstance(body, dict) and body.get("characters") is not None:
            strategy = body.get("strategy")
            page = vestaboard.fit(vestaboard.decode(body["characters"]), rows, cols)
        elif isinstance(body, dict) and isinstance(body.get("text"), str):
            strategy = body.get("strategy")
            # The board has no lowercase flaps; uppercase exactly the way every other
            # text path here does (cp1252-aware, so accents survive as one cell).
            page = vestaboard.layout_text(renderer.cp1252_upper(body["text"]), rows, cols)
        else:
            raise HTTPException(422, "expected a character matrix, {\"characters\": [[...]]}, "
                                     "or {\"text\": \"...\"}")
    except vestaboard.VestaboardError as e:
        raise HTTPException(422, str(e))

    style = vestaboard.style_for(strategy, config.display.get("transition_style", "ltr"))
    # raw=True: the codec already produced final characters, and uppercasing here
    # would turn every colour chip (lowercase r/o/y/g/b/p/w) into a letter.
    controller.send_text_bg(page, style=style, raw=True)
    ha.publish_state()
    return {"ok": True}


# ---------------------------------------------------------------------------
# MCP server (off unless COMPANION_MCP=1, or the dev menu turns it on). Lets an
# LLM client drive the display as tools; the tools are in mcp_server.py, and this
# is the gate in front of them.
#
# NOTE: like the Vestaboard key, this token guards ONLY /mcp. The rest of the
# companion's API is unauthenticated, as it always was.
# ---------------------------------------------------------------------------
MCP_TOKEN_SETTING = "mcp_token"


def mcp_token() -> str:
    """The MCP bearer token: the env value if set, else one generated once and kept
    in the settings store. A token that changed on every restart would silently break
    an already-configured client, so it has to outlive the process."""
    env_token = config.mcp.get("token") or ""
    if env_token:
        return env_token
    stored = plugin_settings.get(MCP_TOKEN_SETTING) or ""
    if not stored:
        stored = secrets.token_urlsafe(24)
        plugin_settings.set(MCP_TOKEN_SETTING, stored)
        log.info("MCP: generated a bearer token (see the Dev menu, or set "
                 "COMPANION_MCP_TOKEN to pin your own)")
    return stored


async def _asgi_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class _MCPGuard:
    """The gate in front of the mounted MCP app: 404 as a whole when the layer is
    off, 401 without the bearer token.

    It is an ASGI wrapper rather than a FastAPI dependency because a Mounted app is
    handed the request whole — route dependencies never run for it.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            if not config.mcp_enabled:
                await _asgi_json(send, 404, "MCP server is off (set COMPANION_MCP=1)")
                return
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1")
            sent = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
            if not sent or not secrets.compare_digest(sent, mcp_token()):
                await _asgi_json(send, 401, "invalid or missing bearer token")
                return
        await self.inner(scope, receive, send)


class _MCPPathFix:
    """Make a bare ``/mcp`` reach the mount below.

    Starlette's ``Mount("/mcp")`` only matches ``/mcp/<something>`` — a request to
    ``/mcp`` itself is a partial match, and because the SPA is mounted at ``/`` it
    gets claimed by StaticFiles instead (which answers 405 to a POST, since it only
    serves GET). But ``/mcp`` with no trailing slash is exactly what every MCP client
    posts to, so normalise the path before routing rather than telling users to add a
    slash no other MCP server needs.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            scope = dict(scope, path="/mcp/", raw_path=b"/mcp/")
        await self.app(scope, receive, send)


# Before the SPA mount below, for the same reason /local-api/* is: "/" swallows
# everything that hasn't already been claimed.
app.mount("/mcp", _MCPGuard(mcp.streamable_http_app()))
app.add_middleware(_MCPPathFix)


# ---------------------------------------------------------------------------
# Static SPA (mounted last so /api/* wins)
# ---------------------------------------------------------------------------
_asset_ver_cache: dict[str, tuple[float, str]] = {}   # asset -> (mtime, hash)


def _asset_version(p: Path) -> str | None:
    """Content hash of an asset, recomputed only when its mtime changes (so we
    don't re-read + md5 the files on every page load)."""
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    cached = _asset_ver_cache.get(p.name)
    if cached and cached[0] == mtime:
        return cached[1]
    ver = hashlib.md5(p.read_bytes()).hexdigest()[:10]
    _asset_ver_cache[p.name] = (mtime, ver)
    return ver


def _cache_bust(html: str, static_dir: Path, base: str = "") -> str:
    """Append ``?v=<content-hash>`` to each SPA asset URL so an updated CSS/JS is
    fetched fresh without a manual browser cache purge. The query changes only
    when the file's bytes change, so unchanged assets still cache. ``base`` prefixes
    them with the ingress path (see spa_index)."""
    for asset in ("styles.css", "app.js"):
        ver = _asset_version(static_dir / asset)
        query = f"?v={ver}" if ver else ""
        html = html.replace(f'"/{asset}"', f'"{base}/{asset}{query}"')
    return html


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
async def spa_index(request: Request):
    """Serve the SPA shell. Two things are stamped in here rather than baked into
    the file, because neither is known until the request arrives:

    * **The ingress prefix.** As a Home Assistant add-on the SPA is served from
      ``/api/hassio_ingress/<token>/``, and Supervisor says so in ``X-Ingress-Path``.
      A browser-side ``/api/...`` URL would resolve against the *HA* root and 404, so
      every asset URL is prefixed here and the SPA reads the same prefix off
      ``window.__BASE__`` for its fetches (see app.js).
    * **The theme.** ``COMPANION_THEME=ha`` layers the Home Assistant skin over the
      base stylesheet — same image, same app, just a different look, so the add-on
      doesn't read as a foreign site inside the sidebar.
    """
    base = (request.headers.get("X-Ingress-Path") or "").rstrip("/")
    html = _cache_bust((STATIC_DIR / "index.html").read_text("utf-8"), STATIC_DIR, base)

    head = f"<script>window.__BASE__={json.dumps(base)};</script>"
    if config.theme == "ha":
        ver = _asset_version(STATIC_DIR / "theme-ha.css")
        head += f'<link rel="stylesheet" href="{base}/theme-ha.css{f"?v={ver}" if ver else ""}" />'
    # Last thing in <head>, so the theme wins over styles.css.
    html = html.replace("</head>", f"  {head}\n</head>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="spa")
