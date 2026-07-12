"""
plugins.py — the plugin runtime (a faithful port of the app-plugin contract).

Discovers and loads apps from ``apps/<id>/`` (functional ``app.py`` or channel
``data.json``), assembles the per-app settings dict exactly as the app-plugin contract expects,
and produces display pages with the same caching/paging semantics. Keeping this
behaviour-identical is what lets any compatible app drop in unchanged — see
COMPATIBILITY.md.

``fetch()`` may do blocking network I/O, so callers run ``get_pages()`` in a
thread executor (see engine.py).
"""

from __future__ import annotations

import ast
import collections
import importlib.util
import inspect
import json
import logging
import os
import threading
import time
from pathlib import Path

from . import appaudit, i18n, location, weather
from .catalog import CATALOG, CATALOG_BY_KEY, CATALOG_KEYS, GLOBAL_STORAGE_KEYS
from .config import Config
from .plugin_settings import PluginSettings

log = logging.getLogger("companion.plugins")

# Passed through from a manifest setting to the frontend field, verbatim.
_PASSTHROUGH = (
    "size", "ph", "min", "max", "step", "stepper", "searchUrl", "resultKey",
    "maxItems", "compute", "watches", "variant", "title", "text", "items",
    "icon", "linkText", "linkHref", "default", "note",
)


class _SettingsOverlay:
    """A read-through view of the settings store with a transient override layer,
    used to render one app with per-instance config (e.g. a playlist entry that
    carries its own location/language) without touching the saved settings."""

    def __init__(self, base, overrides):
        self._base = base
        self._ov = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}

    def get(self, key, default=None):
        if key in self._ov:
            return self._ov[key]
        return self._base.get(key, default)

    def all(self):
        merged = self._base.all()
        merged.update(self._ov)
        return merged


def _cache_key(app_id: str, overrides: dict | None) -> str:
    """Cache/lock key: the app id, plus a stable digest of any overrides so two
    playlist entries of the same app with different config don't share a cache."""
    if not overrides:
        return app_id
    items = sorted((k, v) for k, v in overrides.items() if v not in (None, ""))
    return app_id if not items else app_id + "\x00" + repr(items)


