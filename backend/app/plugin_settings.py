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

import copy
import json
import logging
import os
import threading
from pathlib import Path

from .catalog import GLOBAL_STORAGE_KEYS

log = logging.getLogger("companion.settings")

# Top-level (non-setting) keys — kept out of the global/shared/apps sections. A key
# NOT listed here is a "stray bare key" and is silently dropped by _to_nested, so
# anything that must survive a restart belongs in this tuple.
#
# vestaboard_api_key: the generated Local API key (see main.vestaboard_key). It has
# to outlive the process — a key that changed on every restart would silently break
# an already-configured Home Assistant. Like the other secrets in this store (weather
# and YouTube API keys), it rides along to the gateway's settings mirror.
# mcp_token: the generated MCP bearer token (see main.mcp_token), persisted for the
# same reason — a token regenerated on every restart would silently break a configured
# LLM client.
# last_run: what was driving the display when we last shut down (see main.resume_last_run).
# It has to persist for the same reason the keys above do — a container that updates itself
# would otherwise come back to a dead board, having forgotten the playlist it was running.
_META_KEYS = ("installed_apps", "saved_app_playlists", "triggers", "triggers_enabled",
              "vestaboard_api_key", "mcp_token", "last_run",
              # True once the user has actually saved the Language control; lets
              # the UI-language chain tell "chose en-US" from "never touched"
              # (the store is seeded with en-US). See uilang.setting_is_explicit.
              "language_explicit")


def _detect_timezone() -> str:
    """The host's IANA timezone. tzlocal is the canonical resolver — it honors the
    ``TZ`` env var (how a Docker container's zone is set), the systemd
    ``/etc/localtime`` symlink, and Windows, falling back to ``US/Eastern`` here."""
    try:
        from tzlocal import get_localzone_name
        return get_localzone_name() or "US/Eastern"
    except Exception:
        return "US/Eastern"


