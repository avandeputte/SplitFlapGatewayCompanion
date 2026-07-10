"""
i18n.py — shared localization for apps.

Two pieces:
  * CLDR-correct day / month names via babel — authoritative for every language.
  * A curated table of the UI words apps show (SUNRISE, FULL MOON, DAYS …),
    translated into the supported languages. Anything without an entry falls back to
    the English key, so nothing breaks.

All data — the selectable ``LANGUAGE_OPTIONS`` list, translations, and the default
currency/country per language — is loaded from ``i18n_data.json`` (see that file's
per-key context notes). Regional variants inherit their base language: ``pt-BR``
reuses every ``pt`` translation but resolves to Brazil / BRL.

The runtime binds this to the global Language setting and injects it into any app
whose ``fetch()`` declares an ``i18n`` parameter (like ``get_weather``); on a host
without the helper the app just falls back to English. Translations stay in one
place instead of being copied into every self-contained app.
"""

from __future__ import annotations

import json
import os

# All localization data — the selectable language list, curated UI strings,
# duration-unit suffixes, holiday-name translations, and the default currency/country
# a language implies — lives in i18n_data.json so it can be edited, corrected, or
# extended with a whole new language without touching this module. A missing or
# invalid file degrades gracefully, so the lookups below just return their
# English/None defaults and apps keep working in English.
_DATA_PATH = os.path.join(os.path.dirname(__file__), "i18n_data.json")

# Minimal fallback so the Language selector is never empty if the data file is
# missing/broken (the runtime still works, just English-only).
_DEFAULT_LANGUAGES = [{"value": "en-US", "label": "English (US)"}]


def _translations(section):
    """Flatten a data section into the runtime ``{key: {lang: value}}`` lookup. Each
    entry in the file is ``{"context": "...", "translations": {lang: value}}`` (the
    context note is for translators only); a bare ``{lang: value}`` is also accepted."""
    out = {}
    for key, entry in (section or {}).items():
        if isinstance(entry, dict) and "translations" in entry:
            out[key] = entry["translations"]
        elif isinstance(entry, dict):
            out[key] = entry
    return out


