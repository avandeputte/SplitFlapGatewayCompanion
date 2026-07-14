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
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, gwproxy, helpers, mcp_server, renderer, uilang, vestaboard, weather
from .catalog import GLOBAL_STORAGE_KEYS
from .config import Config, addon_option, default_data_dir
from .display import DisplayManager
from .engine import DisplayController
from .gateway import (addon_public_url, build_sync_patch, detect_local_ip,
                      fetch_gateway_config, fetch_gateway_settings,
                      post_companion, push_gateway_settings, supports_settings)
from .homeassistant import HomeAssistant
from .plugin_settings import PluginSettings
from .plugins import PluginRuntime
from .registry import DisplayRegistry
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

# One companion, N displays (docs/MULTI_DISPLAY_PLAN.md).
#
# Phase 0 gave a Display ownership of what used to be module globals here: the geometry,
# the settings store, the app loop, the HA device. Phase 1 gives the SET of them an
# identity on disk:
#
#   data/displays.json                    which walls exist; which one is the default
#   data/displays/<id>/app_settings.json  one settings store per wall, ENTIRELY its own
#
# Every setting a display has is per display, credentials included. That is not a
# preference: the gateway is the BACKUP for its wall's settings (setup_settings_sync
# mirrors the whole doc onto it, and a rebuilt host restores from it), so anything held
# in a companion-local file shared across displays would have no gateway to live on and
# could never be recovered. The cost is entering an API key once per wall.
#
# The registry seeds itself from the GATEWAY_URL the companion was already configured
# with, so an existing install upgrades with no configuration at all and the add-on's
# one required option keeps meaning what it meant.
DATA_DIR = default_data_dir()
# GATEWAY_URL (and the add-on's gateway_url option) takes a COMMA-DELIMITED list, so two
# walls can be configured from the single option a Home Assistant user already has:
#   GATEWAY_URL=http://192.168.1.218,http://192.168.1.50
_seed_urls = [u.strip() for u in
              (Config(DATA_DIR).transport.get("gateway_url") or "").split(",") if u.strip()]
_seed_url = _seed_urls[0] if _seed_urls else ""

registry = DisplayRegistry(DATA_DIR).ensure(gateway_url=_seed_url)
# The first entry keeps owning display `default`: that Configuration tab is where a Home
# Assistant user has always set their gateway, and someone fixing a typo'd IP there must
# not find the registry silently ignored them. Later entries only ever ADD displays —
# a wall added in the UI is never removed because it stopped appearing in the env.
registry.adopt_env_gateway(_seed_url)
registry.adopt_env_gateways(_seed_urls)


def _mirror_registry() -> None:
    """The set of walls changed — make sure every gateway's copy is brought up to date, not
    just the ones whose own settings happen to have moved."""
    for d in displays.all():
        try:
            d.settings.sync_now()
        except Exception:
            pass

displays = DisplayManager(APPS_DIR, registry=registry, data_dir=DATA_DIR)
displays.load_registry()
registry.on_change = _mirror_registry
_default = displays.default

# Names kept for the module-level consumers that are not per-request: the lifespan, the
# background loops, and the import-time wiring below (an ASGI app cannot be mounted
# after startup). They are the DEFAULT display's objects -- the same instances the
# manager hands out -- so patching one patches both, and there is a single source of
# truth for the display the display-less surfaces mean.
config = _default.config
state = _default.state
controller = _default.controller
plugin_settings = _default.settings
plugins = _default.plugins
scheduler = _default.scheduler
ha = _default.ha


def display_by_id(display_id: str):
    """The display a PATH names. Used where the caller cannot send `?display=` — a
    Vestaboard client posts to a fixed URL and knows nothing about our displays."""
    d = displays.get(display_id)
    if d is None:
        raise HTTPException(404, f"no such display: {display_id}")
    return d


def display_for(request: Request | None = None):
    """The display this request is about — the seam every endpoint resolves through.

    Phase 0 always returns the default (there is only one). Phase 2 teaches
    DisplayManager.current() to honour ?display=<id> here, and every endpoint below
    follows without being touched again.
    """
    try:
        return displays.current(request)
    except KeyError as e:
        raise HTTPException(404, f"no such display: {e.args[0]}")

# The MCP server is built unconditionally, even when the layer is off: the Dev-menu
# switch has to be able to turn it on without a restart, and an ASGI app can't be
# mounted after startup. _MCPGuard below is what makes the surface exist or 404.
mcp = mcp_server.build(displays)


