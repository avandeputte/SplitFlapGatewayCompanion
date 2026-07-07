"""
plugins.py — the plugin runtime (faithful port of splitflap-os).

Discovers and loads apps from ``apps/<id>/`` (functional ``app.py`` or channel
``data.json``), assembles the per-app settings dict exactly as splitflap-os does,
and produces display pages with the same caching/paging semantics. Keeping this
behaviour-identical is what makes any splitflap-os app drop in unchanged — see
COMPATIBILITY.md.

``fetch()`` may do blocking network I/O, so callers run ``get_pages()`` in a
thread executor (see engine.py).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path

from . import renderer
from .config import Config
from .plugin_settings import PluginSettings

log = logging.getLogger("companion.plugins")

# Passed through from a manifest setting to the frontend field, verbatim.
_PASSTHROUGH = (
    "size", "ph", "min", "max", "step", "stepper", "searchUrl", "resultKey",
    "maxItems", "compute", "watches", "variant", "title", "text", "items",
    "icon", "linkText", "linkHref", "default",
)


class PluginRuntime:
    def __init__(self, config: Config, settings: PluginSettings, apps_dir: Path):
        self.config = config
        self.settings = settings
        self.apps_dir = Path(apps_dir)
        self._registry: dict[str, dict] = {}   # app_id -> manifest
        self._modules: dict[str, object] = {}   # app_id -> imported module
        self._channel: dict[str, list] = {}     # app_id -> pages
        self._triggers: dict[str, object] = {}  # app_id -> trigger fn
        self._caches: dict[str, dict] = {}       # app_id -> {pages, fetched_at}

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
    def discover(self) -> list[str]:
        """All app ids present on disk (folders with a manifest.json)."""
        if not self.apps_dir.is_dir():
            return []
        out = []
        for name in sorted(os.listdir(self.apps_dir)):
            if (self.apps_dir / name / "manifest.json").is_file():
                out.append(name)
        return out

    def load(self) -> None:
        """(Re)load all *installed* apps into the registry."""
        # Some apps (e.g. countdown) read FLAP_CHARS off __main__; expose it so
        # they behave identically to running under splitflap-os.
        try:
            setattr(sys.modules["__main__"], "FLAP_CHARS", renderer.FLAP_CHARS)
        except Exception:
            pass
        self._registry.clear()
        self._modules.clear()
        self._channel.clear()
        self._triggers.clear()
        self._caches.clear()
        enabled = set(self.settings.installed_apps)
        for app_id in self.discover():
            if app_id in enabled:
                self._load_one(app_id)

    def _load_one(self, app_id: str) -> None:
        app_dir = self.apps_dir / app_id
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
        else:
            log.error("plugin %s: app.py has no fetch()", app_id)
        if hasattr(mod, "trigger") and callable(mod.trigger):
            self._triggers[app_id] = mod.trigger

    # -- settings assembly (faithful) -------------------------------------
    def _plugin_settings(self, app_id: str, manifest: dict) -> dict:
        s = self.settings.all()
        # currency_symbol is owned by the display config; keep them in sync.
        s["currency_symbol"] = self.config.display.get("currency_symbol", "$")
        for st in manifest.get("settings", []):
            key = st.get("key")
            if not key or st.get("global_key"):
                continue  # global keys already present in `s`
            s[key] = self.settings.get(f"plugin_{app_id}_{key}", st.get("default", ""))
        return s

    # -- pages (faithful get_plugin_pages) --------------------------------
    def get_pages(self, app_id: str) -> list[str]:
        cols = self.get_cols()
        manifest = self._registry.get(app_id)
        if not manifest:
            return [self.format_lines("PLUGIN ERROR", app_id.upper()[:cols], "NOT FOUND")]
        app_type = manifest.get("type")
        refresh = manifest.get("refresh_interval", 300)
        poll = self.settings.get(f"plugin_{app_id}_polling_rate")
        if poll:
            try:
                refresh = max(10, int(float(poll)))
            except (ValueError, TypeError):
                pass

        if app_type == "channel":
            pages = self._channel.get(app_id, [])
            return pages or [self.format_lines(manifest.get("name", app_id).upper()[:cols], "NO DATA", "")]

        if app_type == "functional":
            mod = self._modules.get(app_id)
            if not mod:
                return [self.format_lines("PLUGIN ERROR", app_id.upper()[:cols], "NOT LOADED")]
            now = time.time()
            cached = self._caches.get(app_id)
            if cached and (now - cached["fetched_at"]) < refresh:
                return cached["pages"]
            try:
                ps = self._plugin_settings(app_id, manifest)
                pages = mod.fetch(ps, self.format_lines, self.get_rows, self.get_cols)
                if not isinstance(pages, list):
                    pages = [str(pages)]
                self._caches[app_id] = {"pages": pages, "fetched_at": now}
                return pages
            except Exception as e:
                log.warning("plugin %s fetch error: %s", app_id, e)
                cp = self._caches.get(app_id, {}).get("pages")
                if cp:
                    return cp
                err = str(e).lower()
                if "timeout" in err or "connection" in err or "network" in err:
                    return [self.format_lines(manifest.get("name", app_id).upper()[:cols], "OFFLINE", "")]
                return [self.format_lines("APP ERROR", app_id.upper()[:cols], str(e)[:cols])]

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

    def loop_delay(self, app_id: str) -> float:
        m = self._registry.get(app_id, {})
        if self.is_anim(app_id):
            return max(0.1, float(self.settings.get("anim_speed", "0.4") or 0.4))
        saved = self.settings.get(f"plugin_{app_id}_loop_delay", "")
        default = float(m.get("loop_delay", self.settings.get("global_loop_delay", 5)))
        try:
            return float(saved) if saved else default
        except (ValueError, TypeError):
            return default

    def page_timing(self, app_id: str) -> dict:
        """Style/speed/delay for the play loop (mirrors playlist_loop)."""
        m = self._registry.get(app_id, {})
        disp = self.config.display
        speed = int(disp.get("transition_speed", 15))
        if self.is_anim(app_id):
            style = self.settings.get("anim_style", "ltr") or "ltr"
            return {"is_anim": True, "style": style, "speed": speed,
                    "loop_delay": self.loop_delay(app_id), "skip_rotation": True}
        style = self.settings.get(f"plugin_{app_id}_transition_style") or \
            disp.get("transition_style", "ltr")
        return {"is_anim": False, "style": style, "speed": speed,
                "loop_delay": self.loop_delay(app_id),
                "skip_rotation": bool(m.get("skip_rotation_wait"))}

    # -- listings ----------------------------------------------------------
    def _entry(self, app_id: str, manifest: dict, installed: bool) -> dict:
        return {
            "id": app_id,
            "name": manifest.get("name", app_id),
            "icon": manifest.get("icon", "🧩"),
            "description": manifest.get("description", ""),
            "category": manifest.get("category", "other"),
            "type": manifest.get("type", "functional"),
            "installed": installed,
            "loaded": app_id in self._registry,
            "animation": self.is_anim(app_id) if app_id in self._registry else app_id.startswith("anim_"),
            "has_settings": bool(manifest.get("settings")),
            "min_rows": manifest.get("min_rows"),
            "min_cols": manifest.get("min_cols"),
        }

    def app_list(self) -> list[dict]:
        """Installed (loaded) apps, sorted by name — powers the Apps grid."""
        out = [self._entry(i, m, True) for i, m in self._registry.items()]
        out.sort(key=lambda a: a["name"].lower())
        return out

    def available_list(self) -> list[dict]:
        """Every app on disk, with an ``installed`` flag (for the library)."""
        enabled = set(self.settings.installed_apps)
        out = []
        for app_id in self.discover():
            manifest = self._registry.get(app_id)
            if manifest is None:
                try:
                    manifest = json.loads((self.apps_dir / app_id / "manifest.json").read_text("utf-8"))
                except Exception:
                    continue
            out.append(self._entry(app_id, manifest, app_id in enabled))
        out.sort(key=lambda a: a["name"].lower())
        return out

    # -- per-app settings schema + values ---------------------------------
    def _resolve_key(self, app_id: str, raw_key: str, global_key: bool) -> str:
        return raw_key if global_key else f"plugin_{app_id}_{raw_key}"

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
        fields = [self._field(app_id, s, resolved) for s in raw_settings]
        values = {}
        for s in raw_settings:
            rk = resolved[s["key"]]
            values[rk] = self.settings.get(rk, s.get("default", ""))
            it = s.get("inline_toggle")
            if it and it.get("key"):
                ik = self._resolve_key(app_id, it["key"], it.get("global_key", False))
                values[ik] = self.settings.get(ik, it.get("default", ""))
        return {
            "id": app_id,
            "name": manifest.get("name", app_id),
            "icon": manifest.get("icon", "🧩"),
            "fields": fields,
            "values": values,
        }

    def save_settings(self, app_id: str, values: dict) -> None:
        """Store already-resolved setting keys sent by the frontend."""
        if app_id not in self._registry:
            raise KeyError(app_id)
        # Guard: only accept this app's plugin_ keys or known global keys.
        allowed_globals = set(self.settings.all().keys())
        clean = {}
        for k, v in values.items():
            if k.startswith(f"plugin_{app_id}_") or k in allowed_globals:
                clean[k] = v
        if clean:
            self.settings.update(clean)
            self._caches.pop(app_id, None)  # settings changed -> drop cache

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
