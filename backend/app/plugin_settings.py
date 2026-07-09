"""
plugin_settings.py — the app-facing settings store.

Each plugin is handed a flat ``dict(settings)`` of global keys
(``zip_code``, ``weather_api_key``, ``crypto_list``, …) plus per-plugin values
stored as ``plugin_<id>_<key>``. To keep apps drop-in compatible, the companion
maintains the same flat store here, seeded with the content-relevant defaults
ported from the app-plugin settings (hardware/calibration keys are intentionally omitted).

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

from .catalog import GLOBAL_STORAGE_KEYS

log = logging.getLogger("companion.settings")

# Top-level (non-setting) keys — kept out of the global/shared/apps sections.
_META_KEYS = ("installed_apps", "saved_app_playlists", "triggers", "triggers_enabled")


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


# Content-relevant defaults (the app-facing subset of the plugin settings).
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
        "livestream_interval": "25",
        "livestream_comments": "",
        "sports_nfl": "", "sports_nba": "", "sports_mlb": "", "sports_nhl": "",
        "sports_ncaaf": "", "sports_ncaab": "", "sports_mls": "", "sports_epl": "",
        "sports_laliga": "", "sports_ucl": "", "sports_wnba": "", "sports_pga": "",
        "sports_ufc": "",
        "global_loop_delay": 5,
        # NOTE: the global transition style/speed, slot speed and currency symbol
        # are display config owned by ``config.display`` (defaults <- gateway <-
        # env) and read from there, so they are intentionally NOT duplicated here.
        # Only ``plugin_<id>_transition_style`` (a per-app override) lives in this
        # store.
        # Playlists + triggers (schedules/quiet-time now live on the gateway).
        "saved_app_playlists": {},
        "triggers": [],
        "triggers_enabled": True,
        # Which apps are enabled (shown in the grid + loaded). The default app
        # set; the App Library manages this at runtime.
        "installed_apps": [
            "time", "date", "weather", "stocks", "sports", "countdown",
            "world_clock", "crypto", "iss", "metro", "youtube", "yt_comments",
            "dashboard", "livestream",
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
        self._app_ids: set[str] = set()    # set by the runtime for tidy per-app nesting
        self._list_keys: set[str] = set()  # keys stored as JSON arrays (multi-value)
        if self.path.exists():
            try:
                self._data.update(self._from_nested(json.loads(self.path.read_text("utf-8"))))
            except (json.JSONDecodeError, OSError) as e:
                log.warning("could not read %s: %s", self.path, e)

    def set_known_apps(self, app_ids) -> None:
        """The runtime supplies the known app ids so per-app keys can nest by app
        when persisted (``plugin_<app>_<key>`` -> ``apps[<app>][<key>]``)."""
        with self._lock:
            self._app_ids = set(app_ids)

    def set_list_keys(self, keys) -> None:
        """Keys whose value is a comma-list (multi-value) — stored as JSON arrays
        on disk, but kept as comma-strings in memory (what apps expect)."""
        with self._lock:
            self._list_keys = set(keys)

    # -- on-disk structure (meta + global + shared + per-app) --------------
    @staticmethod
    def _from_nested(doc: dict) -> dict:
        """Read the sectioned layout — or a legacy flat file — into a flat dict.
        Array values (multi-value settings) become comma-strings in memory."""
        if not isinstance(doc, dict):
            return {}
        if not any(k in doc for k in ("global", "shared", "apps")):
            return doc  # legacy flat file (loaded as-is; re-saved sectioned)

        def unlist(v):
            return ",".join(str(x) for x in v) if isinstance(v, list) else v

        flat: dict = {}
        for k in _META_KEYS:
            if k in doc:
                flat[k] = doc[k]
        for section in ("global", "shared"):
            for k, v in (doc.get(section) or {}).items():
                if k.startswith("_"):
                    continue  # documentation/comment key
                flat[k] = unlist(v)
        for app_id, kv in (doc.get("apps") or {}).items():
            if app_id.startswith("_") and app_id != "_other":
                continue
            for k, v in (kv or {}).items():
                # "_other" holds keys we couldn't attribute to a known app; they
                # were stored under their full flat key, so keep them verbatim.
                flat[k if app_id == "_other" else f"plugin_{app_id}_{k}"] = unlist(v)
        return flat

    def _to_nested(self) -> dict:
        """Group the flat store: meta at top; reusable global keys under
        ``global``; other bare (cross-app) keys under ``shared``;
        ``plugin_<app>_*`` under ``apps``. Multi-value keys become JSON arrays."""
        def as_stored(k, v):
            if k in self._list_keys:
                return [x.strip() for x in str(v).split(",") if x.strip()]
            return v

        # A self-documenting header (JSON has no comments). Keys starting with
        # "_" are ignored on load, so this is safe to carry in the file.
        doc: dict = {"_about": {
            "_": "SplitFlap Gateway Companion settings — prefer editing in the app UI.",
            "global": "Reusable settings shared across apps (the Global settings editor).",
            "shared": "Other settings used across apps; edited in each app's own dialog.",
            "apps": "Per-app settings, keyed by app id (e.g. apps.weather.show_aqi).",
            "arrays": "Multi-value settings are stored as JSON arrays.",
        }}
        for k in _META_KEYS:
            doc[k] = self._data.get(k, _defaults().get(k))
        glob, shared, apps = {}, {}, {}
        ids = sorted(self._app_ids, key=len, reverse=True)  # greedy longest match
        for k, v in self._data.items():
            if k in _META_KEYS:
                continue
            if k.startswith("plugin_"):
                app = next((a for a in ids if k.startswith(f"plugin_{a}_")), None)
                if app:
                    apps.setdefault(app, {})[k[len(f"plugin_{app}_"):]] = as_stored(k, v)
                else:
                    apps.setdefault("_other", {})[k] = as_stored(k, v)  # unknown app
            elif k in GLOBAL_STORAGE_KEYS:
                glob[k] = as_stored(k, v)
            else:
                shared[k] = as_stored(k, v)
        doc["global"] = dict(sorted(glob.items()))
        doc["shared"] = dict(sorted(shared.items()))
        doc["apps"] = {a: dict(sorted(kv.items())) for a, kv in sorted(apps.items())}
        return doc

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(self._to_nested(), indent=2, ensure_ascii=False), "utf-8")

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