def _redact(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    mqtt = cfg.get("transport", {}).get("mqtt", {})
    if mqtt.get("password"):
        mqtt["password"] = "********"
    return cfg


async def do_gateway_sync(d=None) -> dict:
    """Pull grid + MQTT settings from ONE display's gateway and apply them.

    The gateway is the source of truth for hardware config; the companion keeps
    only what the gateway can't give it (transport choice, MQTT password). Scoped to a
    display: each wall has its own geometry, and a second gateway must not resize the
    first.
    """
    d = d or displays.default
    config, controller, plugins = d.config, d.controller, d.plugins
    url = d.gateway_url
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
    before = dict(config.grid)
    changed = False
    if patch:
        config.update(patch)
        # The gateway reports its geometry every time, so "grid in patch" says nothing
        # about whether it MOVED. Compare the effective grid instead: without this the
        # periodic resync below would re-render every channel app every 30 seconds.
        if config.grid != before:
            changed = True
            log.info("gateway geometry changed [%s]: %sx%s -> %sx%s (%d modules)",
                     d.id, before.get("rows"), before.get("cols"),
                     config.grid["rows"], config.grid["cols"], config.module_count())
            controller.resize_grid()
            plugins.on_grid_changed()   # cached/channel pages were sized for the old grid
        # A sync only touches grid (resized above) and the HA MQTT broker; the
        # REST display transport depends only on gateway_url, which sync never
        # changes, so there's nothing to reload here.

    # Re-ask what the wall can show. Capabilities are not fixed for the life of a process: the
    # gateway can be re-flashed to a firmware that grows a feature, and a module can be swapped
    # for one carrying a different reel. Probing only at boot would leave the companion driving
    # today's wall with last week's answer — folding case it no longer needs to fold, or
    # sending a character that is no longer on the reel.
    probe = getattr(controller.transport, "probe_capabilities", None)
    if probe is not None:
        try:
            await probe()
        except Exception as e:                       # a resync must not fail over this
            log.debug("capabilities re-probe failed [%s]: %s", d.id, e)

    return {
        "ok": True,
        "applied": patch,
        "grid_changed": changed,
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


LAST_RUN_SETTING = "last_run"


def _remember_driver(doc: dict | None, d=None) -> None:
    """Record what is driving the display, so a restart can pick it up again.

    Wired into the engine (attach_persist), which is the one place that sees every way
    the display gets driven — the API, the scheduler, a trigger, MCP.

    Only writes on a *change*: this fires on every manual message too, and the settings
    store writes to disk and mirrors to the gateway.
    """
    d = d or displays.default
    doc = doc or {}
    if (d.settings.get(LAST_RUN_SETTING) or {}) == doc:
        return
    d.settings.set(LAST_RUN_SETTING, doc)


async def resume_last_run(d=None) -> None:
    """Start whatever was playing when we were last shut down — for ONE display.

    A container that updates itself (the Home Assistant add-on does) used to come back to
    a dead display: the playlist that had been running simply stopped. Nothing else knows
    to restart it — the gateway holds the hardware config, not what the companion was
    doing — so it has to be remembered here.
    """
    d = d or displays.default
    doc = d.settings.get(LAST_RUN_SETTING) or {}
    kind = doc.get("kind")
    if not kind:
        return
    try:
        if kind == "app":
            await d.controller.run_app(doc["app"])
            log.info("resumed app %s after restart [%s]", doc["app"], d.id)
        elif kind == "playlist":
            entries = doc.get("entries") or []
            if not entries:
                raise ValueError("no entries")
            name = doc.get("name") or None
            await d.controller.run_playlist(entries, doc.get("loop", True), name)
            log.info("resumed playlist %s after restart [%s]", name or "(unsaved)", d.id)
        d.ha.publish_state()
    except Exception as e:
        # An app uninstalled since the last run, a playlist emptied — don't keep trying.
        log.warning("could not resume the %s that was running (%s); forgetting it", kind, e)
        d.settings.set(LAST_RUN_SETTING, {})


async def setup_settings_sync(d=None) -> None:
    """Wire settings storage per COMPANION_SETTINGS_STORE (needs Gateway 3.1+).

    Scoped to ONE display: the settings blob lives on the gateway that display drives,
    so with several gateways each mirrors its own settings to its own box. Nothing is
    shared — a second wall's installed apps, playlists and triggers are its own.

      * local   — nothing to do (the default local file is authoritative).
      * mirror  — mirror local settings onto the gateway; a fresh host with no local
                  file restores from the gateway, and an empty gateway is seeded.
      * gateway — the gateway is authoritative: load from it, stop writing locally.
    On a pre-3.1 (or unreachable) gateway this degrades to local, so the companion
    stays backward compatible with Gateway 3.0."""
    d = d or displays.default
    settings, cfg = d.settings, d.config
    mode = cfg.effective.get("settings_store", "mirror")
    url = d.gateway_url
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
        # The registry rides along. It is the one thing the companion knows that no single
        # gateway does — which OTHER gateways exist — so left only on our disk it would be
        # the one thing a rebuilt companion could not recover: each wall's settings come
        # back from its own gateway, but the LIST of walls, their names and the default
        # would not. Every gateway holds a copy; any one of them can restore the set.
        doc = dict(doc)
        doc["displays"] = registry.snapshot()
        return push_gateway_settings(url, doc)

    debounce = float(cfg.effective.get("settings_debounce", 3.0))
    if mode == "gateway":
        remote = await asyncio.to_thread(fetch_gateway_settings, url)
        if remote is not None:
            settings.restore_from_doc(remote)
        settings.set_gateway_only()               # nothing local
        settings.attach_gateway_sync(_pusher, debounce)
        if remote is None:
            settings.sync_now()                   # seed the empty gateway
        log.info("settings[%s]: stored ONLY on the gateway (3.1+)", d.id)
    else:  # mirror
        settings.attach_gateway_sync(_pusher, debounce)
        has_local = settings.has_local()
        remote = await asyncio.to_thread(fetch_gateway_settings, url)
        if not has_local and remote is not None:
            settings.restore_from_doc(remote)
            log.info("settings[%s]: restored from gateway (fresh host); mirroring enabled", d.id)
        elif has_local and remote is None:
            settings.sync_now()                   # seed the empty gateway from local
            log.info("settings[%s]: mirroring to gateway (seeding it from local)", d.id)
        else:
            log.info("settings[%s]: local primary, mirroring to gateway enabled", d.id)


async def _settings_flush_loop(d=None) -> None:
    """Periodically retry a pending settings push (covers a gateway that was briefly
    unreachable when a change happened). One loop per display: each has its own store
    and its own gateway to push it to."""
    d = d or displays.default
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(d.settings.flush)
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


async def _companion_heartbeat(gateway_url: str, companion_url: str, display=None):
    """Register immediately, then keep the gateway posted on our status.

    Runs entirely in the background so an unreachable gateway never delays
    startup (a POST to an unreachable host can take several seconds). ``display`` is
    the wall this gateway drives: the tabs it advertises are recorded there."""
    display = display or displays.default
    try:
        ok = await post_companion(gateway_url, url=companion_url,
                                  status=companion_status_string(), display=display)
        log.info("companion registered as %s (%s)", companion_url,
                 "ok" if ok else "gateway unreachable — will retry")
    except Exception as e:
        log.debug("companion registration error: %s", e)
    while True:
        await asyncio.sleep(30)
        try:
            await post_companion(gateway_url, url=companion_url,
                                 status=companion_status_string(), display=display)
        except Exception as e:
            log.debug("companion heartbeat error: %s", e)
        # Re-read the gateway's config on every heartbeat. Sync used to run ONCE, at
        # startup: a gateway that was still booting (or briefly busy) left the companion
        # on its default 3x15 forever, and a wall whose geometry changed on the gateway
        # -- a bigger panel, a swapped board -- was never noticed. do_gateway_sync only
        # acts when something actually moved, so this is a cheap GET the rest of the time.
        if display.config.effective.get("sync_from_gateway"):
            try:
                await do_gateway_sync(display)
            except Exception as e:
                log.debug("periodic gateway sync error: %s", e)


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


# The background tasks each display owns (heartbeat, HA, settings flush), by display id.
# Module-level because a display can now be added at RUNTIME, not just at boot: the
# /api/displays route needs somewhere to hand its tasks so shutdown can still stop them.
_display_tasks: dict[str, list] = {}
_companion_url: str = ""


async def restore_registry_from(d) -> list:
    """A rebuilt companion learns the OTHER walls from the one gateway it was given.

    Only when the registry had to be SEEDED — i.e. there was no displays.json, so this is a
    fresh install or a lost disk. On any other boot the local file is the user's own, and
    restoring over it would resurrect a display they had deliberately removed.

    Returns the displays that were not already here, built and ready to be started.
    """
    if not registry.seeded or not d.gateway_url:
        return []
    try:
        doc = await asyncio.to_thread(fetch_gateway_settings, d.gateway_url)
    except Exception:
        return []
    added = registry.adopt((doc or {}).get("displays") or {})
    built = [displays.build_from(rec) for rec in added if rec.enabled]
    # The default is a CHOICE, and it is part of what was backed up. The manager fixed its
    # own default when it loaded the seeded (single-display) registry, so without this the
    # wall the user had chosen comes back as just another display.
    displays.adopt_default(registry.default_id)
    return built


async def start_display(d, companion_url: str = "") -> list:
    """Bring ONE display up, and return the background tasks it owns.

    Everything in here is per-wall: its gateway's geometry, its settings mirror, its
    app loop, its Home Assistant device, its heartbeat. Two displays run two of each,
    and neither may touch the other's.
    """
    tasks: list = []
    cfg, ctl, plg = d.config, d.controller, d.plugins
    url = d.gateway_url
    log.info("display %r starting (gateway=%s, grid=%sx%s)",
             d.id, url or "none", cfg.grid["rows"], cfg.grid["cols"])

    await ctl.start()
    gw_ha = None
    if cfg.effective.get("sync_from_gateway") and url:
        res = await do_gateway_sync(d)
        if res.get("ok"):
            log.info("display %r synced config from gateway: %s", d.id, res.get("applied"))
            gw_ha = res.get("gateway", {}).get("haEnabled")
        else:
            log.info("display %r gateway sync skipped at startup: %s", d.id, res.get("error"))

    # Settings storage (mirror / gateway-only) — before plugins.load() so a list of
    # installed apps restored from THIS gateway drives what gets loaded for it.
    await setup_settings_sync(d)
    plg.load()
    log.info("display %r loaded %d app plugins", d.id, len(plg.app_list()))
    d.scheduler.start()

    # Pick up whatever was playing before we went down. After plugins.load(), because an
    # app has to exist before it can be resumed; before the heartbeat, so the status the
    # gateway is told is the one we are actually in. Recording is wired here too — the
    # engine is what knows when the driver changes.
    ctl.attach_persist(lambda doc, _d=d: _remember_driver(doc, _d))
    await resume_last_run(d)

    # Home Assistant: "auto" follows the gateway's haEnabled; true/false force it.
    # Started in the background so a slow/unreachable broker never delays startup.
    ha_mode = cfg.effective.get("ha", {}).get("enabled", "auto")
    ha_on = ha_mode is True if isinstance(ha_mode, bool) else bool(gw_ha)
    if ha_on:
        log.info("display %r: Home Assistant integration enabled", d.id)
        # Keep a strong reference (a bare create_task() can be GC'd mid-await, and its
        # exception would go unretrieved) and cancel it at shutdown.
        tasks.append(asyncio.create_task(d.ha.start()))
    if url and companion_url:
        tasks.append(asyncio.create_task(_companion_heartbeat(url, companion_url, d)))
    # Retry pending settings pushes if this gateway was briefly unreachable.
    tasks.append(asyncio.create_task(_settings_flush_loop(d)))
    return tasks


async def stop_display(d, tasks: list) -> None:
    """Take ONE display down: flush what it owes its gateway, then unwind."""
    try:
        await asyncio.to_thread(d.settings.flush)
    except Exception:
        pass
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    if d.gateway_url:
        try:
            await post_companion(d.gateway_url, url="")
            log.info("companion deregistered from display %r", d.id)
        except Exception:
            pass
    await d.ha.stop()
    await d.scheduler.stop()
    await d.controller.stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Hard requirement: without a gateway there is nothing to drive. Refuse to
    # start (rather than silently retrying a phantom host). __main__.py catches
    # this earlier with a friendlier message; this also covers `uvicorn app.main:app`.
    # Checked on the DEFAULT display: it is the one an unconfigured install has, and
    # the one every display-less URL resolves to.
    if not _default.gateway_url:
        raise RuntimeError(
            "GATEWAY_URL is not set. Set it to your SplitFlapGateway's URL "
            "(e.g. GATEWAY_URL=http://192.168.1.50) and restart."
        )
    log.info("SplitFlapGatewayCompanion v%s starting (%d display(s), default=%r)",
             __version__, len(displays.all()), displays.default_id)

    global _companion_url
    _companion_url = await resolve_companion_url()
    companion_url = _companion_url
    for d in displays.all():
        _display_tasks[d.id] = await start_display(d, companion_url)

    # The default display has just restored its settings from its gateway; if we had to seed
    # the registry, that blob also carries the SET of walls. Bring back the ones we did not
    # know about — otherwise a rebuilt companion silently forgets every display that was
    # added in the UI rather than in GATEWAY_URL.
    for d in await restore_registry_from(displays.default):
        log.info("display %r came back from the gateway's copy of the registry", d.id)
        _display_tasks[d.id] = await start_display(d, companion_url)

    # If we auto-detected our URL, verify it's actually reachable. Once, not per display
    # — it is the same companion either way. Skipped as an add-on: Supervisor already
    # told us the host's address and our published port, and probing it from inside the
    # container proves nothing about the LAN.
    verify: list = []
    if companion_url and not (config.effective.get("companion_url") or "").strip() \
            and not os.environ.get("SUPERVISOR_TOKEN"):
        verify.append(asyncio.create_task(_verify_reachable(companion_url)))

    # Mounting an MCP app disables its own lifespan, so the host owns its session
    # manager — without this the first /mcp request fails. Cheap, and harmless when
    # the layer is off (the guard 404s before anything reaches it).
    async with mcp.session_manager.run():
        yield

    for t in verify:
        t.cancel()
    for d in displays.all():
        await stop_display(d, _display_tasks.get(d.id, []))


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


class MessageRequest(BaseModel):
    text: str
    style: str | None = None
    seconds: int | None = None      # >0 = temporary, then revert to what was playing


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


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__}


# ---------------------------------------------------------------------------
# Displays — the registry (Phase 1). These are the only routes that are ABOUT the
# set of displays rather than about one of them, so they talk to the manager and
# the registry directly instead of resolving through display_for().
# ---------------------------------------------------------------------------
@app.get("/api/displays")
async def list_displays():
    """Every wall we drive, plus which one is the default. The UI's switcher reads this;
    so does anything that wants to address a display explicitly."""
    out = displays.status()
    known = {d["id"] for d in out["displays"]}
    # Registered but disabled displays have no runtime object — say so rather than
    # hiding them, or the UI could not offer to turn one back on.
    for rec in registry.all():
        if rec.id not in known:
            out["displays"].append({
                "id": rec.id, "name": rec.name, "gateway_url": rec.gateway_url,
                "enabled": False, "grid": None, "module_count": 0,
                "active_app": None, "active_playlist": None,
            })
    for d in out["displays"]:
        d.setdefault("enabled", True)
    return out


@app.post("/api/displays")
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
    if any(r.gateway_url == url for r in registry.all()):
        raise HTTPException(409, f"a display already points at {url}")

    rec = registry.add(name=body.name, gateway_url=url, display_id=body.id or "")
    d = displays.build_from(rec)

    # If the new gateway already holds a settings blob (it was driven by a companion
    # before), that blob wins — start_display restores it, and we must not scribble over
    # it first. Only a gateway with nothing to say gets seeded from an existing wall.
    remote = await asyncio.to_thread(fetch_gateway_settings, url)
    if body.copy_settings and remote is None:
        src = displays.get(body.copy_settings_from) if body.copy_settings_from else displays.default
        if src is not None and src is not d:
            seed = {k: v for k, v in src.settings.all().items() if k in GLOBAL_STORAGE_KEYS}
            d.settings.update(seed)      # a copy — it is this display's own from here on
            log.info("display %r seeded its global settings from %r", d.id, src.id)

    _display_tasks[d.id] = await start_display(d, _companion_url)
    return {"ok": True, "display": rec.to_dict(), "status": d.status()}


@app.patch("/api/displays/{display_id}")
async def patch_display(display_id: str, body: DisplayPatch):
    try:
        rec = registry.update(display_id, **body.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, f"no such display: {display_id}")

    live = displays.get(display_id)
    if live is not None and body.name:
        live.name = rec.name

    # Enabling/disabling starts and stops the wall's whole runtime — its app loop,
    # settings mirror, HA device and heartbeat.
    if body.enabled is True and live is None:
        d = displays.build_from(rec)
        _display_tasks[d.id] = await start_display(d, _companion_url)
    elif body.enabled is False and live is not None:
        await stop_display(live, _display_tasks.pop(display_id, []))
        displays.remove(display_id)

    # Re-pointing a running display at a different gateway is the one thing we don't do
    # in place: the settings mirror, the HA device and the heartbeat are all bound to the
    # old URL, and swapping it underneath them is how you end up mirroring one wall's
    # playlists onto another's box.
    restart = body.gateway_url is not None and live is not None
    return {"ok": True, "display": rec.to_dict(), "restart_required": restart}


@app.delete("/api/displays/{display_id}")
async def remove_display(display_id: str):
    """Deregister a wall. Its settings directory is deliberately left on disk: removing
    a display should not silently destroy the playlists and triggers built for it."""
    try:
        rec = registry.remove(display_id)
    except KeyError:
        raise HTTPException(404, f"no such display: {display_id}")
    except ValueError as e:
        raise HTTPException(409, str(e))

    live = displays.get(display_id)
    if live is not None:
        await stop_display(live, _display_tasks.pop(display_id, []))
        displays.remove(display_id)
    # The registry decides which display is the default; keep the runtime in step.
    displays.adopt_default(registry.default_id)
    return {"ok": True, "removed": rec.to_dict()}


@app.post("/api/displays/{display_id}/default")
async def make_default(display_id: str):
    """Choose which display the display-less surfaces mean: the bare /api/... routes,
    /local-api/message (a Vestaboard client sends no display id), an MCP call with no
    display argument, an existing HACS entry. A choice, never an inference — and it is
    persisted, so a restart does not quietly hand it back."""
    try:
        displays.set_default(display_id)
    except KeyError:
        raise HTTPException(404, f"no such display: {display_id}")
    return {"ok": True, "default": displays.default_id}


@app.get("/api/current_state")
async def current_state(request: Request):
    d = display_for(request)
    return d.state.snapshot()


@app.get("/api/grid")
async def grid(request: Request):
    d = display_for(request)
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


@app.get("/api/config")
async def get_config(request: Request):
    d = display_for(request)
    return _redact(d.config.effective)


@app.post("/api/config")
async def update_config(request: Request, patch: ConfigPatch):
    d = display_for(request)
    body = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not body:
        raise HTTPException(400, "empty config patch")
    old_url = d.config.transport.get("gateway_url")
    d.config.update(body)
    if "grid" in body:
        d.controller.resize_grid()
        d.plugins.on_grid_changed()   # cached/channel pages were sized for the old grid
    if "transport" in body:
        await d.controller.reload_transport()
    # If the gateway URL just changed and auto-sync is on, pull its config now.
    new_url = d.config.transport.get("gateway_url")
    if new_url and new_url != old_url and d.config.effective.get("sync_from_gateway"):
        await do_gateway_sync(d)
    return _redact(d.config.effective)


@app.post("/api/gateway/sync")
async def gateway_sync(request: Request):
    """Pull grid geometry + MQTT settings from the gateway on demand."""
    return await do_gateway_sync(display_for(request))


# ---------------------------------------------------------------------------
# Developer mode (gated by COMPANION_DEV_MODE). The GET is always safe to call
# (the UI uses `enabled` to decide whether to show the dev menu); the actions
# require dev mode to be on.
# ---------------------------------------------------------------------------
def _require_dev():
    """Gate for SIMULATION MODE only. The ⚙ tools menu itself is permanent — the
    Vestaboard/MCP switches, resync and settings sync are ordinary controls on an API
    that is unauthenticated on the LAN anyway, so hiding them behind an env var never
    added protection, just friction. Simulation stays dev-gated: silently not driving
    the wall is a developer's tool, and a trap for anyone else."""
    if not config.dev_mode:
        raise HTTPException(404, "developer mode is off (set COMPANION_DEV_MODE=1)")


@app.get("/api/dev")
async def dev_state(request: Request):
    d = display_for(request)
    return d.config.dev_state()


@app.post("/api/dev/sim")
async def dev_sim(request: Request, req: DevSim):
    """Toggle simulation mode: on = nothing is sent to the display."""
    d = display_for(request)
    _require_dev()
    d.config.set_sim_mode(req.on)          # turning off also clears any grid override
    await d.controller.reload_transport()  # swap REST <-> sim
    d.controller.resize_grid()             # geometry may have reverted
    d.plugins.on_grid_changed()
    return d.config.dev_state()


@app.post("/api/dev/vestaboard")
async def dev_vestaboard(request: Request, req: DevVestaboard):
    """Turn the Vestaboard-compatible Local API on/off at runtime (see the block at
    the bottom of this file). COMPANION_VESTABOARD sets where it starts."""
    d = display_for(request)
    d.config.set_vestaboard(req.on)
    if req.on:
        vestaboard_key()   # mint + persist the key now, so the menu can show it
    log.info("Vestaboard API %s (dev menu)", "enabled" if req.on else "disabled")
    return d.config.dev_state()


@app.get("/api/dev/vestaboard")
async def dev_vestaboard_state(request: Request):
    """The Vestaboard connection details, for the dev menu to display: the key a
    client must send, and the endpoint it posts to. (Not gated: the key guards the
    Vestaboard routes from OUTSIDE clients; anyone who can read this endpoint already
    has the whole unauthenticated companion API.)"""
    d = display_for(request)
    on = d.config.vestaboard_enabled
    return {
        "enabled": on,
        "key": vestaboard_key() if on else "",
        "path": "/local-api/message",
        # Same as MCP: a rest_command is not the browser, and under ingress the browser's
        # own origin is Home Assistant's, which does not reach this endpoint.
        "url": await _external_url("/local-api/message"),
        "env_key": bool(d.config.vestaboard.get("api_key")),   # pinned via env, not generated
    }


@app.post("/api/dev/mcp")
async def dev_mcp(request: Request, req: DevMCP):
    """Turn the MCP server on/off at runtime (see the block further down).
    COMPANION_MCP sets where it starts."""
    d = display_for(request)
    d.config.set_mcp(req.on)
    if req.on:
        mcp_token()        # mint + persist the token now, so the menu can show it
    log.info("MCP server %s (dev menu)", "enabled" if req.on else "disabled")
    return d.config.dev_state()


@app.get("/api/dev/mcp")
async def dev_mcp_state(request: Request):
    """The MCP connection details for the dev menu: the endpoint an LLM client
    points at and the bearer token it must send. (Not gated — same reasoning as the
    Vestaboard key above.)"""
    d = display_for(request)
    on = d.config.mcp_enabled
    return {
        "enabled": on,
        "token": mcp_token() if on else "",
        "path": "/mcp",
        "url": await _external_url("/mcp"),
        "env_token": bool(d.config.mcp.get("token")),          # pinned via env, not generated
    }


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
        base = (await resolve_companion_url()).rstrip("/")
    except Exception:
        base = ""
    return f"{base}{path}" if base else ""


@app.post("/api/dev/resync")
async def dev_resync(request: Request):
    """Force a settings resync with the gateway."""
    return await do_gateway_sync(display_for(request))


@app.post("/api/dev/grid")
async def dev_grid(request: Request, req: DevGrid):
    """Override the grid geometry — only while simulating (so the real display's
    gateway-derived geometry is never touched)."""
    d = display_for(request)
    _require_dev()
    if not d.config.sim_mode:
        raise HTTPException(400, "turn simulation mode on before overriding the grid")
    d.config.set_grid_override(req.rows, req.cols)
    d.controller.resize_grid()
    d.plugins.on_grid_changed()
    return d.config.dev_state()


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
async def dev_settings_pull(request: Request):
    """Force-retrieve the settings blob from the gateway and apply it."""
    d = display_for(request)
    ready = await _gateway_settings_ready()
    if isinstance(ready, dict):
        return ready
    url, _ = ready
    doc = await asyncio.to_thread(fetch_gateway_settings, url)
    if doc is None:
        return {"ok": False, "error": "no settings are stored on the gateway yet"}
    d.settings.restore_from_doc(doc)
    d.plugins.load()                 # apply the restored installed-apps list + settings
    d.ha.refresh_discovery()         # the app/playlist option lists may have changed
    log.info("dev: retrieved settings from the gateway (%d apps installed)", len(d.settings.installed_apps))
    return {"ok": True, "applied": True, "installed": len(d.settings.installed_apps)}


@app.post("/api/dev/settings/push")
async def dev_settings_push(request: Request):
    """Force-write the current settings to the gateway now."""
    d = display_for(request)
    ready = await _gateway_settings_ready()
    if isinstance(ready, dict):
        return ready
    url, _ = ready
    ok = await asyncio.to_thread(push_gateway_settings, url, d.settings.snapshot())
    log.info("dev: pushed settings to the gateway (ok=%s)", ok)
    return {"ok": ok, "error": None if ok else "push failed"}


# ---------------------------------------------------------------------------
# Apps / plugins
# ---------------------------------------------------------------------------
def _ui_lang(request: Request) -> str:
    """The UI (chrome) language for this request — URL param, then the
    explicitly-saved Language setting, then COMPANION_UI_LANGUAGE, then the
    browser's Accept-Language. Chrome only: the flap content language stays
    the single global Language setting."""
    return uilang.resolve(
        request.query_params.get("lang"),
        plugins.settings,
        config.ui_language,
        request.headers.get("accept-language"),
    )


@app.get("/api/apps")
async def apps_list(request: Request):
    d = display_for(request)
    return {"apps": d.plugins.app_list(lang=_ui_lang(request)),
            "active_app": d.controller.active_app}


@app.get("/api/apps/available")
async def apps_available(request: Request):
    d = display_for(request)
    return {"apps": d.plugins.available_list(lang=_ui_lang(request))}


@app.post("/api/apps/run")
async def apps_run(request: Request, req: RunAppRequest):
    d = display_for(request)
    app_id = req.app[7:] if req.app.startswith("plugin_") else req.app
    try:
        await d.controller.run_app(app_id)
    except KeyError:
        raise HTTPException(404, f"app not installed: {app_id}")
    d.ha.publish_state()
    return {"ok": True, "active_app": app_id}


@app.post("/api/apps/stop")
async def apps_stop(request: Request):
    d = display_for(request)
    await d.controller.stop_app()
    d.ha.publish_state()
    return {"ok": True}


@app.get("/api/apps/{app_id}/settings")
async def apps_get_settings(app_id: str, request: Request):
    d = display_for(request)
    try:
        return d.plugins.settings_schema(app_id, lang=_ui_lang(request))
    except KeyError:
        raise HTTPException(404, f"app not installed: {app_id}")


@app.post("/api/apps/{app_id}/settings")
async def apps_save_settings(request: Request, app_id: str, patch: AppSettingsPatch):
    d = display_for(request)
    try:
        d.plugins.save_settings(app_id, patch.values)
    except KeyError:
        raise HTTPException(404, f"app not installed: {app_id}")
    # If this app is on the display right now, restart it so the new settings
    # (page dwell, refresh cadence, content options) take effect immediately.
    if d.controller.active_app == app_id:
        await d.controller.run_app(app_id)
    return {"ok": True}


@app.get("/api/global-settings")
async def global_settings_get(request: Request):
    """Shared settings apps rely on (weather_api_key, timezone, location, …)."""
    d = display_for(request)
    return d.plugins.global_settings_schema(lang=_ui_lang(request))


@app.post("/api/global-settings")
async def global_settings_save(request: Request, patch: AppSettingsPatch):
    # A person changing the Language control marks it explicit, which is what
    # lets it beat the browser's language in the UI-chrome chain (uilang.py).
    # Compared against the stored value, not just presence: the form posts every
    # field, so an untouched seeded en-US must not count as a choice.
    d = display_for(request)
    if "language" in patch.values and \
            str(patch.values["language"]) != str(d.plugins.settings.get("language")):
        d.plugins.settings.set("language_explicit", True)
    d.plugins.save_global_settings(patch.values)
    # Globals (location, provider, page dwell, …) can change what the running app
    # shows or how fast it cycles — restart it so the change is visible at once.
    if d.controller.active_app:
        await d.controller.run_app(d.controller.active_app)
    return {"ok": True}


@app.get("/api/apps/{app_id}/preview")
async def apps_preview(request: Request, app_id: str):
    d = display_for(request)
    if d.plugins.manifest(app_id) is None:
        raise HTTPException(404, f"app not installed: {app_id}")
    pages = await asyncio.get_running_loop().run_in_executor(None, d.plugins.get_pages, app_id)
    return {"pages": pages, "rows": d.plugins.get_rows(), "cols": d.plugins.get_cols()}


@app.post("/api/apps/{app_id}/install")
async def apps_install(request: Request, app_id: str, req: InstallRequest):
    d = display_for(request)
    if app_id not in d.plugins.discover():
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


@app.post("/api/apps/upload")
async def apps_upload(request: Request, file: UploadFile = File(...)):
    """Upload + register a new app from a .zip (manifest.json + app.py/data.json).

    Note: a functional app's app.py is executed to validate it — only upload
    apps you trust."""
    d = display_for(request)
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


@app.delete("/api/apps/{app_id}")
async def apps_delete(request: Request, app_id: str):
    """Delete a user-uploaded app entirely (built-ins can't be deleted)."""
    d = display_for(request)
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


# ---------------------------------------------------------------------------
# Playlists
# ---------------------------------------------------------------------------
@app.get("/api/playlists")
async def playlists_list(request: Request):
    d = display_for(request)
    return {"playlists": d.settings.get("saved_app_playlists", {})}


@app.post("/api/playlists")
async def playlists_save(request: Request, req: PlaylistSave):
    d = display_for(request)
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "name required")
    saved = dict(d.settings.get("saved_app_playlists", {}))
    saved[name] = {"entries": req.entries, "loop": req.loop}
    d.settings.set("saved_app_playlists", saved)
    d.ha.refresh_discovery()  # playlist option list changed
    return {"ok": True, "name": name}