def _load_i18n_data():
    try:
        with open(_DATA_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    return (_translations(data.get("strings")),
            _translations(data.get("duration_units")),
            _translations(data.get("holidays")),
            data.get("base_currency", {}),
            data.get("country", {}),
            data.get("languages") or _DEFAULT_LANGUAGES)


(_STRINGS, _DURATION_UNITS, _HOLIDAYS, _BASE_CURRENCY, _COUNTRY,
 LANGUAGE_OPTIONS) = _load_i18n_data()


def _base_lang(lang):
    """The base language subtag: ``pt-BR`` / ``pt_BR`` -> ``pt``, ``de`` -> ``de``."""
    return str(lang or "").replace("_", "-").split("-")[0].lower()


def _localized(table, key, lang, default):
    """Look up ``key`` in a ``{lang: value}`` table, trying the full language code
    first and then its base subtag, so a regional variant (``pt-BR``) inherits the
    base language (``pt``) wherever it has no entry of its own."""
    variants = table.get(key)
    if not variants:
        return default
    code = str(lang or "").lower()
    if code in variants:
        return variants[code]
    base = _base_lang(lang)
    if base in variants:
        return variants[base]
    return default


def translate(text, lang):
    """English UI string -> localized (or the English key if unknown/English)."""
    if not lang or _base_lang(lang) == "en":
        return text
    return _localized(_STRINGS, text, lang, text)


def _babel_locale(lang):
    """Our language code -> a babel locale id. English carries a region that changes
    date order (US 'July 9' vs UK/AU '9 July'): 'en-GB' -> 'en_GB', 'en'/'en-US' ->
    'en_US'. Other languages just use their base code ('fr', 'de', …)."""
    if not lang:
        return "en_US"
    parts = str(lang).replace("-", "_").split("_")
    base = parts[0].lower()
    if base == "en":
        region = parts[1].upper() if len(parts) > 1 and parts[1] else "US"
        return f"en_{region}"
    return base


def _cldr(dt, fmt, lang):
    try:
        from babel.dates import format_date
        return format_date(dt, fmt, locale=_babel_locale(lang)).upper()
    except Exception:
        return None


def weekday(dt, lang, short=False):
    return _cldr(dt, "EEE" if short else "EEEE", lang) or dt.strftime("%a" if short else "%A").upper()


def month(dt, lang, short=False):
    return _cldr(dt, "MMM" if short else "MMMM", lang) or dt.strftime("%b" if short else "%B").upper()


def date(dt, lang, short=False, year=False):
    """Day + month (optionally year) in the locale's own order and wording:
    ``JULY 9`` (en) but ``9 JUILLET`` (fr), ``9. JULI`` (de), ``9 DE JULIO`` (es)."""
    skeleton = ("MMM" if short else "MMMM") + "d" + ("y" if year else "")
    try:
        from babel.dates import format_skeleton
        return format_skeleton(skeleton, dt, locale=_babel_locale(lang)).upper()
    except Exception:
        base = f"{month(dt, lang, short)} {dt.day}"
        return f"{base} {dt.year}" if year else base


def duration_unit(key, lang):
    """Localized single-letter duration suffix (D/H/M/S) -> e.g. J/H/M/S in French."""
    if not lang or _base_lang(lang) == "en":
        return key
    return _localized(_DURATION_UNITS, key, lang, key)


def uses_24h(lang):
    """AM/PM is essentially an English-language convention; everyone else is 24h."""
    return bool(lang) and _base_lang(lang) != "en"


# Non-ASCII group separators CLDR uses (French narrow/no-break spaces) — the flap
# display only speaks Windows-1252, so we fold them back to a plain space.
_GROUP_SPACES = ("\u202f", "\u00a0", "\u2009")


def number(value, lang, decimals=2, grouping=True):
    """Format a number with the locale's own separators: 1,234.5 (en) vs 1.234,5
    (de/es/it/…) vs 1 234,5 (fr). Falls back to English grouping without babel."""
    try:
        from babel.numbers import format_decimal
        pattern = ("#,##0" if grouping else "0") + ("." + "0" * decimals if decimals > 0 else "")
        s = format_decimal(float(value), format=pattern, locale=_babel_locale(lang))
    except Exception:
        try:
            s = f"{float(value):,.{decimals}f}" if grouping else f"{float(value):.{decimals}f}"
        except Exception:
            return str(value)
    for ch in _GROUP_SPACES:
        s = s.replace(ch, " ")
    return s


def base_currency(lang):
    """The "home" currency a language/region implies (the default base for FX): US ->
    USD, UK -> GBP, Brazil -> BRL, the rest of Western Europe -> EUR. From
    i18n_data.json; a regional variant (``pt-BR``) wins over its base (``pt``), and
    unknown languages default to USD. Users can override explicitly."""
    code = str(lang or "en").lower()
    return _BASE_CURRENCY.get(code) or _BASE_CURRENCY.get(_base_lang(lang or "en"), "USD")


def country(lang):
    """The country a language/region implies — for holidays (which calendar to show)
    and other country-scoped data. Regional variants split by country (``pt-BR`` ->
    BR, ``pt`` -> PT); from i18n_data.json, unknown languages default to US."""
    code = str(lang or "en").lower()
    return _COUNTRY.get(code) or _COUNTRY.get(_base_lang(lang or "en"), "US")


def holiday(name, lang):
    """Localized public-holiday name, or None if we have no translation (caller
    then keeps the source's native name)."""
    if not lang or not name:
        return None
    return _localized(_HOLIDAYS, str(name).strip().lower(), lang, None)


def clock(dt, lang, seconds=False, ampm_space=True):
    """Locale-appropriate wall-clock time: ``3:48 PM`` in English, ``15:48`` elsewhere."""
    if uses_24h(lang):
        return dt.strftime("%H:%M:%S" if seconds else "%H:%M")
    body = dt.strftime("%I:%M:%S" if seconds else "%I:%M").lstrip("0")
    sep = " " if ampm_space else ""
    return f"{body}{sep}{dt.strftime('%p')}"


class Localizer:
    """Language-bound convenience wrapper handed to apps as ``i18n``."""

    def __init__(self, lang):
        self.lang = (lang or "en").lower()

    @property
    def is_24h(self):
        return uses_24h(self.lang)

    def t(self, text):
        return translate(text, self.lang)

    def weekday(self, dt, short=False):
        return weekday(dt, self.lang, short)

    def month(self, dt, short=False):
        return month(dt, self.lang, short)

    def date(self, dt, short=False, year=False):
        return date(dt, self.lang, short, year)

    def time(self, dt, seconds=False, ampm_space=True):
        return clock(dt, self.lang, seconds, ampm_space)

    def unit(self, key):
        return duration_unit(key, self.lang)

    def number(self, value, decimals=2, grouping=True):
        return number(value, self.lang, decimals, grouping)

    def base_currency(self):
        return base_currency(self.lang)

    def country(self):
        return country(self.lang)

    def holiday(self, name):
        return holiday(name, self.lang_base)

    @property
    def lang_base(self):
        """The 2-letter language without region ('en-GB' -> 'en'), for APIs that
        want a plain language code (Wikipedia editions, weather providers, …)."""
        return self.lang.split("-")[0]