class PluginRuntime:
    def __init__(self, config: Config, settings: PluginSettings, apps_dir: Path,
                 user_apps_dir: Path | None = None):
        self.config = config
        self.settings = settings
        self.apps_dir = Path(apps_dir)
        # User-uploaded apps live here (persistent /data volume), and take
        # precedence over a built-in of the same id.
        self.user_apps_dir = Path(user_apps_dir) if user_apps_dir else self.apps_dir / "_user"
        self._registry: dict[str, dict] = {}   # app_id -> manifest
        self._modules: dict[str, object] = {}   # app_id -> imported module
        self._channel: dict[str, list] = {}     # app_id -> pages
        self._triggers: dict[str, object] = {}  # app_id -> trigger fn
        self._caches: dict[str, dict] = {}       # app_id -> {pages, fetched_at}
        self._reads: dict[str, dict] = {}        # app_id -> {settings key it reads: default}
        self._wants_weather: dict[str, bool] = {}  # app_id -> fetch() accepts get_weather
        self._wants_location: dict[str, bool] = {}  # app_id -> fetch() accepts get_location
        self._wants_i18n: dict[str, bool] = {}     # app_id -> fetch() accepts i18n
        self._fetch_locks: dict[str, threading.Lock] = {}  # app_id -> serialize its fetches
        self._first_error: dict[str, str] = {}     # cache key -> its first fetch error

    # -- helpers injected into plugins -------------------------------------
    def get_rows(self) -> int:
        return int(self.config.grid["rows"])

    def get_cols(self) -> int:
        return int(self.config.grid["cols"])

    def format_lines(self, *lines, cols=None) -> str:
        cols = cols or self.get_cols()
        rows = self.get_rows()
        padded = list(lines) + [""] * (rows - len(lines))
        return "".join(l.center(cols)[:cols] for l in padded[:rows])

    # -- discovery / loading ----------------------------------------------
    def _scan(self) -> dict[str, Path]:
        """Map app_id -> its folder, scanning built-in then user dirs (user
        wins on an id collision)."""
        out: dict[str, Path] = {}
        for base in (self.apps_dir, self.user_apps_dir):
            if base and base.is_dir():
                for name in sorted(os.listdir(base)):
                    if name.startswith((".", "_")):
                        continue
                    if (base / name / "manifest.json").is_file():
                        out[name] = base / name
        return out

    def discover(self) -> list[str]:
        """All app ids present on disk (built-in + user-uploaded)."""
        return list(self._scan().keys())

    def _app_dir(self, app_id: str) -> Path | None:
        return self._scan().get(app_id)

    def is_builtin(self, app_id: str) -> bool:
        return self._builtin_in(app_id, self._scan())

    def _builtin_in(self, app_id: str, scan: dict[str, Path]) -> bool:
        """Builtin check against an already-computed scan (avoids re-scanning)."""
        p = scan.get(app_id)
        return p is not None and self.apps_dir == p.parent

    def load(self) -> None:
        """(Re)load all *installed* apps into the registry."""
        # The companion no longer defines a fixed flap character set, so nothing
        # is injected into __main__. A vendored app that still reads
        # ``__main__.FLAP_CHARS`` (e.g. countdown) falls back to its own default.
        self._registry.clear()
        self._modules.clear()
        self._channel.clear()
        self._triggers.clear()
        self._caches.clear()
        self._reads.clear()
        self._wants_weather.clear()
        self._wants_location.clear()
        self._wants_i18n.clear()
        self._fetch_locks.clear()
        self._first_error.clear()
        # Scan the app dirs once and reuse it (discovery + per-app load).
        scan = self._scan()
        # Let the settings store nest per-app keys by app id when it persists.
        self.settings.set_known_apps(list(scan.keys()))
        enabled = set(self.settings.installed_apps)
        for app_id, app_dir in scan.items():
            if app_id in enabled:
                self._load_one(app_id, app_dir)
        # Multi-value settings (search_chips) are stored as JSON arrays on disk.
        self.settings.set_list_keys(self._list_keys())

    def on_grid_changed(self) -> None:
        """The grid dimensions changed. Drop cached pages (they were centered/
        truncated for the old width) and re-render channel apps, whose pages are
        pre-formatted at load time."""
        self._caches.clear()
        scan = self._scan()
        for app_id in list(self._channel):
            app_dir = scan.get(app_id)
            if app_dir:
                self._load_channel(app_id, app_dir)

    def _list_keys(self) -> set[str]:
        """Resolved storage keys whose value is a comma-list (a multi-value
        search_chips), so the store can persist them as arrays."""
        keys: set[str] = set()
        for c in CATALOG:
            if c.get("type") == "search_chips" and c.get("maxItems") != 1 \
                    and not c.get("_composite"):
                keys.add(c["key"])
        for app_id, manifest in self._registry.items():
            for st in manifest.get("settings", []):
                if (st.get("type") == "search_chips" and st.get("maxItems") != 1
                        and st.get("key")):
                    keys.add(self._resolve_key(app_id, st["key"], st.get("global_key", False)))
        return keys

    def _load_one(self, app_id: str, app_dir: Path) -> None:
        try:
            manifest = json.loads((app_dir / "manifest.json").read_text("utf-8"))
        except Exception as e:
            log.error("plugin %s: bad manifest: %s", app_id, e)
            return
        manifest["id"] = app_id
        self._registry[app_id] = manifest
        kind = manifest.get("type")
        if kind == "channel":
            self._load_channel(app_id, app_dir)
        elif kind == "functional":
            self._load_functional(app_id, app_dir)
        log.info("plugin loaded: %s (%s)", app_id, kind)

    def _load_channel(self, app_id: str, app_dir: Path) -> None:
        data_path = app_dir / "data.json"
        if not data_path.is_file():
            return
        try:
            data = json.loads(data_path.read_text("utf-8"))
            pages = []
            for page in data.get("pages", []):
                if isinstance(page, str):
                    pages.append(page)
                elif isinstance(page, dict) and "lines" in page:
                    pages.append(self.format_lines(*page["lines"]))
            self._channel[app_id] = pages
        except Exception as e:
            log.error("plugin %s: data.json error: %s", app_id, e)

    def _load_functional(self, app_id: str, app_dir: Path) -> None:
        module_path = app_dir / "app.py"
        if not module_path.is_file():
            return
        try:
            spec = importlib.util.spec_from_file_location(f"plugin_{app_id}", str(module_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            log.error("plugin %s: import error: %s", app_id, e)
            return
        if hasattr(mod, "fetch") and callable(mod.fetch):
            self._modules[app_id] = mod
            self._wants_weather[app_id] = self._fetch_accepts(mod.fetch, "get_weather")
            self._wants_location[app_id] = self._fetch_accepts(mod.fetch, "get_location")
            self._wants_i18n[app_id] = self._fetch_accepts(mod.fetch, "i18n")
            self._fetch_locks[app_id] = threading.Lock()
        else:
            log.error("plugin %s: app.py has no fetch()", app_id)
        if hasattr(mod, "trigger") and callable(mod.trigger):
            self._triggers[app_id] = mod.trigger
        # Record which settings the app actually reads, so the UI can surface
        # settings it consumes but never declares in its manifest.
        try:
            self._reads[app_id] = self._scan_reads(module_path.read_text("utf-8"))
        except Exception:
            self._reads[app_id] = {}

    @staticmethod
    def _fetch_accepts(fn, name) -> bool:
        """True if an app's fetch() declares a parameter called ``name`` (how an
        app opts into an injected helper like ``get_weather`` or ``i18n``), or
        accepts arbitrary keywords. Classic 4-arg apps accept neither and are
        called unchanged."""
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False
        return name in params or any(p.kind == p.VAR_KEYWORD for p in params.values())

    @staticmethod
    def _scan_reads(src: str) -> dict:
        """Keys read via ``<settings>.get('k'[, default])`` / ``<settings>['k']`` in
        an app's source, mapped to the literal default when given (for type
        inference). The settings variable is the first parameter of fetch()/
        trigger() — detected by name, so apps that call it ``s`` are handled too.
        AST-based, so it's robust to formatting."""
        reads: dict = {}
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return reads
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name in ("fetch", "trigger") and node.args.args:
                names.add(node.args.args[0].arg)
        if not names:
            names = {"settings"}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in names and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                dflt = None
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    dflt = node.args[1].value
                reads.setdefault(node.args[0].value, dflt)
            elif (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in names
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                reads.setdefault(node.slice.value, None)
        return reads

    # -- settings assembly (faithful) -------------------------------------
    def _plugin_settings(self, app_id: str, manifest: dict, settings=None) -> dict:
        settings = settings or self.settings
        s = settings.all()
        # currency_symbol is owned by the display config; keep them in sync.
        s["currency_symbol"] = self.config.display.get("currency_symbol", "$")
        declared = set()
        for st in manifest.get("settings", []):
            key = st.get("key")
            if not key:
                continue
            declared.add(key)
            if key in GLOBAL_STORAGE_KEYS:
                continue  # a global — already present in `s`
            s[key] = settings.get(f"plugin_{app_id}_{key}", st.get("default", ""))
        # Settings the app READS but never declares are per-app too (not shared
        # bare keys), so each app keeps its own value.
        for key, dflt in self._reads.get(app_id, {}).items():
            if key in GLOBAL_STORAGE_KEYS or key in declared:
                continue
            s[key] = settings.get(f"plugin_{app_id}_{key}",
                                  dflt if dflt is not None else s.get(key, ""))
        # Per-app Location override: a place chip ("lat,lon|name") auto-injected for
        # location-using apps, overriding the global location for this app only. We
        # write it into the keys the helpers already read (location_lat/lon/name).
        if "location" not in declared:                   # weather owns its own 'location' field
            loc_ovr = settings.get(f"plugin_{app_id}_location")
            if loc_ovr:
                coords, _, nm = str(loc_ovr).partition("|")
                lat, _, lon = coords.partition(",")
                if lat.strip() and lon.strip():
                    s["location_lat"], s["location_lon"] = lat.strip(), lon.strip()
                    if nm.strip():
                        s["location_name"] = nm.strip()
                    s["location"] = loc_ovr
        return s

    def _refresh_secs(self, app_id: str, manifest: dict, settings=None) -> int:
        """How often fetch() is re-run (its result is cached in between): the
        manifest's refresh_interval, overridden by a per-app polling_rate (seconds)
        or the friendlier refresh_minutes, if the app declares one."""
        refresh = manifest.get("refresh_interval", 300)
        poll = self._perapp_value(app_id, "polling_rate", settings)
        if poll not in (None, ""):
            try:
                refresh = max(10, int(float(poll)))
            except (ValueError, TypeError):
                pass
        mins = self._perapp_value(app_id, "refresh_minutes", settings)
        if mins not in (None, ""):
            try:
                refresh = max(10, int(float(mins) * 60))
            except (ValueError, TypeError):
                pass
        return refresh

    # A C extension built with single-phase init — numpy is the one that matters here —
    # can only be initialised ONCE per process. If its first import dies (numpy 2.4+ on a
    # CPU without the x86-64-v2 baseline, say — a Proxmox `kvm64` VM), the .so is already
    # loaded but the module is gone from sys.modules, so every later import raises this
    # instead. It is permanent until restart, and it names nothing useful.
    _POISONED_IMPORT = "more than once per process"

    def _fetch_error_message(self, ckey: str, app_id: str, e: Exception) -> str:
        """The message to log and show for a failed fetch.

        Keeps the FIRST error a plugin raised, because the one you get afterwards can be
        a useless echo of it (see _POISONED_IMPORT): the real cause was in the first line
        and the repeats then bury it, once every refresh, forever.
        """
        msg = str(e)
        first = self._first_error.get(ckey)
        if first and self._POISONED_IMPORT in msg:
            # The echo. Report what actually broke, and stop shouting about it.
            log.debug("plugin %s fetch error (unchanged): %s", app_id, first)
            return first
        if first != msg:
            self._first_error[ckey] = msg
            log.warning("plugin %s fetch error: %s", app_id, msg)
        else:
            log.debug("plugin %s fetch error (unchanged): %s", app_id, msg)
        return msg

    # -- pages (faithful get_plugin_pages) --------------------------------
    def get_pages(self, app_id: str, overrides: dict | None = None) -> list[str]:
        cols = self.get_cols()
        manifest = self._registry.get(app_id)
        if not manifest:
            return [self.format_lines("PLUGIN ERROR", app_id.upper()[:cols], "NOT FOUND")]
        app_type = manifest.get("type")
        # Per-entry overrides (playlist) render this app with its own config without
        # disturbing the saved settings; the cache/lock key includes them so two
        # entries of the same app don't collide.
        settings = _SettingsOverlay(self.settings, overrides) if overrides else self.settings
        ckey = _cache_key(app_id, overrides)
        refresh = self._refresh_secs(app_id, manifest, settings)

        if app_type == "channel":
            pages = self._channel.get(app_id, [])
            return pages or [self.format_lines(manifest.get("name", app_id).upper()[:cols], "NO DATA", "")]

        if app_type == "functional":
            mod = self._modules.get(app_id)
            if not mod:
                return [self.format_lines("PLUGIN ERROR", app_id.upper()[:cols], "NOT LOADED")]
            # Serialize fetches for this app: get_pages runs in executor threads
            # (app loop + a preview can hit the same app at once), so this
            # coalesces duplicate fetches and protects non-reentrant app state.
            lock = self._fetch_locks.get(ckey)
            if lock is None:
                lock = self._fetch_locks[ckey] = threading.Lock()
            with lock:
                now = time.time()
                cached = self._caches.get(ckey)
                if cached and (now - cached["fetched_at"]) < refresh:
                    return cached["pages"]
                try:
                    ps = self._plugin_settings(app_id, manifest, settings)
                    kwargs = {}
                    if self._wants_weather.get(app_id):
                        kwargs["get_weather"] = lambda s=None: weather.fetch_current(s if s is not None else ps)
                    if self._wants_location.get(app_id):
                        kwargs["get_location"] = lambda: location.resolve(ps)
                    if self._wants_i18n.get(app_id):
                        # A per-app Language override (plugin_<id>_language) wins over
                        # the global Language; blank/unset = follow global.
                        lang = self._perapp_value(app_id, "language", settings) or settings.get("language", "en-US")
                        kwargs["i18n"] = i18n.Localizer(lang)
                    pages = mod.fetch(ps, self.format_lines, self.get_rows, self.get_cols, **kwargs)
                    if not isinstance(pages, list):
                        pages = [str(pages)]
                    self._caches[ckey] = {"pages": pages, "fetched_at": now}
                    log.debug("fetched %s: %d page(s) (refresh %ss)", ckey, len(pages), refresh)
                    return pages
                except Exception as e:
                    msg = self._fetch_error_message(ckey, app_id, e)
                    cp = self._caches.get(ckey, {}).get("pages")
                    if cp:
                        return cp
                    err = msg.lower()
                    if "timeout" in err or "connection" in err or "network" in err:
                        return [self.format_lines(manifest.get("name", app_id).upper()[:cols], "OFFLINE", "")]
                    return [self.format_lines("APP ERROR", app_id.upper()[:cols], msg[:cols])]

        return [self.format_lines("PLUGIN ERROR", "UNKNOWN TYPE", "")]

    # -- triggers ----------------------------------------------------------
    def has_trigger(self, app_id: str) -> bool:
        return app_id in self._triggers

    def trigger_apps(self) -> list[dict]:
        """Apps that expose a trigger() — for the Triggers UI."""
        out = []
        for app_id in self._triggers:
            m = self._registry.get(app_id, {})
            out.append({
                "id": app_id,
                "name": m.get("name", app_id),
                "icon": m.get("icon", "🧩"),
                "trigger_interval": m.get("trigger_interval", 60),
                "trigger_display_seconds": m.get("trigger_display_seconds", 30),
                "trigger_cooldown": m.get("trigger_cooldown", 300),
                "trigger_conditions": m.get("trigger_conditions", []),
            })
        out.sort(key=lambda a: a["name"].lower())
        return out

    def call_trigger(self, app_id: str, conditions: dict) -> bool:
        """Run an app's trigger(settings, conditions). Blocking — use executor."""
        fn = self._triggers.get(app_id)
        manifest = self._registry.get(app_id)
        if not fn or not manifest:
            return False
        ps = self._plugin_settings(app_id, manifest)
        return bool(fn(ps, conditions or {}))

    # -- run metadata ------------------------------------------------------
    def manifest(self, app_id: str) -> dict | None:
        return self._registry.get(app_id)

    def is_anim(self, app_id: str) -> bool:
        m = self._registry.get(app_id, {})
        return app_id.startswith("anim_") or bool(m.get("animation"))

    def _setting_default(self, app_id: str, key: str):
        """The manifest's declared default for a setting (what the app dialog
        shows). None if the app doesn't declare that setting."""
        for st in self._registry.get(app_id, {}).get("settings", []):
            if st.get("key") == key:
                d = st.get("default")
                return d if d not in (None, "") else None
        return None

    _LOCATION_KEYS = {"location_lat", "location_lon", "location_name", "zip_code", "location"}

    def _uses_location(self, app_id: str) -> bool:
        """True if the app is tied to a place — via the weather/location helpers or by
        reading a location key directly — so it should offer a per-app Location override."""
        return bool(self._wants_weather.get(app_id) or self._wants_location.get(app_id)
                    or (set(self._reads.get(app_id, {})) & self._LOCATION_KEYS))

    def _perapp_value(self, app_id: str, key: str, settings=None):
        """Effective value of a runtime-consumed per-app setting: the saved value,
        else the manifest's declared default. So a setting's default takes effect
        immediately — the user shouldn't have to save it first. None if neither."""
        saved = (settings or self.settings).get(f"plugin_{app_id}_{key}")
        if saved not in (None, ""):
            return saved
        return self._setting_default(app_id, key)

    def loop_delay(self, app_id: str, settings=None) -> float:
        m = self._registry.get(app_id, {})
        settings = settings or self.settings
        if self.is_anim(app_id):
            # anim speed is a per-app setting (each animation keeps its own).
            v = self._perapp_value(app_id, "anim_speed", settings)
            try:
                return max(0.1, float(v)) if v is not None else 0.4
            except (ValueError, TypeError):
                return 0.4
        # The declared setting default (what the dialog shows) is used before the
        # manifest's top-level loop_delay or the global default — so it applies
        # even when the user hasn't explicitly saved the app's settings.
        v = self._perapp_value(app_id, "loop_delay", settings)
        if v is None:
            v = m.get("loop_delay", settings.get("global_loop_delay", 8))
        try:
            return float(v)
        except (ValueError, TypeError):
            return float(settings.get("global_loop_delay", 8) or 8)

    def page_timing(self, app_id: str, overrides: dict | None = None) -> dict:
        """Style/speed/delay for the play loop (mirrors playlist_loop). Accepts
        per-entry overrides so a playlist entry's own loop_delay/style is honored."""
        settings = _SettingsOverlay(self.settings, overrides) if overrides else self.settings
        m = self._registry.get(app_id, {})
        disp = self.config.display
        speed = int(disp.get("transition_speed", 15))
        if self.is_anim(app_id):
            # anim style is a per-app setting (each animation keeps its own).
            style = settings.get(f"plugin_{app_id}_anim_style", "ltr") or "ltr"
            return {"is_anim": True, "style": style, "speed": speed,
                    "loop_delay": self.loop_delay(app_id, settings), "skip_rotation": True}
        style = settings.get(f"plugin_{app_id}_transition_style") or \
            disp.get("transition_style", "ltr")
        return {"is_anim": False, "style": style, "speed": speed,
                "loop_delay": self.loop_delay(app_id, settings),
                "skip_rotation": bool(m.get("skip_rotation_wait"))}

    # -- listings ----------------------------------------------------------
    def _entry(self, app_id: str, manifest: dict, installed: bool, builtin: bool) -> dict:
        return {
            "id": app_id,
            "name": manifest.get("name", app_id),
            "icon": manifest.get("icon", "🧩"),
            "description": manifest.get("description", ""),
            "category": manifest.get("category", "other"),
            "type": manifest.get("type", "functional"),
            "version": str(manifest.get("version", "")),
            "installed": installed,
            "loaded": app_id in self._registry,
            "animation": self.is_anim(app_id) if app_id in self._registry else app_id.startswith("anim_"),
            "has_settings": bool(manifest.get("settings")),
            "i18n": bool(manifest.get("i18n")),           # adapts to the global Language
            "min_rows": manifest.get("min_rows"),
            "min_cols": manifest.get("min_cols"),
            "min_modules": manifest.get("min_modules"),   # total-module minimum (any shape)
            "builtin": builtin,
        }

    def app_list(self) -> list[dict]:
        """Installed (loaded) apps, sorted by name — powers the Apps grid."""
        scan = self._scan()   # scan once; don't re-scan per app for builtin-ness
        out = [self._entry(i, m, True, self._builtin_in(i, scan))
               for i, m in self._registry.items()]
        out.sort(key=lambda a: a["name"].lower())
        return out

    def available_list(self) -> list[dict]:
        """Every app on disk, with an ``installed`` flag (for the library)."""
        enabled = set(self.settings.installed_apps)
        out = []
        for app_id, app_dir in self._scan().items():
            manifest = self._registry.get(app_id)
            if manifest is None:
                try:
                    manifest = json.loads((app_dir / "manifest.json").read_text("utf-8"))
                except Exception:
                    continue
            out.append(self._entry(app_id, manifest, app_id in enabled,
                                   app_dir.parent == self.apps_dir))
        out.sort(key=lambda a: a["name"].lower())
        return out

    # -- per-app settings schema + values ---------------------------------
    def _resolve_key(self, app_id: str, raw_key: str, global_key: bool = False) -> str:
        # The catalog is the single source of truth for what is global; every
        # other setting is per-app. A manifest's ``global_key`` flag is ignored.
        return raw_key if raw_key in GLOBAL_STORAGE_KEYS else f"plugin_{app_id}_{raw_key}"

    def _field(self, app_id: str, setting: dict, resolved: dict) -> dict:
        raw = setting["key"]
        key = resolved[raw]
        ftype = setting.get("type", "text")

        def map_key(rk):
            return resolved.get(rk) or self._resolve_key(app_id, rk, False)

        field = {"key": key, "label": setting.get("label", raw), "type": ftype}
        if "options" in setting:
            field["options"] = setting["options"]
        for pk in _PASSTHROUGH:
            if pk in setting:
                field[pk] = setting[pk]
        if "visible_when" in setting:
            field["visible_when"] = {map_key(k): v for k, v in setting["visible_when"].items()}
        if "inline_toggle" in setting:
            it = dict(setting["inline_toggle"])
            if it.get("key"):
                it["key"] = self._resolve_key(app_id, it["key"], it.get("global_key", False))
            field["inline_toggle"] = it
        return field

    def settings_schema(self, app_id: str) -> dict:
        manifest = self._registry.get(app_id)
        if not manifest:
            raise KeyError(app_id)
        raw_settings = [s for s in manifest.get("settings", []) if s.get("key")]
        resolved = {
            s["key"]: self._resolve_key(app_id, s["key"], s.get("global_key", False))
            for s in raw_settings
        }
        declared_keys = {s["key"] for s in raw_settings}
        # An inline_toggle declares its own key too (rendered beside its parent),
        # so it must count as declared or it gets double-surfaced by inference.
        for s in raw_settings:
            it = s.get("inline_toggle")
            if it and it.get("key"):
                declared_keys.add(it["key"])

        # App-specific settings only. Catalog/global keys live in the Global
        # editor, so they're excluded here (a hint points to them below).
        fields = []

        # Any app that adapts to language gets a per-app Language override, stored
        # under its own plugin_<id>_language key so it never touches the global one.
        # Blank = follow the global Language.
        if manifest.get("i18n") and not any(s["key"] == "language" for s in raw_settings):
            lang_options = [{"value": "", "label": "Follow global"}]
            lang_options += [dict(o) for o in CATALOG_BY_KEY["language"]["options"]]
            fields.append({
                "key": f"plugin_{app_id}_language",
                "label": "Language",
                "type": "select",
                "options": lang_options,
                "default": "",
                "note": "Override the global Language for this app only.",
            })

        # Any location-tied app gets a per-app Location override (a place search),
        # blank = follow the global Location. Weather owns its own 'location' field.
        if self._uses_location(app_id) and not any(s["key"] == "location" for s in raw_settings):
            fields.append({
                "key": f"plugin_{app_id}_location",
                "label": "Location",
                "type": "search_chips",
                "searchUrl": "/location_search",
                "resultKey": "results",
                "maxItems": 1,
                "default": "",
                "note": "Override the global Location for this app only (place search).",
            })

        for s in raw_settings:
            if s["key"] in GLOBAL_STORAGE_KEYS:
                continue
            f = self._field(app_id, s, resolved)
            f["label"] = f["label"].replace(" (override global)", "")
            fields.append(f)

        # Settings the app READS but never declares are per-app too — surface them
        # (inferred from the default they're read with) so nothing stays hidden.
        for key, default in self._reads.get(app_id, {}).items():
            if key in declared_keys or key in GLOBAL_STORAGE_KEYS or key == "currency_symbol":
                continue
            fields.append(self._infer_field(self._resolve_key(app_id, key), key, default))

        # Point to the reusable globals this app uses (edited under Global settings).
        used_global = set()
        for k in declared_keys | set(self._reads.get(app_id, {})):
            if k in CATALOG_KEYS:
                used_global.add(k)
            for c in CATALOG:
                if k in c.get("_composite", []):
                    used_global.add(c["key"])
        if self._wants_weather.get(app_id):
            used_global |= set(weather.GLOBAL_KEYS)   # used via the shared weather helper
        if self._wants_location.get(app_id):
            used_global |= set(location.GLOBAL_KEYS)  # used via the shared location helper
        if used_global:
            names = ", ".join(CATALOG_BY_KEY[k]["label"]
                              for k in sorted(used_global, key=lambda x: CATALOG_BY_KEY[x]["label"]))
            fields.append({"key": f"_globals_note_{app_id}", "type": "notice",
                           "label": "Also uses global settings: " + names
                                    + " — set these under Global settings."})

        values = {}
        for s in raw_settings:
            rk = resolved[s["key"]]
            values[rk] = self.settings.get(rk, s.get("default", ""))
            it = s.get("inline_toggle")
            if it and it.get("key"):
                ik = self._resolve_key(app_id, it["key"])
                values[ik] = self.settings.get(ik, it.get("default", ""))
        for f in fields:
            values.setdefault(f["key"], self.settings.get(f["key"], f.get("default", "")))
        return {
            "id": app_id,
            "name": manifest.get("name", app_id),
            "icon": manifest.get("icon", "🧩"),
            "fields": fields,
            "values": values,
        }

    def save_settings(self, app_id: str, values: dict) -> None:
        """Store this app's per-app settings. The app dialog only holds
        ``plugin_<id>_*`` keys now — globals live in the Global editor — so only
        those are accepted."""
        if app_id not in self._registry:
            raise KeyError(app_id)
        clean = {k: v for k, v in values.items() if k.startswith(f"plugin_{app_id}_")}
        if clean:
            self.settings.update(clean)
            self._caches.pop(app_id, None)  # settings changed -> drop cache

    # -- global (shared) settings editor ----------------------------------
    def _global_usage(self, keys) -> dict[str, set[str]]:
        """App names that USE each given global key — whether they declare it OR
        just read it in their code (settings.get). For the Global editor's
        'Used by' note."""
        keys = set(keys)
        usage: dict[str, set[str]] = collections.defaultdict(set)
        for app_id, manifest in self._registry.items():
            name = manifest.get("name", app_id)
            for st in manifest.get("settings", []):
                if st.get("key") in keys:
                    usage[st["key"]].add(name)
            for k in self._reads.get(app_id, {}):
                if k in keys:
                    usage[k].add(name)
            if self._wants_weather.get(app_id):   # depends on the weather globals via get_weather
                for wk in weather.GLOBAL_KEYS:
                    if wk in keys:
                        usage[wk].add(name)
        return usage

    def _infer_field(self, key: str, raw_key: str, default) -> dict:
        """A best-effort field for a per-app setting the manifest never declared,
        inferred from the default value the code reads it with. ``key`` is the
        resolved (per-app) storage key; ``raw_key`` names it."""
        f = {"key": key, "label": raw_key.replace("_", " ").title(),
             "note": "Used by this app (auto-detected — not in the app's manifest)"}
        if isinstance(default, bool):
            f.update(type="toggle", default=("true" if default else "false"),
                     options=[{"value": "true", "label": "On"},
                              {"value": "false", "label": "Off"}])
        elif isinstance(default, (int, float)):
            f.update(type="number", default=default)
        elif isinstance(default, str) and default.lower() in ("yes", "no"):
            f.update(type="toggle", default=default.lower(),
                     options=[{"value": "yes", "label": "Yes"},
                              {"value": "no", "label": "No"}])
        else:
            f.update(type="text", default=(default if isinstance(default, str) else ""))
        return f

    def global_settings_schema(self) -> dict:
        """The built-in catalog of well-known reusable global settings — the ONLY
        settings shown in the Global editor. They render from the catalog (so a
        key looks right even if the declaring app isn't installed); a 'Used by'
        note lists the installed apps that declare or read each one."""
        comp_keys = {k for c in CATALOG for k in c.get("_composite", [])}
        usage = self._global_usage(CATALOG_KEYS | comp_keys)
        resolved = {c["key"]: c["key"] for c in CATALOG}
        fields, values = [], {}
        for c in CATALOG:
            f = self._field("", c, resolved)
            used_apps = set()
            for k in [c["key"], *c.get("_composite", [])]:
                used_apps |= usage.get(k, set())
            base = c.get("note", "")
            used = ("Used by " + ", ".join(sorted(used_apps))) if used_apps else ""
            f["note"] = "  ·  ".join(x for x in (base, used) if x)
            fields.append(f)
            if c.get("_composite"):
                values[c["key"]] = self._composite_value(c["_composite"])
            else:
                values[c["key"]] = self.settings.get(c["key"], c.get("default", ""))
        # Language is a headline feature — pin it to the very top (stable sort keeps
        # the rest of the catalog order).
        fields.sort(key=lambda f: 0 if f.get("key") == "language" else 1)
        return {"fields": fields, "values": values}

    def _composite_value(self, comp: list) -> str:
        """Rebuild a location search chip value (``lat,lon|name``) from its stored
        component keys, or '' when no coordinates are set."""
        lat = self.settings.get(comp[0], "")
        lon = self.settings.get(comp[1], "")
        name = self.settings.get(comp[2], "") if len(comp) > 2 else ""
        return f"{lat},{lon}|{name}" if (lat and lon) else ""

    @staticmethod
    def _parse_composite(comp: list, value) -> dict:
        """Split a ``lat,lon|name`` chip value into its component keys (empty
        value clears them)."""
        coords, _, name = str(value or "").partition("|")
        lat, _, lon = coords.partition(",")
        out = {comp[0]: lat.strip(), comp[1]: lon.strip()}
        if len(comp) > 2:
            out[comp[2]] = name.strip()
        return out

    def save_global_settings(self, values: dict) -> None:
        """Persist edited global settings. Only catalog keys are accepted (a
        composite control writes its component keys); changing a global can affect
        many apps, so all caches are dropped."""
        clean: dict = {}
        for k, v in values.items():
            if k not in CATALOG_KEYS:
                continue
            comp = CATALOG_BY_KEY[k].get("_composite")
            if comp:
                clean.update(self._parse_composite(comp, v))
            else:
                clean[k] = v
        if clean:
            self.settings.update(clean)
            self._caches.clear()

    # -- install / uninstall ----------------------------------------------
    def set_installed(self, app_id: str, installed: bool) -> None:
        current = set(self.settings.installed_apps)
        if installed:
            current.add(app_id)
        else:
            current.discard(app_id)
        # Preserve a stable-ish order: keep discovered order.
        ordered = [a for a in self.discover() if a in current]
        self.settings.set_installed(ordered)
        self.load()

    # -- upload / delete user apps ----------------------------------------
    def install_zip(self, data: bytes, *, enable: bool = True) -> dict:
        """Validate + install an uploaded app .zip into the user apps dir.

        The zip must contain exactly one ``manifest.json`` (the app folder). The
        app id is the containing folder's name (or the manifest ``id``). Returns
        {id, name, type}; raises ValueError with a human message on any problem.

        The upload is vetted before install: the manifest is structurally checked,
        a functional app's ``app.py`` is statically audited for disallowed /
        malicious operations (rejected with reasons if unsafe), its fetch()
        signature is verified, and every setting the code reads that isn't a global
        is declared as an app-level setting in the manifest (rewritten if needed).
        Only after the audit passes is the app imported to surface import errors.

        SECURITY: the static audit is defense-in-depth, not a sandbox; a vetted app
        still runs in-process. Only upload apps you trust.
        """
        import io
        import re
        import shutil
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    for n in z.namelist():
                        if n.startswith("/") or ".." in Path(n).parts:
                            raise ValueError("unsafe path in zip")
                    z.extractall(tdp)
            except zipfile.BadZipFile:
                raise ValueError("not a valid .zip file")

            manifests = [m for m in tdp.rglob("manifest.json") if "__MACOSX" not in m.parts]
            if len(manifests) != 1:
                raise ValueError("the zip must contain exactly one manifest.json (the app folder)")
            mpath = manifests[0]
            root = mpath.parent
            try:
                manifest = json.loads(mpath.read_text("utf-8"))
            except Exception as e:
                raise ValueError(f"invalid manifest.json: {e}")

            raw_id = root.name if root != tdp else (manifest.get("id") or manifest.get("name") or "app")
            app_id = re.sub(r"[^A-Za-z0-9_-]", "", str(raw_id)) or "app"

            self._validate_manifest(manifest)
            kind = manifest.get("type")
            if kind == "functional":
                app_py = root / "app.py"
                if not app_py.is_file():
                    raise ValueError("functional app is missing app.py")
                src = app_py.read_text("utf-8", errors="replace")
                # 1) Static safety audit — BEFORE the module is ever executed.
                violations = appaudit.audit_python(src)
                if violations:
                    raise ValueError(
                        "app rejected — app.py contains operations that are not allowed:\n  - "
                        + "\n  - ".join(violations))
                # 2) fetch() must exist with the right arity (checked statically).
                fn = appaudit.find_fetch(src)
                if fn is None:
                    raise ValueError(
                        "app.py must define a fetch(settings, format_lines, get_rows, get_cols) function")
                if len(fn.args.args) < 4:
                    raise ValueError(
                        "fetch() must accept (settings, format_lines, get_rows, get_cols)")
                # 3) Scope settings: declare every non-global setting the code reads
                #    as an app-level setting, rewriting the manifest if needed.
                if self._scope_manifest_settings(manifest, src):
                    mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), "utf-8")
                # 4) Only now import it — the audit has cleared it — to surface
                #    import/syntax errors (missing deps, etc.).
                self._validate_fetch(app_py)
            elif kind == "channel":
                self._validate_channel(root)

            self.user_apps_dir.mkdir(parents=True, exist_ok=True)
            dest = self.user_apps_dir / app_id
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(root, dest)

        if enable:
            self.set_installed(app_id, True)   # also reloads
        else:
            self.load()
        return {"id": app_id, "name": manifest.get("name"), "type": kind}

    @staticmethod
    def _validate_manifest(manifest) -> None:
        """Structural manifest checks with human-readable errors."""
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json must be a JSON object")
        name = manifest.get("name")
        if not name or not isinstance(name, str):
            raise ValueError("manifest.json is missing a 'name'")
        if manifest.get("type") not in ("functional", "channel"):
            raise ValueError("manifest 'type' must be 'functional' or 'channel'")
        settings = manifest.get("settings")
        if settings is not None:
            if not isinstance(settings, list):
                raise ValueError("manifest 'settings' must be a list")
            for i, st in enumerate(settings):
                if not isinstance(st, dict) or not st.get("key"):
                    raise ValueError(f"manifest setting #{i + 1} must be an object with a 'key'")

    @staticmethod
    def _validate_channel(root: Path) -> None:
        dp = root / "data.json"
        if not dp.is_file():
            raise ValueError("channel app is missing data.json")
        try:
            data = json.loads(dp.read_text("utf-8"))
        except Exception as e:
            raise ValueError(f"invalid data.json: {e}")
        if not isinstance(data, dict) or not data.get("pages"):
            raise ValueError("channel app's data.json must have a non-empty 'pages' list")

    def _scope_manifest_settings(self, manifest: dict, src: str) -> bool:
        """Ensure every non-global setting the app reads is declared as an app-level
        setting in the manifest. Adds inferred fields for settings the code reads
        but never declares, and drops a misleading ``global_key`` flag from any
        non-catalog (i.e. genuinely app-level) setting. Returns True if changed."""
        settings = manifest.setdefault("settings", [])
        if not isinstance(settings, list):
            return False
        declared, changed = set(), False
        for st in settings:
            if not isinstance(st, dict):
                continue
            k = st.get("key")
            if k:
                declared.add(k)
            it = st.get("inline_toggle")
            if isinstance(it, dict) and it.get("key"):
                declared.add(it["key"])
            # A non-catalog setting is app-level regardless of any global_key flag;
            # drop the misleading flag so the manifest is honest about its scope.
            if k and k not in CATALOG_KEYS and st.pop("global_key", None) is not None:
                changed = True
        for key, dflt in self._scan_reads(src).items():
            if key in declared or key in GLOBAL_STORAGE_KEYS or key == "currency_symbol":
                continue
            field = self._infer_field(key, key, dflt)   # manifest uses the raw key
            field["note"] = "Auto-declared on upload — the app reads this setting."
            settings.append(field)
            declared.add(key)
            changed = True
        return changed

    @staticmethod
    def _validate_fetch(app_py: Path) -> None:
        spec = importlib.util.spec_from_file_location(f"_upload_check_{app_py.parent.name}", str(app_py))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            raise ValueError(f"app.py failed to import: {e}")
        if not (hasattr(mod, "fetch") and callable(mod.fetch)):
            raise ValueError("app.py has no fetch() function")

    def delete_app(self, app_id: str) -> None:
        """Remove a user-uploaded app entirely (built-ins can't be deleted)."""
        import shutil

        app_dir = self._app_dir(app_id)
        if app_dir is None:
            raise KeyError(app_id)
        if self.is_builtin(app_id):
            raise ValueError("built-in apps cannot be deleted")
        self.settings.set_installed([a for a in self.settings.installed_apps if a != app_id])
        shutil.rmtree(app_dir, ignore_errors=True)
        self.load()
