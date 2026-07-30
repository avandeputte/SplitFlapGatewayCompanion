"""plugin_effects.py — the one-app-per-effect synthesis.

The single ``effects`` template app on disk is presented as ONE installable app per
on-device effect the wall advertises (Plasma, Fire, Matrix Rain…). This module derives
those synthetic apps: their ids, names/icons, and manifests — including the knob fields,
which come from the wall's own effect self-description (fw 3.4 ``effectDefs``) when it
has one. The loader side (registering the synthetic entries against the shared module)
stays in plugins.PluginRuntime._load_effects; everything here is pure derivation.

``rt`` in these functions is the PluginRuntime — the same object ``self`` names in the
thin delegating methods plugins.py keeps for compatibility.
"""

from __future__ import annotations

# Friendly name + icon per on-device effect, for the one-app-per-effect split. An effect a
# future firmware adds still gets an app, named from its own token.
EFFECT_META = {
    "plasma": ("Plasma", "🌀"), "fire": ("Fire", "🔥"), "matrix": ("Matrix Rain", "💚"),
    "fliporama": ("Flip-o-rama", "🎞️"), "clock": ("Panel Clock", "🕛"),
    "life": ("Game of Life", "🦠"), "rainbow": ("Rainbow", "🌈"),
    "spectrum": ("Spectrum", "🎚️"), "soundwall": ("Soundwall", "🔊"),
    "maze": ("Maze", "🌀"), "ripple": ("Beat Ripples", "🌊"), "scope": ("Oscilloscope", "📈"),
    "spectro": ("Spectrogram", "📊"),
}

# The knob fields the effects template declares; replaced wholesale when the wall
# describes its own (effectDefs).
EFFECT_KNOB_KEYS = ("effect", "speed", "hue", "density")


def effect_name_icon(token: str):
    # Effect names get a friendlier label; anything the firmware adds later is
    # title-cased automatically (new_effect -> "New Effect").
    return EFFECT_META.get(token, (token.replace("_", " ").title(), "🎆"))


def effect_defs(rt) -> list[tuple[str, str]]:
    """``(effect_id, token)`` for each on-device effect THIS wall advertises — one app
    each. Empty on a wall with no effects (a physical wall, or caps not yet known) or
    when the ``effects`` template app isn't on disk."""
    if "effects" not in rt._scan():
        return []
    try:
        effects = list(rt._caps().effects)
    except Exception:
        effects = []
    return [(f"effect_{e}", e) for e in effects if e]


def effect_def_for(rt, token: str) -> dict | None:
    """This wall's self-description of one effect (fw 3.4 ``effectDefs``), or None on
    older firmware — callers fall back to the template's fixed knobs."""
    try:
        for d in rt._caps().effect_defs:
            if d.get("id") == token:
                return d
    except Exception:
        pass
    return None


def effect_def_fields(d: dict) -> list[dict]:
    """Settings fields straight from an effect's def: an int param becomes a number
    field with the declared range, a bool a toggle; labels shown verbatim. A param of
    an unknown type gets no field (and the app skips it on the wire) — a future
    firmware type degrades to the effect's on-device default, never an error."""
    fields = []
    for pd in d.get("params") or []:
        key, typ = str(pd.get("key") or ""), str(pd.get("type") or "")
        if not key:
            continue
        label = str(pd.get("label") or key.replace("_", " ").title())
        if typ == "int":
            f = {"key": key, "label": label, "type": "number", "step": "1",
                 "default": "" if pd.get("default") is None else str(pd["default"])}
            if pd.get("min") is not None:
                f["min"] = str(pd["min"])
            if pd.get("max") is not None:
                f["max"] = str(pd["max"])
            fields.append(f)
        elif typ == "bool":
            fields.append({"key": key, "label": label, "type": "toggle",
                           "default": "yes" if pd.get("default") else "no",
                           "options": [{"value": "yes", "label": "On"},
                                       {"value": "no", "label": "Off"}]})
    return fields


def effect_manifest(rt, effect_id: str, token: str) -> dict:
    """A per-effect app's manifest, derived from the shared ``effects`` manifest: the
    effect PICKER is dropped (the app IS one effect, pinned via ``pinned_effect``).
    On a wall that describes its effects (fw 3.4 ``effectDefs``) the knob fields come
    from the effect's own def — exactly the params it consumes, named by the firmware;
    on older firmware the template's speed/hue/density stay."""
    from .plugins import _read_json_cached

    d = rt._scan().get("effects")
    tmpl = dict(_read_json_cached(d / "manifest.json")) if d else {}
    name, icon = effect_name_icon(token)
    m = dict(tmpl)
    m["id"] = effect_id
    edef = effect_def_for(rt, token)
    if edef and edef.get("name"):
        name = str(edef["name"])                     # the firmware owns effect naming
    m["name"] = name
    m["icon"] = icon
    m["description"] = f"The {name} effect, rendered on the panel itself"
    m["pinned_effect"] = token
    if edef is not None:
        others = [s for s in tmpl.get("settings", [])
                  if s.get("key") not in EFFECT_KNOB_KEYS]
        m["settings"] = effect_def_fields(edef) + others
    else:
        m["settings"] = [s for s in tmpl.get("settings", []) if s.get("key") != "effect"]
    return m
