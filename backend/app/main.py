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
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, gwproxy, mcp_server, uilang
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
from .routes import apps as routes_apps
from .routes import canvas_api as routes_canvas_api
from .routes import dev as routes_dev
from .routes import displays as routes_displays
from .routes import helpers_api as routes_helpers_api
from .routes import local_api as routes_local_api
from .routes import message as routes_message
from .routes import playlists as routes_playlists
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
    """Every credential the config can carry, not just the MQTT password —
    /api/config is readable by anything that can reach the UI, and the
    Vestaboard enablement token was leaking here without appearing anywhere
    else in the product."""
    cfg = copy.deepcopy(cfg)
    mqtt = cfg.get("transport", {}).get("mqtt", {})
    if mqtt.get("password"):
        mqtt["password"] = "********"
    for section, key in (("vestaboard", "api_key"), ("vestaboard", "enablement_token"),
                         ("mcp", "token")):
        sec = cfg.get(section, {})
        if isinstance(sec, dict) and sec.get(key):
            sec[key] = "********"
    return cfg


async def do_gateway_sync(d=None) -> dict:
    """Pull grid geometry from ONE display's gateway and apply it.

    The gateway is the source of truth for hardware config; the companion keeps
    only what the gateway can't give it (transport choice, its own HA broker). Scoped to a
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
            d.grid_changed()   # cached/channel pages were sized for the old grid
        # A sync only touches grid (resized above); the REST display transport depends
        # only on gateway_url, which sync never changes, so there's nothing to reload here.

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
        "gateway": {k: gw.get(k) for k in ("gridRows", "gridCols")},
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


def companion_status_string(d=None) -> str:
    """Short human status for the gateway's status page — for THAT gateway's wall,
    not whatever the default display happens to be running."""
    d = d or displays.default
    ctl, plg = d.controller, d.plugins
    if ctl.active_app:
        m = plg.manifest(ctl.active_app)
        return f"App: {m['name']}" if m and m.get("name") else f"App: {ctl.active_app}"
    if ctl.active_playlist:
        return f"Playlist: {ctl.active_playlist}"
    return "Idle"


async def _companion_heartbeat(gateway_url: str, companion_url: str, display=None):
    """Register immediately, then keep the gateway posted on our status.

    Runs entirely in the background so an unreachable gateway never delays
    startup (a POST to an unreachable host can take several seconds). ``display`` is
    the wall this gateway drives: the tabs it advertises are recorded there."""
    display = display or displays.default
    try:
        ok = await post_companion(gateway_url, url=companion_url,
                                  status=companion_status_string(display), display=display)
        log.info("companion registered as %s (%s)", companion_url,
                 "ok" if ok else "gateway unreachable — will retry")
    except Exception as e:
        log.debug("companion registration error: %s", e)
    while True:
        await asyncio.sleep(30)
        try:
            await post_companion(gateway_url, url=companion_url,
                                 status=companion_status_string(display), display=display)
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


def _warn_if_container_address(companion_url: str) -> None:
    """Warn when the URL we are about to register is the container's own bridge address.

    This is the one thing that is broken about running the plain Docker image the way the
    README shows it. `detect_local_ip` opens a socket toward the gateway and reads back our
    own address — which, in a bridge-networked container, is 172.17.0.x. That is OUR address
    on the docker0 bridge, and it is not routable from the gateway, which is a device out on
    the LAN. So the gateway is handed a "Companion" link that can never resolve.

    _verify_reachable() cannot catch this, and it is worth being clear about why: it probes
    the URL FROM INSIDE THE CONTAINER, where 172.17.0.x is trivially reachable — it is us.
    The check passes and the URL is still useless. Reachability is the wrong question; the
    address itself is the answer.

    So we look at the address. 172.16.0.0/12 is Docker's bridge range (172.17 is the default
    bridge, 172.18-31 are user-defined ones). A host-networked container gets the host's real
    LAN IP and never trips this; a home LAN genuinely numbered in 172.16/12 would, and gets a
    warning suggesting a setting that is harmless to set.
    """
    import ipaddress
    from urllib.parse import urlparse

    if not companion_url or (config.effective.get("companion_url") or "").strip():
        return                                   # explicitly configured — nothing to guess at
    if not Path("/.dockerenv").exists():
        return                                   # not in a container; the detected IP is real
    host = urlparse(companion_url).hostname or ""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return
    if addr not in ipaddress.ip_network("172.16.0.0/12"):
        return

    log.warning(
        "the URL registered with the gateway is %s, which is this CONTAINER's address on the "
        "Docker bridge — your gateway is on the LAN and cannot reach it, so its 'Companion' "
        "link will not open. Set COMPANION_PUBLIC_URL to this host's LAN address, e.g. "
        "-e COMPANION_PUBLIC_URL=http://<this-host-ip>:%s (the install script does this for "
        "you). Everything else — driving the display — is unaffected.",
        companion_url, config.effective.get("port", 8000))


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
    if cfg.effective.get("sync_from_gateway") and url:
        res = await do_gateway_sync(d)
        if res.get("ok"):
            log.info("display %r synced config from gateway: %s", d.id, res.get("applied"))
        else:
            log.info("display %r gateway sync skipped at startup: %s", d.id, res.get("error"))

    # Settings storage (mirror / gateway-only) — before plugins.load() so a list of
    # installed apps restored from THIS gateway drives what gets loaded for it.
    await setup_settings_sync(d)
    # load() executes every installed app.py. At boot nothing else is running, but
    # start_display is also reached from POST/PATCH /api/displays — with other
    # walls animating, so keep it off the event loop.
    await asyncio.to_thread(plg.load)
    log.info("display %r loaded %d app plugins", d.id, len(plg.app_list()))
    d.scheduler.start()

    # Pick up whatever was playing before we went down. After plugins.load(), because an
    # app has to exist before it can be resumed; before the heartbeat, so the status the
    # gateway is told is the one we are actually in. Recording is wired here too — the
    # engine is what knows when the driver changes.
    ctl.attach_persist(lambda doc, _d=d: _remember_driver(doc, _d))
    await resume_last_run(d)

    # Home Assistant: "auto" brings the integration up when a broker is configured, off
    # when none is (the gateway no longer publishes an HA switch to follow — firmware 3.0
    # dropped MQTT — so a configured broker is the signal the user wants it). true/false
    # force it. Started in the background so a slow/unreachable broker never delays startup.
    ha_mode = cfg.effective.get("ha", {}).get("enabled", "auto")
    has_broker = bool((cfg.transport.get("mqtt") or {}).get("broker"))
    ha_on = ha_mode is True if isinstance(ha_mode, bool) else has_broker
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
    _warn_if_container_address(companion_url)
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
# API — the routes live in app/routes/, split along the seams the backend audit
# named (E1): displays / dev / apps / playlists+triggers / message / vestaboard
# local-api / the root app-data helpers. Each build() below is handed THIS
# MODULE as its deps: the routers resolve every shared name (displays,
# display_for, do_gateway_sync, vestaboard_key, _companion_url …) through it at
# request time — the same late binding they had as module-level routes here, so
# a monkeypatched main.vestaboard_key is still what /local-api checks, and
# _companion_url is read after the lifespan has set it, not captured empty at
# import. See routes/__init__.py.
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__}


def _ui_lang(request: Request) -> str:
    """The UI (chrome) language for this request — URL param, then the
    explicitly-saved Language setting OF THE REQUEST'S DISPLAY, then
    COMPANION_UI_LANGUAGE, then the browser's Accept-Language. Chrome only: the
    flap content language stays the per-display global Language setting.

    Kept here rather than in a router: the SPA shell (spa_index below) needs it
    too, and the apps router reaches it through deps."""
    d = display_for(request)
    return uilang.resolve(
        request.query_params.get("lang"),
        d.plugins.settings,
        d.config.ui_language,
        request.headers.get("accept-language"),
    )


# ---------------------------------------------------------------------------
# The Vestaboard Local API's key. Minted here, not in routes/local_api.py: it is
# process-wide (one HTTP surface, one secret), the dev menu shows it, and the MCP
# bearer token further down is minted by the same helper. The endpoints it guards
# are in routes/local_api.py.
# ---------------------------------------------------------------------------
VESTABOARD_KEY_SETTING = "vestaboard_api_key"


def _persistent_secret(env_value: str, setting_key: str, log_line: str) -> str:
    """A credential that must outlive the process: the env value if pinned, else one
    generated once and kept in the settings store (which persists to /data and
    mirrors to the gateway). A secret that changed on every restart would silently
    break an already-configured client."""
    if env_value:
        return env_value
    stored = plugin_settings.get(setting_key) or ""
    if not stored:
        stored = secrets.token_urlsafe(24)
        plugin_settings.set(setting_key, stored)
        log.info(log_line)
    return stored


def vestaboard_key() -> str:
    return _persistent_secret(
        config.vestaboard.get("api_key") or "", VESTABOARD_KEY_SETTING,
        "Vestaboard API: generated an API key (see the Dev menu, or set "
        "COMPANION_VESTABOARD_KEY to pin your own)")


def _include_flat(router) -> None:
    """Add a router's routes to the app FLAT, not via include_router.

    FastAPI 0.139 made include_router lazy: app.routes then holds an
    _IncludedRouter wrapper and the real routes only materialise per request.
    These routes have always been flat entries in app.routes — pinned by
    test_multi_display, and what anything introspecting app.routes expects —
    so keep them that way. This is byte-equivalent to the @app.<method>
    declarations the routes used to be: every route in these routers is
    declared with router-level defaults (no prefix, no extra dependencies,
    default response class), and each APIRouter is built with this app as its
    dependency_overrides_provider (see routes/*.build), which is exactly what
    @app.<method> bakes into an APIRoute."""
    app.router.routes.extend(router.routes)


_SELF = sys.modules[__name__]           # the routers' deps — see the comment above
_include_flat(routes_displays.build(_SELF))
_include_flat(routes_dev.build(_SELF))
_include_flat(routes_apps.build(_SELF))
_include_flat(routes_playlists.build(_SELF))
_include_flat(routes_helpers_api.build(_SELF))
_include_flat(routes_message.build(_SELF))
_include_flat(routes_local_api.build(_SELF))
_include_flat(routes_canvas_api.build(_SELF))

# Re-exported: the model lives with the playlist routes now; test_audit_fixes pins
# from here that a non-dict playlist entry is rejected at the door.
PlaylistSave = routes_playlists.PlaylistSave


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
    return _persistent_secret(
        config.mcp.get("token") or "", MCP_TOKEN_SETTING,
        "MCP: generated a bearer token (see the Dev menu, or set "
        "COMPANION_MCP_TOKEN to pin your own)")


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
