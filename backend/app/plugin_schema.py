"""plugin_schema.py — how an app's settings render in the browser.

Everything that turns manifests + the global catalog into the settings-dialog schema the
UI (and the MCP projection) consumes: per-field construction, the per-app dialog
(``settings_schema``), the Global editor (``global_settings_schema``), inference for
settings an app reads but never declares, and the composite location-chip codec. Running
the app is plugins.py's job; this module only *describes settings*.

``rt`` in these functions is the PluginRuntime — the same object ``self`` names in the
thin delegating methods plugins.py keeps for compatibility.
"""

from __future__ import annotations

import collections

from . import location, uilang, weather
from .catalog import CATALOG, CATALOG_BY_KEY, CATALOG_KEYS, GLOBAL_STORAGE_KEYS
from .plugin_effects import effect_def_for, effect_name_icon


def dynamic_options(rt, setting: dict):
    """Options a setting draws from the LIVE wall instead of the manifest.

    With ``"options_source": "effects"`` the picker offers exactly the effects
    THIS Matrix panel advertises in GET /api/capabilities — so it tracks the
    firmware instead of a hard-coded three. Returns None to fall back to the
    manifest's own options (a wall that advertises none, or a physical wall)."""
    if setting.get("options_source") != "effects":
        return None
    try:
        effects = list(rt._caps().effects)
    except Exception:
        effects = []
    if not effects:
        return None

    def label(e):
        d = effect_def_for(rt, e)
        if d and d.get("name"):
            return str(d["name"])
        return effect_name_icon(e)[0]
    return [{"value": e, "label": label(e)} for e in effects]


def field(rt, app_id: str, setting: dict, resolved: dict) -> dict:
    from .plugins import _PASSTHROUGH

    raw = setting["key"]
    key = resolved[raw]
    ftype = setting.get("type", "text")

    def map_key(rk):
        return resolved.get(rk) or rt._resolve_key(app_id, rk)

    # A notice is a block of prose whose content is `text`; it has no label. Using
    # the key as a stand-in when the manifest declares none would print
    # "weatherapi_attribution_notice" at the user (the weather form). Fall back
    # to the key ONLY where a label is actually rendered as a label.
    label = setting.get("label") or ("" if ftype == "notice" else raw)
    f = {"key": key, "label": label, "type": ftype}
    if "options" in setting:
        # options_source (e.g. "effects") overrides the manifest list with what
        # the live wall advertises; falls back to the manifest's own options.
        f["options"] = dynamic_options(rt, setting) or setting["options"]
    for pk in _PASSTHROUGH:
        if pk in setting:
            f[pk] = setting[pk]
    if "visible_when" in setting:
        f["visible_when"] = {map_key(k): v for k, v in setting["visible_when"].items()}
    if "inline_toggle" in setting:
        it = dict(setting["inline_toggle"])
        if it.get("key"):
            it["key"] = rt._resolve_key(app_id, it["key"])
        f["inline_toggle"] = it
    return f


