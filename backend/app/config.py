"""
config.py — persisted companion configuration with env-var overrides.

Config lives in ``<data_dir>/config.json`` (a Docker volume in production).
Environment variables always win over the saved file, so a container can be
configured purely through env without a pre-seeded file.
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
        "type": "sim",  # "sim" | "mqtt" | "rest"
        # Base URL of the SplitFlapGateway (used by the REST transport and the
        # gateway reverse-proxy / status pill).
        "gateway_url": "http://splitflap-gateway.local",
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

    if "COMPANION_TRANSPORT" in e:
        ov["transport"]["type"] = e["COMPANION_TRANSPORT"]
    if "COMPANION_GATEWAY_URL" in e:
        ov["transport"]["gateway_url"] = e["COMPANION_GATEWAY_URL"]
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
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "config.json"
        self._lock = threading.Lock()
        self._file_config: dict = self._read_file()
        self._effective: dict = self._recompute()

    def _read_file(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _recompute(self) -> dict:
        merged = _deep_merge(DEFAULTS, self._file_config)
        return _deep_merge(merged, _env_overrides())

    # -- access -------------------------------------------------------------
    @property
    def effective(self) -> dict:
        """The active config: defaults <- saved file <- env overrides."""
        return copy.deepcopy(self._effective)

    @property
    def grid(self) -> dict:
        return self._effective["grid"]

    @property
    def transport(self) -> dict:
        return self._effective["transport"]

    @property
    def display(self) -> dict:
        return self._effective["display"]

    def module_count(self) -> int:
        return int(self.grid["rows"]) * int(self.grid["cols"])

    # -- mutation -----------------------------------------------------------
    def update(self, patch: dict) -> dict:
        """Merge ``patch`` into the persisted config and re-derive effective.

        Env overrides still win in the effective view, so a value pinned by env
        cannot be changed from the UI (by design).
        """
        with self._lock:
            self._file_config = _deep_merge(self._file_config, patch)
            self.path.write_text(
                json.dumps(self._file_config, indent=2, ensure_ascii=False), "utf-8"
            )
            self._effective = self._recompute()
        return self.effective
