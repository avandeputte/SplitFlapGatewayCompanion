"""
plugins.py — the plugin runtime (a faithful port of the app-plugin contract).

Discovers and loads apps from ``apps/<id>/`` (functional ``app.py`` or channel
``data.json``), assembles the per-app settings dict exactly as the app-plugin contract expects,
and produces display pages with the same caching/paging semantics. Keeping this
behavior-identical is what lets any compatible app drop in unchanged — see
COMPATIBILITY.md.

``fetch()`` may do blocking network I/O, so callers run ``get_pages()`` in a
thread executor (see engine.py).
"""

from __future__ import annotations

import ast
import collections
import functools
import importlib.util
import inspect
import json
import logging
import os
import random
import re
import threading
import time
from pathlib import Path

from . import appaudit, canvas, device, ha_rest, i18n, location, renderer, textlayout, weather
from .catalog import CATALOG, CATALOG_BY_KEY, CATALOG_KEYS, GLOBAL_STORAGE_KEYS
from .config import Config
from .plugin_settings import PluginSettings

log = logging.getLogger("companion.plugins")

# Passed through from a manifest setting to the frontend field, verbatim.
_PASSTHROUGH = (
    "size", "ph", "min", "max", "step", "stepper", "searchUrl", "resultKey",
    "maxItems", "compute", "watches", "variant", "title", "text", "items",
    "icon", "linkText", "linkHref", "default", "note",
    # A select whose options come from the LIVE wall rather than the manifest. "effects"
    # is resolved server-side (_dynamic_options); "anim_library" is filled in the browser
    # from GET /api/panel/library, so the field must carry the source to the client.
    "options_source",
)

# Friendly name + icon per on-device effect, for the one-app-per-effect split. An effect a
# future firmware adds still gets an app, named from its own token.
_EFFECT_META = {
    "plasma": ("Plasma", "🌀"), "fire": ("Fire", "🔥"), "matrix": ("Matrix Rain", "💚"),
    "fliporama": ("Flip-o-rama", "🎞️"), "clock": ("Panel Clock", "🕛"),
    "life": ("Game of Life", "🦠"), "rainbow": ("Rainbow", "🌈"),
}


def _effect_name_icon(token: str):
    return _EFFECT_META.get(token, (token.replace("_", " ").title(), "🎆"))


# A channel app's translated page set: data_fr.json, data_pt-BR.json. The default
# (untranslated) pages stay in data.json, which is also the fallback.
LANG_DATA_FILE = re.compile(r"^data_([A-Za-z]{2}(?:-[A-Za-z]{2})?)\.json$")

# A "quiz" app is a channel whose every group is a [question, answer] pair — shown as a two-screen
# reveal. It shares the channel machinery (same data files, loading, pages, canvas), differing only
# in the pair constraint. So everything that treats a channel treats a quiz the same way.
_CHANNELISH = ("channel", "quiz")

_PLUGIN_PREFIX = "plugin_"


def app_id_from_ref(ref: str) -> str:
    """The bare app id from a possibly-prefixed reference. The UI and API address a plugin app as
    ``plugin_<id>`` (the same namespace its settings keys use); this strips that one prefix.
    Idempotent for an already-bare id — the single place that knows the convention."""
    return ref[len(_PLUGIN_PREFIX):] if ref.startswith(_PLUGIN_PREFIX) else ref

# Translated app-store metadata (names, descriptions, settings labels) lives
# OUTSIDE manifest.json — the manifest stays a plain, portable format (its
# description is read as a plain string). Two layers:
#   backend/app/app_i18n/<lang>.json   central catalog for the vendored library
#   apps/<id>/i18n/<lang>.json         per-app sidecar; travels in uploaded zips
# The sidecar wins. See docs/UI_I18N_PLAN.md.
I18N_META_FILE = re.compile(r"^([A-Za-z]{2}(?:-[A-Za-z]{2})?)\.json$")
APP_I18N_DIR = Path(__file__).parent / "app_i18n"
_META_STR_KEYS = ("name", "flap_name", "description")


_json_cache: dict = {}   # Path -> (mtime_ns, parsed dict)


def _read_json_cached(path: Path) -> dict:
    """A JSON file, parsed at most once per on-disk version. Without the cache the
    central app_i18n/<lang>.json is re-read once per app per /api/apps request
    (~4 reads x N apps in a non-English locale); the mtime check makes every
    repeat read free while picking up edits. Callers must not mutate the
    returned dict."""
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        return {}
    hit = _json_cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        d = json.loads(path.read_text("utf-8"))
        d = d if isinstance(d, dict) else {}
    except Exception:
        d = {}
    _json_cache[path] = (mt, d)
    return d