@app.delete("/api/playlists/{name}")
async def playlists_delete(request: Request, name: str):
    d = display_for(request)
    saved = dict(d.settings.get("saved_app_playlists", {}))
    saved.pop(name, None)
    d.settings.set("saved_app_playlists", saved)
    d.ha.refresh_discovery()
    return {"ok": True}


@app.post("/api/playlists/run")
async def playlists_run(request: Request, req: RunPlaylist):
    d = display_for(request)
    if not req.entries:
        raise HTTPException(400, "playlist has no entries")
    await d.controller.run_playlist(req.entries, req.loop, req.name)
    d.ha.publish_state()
    return {"ok": True, "active_playlist": d.controller.active_playlist}


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------
@app.get("/api/triggers")
async def triggers_get(request: Request):
    d = display_for(request)
    trigs = []
    for t in d.settings.get("triggers", []):
        e = dict(t)
        e["last_fired"] = scheduler.last_fired(t.get("id", ""))
        trigs.append(e)
    return {
        "triggers": trigs,
        "triggers_enabled": d.settings.get("triggers_enabled", True),
        "trigger_apps": d.plugins.trigger_apps(),
    }


@app.post("/api/triggers")
async def triggers_save(request: Request, patch: TriggersPatch):
    d = display_for(request)
    body = {k: v for k, v in patch.model_dump().items() if v is not None}
    if body:
        d.settings.update(body)
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
async def compose_send(request: Request, req: ComposeRequest):
    d = display_for(request)
    if req.style and req.style not in renderer.ALL_STYLES:
        raise HTTPException(400, f"unknown style: {req.style}")
    # A person typed this: on a wall that can show lowercase, show it as they typed it,
    # rather than SHOUTING IT BACK AT THEM — which was all the one-byte protocol could do.
    #
    # …unless it is `raw`, which is the click-to-type GRID: there a lowercase r/o/y/g/b/p/w
    # is a COLOUR CELL the user placed, not a letter they typed.
    target = d.controller.send_text_bg(req.text, style=req.style, speed=req.speed,
                                       frame=req.raw)
    return {"ok": True, "target": target}


