"""
config.py — companion configuration from defaults, the gateway, and env vars.

**Nothing is persisted.** Grid geometry and the MQTT broker are pulled from the
gateway's ``/api/config`` at runtime; the gateway URL and (optional) MQTT
password come from the environment. On restart everything is re-derived, so there
is no config file to manage. Precedence: ``defaults <- gateway sync <- env``, so
env vars always win and a gateway-synced value never gets written to disk.
"""

from __future__ import annotations

import copy
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
    # This companion's own public URL, registered with the gateway (v3.0) so the
    # gateway can show a "Companion" tab linking back here. Blank = auto-detect
    # this host's LAN IP + port. Set via COMPANION_PUBLIC_URL to override.
    "companion_url": "",
    # Bind address + port (also used to build the auto-detected companion URL).
    "host": "0.0.0.0",
    "port": 8000,
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
    if "COMPANION_PUBLIC_URL" in e:
        ov["companion_url"] = e["COMPANION_PUBLIC_URL"]
    if "COMPANION_HOST" in e:
        ov["host"] = e["COMPANION_HOST"]
    if "COMPANION_PORT" in e:
        ov["port"] = int(e["COMPANION_PORT"])
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


def default_data_dir() -> Path:
    # <repo>/data  (backend/app/config.py -> parents[2] == repo root)
    env = os.environ.get("COMPANION_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data"


class Config:
    """Loads, merges and persists companion configuration."""

    def __init__(self, data_dir: Path | None = None):
        # data_dir still holds app_settings.json + uploaded apps; the companion
        # config itself is never written there (or anywhere).
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._synced: dict = {}     # values pulled from the gateway at runtime
        # Developer mode (env-gated). When on, the UI exposes a dev menu that can
        # toggle a "simulation" transport (nothing reaches the display) and, while
        # simulating, override the grid geometry for layout testing.
        self.dev_mode = os.environ.get("COMPANION_DEV_MODE", "").lower() in ("1", "true", "yes", "on")
        self._sim = False
        self._grid_override: dict | None = None   # {rows, cols}; only honored in sim mode
        self._effective: dict = self._recompute()

    def _recompute(self) -> dict:
        merged = _deep_merge(DEFAULTS, self._synced)
        return _deep_merge(merged, _env_overrides())

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

    def dev_state(self) -> dict:
        """State for the developer menu (safe to expose regardless of dev_mode)."""
        return {
            "enabled": self.dev_mode,
            "sim_mode": self._sim,
            "grid": self.grid,
            "gateway_grid": copy.deepcopy(self._effective["grid"]),
            "grid_overridden": bool(self._sim and self._grid_override),
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