# Content-relevant defaults (the app-facing subset of the plugin settings).
# Hardware keys (offsets, calibrations, serial_port, …) are deliberately excluded.
def _defaults() -> dict:
    return {
        # Global (catalog) settings — the only shared keys. Everything else is
        # per-app, stored as ``plugin_<id>_<key>`` and defaulted from manifests.
        "zip_code": "02118",
        "location_lat": "",
        "location_lon": "",
        "location_name": "",
        "timezone": _detect_timezone(),
        "language": "en-US",
        "weather_provider": "openmeteo",
        "weather_api_key": "",
        "yt_api_key": "",
        "global_loop_delay": 8,
        # Playlists + triggers (schedules/quiet-time now live on the gateway).
        "saved_app_playlists": {},
        "triggers": [],
        "triggers_enabled": True,
        # Generated once, when the Vestaboard-compatible API is first used (blank
        # until then, and unused entirely when COMPANION_VESTABOARD_KEY pins one).
        "vestaboard_api_key": "",
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
    def __init__(self, data_dir: Path, *, local_persist: bool = True):
        self.path = Path(data_dir) / "app_settings.json"
        self._lock = threading.Lock()
        self._data = _defaults()
        self._app_ids: set[str] = set()    # set by the runtime for tidy per-app nesting
        self._list_keys: set[str] = set()  # keys stored as JSON arrays (multi-value)
        # Gateway-mirror sync state (attach_gateway_sync wires the pusher).
        self._local_persist = local_persist      # False = gateway-only (nothing local)
        self._pusher = None                       # callable(nested_doc) -> bool
        self._debounce = 3.0
        self._timer: threading.Timer | None = None
        self._dirty = False
        if local_persist and self.path.exists():
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

    # -- on-disk structure (meta + global + per-app) -----------------------
    @staticmethod
    def _from_nested(doc: dict) -> dict:
        """Read the sectioned layout into a flat dict. Only recognized keys are
        kept: meta, reusable ``global`` catalog keys, and per-app
        ``plugin_<app>_*`` values. Settings are cleanly global-or-per-app, so any
        stray cross-app ('shared') key is dropped rather than carried forward.
        Array values (multi-value settings) become comma-strings in memory."""
        if not isinstance(doc, dict):
            return {}

        def unlist(v):
            return ",".join(str(x) for x in v) if isinstance(v, list) else v

        flat: dict = {}
        for k in _META_KEYS:
            if k in doc:
                flat[k] = doc[k]

        if any(k in doc for k in ("global", "apps")):
            for k, v in (doc.get("global") or {}).items():
                if not k.startswith("_"):
                    flat[k] = unlist(v)
            for app_id, kv in (doc.get("apps") or {}).items():
                if app_id.startswith("_") and app_id != "_other":
                    continue
                for k, v in (kv or {}).items():
                    # "_other" holds keys for an app id we don't recognize (e.g. a
                    # since-removed upload); they're stored under their full key.
                    flat[k if app_id == "_other" else f"plugin_{app_id}_{k}"] = unlist(v)
            return flat  # a legacy "shared" section, if present, is intentionally ignored

        # A legacy flat file: keep only recognized keys; drop bare cross-app cruft.
        for k, v in doc.items():
            if k in _META_KEYS or k in GLOBAL_STORAGE_KEYS or k.startswith("plugin_"):
                flat[k] = v
        return flat

    def _to_nested(self) -> dict:
        """Group the flat store: meta at top; reusable catalog keys under
        ``global``; ``plugin_<app>_*`` under ``apps`` (by app id). Every setting is
        either a reusable global (the catalog) or per-app — there is no cross-app
        bucket, so a stray bare key is simply not persisted. Multi-value keys
        become JSON arrays."""
        def as_stored(k, v):
            if k in self._list_keys:
                return [x.strip() for x in str(v).split(",") if x.strip()]
            return v

        # A self-documenting header (JSON has no comments). Keys starting with
        # "_" are ignored on load, so this is safe to carry in the file.
        doc: dict = {"_about": {
            "_": "SplitFlap Gateway Companion settings — prefer editing in the app UI.",
            "global": "Reusable settings shared across apps (the Global settings editor).",
            "apps": "Per-app settings, keyed by app id (e.g. apps.weather.show_aqi).",
            "arrays": "Multi-value settings are stored as JSON arrays.",
        }}
        defaults = _defaults()
        for k in _META_KEYS:
            doc[k] = self._data.get(k, defaults.get(k))
        glob, apps = {}, {}
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
            # else: a stray bare (cross-app) key — intentionally not persisted
        doc["global"] = dict(sorted(glob.items()))
        doc["apps"] = {a: dict(sorted(kv.items())) for a, kv in sorted(apps.items())}
        return doc

    def _save(self) -> None:
        """A settings mutation: persist locally (unless gateway-only) and schedule a
        debounced push to the gateway mirror if one is attached."""
        if self._local_persist:
            self._write_raw_doc(self._to_nested())
        self._dirty = True
        self._schedule_push()

    def _write_raw_doc(self, doc: dict) -> None:
        """Write a nested settings doc atomically: temp file, fsync, rename over the
        target. A crash/kill mid-write leaves the previous good file intact instead
        of a truncated JSON that would reset all settings on next start."""
        data = json.dumps(doc, indent=2, ensure_ascii=False)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    # -- gateway mirror ----------------------------------------------------
    def attach_gateway_sync(self, pusher, debounce: float = 3.0) -> None:
        """Register a push callback (nested_doc -> bool) called, debounced, after
        changes. Used to mirror settings onto the gateway (Gateway 3.1+)."""
        self._pusher = pusher
        self._debounce = max(0.0, float(debounce))

    def restore_from_doc(self, doc: dict) -> None:
        """Replace the store from a gateway-provided nested doc (boot restore on a
        fresh host / gateway-only mode). Writes the local cache verbatim (it's
        already nested) without marking the store dirty — it just came from there."""
        with self._lock:
            self._data = _defaults()
            self._data.update(self._from_nested(doc))
            if self._local_persist:
                try:
                    self._write_raw_doc(doc)
                except OSError as e:
                    log.warning("could not cache restored settings locally: %s", e)

    def snapshot(self) -> dict:
        """The current settings as the nested doc (for a manual export/gateway push)."""
        with self._lock:
            return self._to_nested()

    def has_local(self) -> bool:
        """Whether a non-empty local settings file exists (a fresh host has none)."""
        try:
            return self._local_persist and self.path.exists() and self.path.stat().st_size > 0
        except OSError:
            return False

    def set_gateway_only(self) -> None:
        """Switch to gateway-only storage: stop writing locally and drop any cache."""
        self._local_persist = False
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def sync_now(self) -> None:
        """Mark the store dirty and schedule a push (e.g. to seed an empty gateway)."""
        self._dirty = True
        self._schedule_push()

    def _schedule_push(self) -> None:
        if self._pusher is None:
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self._debounce, self.flush)
        self._timer.daemon = True
        self._timer.start()

    def flush(self) -> bool:
        """Push the current settings to the gateway now if there's anything pending.
        Called by the debounce timer, a periodic retry, and at shutdown. Returns
        True if nothing is pending or the push succeeded."""
        if self._pusher is None:
            return True
        with self._lock:
            if not self._dirty:
                return True
            doc = self._to_nested()
        try:
            ok = bool(self._pusher(doc))
        except Exception as e:
            log.warning("settings push to gateway failed: %s", e)
            ok = False
        if ok:
            self._dirty = False
            log.debug("settings mirrored to gateway (%d installed apps)", len(self._data.get("installed_apps", [])))
        return ok

    def all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, key: str, default=None):
        with self._lock:
            return copy.deepcopy(self._data.get(key, default))

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
