"""
config.py — companion configuration from defaults, the gateway, and env vars.

**Nothing is persisted.** Grid geometry and the MQTT broker are pulled from the
gateway's ``/api/config`` at runtime; the gateway URL and (optional) MQTT
password come from the environment. On restart everything is re-derived, so there
is no config file to manage.

Precedence: ``defaults <- gateway sync <- add-on options <- env``. Env vars always
win, and a gateway-synced value never gets written to disk. The add-on layer reads
``/data/options.json``, which exists only when we run as a Home Assistant add-on —
see ``_addon_overrides``.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

DEFAULTS: dict = {
    "grid": {
        "rows": 3,
        "cols": 15,
        # Physical module id of grid index 0. Frames address module
        # (module_id_base + grid_index); the display is filled row-major.
        "module_id_base": 0,
    },
    "transport": {
        # The companion ALWAYS drives the gateway over REST (a whole page in one
        # /api/rs485/batch request, Gateway 3.0+). This is intentionally NOT
        # configurable — there is no transport selector or env var. The "mqtt"
        # block below is used ONLY by the Home Assistant integration (see the "ha"
        # section), never for the display.
        #
        # gateway_url has NO default on purpose: it must be supplied via the
        # GATEWAY_URL env var, and the app refuses to start without it (rather than
        # silently retrying against a phantom host). See main.py / __main__.py.
        "gateway_url": "",
        "mqtt": {
            "broker": "",
            "port": 1883,
            "prefix": "splitflap",
            "username": "",
            "password": "",
        },
    },
    "display": {
        "transition_style": "ltr",
        "transition_speed": 15,  # ms per step for ordered styles
        "slot_speed": 80,        # ms per lock-in for slot style
        "currency_symbol": "$",
    },
    # Pull grid geometry + MQTT broker from the gateway's own /api/config on
    # startup (the gateway is the source of truth for hardware config).
    "sync_from_gateway": True,
    # Where the companion's settings live (needs Gateway 3.1+ for the gateway):
    #   "mirror"  — local file is primary, mirrored to the gateway (gzipped) on
    #               change; a fresh host with no local file restores from the gateway.
    #   "local"   — local file only, never touches the gateway (pre-3.1 behavior).
    #   "gateway" — stored ONLY on the gateway, nothing written locally.
    # Set via COMPANION_SETTINGS_STORE. On a pre-3.1 gateway this degrades to local.
    "settings_store": "mirror",
    # This companion's own public URL, registered with the gateway (v3.0) so the
    # gateway can show a "Companion" tab linking back here. Blank = auto-detect
    # this host's LAN IP + port. Set via COMPANION_PUBLIC_URL to override.
    "companion_url": "",
    # Bind address + port (also used to build the auto-detected companion URL).
    "host": "0.0.0.0",
    "port": 8000,
    # Vestaboard-compatible Local API (see vestaboard.py). OFF by default: it is an
    # extra, key-authenticated HTTP surface, so it only exists when asked for. The
    # api_key is normally left blank and generated once, then persisted with the app
    # settings (a key regenerated on every restart would break a configured client).
    # enablement_token mirrors Vestaboard's own flow: present it to
    # POST /local-api/enablement and the key comes back.
    "vestaboard": {
        "enabled": False,
        "api_key": "",
        "enablement_token": "",
    },
    # MCP server (see mcp_server.py) — lets an LLM client drive the display as a
    # set of tools. OFF by default and bearer-authenticated, for the same reason
    # the Vestaboard layer is: it is an extra write surface, so it only exists when
    # asked for. `token` is normally blank and generated once, then persisted with
    # the app settings — a token regenerated on every restart would silently break
    # a configured client.
    "mcp": {
        "enabled": False,
        "token": "",
    },
    # Home Assistant MQTT integration. "auto" follows the gateway's own HA
    # setting (haEnabled from its /api/config); true/false force it. Uses the
    # same MQTT broker as the transport (transport.mqtt).
    "ha": {
        "enabled": "auto",
        "discovery_prefix": "homeassistant",
        "topic_prefix": "splitflap-companion",
        "node_id": "splitflap-companion",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides() -> dict:
    """Build a sparse override tree from COMPANION_* environment variables."""
    e = os.environ
    ov: dict = {"grid": {}, "transport": {"mqtt": {}}, "display": {}}

    if "COMPANION_GRID_ROWS" in e:
        ov["grid"]["rows"] = int(e["COMPANION_GRID_ROWS"])
    if "COMPANION_GRID_COLS" in e:
        ov["grid"]["cols"] = int(e["COMPANION_GRID_COLS"])
    if "COMPANION_MODULE_ID_BASE" in e:
        ov["grid"]["module_id_base"] = int(e["COMPANION_MODULE_ID_BASE"])

    if "COMPANION_SYNC_FROM_GATEWAY" in e:
        ov["sync_from_gateway"] = e["COMPANION_SYNC_FROM_GATEWAY"].lower() in ("1", "true", "yes", "on")
    if "COMPANION_SETTINGS_STORE" in e:
        v = e["COMPANION_SETTINGS_STORE"].strip().lower()
        # tolerant aliases: off/none/no -> local, only -> gateway
        v = {"off": "local", "none": "local", "no": "local", "only": "gateway"}.get(v, v)
        ov["settings_store"] = v if v in ("mirror", "local", "gateway") else "mirror"
    if "COMPANION_PUBLIC_URL" in e:
        ov["companion_url"] = e["COMPANION_PUBLIC_URL"]
    if "COMPANION_HOST" in e:
        ov["host"] = e["COMPANION_HOST"]
    if "COMPANION_PORT" in e:
        ov["port"] = int(e["COMPANION_PORT"])
    if "COMPANION_VESTABOARD" in e:
        ov.setdefault("vestaboard", {})["enabled"] = \
            e["COMPANION_VESTABOARD"].lower() in ("1", "true", "yes", "on")
    if "COMPANION_VESTABOARD_KEY" in e:
        ov.setdefault("vestaboard", {})["api_key"] = e["COMPANION_VESTABOARD_KEY"].strip()
    if "COMPANION_VESTABOARD_ENABLEMENT_TOKEN" in e:
        ov.setdefault("vestaboard", {})["enablement_token"] = \
            e["COMPANION_VESTABOARD_ENABLEMENT_TOKEN"].strip()
    if "COMPANION_MCP" in e:
        ov.setdefault("mcp", {})["enabled"] = \
            e["COMPANION_MCP"].lower() in ("1", "true", "yes", "on")
    if "COMPANION_MCP_TOKEN" in e:
        ov.setdefault("mcp", {})["token"] = e["COMPANION_MCP_TOKEN"].strip()
    if "COMPANION_HA" in e:
        v = e["COMPANION_HA"].lower()
        ov.setdefault("ha", {})["enabled"] = True if v in ("1", "true", "yes", "on") \
            else False if v in ("0", "false", "no", "off") else "auto"
    if "COMPANION_HA_DISCOVERY_PREFIX" in e:
        ov.setdefault("ha", {})["discovery_prefix"] = e["COMPANION_HA_DISCOVERY_PREFIX"]

    if "GATEWAY_URL" in e:
        ov["transport"]["gateway_url"] = e["GATEWAY_URL"]
    if "COMPANION_MQTT_BROKER" in e:
        ov["transport"]["mqtt"]["broker"] = e["COMPANION_MQTT_BROKER"]
    if "COMPANION_MQTT_PORT" in e:
        ov["transport"]["mqtt"]["port"] = int(e["COMPANION_MQTT_PORT"])
    if "COMPANION_MQTT_PREFIX" in e:
        ov["transport"]["mqtt"]["prefix"] = e["COMPANION_MQTT_PREFIX"]
    if "COMPANION_MQTT_USER" in e:
        ov["transport"]["mqtt"]["username"] = e["COMPANION_MQTT_USER"]
    if "COMPANION_MQTT_PASSWORD" in e:
        ov["transport"]["mqtt"]["password"] = e["COMPANION_MQTT_PASSWORD"]

    # Drop empty branches so they don't clobber nested defaults.
    ov["transport"] = {k: v for k, v in ov["transport"].items() if v != {}}
    return {k: v for k, v in ov.items() if v != {}}


# Where the Home Assistant Supervisor writes an add-on's configuration. It exists
# only when we ARE an add-on, which is exactly how we detect that we are one.
ADDON_OPTIONS = Path("/data/options.json")


def addon_options() -> dict:
    """The raw Home Assistant add-on options, or {} when we aren't an add-on.

    As an add-on there are no environment variables to set: the user fills in the
    Configuration tab and Supervisor writes the result to ``/data/options.json``.
    We read that file directly rather than shipping a *second* image whose only job
    is to translate it into env vars — same image everywhere, one less thing to keep
    in sync (see addon/config.yaml).
    """
    try:
        raw = json.loads(ADDON_OPTIONS.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def addon_option(key: str, default: str = "") -> str:
    """One add-on option, for the settings read before Config exists (the log level
    is configured at import time, so it can't go through the merge below)."""
    v = addon_options().get(key)
    return v.strip() if isinstance(v, str) and v.strip() else default


def _addon_overrides() -> dict:
    """The add-on options as an override tree.

    Keys mirror the env vars one-for-one, minus the ``COMPANION_`` prefix. Empty
    strings mean "not set" and are skipped, so a blank optional field in the HA UI
    doesn't clobber a default. Precedence keeps env above this, so a `docker run -e`
    still wins when debugging an add-on image by hand.
    """
    raw = addon_options()
    if not raw:
        return {}

    def val(key):
        v = raw.get(key)
        return v.strip() if isinstance(v, str) else v

    ov: dict = {"transport": {"mqtt": {}}}
    if val("gateway_url"):
        ov["transport"]["gateway_url"] = val("gateway_url")
    if val("mqtt_password"):
        ov["transport"]["mqtt"]["password"] = val("mqtt_password")
    if val("companion_public_url"):
        ov["companion_url"] = val("companion_public_url")
    if raw.get("home_assistant") is not None:
        v = str(val("home_assistant")).lower()
        ov["ha"] = {"enabled": True if v in ("1", "true", "yes", "on")
                    else False if v in ("0", "false", "no", "off") else "auto"}
    if raw.get("vestaboard") is not None:
        ov["vestaboard"] = {"enabled": bool(raw["vestaboard"])}
    if val("vestaboard_key"):
        ov.setdefault("vestaboard", {})["api_key"] = val("vestaboard_key")
    if raw.get("mcp") is not None:
        ov["mcp"] = {"enabled": bool(raw["mcp"])}
    if val("mcp_token"):
        ov.setdefault("mcp", {})["token"] = val("mcp_token")

    ov["transport"] = {k: v for k, v in ov["transport"].items() if v != {}}
    return {k: v for k, v in ov.items() if v != {}}


def default_data_dir() -> Path:
    # <repo>/data  (backend/app/config.py -> parents[2] == repo root)
    env = os.environ.get("COMPANION_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data"


class Config:
    """Loads, merges and persists companion configuration."""

    def __init__(self, data_dir: Path | None = None, *, gateway_url: str = ""):
        # data_dir still holds app_settings.json + uploaded apps; the companion
        # config itself is never written there (or anywhere).
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._synced: dict = {}     # values pulled from the gateway at runtime
        # A display's gateway URL IS its identity, so it outranks even the env var —
        # otherwise GATEWAY_URL would drag every display onto the same gateway and the
        # second wall would quietly drive the first. Blank means "no display registry
        # in play": fall through to the env/add-on value, which is the single-display
        # case and every existing install.
        self._identity: dict = ({"transport": {"gateway_url": gateway_url.strip()}}
                                if str(gateway_url or "").strip() else {})
        # Developer mode (env-gated). When on, the UI exposes a dev menu that can
        # toggle a "simulation" transport (nothing reaches the display) and, while
        # simulating, override the grid geometry for layout testing.
        # Also an add-on option, so it can be ticked on the Configuration tab: an add-on
        # user has no way to set an environment variable.
        self.dev_mode = (os.environ.get("COMPANION_DEV_MODE", "").lower() in ("1", "true", "yes", "on")
                         or bool(addon_options().get("dev_mode")))
        # Deployment-level default for the UI (chrome) language — level 3 of the
        # resolution chain in uilang.py. Unset (blank, or the add-on's "auto"
        # sentinel: a list() option cannot offer a blank choice) falls through to
        # Home Assistant's language and then the browser's. Never affects the flap
        # *content* language.
        ui = (os.environ.get("COMPANION_UI_LANGUAGE", "").strip()
              or str(addon_options().get("ui_language") or "").strip())
        self.ui_language = "" if ui.lower() == "auto" else ui
        self._sim = False
        self._grid_override: dict | None = None   # {rows, cols}; only honored in sim mode
        self._effective: dict = self._recompute()
        # The Vestaboard layer starts wherever the env put it, and the dev menu can
        # flip it at runtime from there (like sim mode). It is deliberately NOT read
        # from self._effective on every call: a runtime toggle has to be able to win
        # over the env value for the life of the process, and env always beats the
        # config tree (see _recompute).
        self._vestaboard = bool(self._effective["vestaboard"]["enabled"])
        # Same deal for the MCP server (see mcp_server.py).
        self._mcp = bool(self._effective["mcp"]["enabled"])

    def _recompute(self) -> dict:
        # defaults <- gateway sync <- add-on options <- env <- this display's identity.
        # Env stays above the add-on so a hand-run container can override anything; the
        # display's own gateway_url sits above even that, because it is not a preference
        # — it is which wall this object IS.
        merged = _deep_merge(DEFAULTS, self._synced)
        merged = _deep_merge(merged, _addon_overrides())
        merged = _deep_merge(merged, _env_overrides())
        return _deep_merge(merged, self._identity)

    # -- access -------------------------------------------------------------
    @property
    def effective(self) -> dict:
        """The active config: defaults <- gateway sync <- env overrides."""
        return copy.deepcopy(self._effective)

    @property
    def grid(self) -> dict:
        base = copy.deepcopy(self._effective["grid"])
        if self._sim and self._grid_override:
            base.update(self._grid_override)   # dev geometry override (sim mode only)
        return base

    # -- developer mode -----------------------------------------------------
    @property
    def sim_mode(self) -> bool:
        return self._sim

    def set_sim_mode(self, on: bool) -> None:
        with self._lock:
            self._sim = bool(on)
            if not self._sim:
                self._grid_override = None   # leaving sim reverts to the real geometry

    def set_grid_override(self, rows: int, cols: int) -> None:
        with self._lock:
            self._grid_override = {"rows": max(1, int(rows)), "cols": max(1, int(cols))}

    def clear_grid_override(self) -> None:
        with self._lock:
            self._grid_override = None

    # -- Vestaboard-compatible API ------------------------------------------
    @property
    def vestaboard_enabled(self) -> bool:
        return self._vestaboard

    def set_vestaboard(self, on: bool) -> None:
        with self._lock:
            self._vestaboard = bool(on)

    @property
    def vestaboard(self) -> dict:
        """The Vestaboard block, with `enabled` reflecting any runtime toggle."""
        vb = copy.deepcopy(self._effective["vestaboard"])
        vb["enabled"] = self._vestaboard
        return vb

    @property
    def mcp_enabled(self) -> bool:
        return self._mcp

    def set_mcp(self, on: bool) -> None:
        with self._lock:
            self._mcp = bool(on)

    @property
    def mcp(self) -> dict:
        """The MCP block, with `enabled` reflecting any runtime toggle."""
        m = copy.deepcopy(self._effective["mcp"])
        m["enabled"] = self._mcp
        return m

    def dev_state(self) -> dict:
        """State for the developer menu (safe to expose regardless of dev_mode)."""
        return {
            "enabled": self.dev_mode,
            "sim_mode": self._sim,
            "grid": self.grid,
            "gateway_grid": copy.deepcopy(self._effective["grid"]),
            "grid_overridden": bool(self._sim and self._grid_override),
            # Just the switch. The key lives in the settings store (Config can't see
            # it), so the dev menu reads it from GET /api/dev/vestaboard instead.
            "vestaboard": self._vestaboard,
            # Same for the MCP token — GET /api/dev/mcp has it.
            "mcp": self._mcp,
        }

    @property
    def transport(self) -> dict:
        return copy.deepcopy(self._effective["transport"])

    @property
    def display(self) -> dict:
        return copy.deepcopy(self._effective["display"])

    def module_count(self) -> int:
        return int(self.grid["rows"]) * int(self.grid["cols"])

    # -- mutation -----------------------------------------------------------
    def update(self, patch: dict) -> dict:
        """Apply ``patch`` (gateway sync or a runtime tweak) in memory only.

        Nothing is written to disk — on restart the config is re-derived from
        defaults + the gateway + env. Env overrides still win in the effective
        view, so a value pinned by env can't be changed at runtime (by design).
        """
        with self._lock:
            self._synced = _deep_merge(self._synced, patch)
            self._effective = self._recompute()
        return self.effective
