"""UI (chrome) language resolution.

The flap *content* language is one server-side setting — the wall is shared
hardware. The web UI's chrome has no such constraint, so it is resolved per
request, highest priority first:

    1. ``?lang=fr``                     — whoever crafted the link; that tab only
    2. the global Language setting      — but ONLY once explicitly saved
    3. COMPANION_UI_LANGUAGE env /      — the deployment's default
       ``ui_language`` add-on option
    4. Home Assistant's own language    — the signed-in HA user's profile language,
                                          when we are embedded in HA (see below)
    5. the request's Accept-Language    — the viewer's browser

Any level that is unset (or names a language we don't offer) passes to the
next; the final fallback is en-US. "Explicitly saved" needs care because the
settings store is seeded with ``language: en-US`` — see :func:`setting_is_explicit`.

Level 4 cannot be done on the server: Home Assistant exposes the *system*
language to add-ons at best, never a given user's profile language, and no
ingress header carries it. But the ingress page is served from Home Assistant's
own origin, so the SPA — running inside HA's iframe — can simply read HA's active
language off the parent document (see haLanguage() in app.js). That is per-user
and exact. The server therefore reports whether levels 1–3 already decided the
matter (``locked``): if they did, the client leaves it alone; if they didn't, the
client may substitute Home Assistant's language for the browser's guess.

Whatever wins here names a *catalog*; the catalogs themselves degrade exact
locale -> base language -> English (see loadI18n in app.js), the same chain the
channel apps use for their data_<lang>.json sidecars.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import i18n

DEFAULT = "en-US"

# What the Language setting offers (values like "en-US", "fr", "pt-BR").
OFFERED: list[str] = [o["value"] for o in i18n.LANGUAGE_OPTIONS] or [DEFAULT]
_OFFERED_LC = {o.lower(): o for o in OFFERED}
_OFFERED_BASE = {}  # base -> first offered code with that base ("fr" -> "fr")
for _o in OFFERED:
    _OFFERED_BASE.setdefault(_o.split("-")[0].lower(), _o)


def normalize(code) -> str | None:
    """A raw language code -> the offered code it names, or None.

    Exact match wins (case-insensitive, ``_``/``-`` agnostic); otherwise the
    base language matches any offered variant of it, in both directions:
    browser ``de-DE`` -> offered ``de``, browser ``fr`` -> offered ``fr``.
    """
    if not code:
        return None
    c = str(code).strip().replace("_", "-")
    if not c:
        return None
    if c.lower() in _OFFERED_LC:
        return _OFFERED_LC[c.lower()]
    return _OFFERED_BASE.get(c.split("-")[0].lower())


def parse_accept_language(header: str | None) -> list[str]:
    """An Accept-Language header -> language codes, best first."""
    if not header:
        return []
    items = []
    for i, part in enumerate(header.split(",")):
        bits = part.strip().split(";")
        code = bits[0].strip()
        if not code or code == "*":
            continue
        q = 1.0
        for b in bits[1:]:
            b = b.strip()
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        if q > 0:
            items.append((-q, i, code))
    return [c for _, _, c in sorted(items)]


def setting_is_explicit(settings) -> bool:
    """Whether the global Language setting was actually chosen by a person.

    The store is seeded with ``language: en-US`` and persists catalog keys
    wholesale, so presence proves nothing. Explicit means: the
    ``language_explicit`` flag was set when the Language control was saved —
    or, for installs that predate the flag, the stored language differs from
    the default (nobody gets a non-default value by accident).
    """
    if not settings:
        return False
    if settings.get("language_explicit"):
        return True
    saved = settings.get("language")
    return bool(saved) and saved != DEFAULT


def resolve_locked(query_lang, settings, env_lang) -> str | None:
    """Levels 1-3 only: an explicit choice that no client-side signal may override.
    None when nothing at that level applies (so HA's language, then the browser,
    get their say)."""
    hit = normalize(query_lang)
    if hit:
        return hit
    if settings and setting_is_explicit(settings):
        hit = normalize(settings.get("language"))
        if hit:
            return hit
    return normalize(env_lang)


def resolve(query_lang, settings, env_lang, accept_language) -> str:
    """The UI language for one request, server-side (levels 1-3, then the browser).
    The client may still upgrade the browser's guess to Home Assistant's language
    when it isn't locked — see resolve_locked and haLanguage() in app.js."""
    hit = resolve_locked(query_lang, settings, env_lang)
    if hit:
        return hit
    for code in parse_accept_language(accept_language):
        hit = normalize(code)
        if hit:
            return hit
    return DEFAULT


# ---------------------------------------------------------------------------
# Server-side access to the SPA's own string catalogs (static/i18n/<lang>.json),
# for the rare chrome string the SERVER assembles (e.g. the "also uses global
# settings" notice in a settings schema). Same key convention: English in, the
# translation (or the English itself) out.
_CATALOG_DIR = Path(__file__).parent / "static" / "i18n"
_catalog_cache: dict[str, dict] = {}


def _catalog(code: str) -> dict:
    if code not in _catalog_cache:
        try:
            _catalog_cache[code] = json.loads((_CATALOG_DIR / f"{code}.json").read_text("utf-8"))
        except Exception:
            _catalog_cache[code] = {}
    return _catalog_cache[code]


def ui_t(lang, key: str) -> str:
    """Translate one chrome string server-side (exact locale, then base)."""
    code = str(lang or "").replace("_", "-")
    base = code.split("-")[0].lower()
    if not base or base == "en":
        return key
    for c in dict.fromkeys([code, base]):
        hit = _catalog(c).get(key)
        if isinstance(hit, str) and hit:
            return hit
    return key
