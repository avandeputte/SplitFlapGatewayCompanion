"""
i18n.py — shared localization for apps.

Two pieces:
  * CLDR-correct day / month names via babel — authoritative for every language.
  * A small curated table of the UI words apps show (SUNRISE, FULL MOON, DAYS …),
    translated into the major Western-European languages. Anything without an
    entry falls back to the English key, so nothing breaks.

The runtime binds this to the global Language setting and injects it into any app
whose ``fetch()`` declares an ``i18n`` parameter (like ``get_weather``); off a
companion host the app just falls back to English. Translations stay in one place
instead of being copied into every self-contained app.
"""

from __future__ import annotations

# Curated UI strings. Key = the English UPPERCASE word an app already uses; value
# = {lang: translation}. Only the well-supported languages are filled in; any
# missing (lang, key) falls back to the English key. Accents are kept — the
# modules render the Windows-1252 code page directly.
_STRINGS: dict[str, dict[str, str]] = {
    # sun-times
    "SUNRISE":  {"fr": "LEVER", "de": "AUFGANG", "es": "AMANECER", "it": "ALBA", "pt": "NASCER", "nl": "OPKOMST"},
    "SUNSET":   {"fr": "COUCHER", "de": "UNTERGANG", "es": "OCASO", "it": "TRAMONTO", "pt": "OCASO", "nl": "ONDERGANG"},
    "DAYLIGHT": {"fr": "JOUR", "de": "TAGESLICHT", "es": "LUZ DIA", "it": "LUCE", "pt": "LUZ DIA", "nl": "DAGLICHT"},
    "UP":       {"fr": "LEV", "de": "AUF", "es": "SAL", "it": "ALBA", "pt": "NASC", "nl": "OP"},
    "DN":       {"fr": "COU", "de": "UNT", "es": "OCA", "it": "TRAM", "pt": "OCA", "nl": "OND"},
    # moon-phase
    "NEW MOON":        {"fr": "NOUVELLE LUNE", "de": "NEUMOND", "es": "LUNA NUEVA", "it": "LUNA NUOVA", "pt": "LUA NOVA", "nl": "NIEUWE MAAN"},
    "FULL MOON":       {"fr": "PLEINE LUNE", "de": "VOLLMOND", "es": "LUNA LLENA", "it": "LUNA PIENA", "pt": "LUA CHEIA", "nl": "VOLLE MAAN"},
    "FIRST QUARTER":   {"fr": "1ER QUARTIER", "de": "1. VIERTEL", "es": "CUARTO CREC.", "it": "PRIMO QUARTO", "pt": "QUARTO CRESC.", "nl": "EERSTE KWART."},
    "LAST QUARTER":    {"fr": "DERN. QUARTIER", "de": "LETZT. VIERTEL", "es": "CUARTO MENG.", "it": "ULTIMO QUARTO", "pt": "QUARTO MING.", "nl": "LAATSTE KWART."},
    "WAXING CRESCENT": {"fr": "1ER CROISSANT", "de": "ZUN. SICHEL", "es": "CRECIENTE", "it": "CRESCENTE", "pt": "CRESCENTE", "nl": "WASSEND"},
    "WANING CRESCENT": {"fr": "DERN. CROISSANT", "de": "ABN. SICHEL", "es": "MENGUANTE", "it": "CALANTE", "pt": "MINGUANTE", "nl": "AFNEMEND"},
    "WAXING GIBBOUS":  {"fr": "GIBBEUSE CROIS.", "de": "ZUN. MOND", "es": "GIBOSA CREC.", "it": "GIBBOSA CRESC.", "pt": "GIBOSA CRESC.", "nl": "WASSEND"},
    "WANING GIBBOUS":  {"fr": "GIBBEUSE DECR.", "de": "ABN. MOND", "es": "GIBOSA MENG.", "it": "GIBBOSA CAL.", "pt": "GIBOSA MING.", "nl": "AFNEMEND"},
    "LIT":     {"fr": "ECLAIRE", "de": "HELL", "es": "ILUM.", "it": "ILLUM.", "pt": "ILUM.", "nl": "VERL."},
    "FULL IN": {"fr": "PLEINE DANS", "de": "VOLL IN", "es": "LLENA EN", "it": "PIENA IN", "pt": "CHEIA EM", "nl": "VOL OVER"},
    "NEW IN":  {"fr": "NOUV. DANS", "de": "NEU IN", "es": "NUEVA EN", "it": "NUOVA IN", "pt": "NOVA EM", "nl": "NIEUW OVER"},
    # countdown / durations
    "DAYS":    {"fr": "JOURS", "de": "TAGE", "es": "DIAS", "it": "GIORNI", "pt": "DIAS", "nl": "DAGEN"},
    "HOURS":   {"fr": "HEURES", "de": "STD", "es": "HORAS", "it": "ORE", "pt": "HORAS", "nl": "UUR"},
    "MINS":    {"fr": "MIN", "de": "MIN", "es": "MIN", "it": "MIN", "pt": "MIN", "nl": "MIN"},
    "LEFT":    {"fr": "RESTE", "de": "UBRIG", "es": "QUEDAN", "it": "MANCA", "pt": "FALTAM", "nl": "OVER"},
    "REMAINING": {"fr": "RESTANT", "de": "VERBLEIB.", "es": "RESTANTE", "it": "RIMANENTE", "pt": "RESTANTE", "nl": "RESTEREND"},
    "ARRIVED": {"fr": "ARRIVE", "de": "DA", "es": "LLEGO", "it": "ARRIVATO", "pt": "CHEGOU", "nl": "AANGEKOMEN"},
    "HERE":    {"fr": "ICI", "de": "HIER", "es": "AQUI", "it": "QUI", "pt": "AQUI", "nl": "HIER"},
    "CELEBRATE": {"fr": "FETEZ", "de": "FEIERN", "es": "CELEBRA", "it": "FESTA", "pt": "FESTA", "nl": "VIER"},
    "PARTY":   {"fr": "FETE", "de": "PARTY", "es": "FIESTA", "it": "FESTA", "pt": "FESTA", "nl": "FEEST"},
    "TODAY":   {"fr": "AUJOURDHUI", "de": "HEUTE", "es": "HOY", "it": "OGGI", "pt": "HOJE", "nl": "VANDAAG"},
    "NOW":     {"fr": "MAINTENANT", "de": "JETZT", "es": "AHORA", "it": "ORA", "pt": "AGORA", "nl": "NU"},
}


def translate(text, lang):
    """English UI string -> localized (or the English key if unknown/English)."""
    if not lang or lang.lower().startswith("en"):
        return text
    return _STRINGS.get(text, {}).get(lang.lower(), text)


def _cldr(dt, fmt, lang):
    try:
        from babel.dates import format_date
        return format_date(dt, fmt, locale=lang).upper()
    except Exception:
        return None


def weekday(dt, lang, short=False):
    return _cldr(dt, "EEE" if short else "EEEE", lang) or dt.strftime("%a" if short else "%A").upper()


def month(dt, lang, short=False):
    return _cldr(dt, "MMM" if short else "MMMM", lang) or dt.strftime("%b" if short else "%B").upper()


class Localizer:
    """Language-bound convenience wrapper handed to apps as ``i18n``."""

    def __init__(self, lang):
        self.lang = (lang or "en").lower()

    def t(self, text):
        return translate(text, self.lang)

    def weekday(self, dt, short=False):
        return weekday(dt, self.lang, short)

    def month(self, dt, short=False):
        return month(dt, self.lang, short)