def _merge_meta(into: dict, entry: dict) -> None:
    for k in _META_STR_KEYS:
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            into[k] = v
    s = entry.get("settings")
    if isinstance(s, dict):
        into.setdefault("settings", {}).update(s)


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
        self._channel: dict[str, dict[str, list]] = {}  # app_id -> {lang: pages} ("" = data.json)
        self._triggers: dict[str, object] = {}  # app_id -> trigger fn
        self._caches: dict[str, dict] = {}       # app_id -> {pages, fetched_at}
        self._reads: dict[str, dict] = {}        # app_id -> {settings key it reads: default}
        # app_id -> the injected helpers its fetch()/trigger() opts into by
        # parameter name. Precomputed once at load: signature inspection is not
        # something to repeat on every fetch, and one structure serves both the
        # injection path and the UI ("uses location", global-usage notes).
        self._wants: dict[str, frozenset] = {}          # fetch()'s injected helpers
        self._wants_matrix: dict[str, frozenset] = {}   # fetch_matrix()'s injected helpers
        self._trigger_wants: dict[str, frozenset] = {}
        # What THIS display's wall can show. A callable, because the answer is only known
        # once the transport has talked to the gateway — and it can change if the gateway
        # is swapped. Defaults to the pessimistic answer: a real reel, no pictographs.
        self._caps = lambda: device.SPLIT_FLAP
        self._fetch_locks: dict[str, threading.Lock] = {}  # app_id -> serialize its fetches
        self._first_error: dict[str, str] = {}     # cache key -> its first fetch error
        # Bumped whenever settings change wholesale (load/global save): a fetch
        # that started before the bump computed from the OLD settings and must
        # not populate the fresh cache (see get_pages).
        self._gen = 0

    def attach_caps(self, provider) -> None:
        """Tell the runtime how to ask what THIS display's wall can show (see Display.build).

        A callable, not a value: the answer is only known once the transport has reached the
        gateway, and a display can be re-pointed at a different one.
        """
        self._caps = provider

    # -- helpers injected into plugins -------------------------------------
    def get_rows(self) -> int:
        return int(self.config.grid["rows"])

    def get_cols(self) -> int:
        return int(self.config.grid["cols"])

    # An app may declare where its block sits, with "vertical_align" in its manifest:
    #
    #   center  (default)  the block is centered on the wall
    #   top                the block starts at the top; spare rows fall to the bottom
    #   bottom             the block is pushed to the bottom
    #
    # Absent means "center", so every existing app keeps
    # working untouched — the key is additive, and an app that never heard of it gets the
    # behavior it already had.
    #
    # `top` is the escape hatch — pad only at the bottom — so an app that wants
    # to place its own rows (a fixed header, a hand-built layout) declares `top` and emits
    # blank lines wherever it wants them. Without it, an app doing its own vertical
    # placement gets centered a SECOND time and drifts below the middle — which is why
    # cat-facts, on-this-day and sarcastic-fortune-cookies declare it.
    ALIGNMENTS = ("center", "top", "bottom")

    def vertical_align(self, app_id: str | None) -> str:
        """Where this app's block sits. Unknown values fall back to centering rather than
        failing the app: a typo in a manifest should not take the wall down."""
        if not app_id:
            return "center"
        want = str((self._registry.get(app_id) or {}).get("vertical_align") or "center").lower()
        if want not in self.ALIGNMENTS:
            log.warning("plugin %s: unknown vertical_align %r — using center (one of: %s)",
                        app_id, want, ", ".join(self.ALIGNMENTS))
            return "center"
        return want

    def format_lines(self, *lines, cols=None, align="center") -> str:
        """Build one page from up to `rows` lines: each centered horizontally, and the
        block placed VERTICALLY when the app gives fewer lines than the wall is tall.

        Centered by default — a deliberate, documented choice (COMPATIBILITY.md):
        bottom-only padding is invisible on a 3-row wall but leaves a 3-line app
        stranded at the top of a 5-row wall with two dead rows under it. An app that
        wants its block at the top, or wants to place its own rows, says so with
        "vertical_align": "top" in its manifest.

        Nothing changes when an app fills the wall exactly.
        """
        cols = cols or self.get_cols()
        rows = self.get_rows()
        given = list(lines)[:rows]
        pad = rows - len(given)
        if align == "top":
            top = 0                         # block at the top; spare rows fall to the bottom
        elif align == "bottom":
            top = pad
        else:
            top = pad // 2                  # centered; an odd remainder falls to the bottom
        padded = [""] * top + given + [""] * (pad - top)
        # Expand BEFORE centering: a character the wall cannot show may need two flaps (ß -> SS
        # on a reel with no ß), and this is the last moment the line is allowed to get longer.
        # Afterwards it is one flap per character and "SS" no longer fits where "ß" was.
        caps = self._caps()
        return "".join(renderer.expand(str(l), caps).center(cols)[:cols] for l in padded[:rows])

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

    def installable_ids(self) -> list[str]:
        """Every app id a user can install: the ones on disk, plus the synthetic per-effect
        apps this wall advertises (``effect_<token>``), which have no folder of their own."""
        return list(self._scan().keys()) + [eid for eid, _ in self._effect_defs()]

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
        self._gen += 1
        self._registry.clear()
        self._modules.clear()
        self._channel.clear()
        self._triggers.clear()
        self._caches.clear()
        self._reads.clear()
        self._wants.clear()
        self._wants_matrix.clear()
        self._trigger_wants.clear()
        self._fetch_locks.clear()
        self._first_error.clear()
        # Scan the app dirs once and reuse it (discovery + per-app load).
        scan = self._scan()
        # Let the settings store nest per-app keys by app id when it persists.
        self.settings.set_known_apps(list(scan.keys()))
        self._migrate_renamed_apps()
        enabled = set(self.settings.installed_apps)
        for app_id, app_dir in scan.items():
            if app_id == "effects":
                continue                          # a template for the per-effect apps (see below)
            if app_id in enabled:
                self._load_one(app_id, app_dir)
        self._load_effects(scan.get("effects"), enabled)
        # Multi-value settings (search_chips) are stored as JSON arrays on disk.
        self.settings.set_list_keys(self._list_keys())

    def _load_effects(self, eff_dir, enabled: set) -> None:
        """The single "Effects" app is presented as ONE app per effect the wall advertises (Plasma,
        Fire, Matrix Rain…), all sharing its module. Load that module once, then register a thin
        per-effect entry — its own manifest, the shared module and helper sets — for each installed
        one. The generic ``effects`` app itself is never listed."""
        if not eff_dir or not self._effect_defs():
            return
        self._load_one("effects", eff_dir)           # loads the shared module (and a temp entry)
        eff_mod = self._modules.get("effects")
        if eff_mod is None:
            return
        eff_wants = self._wants.get("effects", frozenset())
        eff_wants_matrix = self._wants_matrix.get("effects", frozenset())
        self._registry.pop("effects", None)          # the generic app itself is never listed
        for effect_id, token in self._effect_defs():
            if effect_id in enabled:
                self._registry[effect_id] = self._effect_manifest(effect_id, token)
                self._modules[effect_id] = eff_mod
                self._wants[effect_id] = eff_wants
                self._wants_matrix[effect_id] = eff_wants_matrix

    # Apps that were absorbed into another. A wall that had the old id installed
    # (or in a playlist) follows the merge instead of waking up to PLUGIN ERROR.
    _RENAMED_APPS = {"national-today": "holidays"}

    def _migrate_renamed_apps(self) -> None:
        installed = list(self.settings.installed_apps)
        migrated = [self._RENAMED_APPS.get(a, a) for a in installed]
        deduped = list(dict.fromkeys(migrated))
        if deduped != installed:
            self.settings.set_installed(deduped)
            log.info("migrated renamed apps in the installed list: %s", installed)
        playlists = self.settings.get("saved_app_playlists")
        if isinstance(playlists, dict):
            changed = False
            for doc in playlists.values():
                for e in (doc or {}).get("entries", []):
                    if isinstance(e, dict) and e.get("app") in self._RENAMED_APPS:
                        e["app"] = self._RENAMED_APPS[e["app"]]
                        changed = True
            if changed:
                self.settings.set("saved_app_playlists", playlists)
                log.info("migrated renamed apps inside saved playlists")

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
                    keys.add(self._resolve_key(app_id, st["key"]))
        return keys

    def _load_one(self, app_id: str, app_dir: Path) -> None:
        try:
            manifest = dict(_read_json_cached(app_dir / "manifest.json"))
            if not manifest:
                raise ValueError("manifest.json missing or invalid")
        except Exception as e:
            log.error("plugin %s: bad manifest: %s", app_id, e)
            return
        manifest["id"] = app_id
        self._registry[app_id] = manifest
        kind = manifest.get("type")
        if kind in _CHANNELISH:
            self._load_channel(app_id, app_dir)
        elif kind == "functional":
            self._load_functional(app_id, app_dir)
        log.info("plugin loaded: %s (%s)", app_id, kind)

    def _read_pages(self, app_id: str, path: Path) -> list | None:
        """The 'pages' of one channel data file, rendered — grouped into ITEMS so
        a multi-page item (a joke's setup + punchline) stays together and in order
        even when the app shuffles. Returns a list of items, each a list of one or
        more rendered page strings.

        Grouping, in order of precedence:
          * an explicit ``group`` on a page — consecutive pages sharing it are one
            item (for a data file that mixes single- and multi-page items);
          * else the manifest's ``group_size`` (default 1) — pages chunked N at a
            time (for a file whose items are all the same length, e.g. every joke
            is setup+punchline).
        """
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception as e:
            log.error("plugin %s: %s error: %s", app_id, path.name, e)
            return None

        # New format: {"groups": [...]} where a group is a STRING (one page) or a
        # LIST of strings (a multi-page item — a joke's setup + punchline). Each
        # string is the page's full text and the ENGINE wraps it to the wall, so
        # the data does not hard-code line breaks for a 15-column sign. A ``\n``
        # in a string forces a break (an attribution on its own line); everything
        # else word-wraps. Grouping is structural, so a shuffle can never split a
        # multi-page item.
        if "groups" in data:
            return self._read_groups(app_id, data["groups"])

        rendered = []      # (page_string, explicit_group_or_None)
        for page in data.get("pages", []):
            if isinstance(page, str):
                rendered.append((page, None))
            elif isinstance(page, dict) and "lines" in page:
                rendered.append((self.format_lines(*page["lines"],
                                                   align=self.vertical_align(app_id)),
                                 page.get("group")))

        if any(g is not None for _, g in rendered):
            items, prev = [], object()
            for text, g in rendered:
                if g is not None and g == prev:
                    items[-1].append(text)
                else:
                    items.append([text])
                prev = g if g is not None else object()
            return items

        size = max(1, int(self._registry.get(app_id, {}).get("group_size", 1) or 1))
        texts = [text for text, _ in rendered]
        return [texts[i:i + size] for i in range(0, len(texts), size)]

    @staticmethod
    def _wrap_text(text: str, rows: int, cols: int) -> list:
        """A page's text, wrapped to the wall: word-wrapped to ``cols``, then
        paginated into pages of at most ``rows`` lines (a long segment spills to
        more pages, kept in its group). ``\\n`` is a forced break."""
        lines: list[str] = []
        for part in str(text).split("\n"):
            words = part.split()
            if not words:
                lines.append("")
                continue
            cur = ""
            for w in words:
                if len(w) > cols:                 # a single word longer than the wall
                    if cur:
                        lines.append(cur)
                        cur = ""
                    lines.append(w[:cols])
                    continue
                if cur and len(cur) + 1 + len(w) > cols:
                    lines.append(cur)
                    cur = w
                else:
                    cur = f"{cur} {w}".strip()
            if cur:
                lines.append(cur)
        if not lines:
            lines = [""]
        return [lines[i:i + rows] for i in range(0, len(lines), rows)]

    def _read_groups(self, app_id: str, groups: list) -> list:
        """The ``groups`` channel format → items (page-groups). A group is one
        string (single page) or a list of strings (one page each). Each string is
        engine-wrapped; all pages of a group stay together and in order."""
        rows, cols = self.get_rows(), self.get_cols()
        align = self.vertical_align(app_id)
        items = []
        for group in groups:
            segments = [group] if isinstance(group, str) else group
            pages = []
            for seg in segments:
                if not isinstance(seg, str):
                    continue
                for page_lines in self._wrap_text(seg, rows, cols):
                    pages.append(self.format_lines(*page_lines, align=align))
            if pages:
                items.append(pages)
        return items

    def _load_channel(self, app_id: str, app_dir: Path) -> None:
        """Load a channel app's pages. ``data.json`` is the default set; an app may
        also ship translations as ``data_<lang>.json`` sidecars (``data_fr.json``,
        ``data_fr-BE.json``), which are picked at render time from the effective
        Language. Keeping data.json as the fallback means a translated app still
        runs unchanged anywhere that ignores the sidecars."""
        by_lang: dict[str, list] = {}
        default = app_dir / "data.json"
        if default.is_file():
            pages = self._read_pages(app_id, default)
            if pages is not None:
                by_lang[""] = pages
        for f in sorted(app_dir.glob("data_*.json")):
            m = LANG_DATA_FILE.match(f.name)
            if not m:
                continue
            pages = self._read_pages(app_id, f)
            if pages:
                by_lang[m.group(1).lower()] = pages
        if not by_lang:
            return
        self._channel[app_id] = by_lang
        # An app that ships translations adapts to Language whether or not its
        # manifest declares i18n -- that flag drives the 🌐 badge and the per-app
        # Language override, and it would be a lie in either direction to ignore
        # the files that are actually there.
        manifest = self._registry.get(app_id)
        if manifest is not None:
            manifest["i18n"] = len(by_lang) > 1 or bool(manifest.get("i18n"))

    def app_meta_i18n(self, app_id: str, app_dir: Path | None, lang) -> dict:
        """Translated metadata for one app in one language ({} for English or
        unset): the central catalog first, the app's own sidecar on top — each
        read base-then-exact, so a pt-BR viewer gets pt plus any pt-BR extras."""
        code = str(lang or "").replace("_", "-")
        base = code.split("-")[0].lower()
        if not base or base == "en":
            return {}
        out: dict = {}
        for c in dict.fromkeys([base, code]):        # base first, exact wins
            entry = _read_json_cached(APP_I18N_DIR / f"{c}.json").get(app_id)
            if isinstance(entry, dict):
                _merge_meta(out, entry)
        if app_dir:
            for c in dict.fromkeys([base, code]):
                _merge_meta(out, _read_json_cached(app_dir / "i18n" / f"{c}.json"))
        return out

    def _flap_fallback(self, app_id: str, manifest: dict | None, settings,
                       *lines) -> str:
        """A fallback page rendered TO THE FLAPS ("NO DATA", "APP ERROR", ...),
        localized to the CONTENT language — the wall is shared, so this follows
        the global Language, not the viewer's browser. The app's display name
        may come from its i18n sidecar (flap_name beats name: it exists for
        reels whose character set can't show the pretty translated name)."""
        lang = settings.get("language", "en-US") if settings else "en-US"
        cols = self.get_cols()
        meta = self.app_meta_i18n(app_id, self._scan().get(app_id), lang) if manifest else {}
        name = meta.get("flap_name") or meta.get("name") or \
            (manifest or {}).get("name", app_id)
        out = []
        for ln in lines:
            if ln == "{name}":
                out.append(str(name).upper()[:cols])
            else:
                out.append(i18n.translate(ln, lang) if ln else ln)
        return self.format_lines(*out)

    def _channel_pages(self, app_id: str, lang: str, settings=None) -> list:
        """Pages for a channel app in the effective language: an exact locale match
        (``fr-BE``) wins, then the base language (``fr``), then data.json. Same
        precedence a Localizer gives a functional app, so both kinds of app answer
        a Language change the same way.

        Stored as items (page-groups); flattened here. When the effective order is
        ``random`` the ITEMS are shuffled — each fetch is a fresh pass, so a quotes
        channel doesn't march in the same order every day, while a joke's two pages
        never come apart. A multi-page item is never split across the shuffle."""
        by_lang = self._channel.get(app_id) or {}
        code = str(lang or "").replace("_", "-").lower()
        items = next((by_lang[k] for k in (code, code.split("-")[0], "") if by_lang.get(k)),
                     None)
        if items is None:
            items = next((v for v in by_lang.values() if v), [])

        return [page for item in self._ordered(items, app_id, settings) for page in item]

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
        # A functional app renders each surface it declares with a matching entry point:
        # fetch() for flaps, fetch_matrix() for a Matrix panel. It needs at least one.
        has_fetch = hasattr(mod, "fetch") and callable(mod.fetch)
        has_matrix = hasattr(mod, "fetch_matrix") and callable(mod.fetch_matrix)
        if has_fetch or has_matrix:
            self._modules[app_id] = mod
            if has_fetch:
                self._wants[app_id] = self._wanted_helpers(mod.fetch)
                self._fetch_locks[app_id] = threading.Lock()
            if has_matrix:
                self._wants_matrix[app_id] = self._wanted_helpers(mod.fetch_matrix)
        else:
            log.error("plugin %s: app.py has neither fetch() nor fetch_matrix()", app_id)
        if hasattr(mod, "trigger") and callable(mod.trigger):
            self._triggers[app_id] = mod.trigger
            self._trigger_wants[app_id] = self._wanted_helpers(mod.trigger)
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

    # The helpers an app (or trigger) opts into by parameter name — ONE table, so the set of names
    # and how each is built stay in one place. Each builder takes (self, app_id, ps, settings) and
    # returns the value injected under that name; a fetch/trigger asks for one just by naming the
    # parameter. (A trigger needs the weather/timezone for the same reasons a fetch does.)
    _HELPER_BUILDERS = {
        # get_weather() = current conditions; days=N adds a forecast + hourly temps;
        # air=True adds AQI/UV/pollen (weather.py).
        "get_weather": lambda self, app_id, ps, settings:
            (lambda s=None, days=0, air=False:
                weather.fetch_weather(s if s is not None else ps, days=days, air=air)),
        "get_location": lambda self, app_id, ps, settings: (lambda: location.resolve(ps)),
        # All Home Assistant entity states (Supervisor proxy or configured URL/token), cached; the
        # Dashboard app filters to the entities it was told to show.
        "get_ha_states": lambda self, app_id, ps, settings: (lambda: ha_rest.fetch_states()),
        # A per-app Language override (plugin_<id>_language) wins over the global Language.
        "i18n": lambda self, app_id, ps, settings: i18n.Localizer(self.content_lang(app_id, settings)),
        # What this wall can show, so an app offers a pictograph where the wall has one and a WORD
        # where it does not (♥, ♪, ● all degrade to "*" on a real reel).
        "caps": lambda self, app_id, ps, settings: self._caps(),
        # paginate(text, title="") → finished pages for a block of text, word-wrapped and balanced
        # to THIS wall — shared here so the advice/quote/fact apps don't each carry a copy.
        "paginate": lambda self, app_id, ps, settings: self._make_paginate(app_id),
    }

    @classmethod
    def _wanted_helpers(cls, fn) -> frozenset:
        """Which injected helpers ``fn`` opts into by parameter name — computed once at load."""
        return frozenset(n for n in cls._HELPER_BUILDERS if cls._fetch_accepts(fn, n))

    def _helper_kwargs(self, app_id: str, wanted: frozenset, ps: dict, settings=None) -> dict:
        """The injected-helper kwargs for a fetch()/trigger() — the ``wanted`` names built from the
        one ``_HELPER_BUILDERS`` table."""
        settings = settings or self.settings
        return {name: self._HELPER_BUILDERS[name](self, app_id, ps, settings) for name in wanted}

    def _make_paginate(self, app_id: str):
        fmt = functools.partial(self.format_lines, align=self.vertical_align(app_id))
        return lambda text, title="": [fmt(*page) for page in
                                       textlayout.balanced_pages(text, self.get_rows(),
                                                                 self.get_cols(), title)]

    def build_canvas_surface(self):
        """A CanvasSurface for this wall, or None if it has no framebuffer. The transport the
        canvas apps draw through — shared so the channel-on-canvas renderer uses the same path.
        The surface derives everything it needs from the Capabilities itself."""
        caps = self._caps()
        if not caps.has_canvas:
            return None
        url = str(self.config.transport.get("gateway_url") or "").strip()
        return canvas.CanvasSurface(url, caps)

    def is_channel_app(self, app_id: str) -> bool:
        return self._registry.get(app_id, {}).get("type") in _CHANNELISH

    def channel_canvas_motif(self, app_id: str) -> str:
        """The art motif a channel draws on the panel (manifest ``canvas_art``), default quote marks."""
        return str(self._registry.get(app_id, {}).get("canvas_art") or "quote")

    # -- surfaces (which displays an app draws on) -------------------------
    def surfaces(self, app_id: str) -> list:
        """The surfaces this app renders on: ``["flap"]`` (default), ``["matrix"]``, or both. A
        functional app renders each with a matching entry point — ``fetch`` for flaps,
        ``fetch_matrix`` for a Matrix panel; a channel/quiz's matrix surface is drawn generically
        (its text + a themed icon)."""
        s = self._registry.get(app_id, {}).get("surfaces")
        return list(s) if isinstance(s, list) and s else ["flap"]

    def renders_on(self, app_id: str, surface: str) -> bool:
        return surface in self.surfaces(app_id)

    def is_matrix_only(self, app_id: str) -> bool:
        """Draws ONLY on a Matrix panel (``surfaces == ["matrix"]``) — gated off a flap wall."""
        return self.surfaces(app_id) == ["matrix"]

    def has_matrix_render(self, app_id: str) -> bool:
        """The app can actually render on a panel — a functional app with ``fetch_matrix``, or a
        channel/quiz (drawn generically). The capability behind the toggle and the badge."""
        if "matrix" not in self.surfaces(app_id):
            return False
        return callable(getattr(self._modules.get(app_id), "fetch_matrix", None)) \
            or self.is_channel_app(app_id)

    def is_dual_surface(self, app_id: str) -> bool:
        """Renders on flaps AND a panel — the apps that carry the dual-surface badge and the toggle."""
        s = self.surfaces(app_id)
        return "flap" in s and "matrix" in s and self.has_matrix_render(app_id)

    def matrix_on(self, app_id: str, settings=None) -> bool:
        """Whether this app should render on the Matrix panel right now. A matrix-only app: always.
        A dual-surface app: the per-app ``matrix`` toggle (default ON) — off ⇒ its flap view (plain
        text) on the panel. The engine checks the wall actually HAS a panel before routing."""
        if not self.has_matrix_render(app_id):
            return False
        if self.is_matrix_only(app_id):
            return True
        v = self._perapp_value(app_id, "matrix", settings)
        if v is None:
            return True
        return str(v).strip().lower() in ("yes", "on", "1", "true")

    def _channel_raw_groups(self, app_id: str, lang: str) -> list:
        """The channel's RAW groups (unformatted text — a string or a list of segment strings) for
        the effective language: exact locale wins, then base, then data.json. Read straight from the
        data file so the canvas renderer re-wraps the real words (the flap-formatted pages center and
        pad each line, which butts words together and can't be un-wrapped)."""
        app_dir = self._scan().get(app_id)
        if not app_dir:
            return []
        code = str(lang or "").replace("_", "-").lower()
        for cand in (f"data_{code}.json", f"data_{code.split('-')[0]}.json", "data.json"):
            f = app_dir / cand
            if not f.is_file():
                continue
            try:
                doc = _read_json_cached(f)
            except Exception:
                continue
            if isinstance(doc.get("groups"), list) and doc["groups"]:
                return doc["groups"]
            if isinstance(doc.get("pages"), list) and doc["pages"]:       # legacy shape
                return [p if isinstance(p, str) else (p.get("lines") if isinstance(p, dict) else p)
                        for p in doc["pages"]]
        return []

    def channel_canvas_items(self, app_id: str, overrides: dict | None = None) -> list:
        """The channel's lines as plain text for the panel — one per group, a multi-segment group
        (setup/punchline, quote + attribution) joined with a newline the renderer honors. Shuffled
        when the order is random. ``overrides`` are per-playlist-entry settings, layered like
        get_pages does so an entry's own Language is honored."""
        settings = _SettingsOverlay(self.settings, overrides) if overrides else self.settings
        lang = self.content_lang(app_id, settings)
        groups = self._ordered(self._channel_raw_groups(app_id, lang), app_id, settings)
        out = []
        for g in groups:
            segs = [g] if isinstance(g, str) else [s for s in g if isinstance(s, str)]
            # each screen is its own frame — a quiz's question, then (after the dwell) its answer.
            # A newline inside a screen is a deliberate break (a movie quote over its title) and is
            # kept; only the padding within each line is collapsed.
            for seg in segs:
                txt = "\n".join(" ".join(ln.split()) for ln in str(seg).split("\n") if ln.strip())
                if txt:
                    out.append(txt)
        return out

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
                parts = self._parse_composite(
                    ["location_lat", "location_lon", "location_name"], loc_ovr)
                if parts["location_lat"] and parts["location_lon"]:
                    s["location_lat"] = parts["location_lat"]
                    s["location_lon"] = parts["location_lon"]
                    if parts["location_name"]:
                        s["location_name"] = parts["location_name"]
                    s["location"] = loc_ovr
        # A per-effect app pins its effect (there is no picker): hand the shared effects
        # module the effect its id stands for.
        if manifest.get("pinned_effect"):
            s["effect"] = manifest["pinned_effect"]
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
    # can only be initialized ONCE per process. If its first import dies (numpy 2.4+ on a
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
            return [self._flap_fallback(app_id, None, self.settings,
                                        "PLUGIN ERROR", app_id.upper()[:cols], "NOT FOUND")]
        app_type = manifest.get("type")
        # Per-entry overrides (playlist) render this app with its own config without
        # disturbing the saved settings; the cache/lock key includes them so two
        # entries of the same app don't collide.
        settings = _SettingsOverlay(self.settings, overrides) if overrides else self.settings
        ckey = _cache_key(app_id, overrides)
        refresh = self._refresh_secs(app_id, manifest, settings)

        if app_type in _CHANNELISH:
            # A per-app Language override (plugin_<id>_language) wins over the global
            # Language; blank/unset = follow global. Same rule as a functional app.
            lang = self.content_lang(app_id, settings)
            pages = self._channel_pages(app_id, lang, settings)
            return pages or [self._flap_fallback(app_id, manifest, settings,
                                                 "{name}", "NO DATA", "")]

        if app_type == "functional":
            mod = self._modules.get(app_id)
            if not mod:
                return [self._flap_fallback(app_id, manifest, settings,
                                            "PLUGIN ERROR", "{name}", "NOT LOADED")]
            # Serialize fetches for this app: get_pages runs in executor threads
            # (app loop + a preview can hit the same app at once), so this
            # coalesces duplicate fetches and protects non-reentrant app state.
            # setdefault, atomically: check-then-set here once let two executor
            # threads (app loop + preview) each mint a lock for the same key and
            # fetch twice — the exact duplication this lock exists to coalesce.
            lock = self._fetch_locks.setdefault(ckey, threading.Lock())
            with lock:
                now = time.time()
                cached = self._caches.get(ckey)
                if cached and (now - cached["fetched_at"]) < refresh:
                    return cached["pages"]
                try:
                    # A reload/global-save mid-fetch invalidates what this fetch is
                    # computing (it read the OLD settings). Capture the generation
                    # now; only a fetch from the current generation may be cached.
                    gen = self._gen
                    ps = self._plugin_settings(app_id, manifest, settings)
                    kwargs = self._helper_kwargs(
                        app_id, self._wants.get(app_id, frozenset()), ps, settings)
                    # Bound to THIS app's alignment, so the app calls format_lines(*lines)
                    # with the plain signature apps expect — no alignment argument.
                    fmt = functools.partial(self.format_lines,
                                            align=self.vertical_align(app_id))
                    pages = mod.fetch(ps, fmt, self.get_rows, self.get_cols, **kwargs)
                    if not isinstance(pages, list):
                        pages = [str(pages)]
                    if gen == self._gen:
                        self._caches[ckey] = {"pages": pages, "fetched_at": now}
                    self._first_error.pop(ckey, None)   # a success retires the old story
                    log.debug("fetched %s: %d page(s) (refresh %ss)", ckey, len(pages), refresh)
                    return pages
                except Exception as e:
                    msg = self._fetch_error_message(ckey, app_id, e)
                    cp = self._caches.get(ckey, {}).get("pages")
                    if cp:
                        return cp
                    err = msg.lower()
                    if "timeout" in err or "connection" in err or "network" in err:
                        return [self._flap_fallback(app_id, manifest, settings,
                                                    "{name}", "OFFLINE", "")]
                    return [self._flap_fallback(app_id, manifest, settings,
                                                "APP ERROR", "{name}", msg[:cols])]

        return [self._flap_fallback(app_id, manifest, settings,
                                    "PLUGIN ERROR", "UNKNOWN TYPE", "")]

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
                "surfaces": self.surfaces(app_id),
                "trigger_interval": m.get("trigger_interval", 60),
                "trigger_display_seconds": m.get("trigger_display_seconds", 30),
                "trigger_cooldown": m.get("trigger_cooldown", 300),
                "trigger_conditions": m.get("trigger_conditions", []),
            })
        out.sort(key=lambda a: a["name"].lower())
        return out

    def call_trigger(self, app_id: str, conditions: dict) -> bool:
        """Run an app's trigger(settings, conditions). Blocking — use executor.
        Triggers opt into the same injected helpers as fetch(), by parameter
        name (``i18n``, ``caps``, ``get_weather``, ``get_location``)."""
        fn = self._triggers.get(app_id)
        manifest = self._registry.get(app_id)
        if not fn or not manifest:
            return False
        ps = self._plugin_settings(app_id, manifest)
        kwargs = self._helper_kwargs(app_id, self._trigger_wants.get(app_id, frozenset()), ps)
        return bool(fn(ps, conditions or {}, **kwargs))

    # -- run metadata ------------------------------------------------------
    def manifest(self, app_id: str) -> dict | None:
        return self._registry.get(app_id)

    def render_matrix(self, app_id: str, overrides: dict | None = None):
        """Run a matrix app's ``fetch_matrix(settings, canvas, **helpers)`` once — it draws through
        the ``canvas`` surface (the drawing is the point; there are no pages to return). Its return
        value, if a number, is the seconds the engine should hold before redrawing.

        ``overrides`` are per-playlist-entry setting values (e.g. a Scoreboard following its own
        teams), applied as a transient overlay exactly like a flap app's — so the same matrix app
        can appear twice in a playlist configured differently."""
        mod = self._modules.get(app_id)
        manifest = self._registry.get(app_id)
        fn = getattr(mod, "fetch_matrix", None)
        if not mod or not manifest or not callable(fn):
            return None
        surface = self.build_canvas_surface()
        if surface is None:                                 # no framebuffer — nothing to draw on
            return None
        src = _SettingsOverlay(self.settings, overrides) if overrides else self.settings
        ps = self._plugin_settings(app_id, manifest, src)
        kwargs = self._helper_kwargs(app_id, self._wants_matrix.get(app_id, frozenset()), ps, src)
        result = fn(ps, surface, **kwargs)
        try:
            return float(result) if result is not None else None
        except (TypeError, ValueError):
            return None

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
        wants = self._wants.get(app_id, frozenset())
        return bool({"get_weather", "get_location"} & wants
                    or (set(self._reads.get(app_id, {})) & self._LOCATION_KEYS))

    def content_lang(self, app_id: str, settings=None) -> str:
        """The language this app renders in: its per-app Language override
        (plugin_<id>_language) if set, else the global Language. The one rule,
        written once — channels and functional apps all resolve through it."""
        settings = settings if settings is not None else self.settings
        return (self._perapp_value(app_id, "language", settings)
                or settings.get("language", "en-US"))

    def app_settings_public(self, app_id: str) -> list:
        """This app's own settings as ``{name, label, type, value, options?}`` — the settings
        schema projected for a machine client (the MCP tools), with the storage prefix
        (``plugin_<id>_``) stripped off the name. Skips notice rows and globals. Derived from
        ``settings_schema`` so the two surfaces can never drift."""
        schema = self.settings_schema(app_id)
        values = schema.get("values", {})
        prefix = f"plugin_{app_id}_"
        out = []
        for f in schema.get("fields", []):
            key = f.get("key", "")
            if f.get("type") == "notice" or not key.startswith(prefix):
                continue
            name = key[len(prefix):]
            item = {"name": name, "label": f.get("label", name), "type": f.get("type"),
                    "value": values.get(key, f.get("default", ""))}
            if f.get("options"):
                item["options"] = [o.get("value") for o in f["options"]]
            out.append(item)
        return out

    def _ordered(self, items: list, app_id: str, settings=None) -> list:
        """Items in the channel's effective order: a per-app ``order`` override wins over the
        manifest's declared order; ``random`` returns a shuffled COPY (each fetch a fresh pass, so a
        channel doesn't march the same way every day) — never the input list. The rule the flap
        pages and the panel items both apply."""
        order = (self._perapp_value(app_id, "order", settings)
                 or self._registry.get(app_id, {}).get("order", "sequential"))
        if str(order).lower() == "random":
            items = list(items)
            random.shuffle(items)
        return items

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
            # The fallback is sized for a physical wall: a module's full revolution
            # takes up to ~4 s, and a frame can send any flap anywhere — advance
            # faster and the wall is still clattering toward one frame when the
            # next arrives.
            v = self._perapp_value(app_id, "anim_speed", settings)
            try:
                return max(0.1, float(v)) if v is not None else 4.0
            except (ValueError, TypeError):
                return 4.0
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
                    "loop_delay": self.loop_delay(app_id, settings)}
        style = settings.get(f"plugin_{app_id}_transition_style") or \
            disp.get("transition_style", "ltr")
        return {"is_anim": False, "style": style, "speed": speed,
                "loop_delay": self.loop_delay(app_id, settings)}

    # -- listings ----------------------------------------------------------
    def _entry(self, app_id: str, manifest: dict, installed: bool, builtin: bool,
               lang=None, app_dir: Path | None = None) -> dict:
        meta = self.app_meta_i18n(app_id, app_dir, lang) if lang else {}
        return {
            "id": app_id,
            "name": meta.get("name") or manifest.get("name", app_id),
            "icon": manifest.get("icon", "🧩"),
            "description": meta.get("description") or manifest.get("description", ""),
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
            # The surfaces this app draws on: ["flap"], ["matrix"], or both. The UI badges it and
            # hides a matrix-only app where the wall has no framebuffer to run it on. Read from the
            # manifest we were handed — the per-effect catalog entries aren't in the registry.
            "surfaces": list(manifest.get("surfaces") or ["flap"]),
            "builtin": builtin,
        }

    def app_list(self, lang=None) -> list[dict]:
        """Installed (loaded) apps, sorted by name — powers the Apps grid.
        ``lang`` is the viewer's chrome language: names/descriptions come back
        translated when a catalog or sidecar covers them."""
        scan = self._scan()   # scan once; don't re-scan per app for builtin-ness
        out = [self._entry(i, m, True, self._builtin_in(i, scan),
                           lang=lang, app_dir=scan.get(i))
               for i, m in self._registry.items()]
        out.sort(key=lambda a: a["name"].lower())
        return out

    def _effect_defs(self) -> list[tuple[str, str]]:
        """``(effect_id, token)`` for each on-device effect THIS wall advertises — one app
        each. Empty on a wall with no effects (a physical wall, or caps not yet known) or
        when the ``effects`` template app isn't on disk."""
        if "effects" not in self._scan():
            return []
        try:
            effects = list(self._caps().effects)
        except Exception:
            effects = []
        return [(f"effect_{e}", e) for e in effects if e]

    def _effect_manifest(self, effect_id: str, token: str) -> dict:
        """A per-effect app's manifest, derived from the shared ``effects`` manifest: the
        effect PICKER is dropped (the app IS one effect, pinned via ``pinned_effect``), the
        speed/hue/density knobs stay."""
        d = self._scan().get("effects")
        tmpl = dict(_read_json_cached(d / "manifest.json")) if d else {}
        name, icon = _effect_name_icon(token)
        m = dict(tmpl)
        m["id"] = effect_id
        m["name"] = name
        m["icon"] = icon
        m["description"] = f"The {name} effect, rendered on the panel itself"
        m["pinned_effect"] = token
        m["settings"] = [s for s in tmpl.get("settings", []) if s.get("key") != "effect"]
        return m

    def available_list(self, lang=None) -> list[dict]:
        """Every app on disk, with an ``installed`` flag (for the library). The generic
        ``effects`` app is replaced by one entry per effect the wall advertises."""
        enabled = set(self.settings.installed_apps)
        out = []
        for app_id, app_dir in self._scan().items():
            if app_id == "effects":
                continue
            manifest = self._registry.get(app_id)
            if manifest is None:
                manifest = dict(_read_json_cached(app_dir / "manifest.json"))
                if not manifest:
                    continue
            out.append(self._entry(app_id, manifest, app_id in enabled,
                                   app_dir.parent == self.apps_dir,
                                   lang=lang, app_dir=app_dir))
        for effect_id, token in self._effect_defs():
            out.append(self._entry(effect_id, self._effect_manifest(effect_id, token),
                                   effect_id in enabled, True, lang=lang, app_dir=None))
        out.sort(key=lambda a: a["name"].lower())
        return out

    # -- per-app settings schema + values ---------------------------------
    def _resolve_key(self, app_id: str, raw_key: str) -> str:
        # The catalog is the single source of truth for what is global; every other setting is
        # per-app, namespaced under the app id.
        return raw_key if raw_key in GLOBAL_STORAGE_KEYS else f"plugin_{app_id}_{raw_key}"

    # Effect names get a friendlier label; anything the firmware adds later is
    # title-cased automatically (new_effect -> "New Effect").
    _EFFECT_LABELS = {"plasma": "Plasma", "fire": "Fire", "matrix": "Matrix rain"}

    def _dynamic_options(self, setting: dict):
        """Options a setting draws from the LIVE wall instead of the manifest.

        With ``"options_source": "effects"`` the picker offers exactly the effects
        THIS Matrix panel advertises in GET /api/capabilities — so it tracks the
        firmware instead of a hard-coded three. Returns None to fall back to the
        manifest's own options (a wall that advertises none, or a physical wall)."""
        if setting.get("options_source") != "effects":
            return None
        try:
            effects = list(self._caps().effects)
        except Exception:
            effects = []
        if not effects:
            return None
        return [{"value": e, "label": self._EFFECT_LABELS.get(e, e.replace("_", " ").title())}
                for e in effects]

    def _field(self, app_id: str, setting: dict, resolved: dict) -> dict:
        raw = setting["key"]
        key = resolved[raw]
        ftype = setting.get("type", "text")

        def map_key(rk):
            return resolved.get(rk) or self._resolve_key(app_id, rk, False)

        # A notice is a block of prose whose content is `text`; it has no label. Using
        # the key as a stand-in when the manifest declares none would print
        # "weatherapi_attribution_notice" at the user (the weather form). Fall back
        # to the key ONLY where a label is actually rendered as a label.
        label = setting.get("label") or ("" if ftype == "notice" else raw)
        field = {"key": key, "label": label, "type": ftype}
        if "options" in setting:
            # options_source (e.g. "effects") overrides the manifest list with what
            # the live wall advertises; falls back to the manifest's own options.
            field["options"] = self._dynamic_options(setting) or setting["options"]
        for pk in _PASSTHROUGH:
            if pk in setting:
                field[pk] = setting[pk]
        if "visible_when" in setting:
            field["visible_when"] = {map_key(k): v for k, v in setting["visible_when"].items()}
        if "inline_toggle" in setting:
            it = dict(setting["inline_toggle"])
            if it.get("key"):
                it["key"] = self._resolve_key(app_id, it["key"])
            field["inline_toggle"] = it
        return field

    def settings_schema(self, app_id: str, lang=None) -> dict:
        manifest = self._registry.get(app_id)
        if not manifest:
            raise KeyError(app_id)
        # Manifest-declared labels can carry translations via the app's i18n
        # sidecar / the central catalog ({"settings": {key: label}}). Catalog
        # (global) labels are chrome strings, translated client-side by t().
        _tr_settings = (self.app_meta_i18n(app_id, self._scan().get(app_id), lang)
                        .get("settings") or {}) if lang else {}
        raw_settings = [s for s in manifest.get("settings", []) if s.get("key")]
        resolved = {
            s["key"]: self._resolve_key(app_id, s["key"])
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
            tr = _tr_settings.get(s["key"])
            if isinstance(tr, str) and tr.strip():
                f["label"] = tr
            elif isinstance(tr, dict):
                if tr.get("label"):
                    f["label"] = tr["label"]
                if tr.get("note"):
                    f["note"] = tr["note"]
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
        wants = self._wants.get(app_id, frozenset())
        if "get_weather" in wants:
            used_global |= set(weather.GLOBAL_KEYS)   # used via the shared weather helper
        if "get_location" in wants:
            used_global |= set(location.GLOBAL_KEYS)  # used via the shared location helper
        if used_global:
            names = ", ".join(CATALOG_BY_KEY[k]["label"]
                              for k in sorted(used_global, key=lambda x: CATALOG_BY_KEY[x]["label"]))
            from . import uilang
            fields.append({"key": f"_globals_note_{app_id}", "type": "notice",
                           "label": uilang.ui_t(lang, "Also uses global settings: %s — set these under Global settings.")
                                    .replace("%s", names)})

        # A DUAL-SURFACE app (surfaces has both flap and matrix) can render on a Matrix panel instead
        # of the plain firmware text of its flap view. One toggle governs it — a functional app draws
        # its own rich view, a channel/quiz its text with a themed icon. It's meaningless without a
        # framebuffer, so it appears ONLY on a Matrix-panel display, and leads the form (it changes
        # what the rest of the settings even apply to).
        if self.is_dual_surface(app_id) and self._caps().has_canvas:
            rich = "art + text" if manifest.get("type") in _CHANNELISH else "rich view"
            fields.insert(0, {
                "key": f"plugin_{app_id}_matrix",
                "label": f"Show on Matrix panel ({rich})",
                "type": "toggle",
                "default": "yes",
                "options": [{"value": "yes", "label": "On"}, {"value": "no", "label": "Off"}],
            })

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

    def _drop_caches(self, app_id: str) -> None:
        """Forget every cached render of this app — the bare key AND the
        override-keyed playlist entries (app_id\\x00...). Popping only the bare
        key would leave a playlist entry showing pre-edit pages until its
        refresh elapsed."""
        prefix = app_id + "\x00"
        for k in [k for k in self._caches if k == app_id or k.startswith(prefix)]:
            self._caches.pop(k, None)
        for k in [k for k in self._first_error if k == app_id or k.startswith(prefix)]:
            self._first_error.pop(k, None)

    def save_settings(self, app_id: str, values: dict) -> None:
        """Store this app's per-app settings. The app dialog only holds
        ``plugin_<id>_*`` keys now — globals live in the Global editor — so only
        those are accepted."""
        if app_id not in self._registry:
            raise KeyError(app_id)
        clean = {k: v for k, v in values.items() if k.startswith(f"plugin_{app_id}_")}
        if clean:
            self.settings.update(clean)
            self._drop_caches(app_id)       # settings changed -> drop cache

    # -- global (shared) settings editor ----------------------------------
    def _global_usage(self, keys) -> dict[str, set[str]]:
        """App IDS that USE each given global key — whether they declare it OR
        just read it in their code (settings.get). For the Global editor's
        'Used by' note. Ids, not names: the caller renders the name, which is
        itself translated (Weather -> Météo)."""
        keys = set(keys)
        usage: dict[str, set[str]] = collections.defaultdict(set)
        for app_id, manifest in self._registry.items():
            name = app_id
            for st in manifest.get("settings", []):
                if st.get("key") in keys:
                    usage[st["key"]].add(name)
            for k in self._reads.get(app_id, {}):
                if k in keys:
                    usage[k].add(name)
            if "get_weather" in self._wants.get(app_id, frozenset()):   # via get_weather
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

    def global_settings_schema(self, lang=None) -> dict:
        """The built-in catalog of well-known reusable global settings — the ONLY
        settings shown in the Global editor. They render from the catalog (so a
        key looks right even if the declaring app isn't installed); a 'Used by'
        note lists the installed apps that declare or read each one.

        The note is ASSEMBLED here (catalog text + the app list), so it cannot be
        translated by the client the way a plain label is — the composed string is
        no catalog key. Both halves are therefore translated here, app names
        included."""
        from . import uilang

        comp_keys = {k for c in CATALOG for k in c.get("_composite", [])}
        usage = self._global_usage(CATALOG_KEYS | comp_keys)
        resolved = {c["key"]: c["key"] for c in CATALOG}
        scan = self._scan()
        fields, values = [], {}

        def app_name(app_id: str) -> str:
            meta = self.app_meta_i18n(app_id, scan.get(app_id), lang) if lang else {}
            return meta.get("name") or \
                (self._registry.get(app_id) or {}).get("name", app_id)

        for c in CATALOG:
            f = self._field("", c, resolved)
            f["label"] = uilang.ui_t(lang, f["label"])
            used_apps = set()
            for k in [c["key"], *c.get("_composite", [])]:
                used_apps |= usage.get(k, set())
            base = uilang.ui_t(lang, c.get("note", "")) if c.get("note") else ""
            used = (uilang.ui_t(lang, "Used by %s")
                    .replace("%s", ", ".join(sorted(app_name(a) for a in used_apps)))
                    if used_apps else "")
            f["note"] = "  ·  ".join(x for x in (base, used) if x)
            for o in f.get("options") or []:
                if isinstance(o, dict) and o.get("label"):
                    o["label"] = uilang.ui_t(lang, o["label"])
            if f.get("ph"):
                f["ph"] = uilang.ui_t(lang, f["ph"])
            fields.append(f)
            if c.get("_composite"):
                values[c["key"]] = self._composite_value(c["_composite"])
            else:
                values[c["key"]] = self.settings.get(c["key"], c.get("default", ""))
        # The localization trio are the settings people reach for first, so pin them to
        # the top in this order — Language, then Location, then Timezone — ahead of the
        # weather/provider fields. A stable sort keeps the catalog order for everything
        # else (which all shares priority 99).
        _TOP = {"language": 0, "zip_code": 1, "location_precise": 2, "timezone": 3}
        fields.sort(key=lambda f: _TOP.get(f.get("key"), 99))
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
            self._gen += 1          # in-flight fetches read the old globals
            self._caches.clear()

    # -- install / uninstall ----------------------------------------------
    def set_installed(self, app_id: str, installed: bool) -> None:
        current = set(self.settings.installed_apps)
        if installed:
            current.add(app_id)
        else:
            current.discard(app_id)
        # Preserve a stable-ish order: keep discovered order. Includes the synthetic
        # per-effect apps, which aren't on disk but are installable all the same.
        ordered = [a for a in self.installable_ids() if a in current]
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
                    # The route caps the COMPRESSED size; a zip bomb is judged by
                    # what it inflates to. In Docker the tempdir is tmpfs — RAM —
                    # so an unchecked extractall is an OOM, not just a full disk.
                    infos = z.infolist()
                    if len(infos) > 512:
                        raise ValueError("zip has too many files (max 512)")
                    total = sum(i.file_size for i in infos)
                    if total > 64 * 1024 * 1024:
                        raise ValueError("zip expands too large (max 64 MB uncompressed)")
                    for i in infos:
                        n = i.filename
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
            elif kind in _CHANNELISH:
                self._validate_channel(root, quiz=(kind == "quiz"))
            self._validate_i18n_sidecars(root)

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
        if manifest.get("type") not in ("functional", "channel", "quiz"):
            raise ValueError("manifest 'type' must be 'functional', 'channel' or 'quiz'")
        settings = manifest.get("settings")
        if settings is not None:
            if not isinstance(settings, list):
                raise ValueError("manifest 'settings' must be a list")
            for i, st in enumerate(settings):
                if not isinstance(st, dict) or not st.get("key"):
                    raise ValueError(f"manifest setting #{i + 1} must be an object with a 'key'")

    @staticmethod
    def _validate_i18n_sidecars(root: Path) -> None:
        """Reject a broken i18n/<lang>.json at upload rather than at render.
        Optional; only files matching the language pattern are held to shape."""
        i18n_dir = root / "i18n"
        if not i18n_dir.is_dir():
            return
        for f in sorted(i18n_dir.glob("*.json")):
            if not I18N_META_FILE.match(f.name):
                continue
            try:
                d = json.loads(f.read_text("utf-8"))
            except Exception as e:
                raise ValueError(f"invalid i18n/{f.name}: {e}")
            if not isinstance(d, dict):
                raise ValueError(f"i18n/{f.name} must be a JSON object")
            for k in _META_STR_KEYS:
                if k in d and not isinstance(d[k], str):
                    raise ValueError(f"i18n/{f.name}: '{k}' must be a string")
            if "settings" in d and not isinstance(d["settings"], dict):
                raise ValueError(f"i18n/{f.name}: 'settings' must be an object")

    @staticmethod
    def _validate_channel(root: Path, quiz: bool = False) -> None:
        dp = root / "data.json"
        if not dp.is_file():
            raise ValueError("channel app is missing data.json")
        # data.json is required (it is the fallback for any language that has no
        # translation); data_<lang>.json sidecars are optional and held to the same
        # shape, so a broken translation is rejected at upload rather than at render.
        files = [dp] + sorted(f for f in root.glob("data_*.json") if LANG_DATA_FILE.match(f.name))
        for f in files:
            try:
                data = json.loads(f.read_text("utf-8"))
            except Exception as e:
                raise ValueError(f"invalid {f.name}: {e}")
            if not isinstance(data, dict) or not (data.get("pages") or data.get("groups")):
                raise ValueError(
                    f"channel app's {f.name} must have a non-empty 'pages' or 'groups' list")
            if quiz:
                # A quiz's every group is a [question, answer] pair — shown as a two-screen reveal.
                for g in data.get("groups") or []:
                    if not (isinstance(g, list) and len(g) == 2
                            and all(isinstance(s, str) and s.strip() for s in g)):
                        raise ValueError(
                            f"quiz app's {f.name}: every group must be a [question, answer] pair")

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
