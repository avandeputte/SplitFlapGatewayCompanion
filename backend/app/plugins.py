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
import time
from pathlib import Path

from .config import Config
from .plugin_settings import PluginSettings

log = logging.getLogger("companion.plugins")

# Passed through from a manifest setting to the frontend field, verbatim.
_PASSTHROUGH = (
    "size", "ph", "min", "max", "step", "stepper", "searchUrl", "resultKey",
    "maxItems", "compute", "watches", "variant", "title", "text", "items",
    "icon", "linkText", "linkHref", "default", "note",
)

# Global keys that apps READ but that no manifest exposes as an editable setting.
# Surfaced in the global-settings editor so they can be set in one place.
_GLOBAL_CORE = (
    {"key": "zip_code", "label": "Location — ZIP, postcode, or city",
     "type": "text", "ph": "02118",
     "note": "Used to locate you when an app has no precise coordinates (geocoded)."},
)


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
        enabled = set(self.settings.installed_apps)
        for app_id, app_dir in self._scan().items():
            if app_id in enabled:
                self._load_one(app_id, app_dir)

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
    def _entry(self, app_id: str, manifest: dict, installed: bool, builtin: bool) -> dict:
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
        # Some of an app's settings are shared globals (e.g. weather_api_key,
        # timezone) — flag them so the dialog shows they affect other apps too.
        used = self._declared_globals()
        this_name = manifest.get("name", app_id)
        fields = []
        for s in raw_settings:
            f = self._field(app_id, s, resolved)
            # Flag a global only when it is ACTUALLY shared with other apps, so
            # the badge means what it says (a global used only here isn't "shared").
            if s.get("global_key"):
                others = sorted(set(used.get(s["key"], [])) - {this_name})
                if others:
                    f["shared"] = True
                    shared = "Shared setting — also used by " + ", ".join(others)
                    f["note"] = (f["note"] + "  ·  " + shared) if f.get("note") else shared
            fields.append(f)
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
        manifest = self._registry.get(app_id)
        if manifest is None:
            raise KeyError(app_id)
        # Accept: this app's own ``plugin_<id>_*`` keys, any key already in the
        # store, and the GLOBAL keys this app's manifest declares (``global_key``
        # settings + ``inline_toggle`` globals). That last part is essential —
        # not every global an app uses is pre-seeded in the defaults, and without
        # it those settings would be silently dropped and never persisted to
        # app_settings.json.
        allowed = set(self.settings.all().keys())
        for st in manifest.get("settings", []):
            if st.get("global_key") and st.get("key"):
                allowed.add(st["key"])
            it = st.get("inline_toggle")
            if isinstance(it, dict) and it.get("global_key") and it.get("key"):
                allowed.add(it["key"])
        clean = {k: v for k, v in values.items()
                 if k.startswith(f"plugin_{app_id}_") or k in allowed}
        if clean:
            self.settings.update(clean)
            self._caches.pop(app_id, None)  # settings changed -> drop cache

    # -- global (shared) settings editor ----------------------------------
    def _declared_globals(self) -> dict[str, list[str]]:
        """Map each global setting key -> the names of installed apps using it."""
        used: dict[str, list[str]] = {}
        # Deterministic order so dedupe (first-wins) is stable.
        for app_id, manifest in sorted(
                self._registry.items(),
                key=lambda kv: kv[1].get("name", kv[0]).lower()):
            name = manifest.get("name", app_id)
            for st in manifest.get("settings", []):
                for k in (st.get("key") if st.get("global_key") else None,
                          (st.get("inline_toggle") or {}).get("key")
                          if isinstance(st.get("inline_toggle"), dict)
                          and st["inline_toggle"].get("global_key") else None):
                    if k:
                        used.setdefault(k, []).append(name)
        return used

    def global_settings_schema(self) -> dict:
        """Every ``global_key`` setting declared by installed apps (deduped),
        plus core shared keys no manifest exposes (e.g. zip_code) — so the shared
        settings apps rely on can all be edited in one place."""
        seen: dict[str, dict] = {}       # key -> first-seen setting definition
        used = self._declared_globals()
        # Some globals are really one app's private multi-field store (Countdown's
        # five events) or non-inputs (a notice) — they live in that app's own
        # settings, so keep them out of the shared editor.
        skip_types = {"notice", "computed", "button"}
        for app_id, manifest in sorted(
                self._registry.items(),
                key=lambda kv: kv[1].get("name", kv[0]).lower()):
            for st in manifest.get("settings", []):
                key = st.get("key")
                if not (st.get("global_key") and key):
                    continue
                if st.get("type") in skip_types or key.startswith("countdown_"):
                    continue
                seen.setdefault(key, st)
        for core in _GLOBAL_CORE:
            seen.setdefault(core["key"], core)

        resolved = {k: k for k in seen}   # globals resolve to their bare key
        fields = []
        for key, st in seen.items():
            f = self._field("", st, resolved)
            # Labels are written from an app's point of view ("override global");
            # here these ARE the globals, so drop that now-misleading wording.
            for phrase in (" (override global)", " (leave blank for global)"):
                f["label"] = f["label"].replace(phrase, "")
            apps = used.get(key)
            if apps:
                f["note"] = "Used by " + ", ".join(sorted(set(apps)))
            fields.append(f)
        fields.sort(key=lambda f: f["label"].lower())
        values = {k: self.settings.get(k, seen[k].get("default", "")) for k in seen}
        return {"fields": fields, "values": values}

    def save_global_settings(self, values: dict) -> None:
        """Persist edited global settings. Only keys declared as a global by an
        installed app (or a known core key) are accepted; changing a global can
        affect many apps, so all caches are dropped."""
        allowed = {c["key"] for c in _GLOBAL_CORE} | set(self._declared_globals())
        clean = {k: v for k, v in values.items() if k in allowed}
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

        SECURITY: a functional app's ``app.py`` is imported (executed) to verify
        it exposes fetch() — i.e. uploading runs arbitrary Python. Only upload
        apps you trust.
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

            if not manifest.get("name"):
                raise ValueError("manifest.json is missing a 'name'")
            kind = manifest.get("type")
            if kind == "functional":
                if not (root / "app.py").is_file():
                    raise ValueError("functional app is missing app.py")
                self._validate_fetch(root / "app.py")
            elif kind == "channel":
                if not (root / "data.json").is_file():
                    raise ValueError("channel app is missing data.json")
            else:
                raise ValueError("manifest 'type' must be 'functional' or 'channel'")

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