@app.post("/api/message")
async def show_message(request: Request, req: MessageRequest):
    """Show a plain-text message, centred and word-wrapped onto the grid — the same layout
    the apps and the Vestaboard endpoint use. Unlike /api/compose/send (which takes a raw
    grid string from the click-to-type editor), this takes ordinary text.

    `seconds` makes it temporary: after that long the display reverts to whatever was
    playing (or blanks if nothing was). This is what the Home Assistant integration and a
    `rest_command` use — no Vestaboard key needed."""
    d = display_for(request)
    if req.style and req.style not in renderer.ALL_STYLES:
        raise HTTPException(400, f"unknown style: {req.style}")
    g = d.config.grid
    rows, cols = int(g["rows"]), int(g["cols"])
    page = vestaboard.layout_text(req.text, rows, cols)
    if req.seconds and req.seconds > 0:
        running = d.controller.show_temporary(page, req.seconds, style=req.style or "ltr")
        d.ha.publish_state()
        return {"ok": True, "seconds": req.seconds,
                "reverts_to": "app/playlist" if running else "blank"}
    d.controller.send_text_bg(page, style=req.style)
    d.ha.publish_state()
    return {"ok": True}


@app.post("/api/display/clear")
async def display_clear(request: Request):
    d = display_for(request)
    await d.controller.clear()
    return {"ok": True}