def settings_schema(rt, app_id: str, lang=None) -> dict:
    from .plugins import _CHANNELISH

    manifest = rt._registry.get(app_id)
    if not manifest:
        raise KeyError(app_id)
    # Manifest-declared labels can carry translations via the app's i18n
    # sidecar / the central catalog ({"settings": {key: label}}). Catalog
    # (global) labels are chrome strings, translated client-side by t().
    _tr_settings = (rt.app_meta_i18n(app_id, rt._scan().get(app_id), lang)
                    .get("settings") or {}) if lang else {}
    raw_settings = [s for s in manifest.get("settings", []) if s.get("key")]
    resolved = {
        s["key"]: rt._resolve_key(app_id, s["key"])
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
    if rt._uses_location(app_id) and not any(s["key"] == "location" for s in raw_settings):
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
        f = field(rt, app_id, s, resolved)
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
    for key, default in rt._reads.get(app_id, {}).items():
        if key in declared_keys or key in GLOBAL_STORAGE_KEYS or key == "currency_symbol":
            continue
        fields.append(infer_field(rt._resolve_key(app_id, key), key, default))

    # Point to the reusable globals this app uses (edited under Global settings).
    used_global = set()
    for k in declared_keys | set(rt._reads.get(app_id, {})):
        if k in CATALOG_KEYS:
            used_global.add(k)
        for c in CATALOG:
            if k in c.get("_composite", []):
                used_global.add(c["key"])
    wants = rt._wants.get(app_id, frozenset())
    if "get_weather" in wants:
        used_global |= set(weather.GLOBAL_KEYS)   # used via the shared weather helper
    if "get_location" in wants:
        used_global |= set(location.GLOBAL_KEYS)  # used via the shared location helper
    if used_global:
        names = ", ".join(CATALOG_BY_KEY[k]["label"]
                          for k in sorted(used_global, key=lambda x: CATALOG_BY_KEY[x]["label"]))
        fields.append({"key": f"_globals_note_{app_id}", "type": "notice",
                       "label": uilang.ui_t(lang, "Also uses global settings: %s — set these under Global settings.")
                                .replace("%s", names)})

    # A DUAL-SURFACE app (surfaces has both flap and matrix) can render on a Matrix panel instead
    # of the plain firmware text of its flap view. One toggle governs it — a functional app draws
    # its own rich view, a channel/quiz its text with a themed icon. It's meaningless without a
    # framebuffer, so it appears ONLY on a Matrix-panel display, and leads the form (it changes
    # what the rest of the settings even apply to).
    if rt.is_dual_surface(app_id) and rt._caps().has_canvas:
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
        values[rk] = rt.settings.get(rk, s.get("default", ""))
        it = s.get("inline_toggle")
        if it and it.get("key"):
            ik = rt._resolve_key(app_id, it["key"])
            values[ik] = rt.settings.get(ik, it.get("default", ""))
    for f in fields:
        values.setdefault(f["key"], rt.settings.get(f["key"], f.get("default", "")))
    return {
        "id": app_id,
        "name": manifest.get("name", app_id),
        "icon": manifest.get("icon", "🧩"),
        "fields": fields,
        "values": values,
    }


def global_usage(rt, keys) -> dict[str, set[str]]:
    """App IDS that USE each given global key — whether they declare it OR
    just read it in their code (settings.get). For the Global editor's
    'Used by' note. Ids, not names: the caller renders the name, which is
    itself translated (Weather -> Météo)."""
    keys = set(keys)
    usage: dict[str, set[str]] = collections.defaultdict(set)
    for app_id, manifest in rt._registry.items():
        name = app_id
        for st in manifest.get("settings", []):
            if st.get("key") in keys:
                usage[st["key"]].add(name)
        for k in rt._reads.get(app_id, {}):
            if k in keys:
                usage[k].add(name)
        if "get_weather" in rt._wants.get(app_id, frozenset()):   # via get_weather
            for wk in weather.GLOBAL_KEYS:
                if wk in keys:
                    usage[wk].add(name)
    return usage


def infer_field(key: str, raw_key: str, default) -> dict:
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


def global_settings_schema(rt, lang=None) -> dict:
    """The built-in catalog of well-known reusable global settings — the ONLY
    settings shown in the Global editor. They render from the catalog (so a
    key looks right even if the declaring app isn't installed); a 'Used by'
    note lists the installed apps that declare or read each one.

    The note is ASSEMBLED here (catalog text + the app list), so it cannot be
    translated by the client the way a plain label is — the composed string is
    no catalog key. Both halves are therefore translated here, app names
    included."""
    comp_keys = {k for c in CATALOG for k in c.get("_composite", [])}
    usage = global_usage(rt, CATALOG_KEYS | comp_keys)
    resolved = {c["key"]: c["key"] for c in CATALOG}
    scan = rt._scan()
    fields, values = [], {}

    def app_name(app_id: str) -> str:
        meta = rt.app_meta_i18n(app_id, scan.get(app_id), lang) if lang else {}
        return meta.get("name") or \
            (rt._registry.get(app_id) or {}).get("name", app_id)

    for c in CATALOG:
        f = field(rt, "", c, resolved)
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
            values[c["key"]] = composite_value(rt, c["_composite"])
        else:
            values[c["key"]] = rt.settings.get(c["key"], c.get("default", ""))
    # The localization trio are the settings people reach for first, so pin them to
    # the top in this order — Language, then Location, then Timezone — ahead of the
    # weather/provider fields. A stable sort keeps the catalog order for everything
    # else (which all shares priority 99).
    _TOP = {"language": 0, "zip_code": 1, "location_precise": 2, "timezone": 3}
    fields.sort(key=lambda f: _TOP.get(f.get("key"), 99))
    return {"fields": fields, "values": values}


def composite_value(rt, comp: list) -> str:
    """Rebuild a location search chip value (``lat,lon|name``) from its stored
    component keys, or '' when no coordinates are set."""
    lat = rt.settings.get(comp[0], "")
    lon = rt.settings.get(comp[1], "")
    name = rt.settings.get(comp[2], "") if len(comp) > 2 else ""
    return f"{lat},{lon}|{name}" if (lat and lon) else ""


def parse_composite(comp: list, value) -> dict:
    """Split a ``lat,lon|name`` chip value into its component keys (empty
    value clears them)."""
    coords, _, name = str(value or "").partition("|")
    lat, _, lon = coords.partition(",")
    out = {comp[0]: lat.strip(), comp[1]: lon.strip()}
    if len(comp) > 2:
        out[comp[2]] = name.strip()
    return out
