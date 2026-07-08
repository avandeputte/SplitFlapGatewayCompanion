"""
plugin_settings.py — the app-facing settings store.

splitflap-os passes each plugin ``dict(settings)`` — a flat dict of global keys
(``zip_code``, ``weather_api_key``, ``crypto_list``, …) plus per-plugin values
stored as ``plugin_<id>_<key>``. To keep apps drop-in compatible, the companion
maintains the same flat store here, seeded with the content-relevant defaults
ported from splitflap-os (hardware/calibration keys are intentionally omitted).

Persisted to ``<data_dir>/app_settings.json``. (The companion keeps no config
file of its own — hardware config is read from the gateway at runtime; see
``config.py``.)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("companion.settings")


def _detect_timezone() -> str:
    try:
        tz = Path("/etc/timezone").read_text("utf-8").strip()
        if tz:
            return tz
    except OSError:
        pass
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/")[-1]
    except OSError:
        return "US/Eastern"


# Content-relevant defaults (the app-facing subset of splitflap-os load_settings).
# Hardware keys (offsets, calibrations, serial_port, …) are deliberately excluded.
def _defaults() -> dict:
    return {
        "zip_code": "02118",
        "location_lat": "",
        "location_lon": "",
        "location_name": "",
        "timezone": _detect_timezone(),
        "weather_api_key": "",
        "mbta_stop": "",
        "mbta_route": "",
        "stocks_list": "",
        "yt_channel_id": "",
        "yt_api_key": "",
        "yt_video_id": "",
        "countdown_event": "NEW YEAR",
        "countdown_target": "2027-01-01T00:00:00",
        "world_clock_zones": "US/Eastern,US/Pacific,Europe/London",
        "crypto_list": "bitcoin,ethereum,solana",
        "anim_style": "ltr",
        "anim_speed": "0.4",
        "anim_text": "SPLIT  FLAP  DISPLAY",
        "currency_symbol": "$",
        "livestream_interval": "25",
        "livestream_comments": "",
        "sports_nfl": "", "sports_nba": "", "sports_mlb": "", "sports_nhl": "",
        "sports_ncaaf": "", "sports_ncaab": "", "sports_mls": "", "sports_epl": "",
        "sports_laliga": "", "sports_ucl": "", "sports_wnba": "", "sports_pga": "",
        "sports_ufc": "",
        "global_loop_delay": 5,
        "transition_style": "ltr",
        "transition_speed": 15,
        # Playlists + triggers (schedules/quiet-time now live on the gateway).
        "saved_app_playlists": {},
        "triggers": [],
        "triggers_enabled": True,
        # Which apps are enabled (shown in the grid + loaded). Mirrors the
        # splitflap-os default set; the app library (later phase) manages this.
        "installed_apps": [
            "time", "date", "weather", "stocks", "sports", "countdown",
            "world_clock", "crypto", "iss", "metro", "youtube", "yt_comments",
            "dashboard", "demo", "livestream",
            "anim_rainbow", "anim_sweep", "anim_twinkle", "anim_checker",
            "anim_matrix", "anim_random_spin",
            "word-clock", "moon-phase", "star-wars-quotes",
        ],
    }


class PluginSettings:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "app_settings.json"
        self._lock = threading.Lock()
        self._data = _defaults()
        if self.path.exists():
            try:
                self._data.update(json.loads(self.path.read_text("utf-8")))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("could not read %s: %s", self.path, e)

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")

    def all(self) -> dict:
        return dict(self._data)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def update(self, mapping: dict) -> None:
        with self._lock:
            self._data.update(mapping)
            self._save()

    @property
    def installed_apps(self) -> list[str]:
        return list(self._data.get("installed_apps", []))

    def set_installed(self, apps: list[str]) -> None:
        self.update({"installed_apps": list(dict.fromkeys(apps))})