@app.post("/api/display/home")
async def display_home(request: Request):
    """Physically home every module (gateway broadcast), stop any running
    app/playlist, and blank the live preview. Best-effort: reports the reason on
    failure rather than raising, so the UI can surface it inline."""
    d = display_for(request)
    try:
        ok = await d.controller.home_all()
        return {"ok": ok, "error": None if ok else "gateway rejected the home command"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/gateway/status")
async def gateway_status(request: Request):
    """Probe the gateway's /api/status and return its URL (for the Display tab).

    ``tabs`` is the gateway's own tab list as it advertised it when we registered
    (Gateway 3.4+); empty means it never did — an older firmware, or we haven't
    reached it yet — and the UI falls back to its built-in list. See tabs.py.
    """
    d = display_for(request)
    import httpx

    tabs = list(d.gateway_tabs)
    url = d.config.transport.get("gateway_url", "").rstrip("/")
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


# A wrong key must answer exactly this — plain text, this string — because that is what a
# real Vestaboard returns and what clients test for verbatim (the popular ha-vestaboard
# does `resp.status == 401 and resp.text() == "Invalid API key"` to offer re-auth; a JSON
# {"detail": ...} body instead just reads as an unknown error).
VB_INVALID_KEY = "Invalid API key"


def _key_error(request: Request) -> PlainTextResponse | None:
    """The 401 to return if the Vestaboard key is missing/wrong, else None. Returns the
    response rather than raising so the caller controls the exact body (see VB_INVALID_KEY)."""
    key = request.headers.get("X-Vestaboard-Local-Api-Key", "")
    if not key or not secrets.compare_digest(key, vestaboard_key()):
        return PlainTextResponse(VB_INVALID_KEY, status_code=401)
    return None


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


@app.get("/local-api/{display_id}/message")
@app.get("/local-api/message")
async def vb_read_message(request: Request, display_id: str | None = None):
    """The board as it stands — whatever the flaps are showing (a running app's output
    included), not merely the last message someone posted, which is what this endpoint
    means on real hardware.

    The matrix is wrapped in ``{"message": [[...]]}``, matching the real Local API: every
    Vestaboard client reads it back as ``response["message"]``. Returning a bare array
    (as we did) makes a client crash the moment it does ``.get("message")`` on a list —
    which is why the reference integration would not even finish setup against us.

    /local-api/message stays bound to the DEFAULT display: a Vestaboard IS one board, and
    every existing client (ha-vestaboard included) posts to that fixed path with no way to
    name a wall. /local-api/<display-id>/message addresses the others."""
    d = display_by_id(display_id) if display_id else display_for(request)
    _require_vestaboard()
    if err := _key_error(request):
        return err
    g = d.config.grid
    rows, cols = int(g["rows"]), int(g["cols"])
    return {"message": vestaboard.encode(d.state.current_chars, rows, cols)}


@app.post("/local-api/{display_id}/message")
@app.post("/local-api/message")
async def vb_send_message(request: Request, display_id: str | None = None):
    """Post a message. Takes every shape a Vestaboard client sends:

        [[0,8,5,...], ...]                        a bare character-code matrix
        {"characters": [[...]], "strategy": ...}  ...with an animation
        {"text": "HELLO"}                         an extension of ours, because most
                                                  Home Assistant setups send text

    Like a compose push, this takes the display over: any running app or playlist is
    cancelled (send_text_bg), which is what posting to a Vestaboard implies.

    The bare path drives the DEFAULT display, because that is the only one an existing
    Vestaboard client can reach; /local-api/<display-id>/message drives a named wall.
    """
    d = display_by_id(display_id) if display_id else display_for(request)
    _require_vestaboard()
    if err := _key_error(request):
        return err
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")

    g = d.config.grid
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
            page = vestaboard.layout_text(body["text"], rows, cols)
        else:
            raise HTTPException(422, "expected a character matrix, {\"characters\": [[...]]}, "
                                     "or {\"text\": \"...\"}")
    except vestaboard.VestaboardError as e:
        raise HTTPException(422, str(e))

    style = vestaboard.style_for(strategy, d.config.display.get("transition_style", "ltr"))
    # Not a frame: the codec already turned every colour chip into a COLOUR (its own
    # codepoint), so nothing here is a lowercase letter standing in for one.
    d.controller.send_text_bg(page, style=style)
    d.ha.publish_state()
    # 201, not 200: the real Local API returns 201 Created on a successful write, and
    # clients treat anything else as failure (ha-vestaboard's coordinator raises
    # UpdateFailed unless the write returns 201, so a 200 broke every message it sent).
    return JSONResponse({"ok": True}, status_code=201)


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

# The gateway's own UI, served through us at /gw/ — the only way it can appear inside
# Home Assistant, which can only put this add-on's port in the sidebar. See gwproxy.py.
app.include_router(gwproxy.build(displays))


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
    """Serve the SPA shell, stamping in the ingress prefix — which isn't known until
    the request arrives. As a Home Assistant add-on the SPA is served from
    ``/api/hassio_ingress/<token>/``, and Supervisor says so in ``X-Ingress-Path``.
    A browser-side ``/api/...`` URL would resolve against the *HA* root and 404, so
    every asset URL is prefixed here and the SPA reads the same prefix off
    ``window.__BASE__`` for its fetches (see app.js)."""
    d = display_for(request)
    base = (request.headers.get("X-Ingress-Path") or "").rstrip("/")
    html = _cache_bust((STATIC_DIR / "index.html").read_text("utf-8"), STATIC_DIR, base)
    lang = _ui_lang(request)
    # __LOCKED__ says levels 1-3 (URL / saved setting / ui_language) already decided,
    # so the client must not second-guess it. Unlocked means the server only had the
    # browser to go on, and the SPA may upgrade that to Home Assistant's own language
    # — which is per-user and only reachable from inside HA's iframe. __LANGS__ is the
    # offered list, so the client can validate whatever it finds there.
    locked = uilang.resolve_locked(
        request.query_params.get("lang"), d.plugins.settings, d.config.ui_language)
    head = (f"<script>window.__BASE__={json.dumps(base)};"
            f"window.__LANG__={json.dumps(lang)};"
            f"window.__LOCKED__={json.dumps(bool(locked))};"
            f"window.__LANGS__={json.dumps(uilang.OFFERED)};</script>")
    html = html.replace("</head>", f"  {head}\n</head>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="spa")
