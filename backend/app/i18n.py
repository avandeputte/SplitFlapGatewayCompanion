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
    # weather — condition text (the keyless Open-Meteo provider maps codes to these;
    # the keyed providers already return localized text from their own API).
    "CLEAR":          {"fr": "DEGAGE", "de": "KLAR", "es": "DESPEJADO", "it": "SERENO", "pt": "LIMPO", "nl": "HELDER"},
    "MAINLY CLEAR":   {"fr": "PLUTOT CLAIR", "de": "MEIST KLAR", "es": "MAYORM. CLARO", "it": "POCO NUVOLOSO", "pt": "QUASE LIMPO", "nl": "VNL. HELDER"},
    "PARTLY CLOUDY":  {"fr": "NUAGEUX", "de": "TEILS WOLKIG", "es": "PARC. NUBLADO", "it": "POCO NUVOLOSO", "pt": "PARC. NUBLADO", "nl": "HALF BEWOLKT"},
    "OVERCAST":       {"fr": "COUVERT", "de": "BEDECKT", "es": "CUBIERTO", "it": "COPERTO", "pt": "ENCOBERTO", "nl": "BEWOLKT"},
    "FOG":            {"fr": "BROUILLARD", "de": "NEBEL", "es": "NIEBLA", "it": "NEBBIA", "pt": "NEVOEIRO", "nl": "MIST"},
    "RIME FOG":       {"fr": "BRUME GIVRANTE", "de": "RAUREIF", "es": "NIEBLA HELADA", "it": "NEBBIA GHIAC.", "pt": "NEVOA GELADA", "nl": "RIJP-MIST"},
    "LIGHT DRIZZLE":  {"fr": "BRUINE LEGERE", "de": "LEICHT NIESEL", "es": "LLOVIZNA LEVE", "it": "PIOVIGGINE", "pt": "CHUVISCO FRACO", "nl": "LICHTE MOTREG"},
    "DRIZZLE":        {"fr": "BRUINE", "de": "NIESELN", "es": "LLOVIZNA", "it": "PIOVIGGINE", "pt": "CHUVISCO", "nl": "MOTREGEN"},
    "HEAVY DRIZZLE":  {"fr": "BRUINE FORTE", "de": "STARK. NIESEL", "es": "LLOVIZNA FTE", "it": "PIOVIGGINE FT", "pt": "CHUVISCO FORTE", "nl": "ZWARE MOTREG"},
    "LIGHT RAIN":     {"fr": "PLUIE LEGERE", "de": "LEICHT. REGEN", "es": "LLUVIA LEVE", "it": "PIOGGIA LEGG.", "pt": "CHUVA FRACA", "nl": "LICHTE REGEN"},
    "RAIN":           {"fr": "PLUIE", "de": "REGEN", "es": "LLUVIA", "it": "PIOGGIA", "pt": "CHUVA", "nl": "REGEN"},
    "HEAVY RAIN":     {"fr": "PLUIE FORTE", "de": "STARKREGEN", "es": "LLUVIA FUERTE", "it": "PIOGGIA FORTE", "pt": "CHUVA FORTE", "nl": "ZWARE REGEN"},
    "LIGHT FREEZING RAIN": {"fr": "PLUIE VERGLAC.", "de": "L. GEFR. REGEN", "es": "LLUVIA HELADA", "it": "PIOGGIA GELATA", "pt": "CHUVA GELADA", "nl": "LICHTE IJZEL"},
    "FREEZING RAIN":  {"fr": "PLUIE VERGLAC.", "de": "GEFRIER-REGEN", "es": "LLUVIA HELADA", "it": "PIOGGIA GELATA", "pt": "CHUVA GELADA", "nl": "IJZEL"},
    "LIGHT SNOW":     {"fr": "NEIGE LEGERE", "de": "LEICHT SCHNEE", "es": "NIEVE LEVE", "it": "NEVE LEGGERA", "pt": "NEVE FRACA", "nl": "LICHTE SNEEUW"},
    "SNOW":           {"fr": "NEIGE", "de": "SCHNEE", "es": "NIEVE", "it": "NEVE", "pt": "NEVE", "nl": "SNEEUW"},
    "HEAVY SNOW":     {"fr": "NEIGE FORTE", "de": "STARK. SCHNEE", "es": "NIEVE FUERTE", "it": "NEVE FORTE", "pt": "NEVE FORTE", "nl": "ZWARE SNEEUW"},
    "SNOW GRAINS":    {"fr": "GRAINS NEIGE", "de": "SCHNEEGRIESEL", "es": "GRANIZO NIEVE", "it": "NEVE GRANUL.", "pt": "NEVE GRANULAR", "nl": "KORRELSNEEUW"},
    "RAIN SHOWERS":   {"fr": "AVERSES", "de": "REGENSCHAUER", "es": "CHUBASCOS", "it": "ROVESCI", "pt": "AGUACEIROS", "nl": "REGENBUIEN"},
    "HEAVY SHOWERS":  {"fr": "AVERSES FORTES", "de": "STARK. SCHAUER", "es": "CHUBASCOS FTES", "it": "ROVESCI FORTI", "pt": "AGUAC. FORTES", "nl": "ZWARE BUIEN"},
    "SNOW SHOWERS":   {"fr": "AVERSES NEIGE", "de": "SCHNEESCHAUER", "es": "CHUBASCOS NIEVE", "it": "ROVESCI NEVE", "pt": "AGUAC. NEVE", "nl": "SNEEUWBUIEN"},
    "HEAVY SNOW SHOWERS": {"fr": "AVERSES NEIGE", "de": "SCHNEESCHAUER", "es": "CHUBASCOS NIEVE", "it": "ROVESCI NEVE", "pt": "AGUAC. NEVE", "nl": "SNEEUWBUIEN"},
    "THUNDERSTORM":   {"fr": "ORAGE", "de": "GEWITTER", "es": "TORMENTA", "it": "TEMPORALE", "pt": "TROVOADA", "nl": "ONWEER"},
    "THUNDER HAIL":   {"fr": "ORAGE GRELE", "de": "GEWITTER HAGEL", "es": "TORM. GRANIZO", "it": "TEMP. GRANDINE", "pt": "TROV. GRANIZO", "nl": "ONWEER HAGEL"},
    "SEVERE TSTORM":  {"fr": "ORAGE VIOLENT", "de": "SCHW. GEWITTER", "es": "TORM. FUERTE", "it": "TEMP. VIOLENTO", "pt": "TROV. FORTE", "nl": "ZWAAR ONWEER"},
    "CURRENT CONDITIONS": {"fr": "CONDITIONS", "de": "WETTER", "es": "TIEMPO", "it": "METEO", "pt": "TEMPO", "nl": "WEER"},
    # weather — labels + qualitative levels (computed in-app for every provider)
    "FEELS":       {"fr": "RESSENTI", "de": "GEFUHLT", "es": "SENSAC.", "it": "PERCEP.", "pt": "SENSAC.", "nl": "GEVOELD"},
    "FLS":         {"fr": "RES", "de": "GEF", "es": "SEN", "it": "PER", "pt": "SEN", "nl": "GVL"},
    "AIR QUALITY": {"fr": "QUALITE AIR", "de": "LUFTQUALITAT", "es": "CALIDAD AIRE", "it": "QUALITA ARIA", "pt": "QUALID. AR", "nl": "LUCHTKWAL."},
    "SUN EXPOSURE":{"fr": "EXPO SOLEIL", "de": "UV-BELASTUNG", "es": "EXPO SOLAR", "it": "ESPOS. SOLE", "pt": "EXPO SOLAR", "nl": "ZONKRACHT"},
    "SUN UV":      {"fr": "UV SOLEIL", "de": "SONNE UV", "es": "UV SOLAR", "it": "UV SOLE", "pt": "UV SOLAR", "nl": "ZON UV"},
    "POLLEN":      {"fr": "POLLEN", "de": "POLLEN", "es": "POLEN", "it": "POLLINE", "pt": "POLEN", "nl": "POLLEN"},
    "OVERALL":     {"fr": "GLOBAL", "de": "GESAMT", "es": "TOTAL", "it": "TOTALE", "pt": "GERAL", "nl": "TOTAAL"},
    "OVR":         {"fr": "GLB", "de": "GES", "es": "TOT", "it": "TOT", "pt": "GER", "nl": "TOT"},
    "GRASS":       {"fr": "HERBE", "de": "GRAS", "es": "HIERBA", "it": "ERBA", "pt": "RELVA", "nl": "GRAS"},
    "GRS":         {"fr": "HRB", "de": "GRA", "es": "HIE", "it": "ERB", "pt": "REL", "nl": "GRS"},
    "TREE":        {"fr": "ARBRE", "de": "BAUM", "es": "ARBOL", "it": "ALBERO", "pt": "ARVORE", "nl": "BOOM"},
    "TRE":         {"fr": "ARB", "de": "BAU", "es": "ARB", "it": "ALB", "pt": "ARV", "nl": "BOM"},
    "WEED":        {"fr": "HERBACEE", "de": "UNKRAUT", "es": "MALEZA", "it": "ERBACCE", "pt": "ERVAS", "nl": "ONKRUID"},
    "WED":         {"fr": "HER", "de": "UNK", "es": "MAL", "it": "ERB", "pt": "ERV", "nl": "ONK"},
    "PROV":        {"fr": "SRCE", "de": "QUELLE", "es": "FUENTE", "it": "FONTE", "pt": "FONTE", "nl": "BRON"},
    "PRV":         {"fr": "SRC", "de": "QLE", "es": "FTE", "it": "FNT", "pt": "FNT", "nl": "BRN"},
    "GOOD":        {"fr": "BON", "de": "GUT", "es": "BUENA", "it": "BUONA", "pt": "BOA", "nl": "GOED"},
    "FAIR":        {"fr": "CORRECT", "de": "MASSIG", "es": "ACEPTABLE", "it": "DISCRETA", "pt": "RAZOAVEL", "nl": "REDELIJK"},
    "MODERATE":    {"fr": "MODERE", "de": "MASSIG", "es": "MODERADA", "it": "MODERATA", "pt": "MODERADA", "nl": "MATIG"},
    "POOR":        {"fr": "MAUVAIS", "de": "SCHLECHT", "es": "MALA", "it": "SCARSA", "pt": "FRACA", "nl": "SLECHT"},
    "V.POOR":      {"fr": "TRES MAUVAIS", "de": "SEHR SCHL.", "es": "MUY MALA", "it": "PESSIMA", "pt": "MUITO FRACA", "nl": "ZEER SLECHT"},
    "MOD":         {"fr": "MOYEN", "de": "MITTEL", "es": "MODER.", "it": "MEDIO", "pt": "MODER.", "nl": "MATIG"},
    "USG":         {"fr": "SENSIBLES", "de": "EMPFINDL.", "es": "SENSIBLES", "it": "SENSIBILI", "pt": "SENSIVEIS", "nl": "GEVOELIG"},
    "UNHEALTHY":   {"fr": "MALSAIN", "de": "UNGESUND", "es": "INSALUBRE", "it": "MALSANO", "pt": "INSALUBRE", "nl": "ONGEZOND"},
    "V.UNHLTHY":   {"fr": "TRES MALSAIN", "de": "SEHR UNGESUND", "es": "MUY INSALUBRE", "it": "MOLTO MALSANO", "pt": "MUITO INSALUB", "nl": "ZEER ONGEZOND"},
    "HAZARDOUS":   {"fr": "DANGEREUX", "de": "GEFAHRLICH", "es": "PELIGROSA", "it": "PERICOLOSA", "pt": "PERIGOSA", "nl": "GEVAARLIJK"},
    "LOW":         {"fr": "FAIBLE", "de": "NIEDRIG", "es": "BAJO", "it": "BASSO", "pt": "BAIXO", "nl": "LAAG"},
    "HIGH":        {"fr": "ELEVE", "de": "HOCH", "es": "ALTO", "it": "ALTO", "pt": "ALTO", "nl": "HOOG"},
    "V.HIGH":      {"fr": "TRES ELEVE", "de": "SEHR HOCH", "es": "MUY ALTO", "it": "MOLTO ALTO", "pt": "MUITO ALTO", "nl": "ZEER HOOG"},
    "EXTREME":     {"fr": "EXTREME", "de": "EXTREM", "es": "EXTREMO", "it": "ESTREMO", "pt": "EXTREMO", "nl": "EXTREEM"},
    "NONE":        {"fr": "AUCUN", "de": "KEIN", "es": "NINGUNO", "it": "NESSUNO", "pt": "NENHUM", "nl": "GEEN"},
    "UNKNOWN":     {"fr": "INCONNU", "de": "UNBEKANNT", "es": "DESCON.", "it": "SCONOSC.", "pt": "DESCON.", "nl": "ONBEKEND"},
    # precious metals
    "GOLD":       {"fr": "OR", "de": "GOLD", "es": "ORO", "it": "ORO", "pt": "OURO", "nl": "GOUD"},
    "SILVER":     {"fr": "ARGENT", "de": "SILBER", "es": "PLATA", "it": "ARGENTO", "pt": "PRATA", "nl": "ZILVER"},
    "PLATINUM":   {"fr": "PLATINE", "de": "PLATIN", "es": "PLATINO", "it": "PLATINO", "pt": "PLATINA", "nl": "PLATINA"},
    "PALLADIUM":  {"fr": "PALLADIUM", "de": "PALLADIUM", "es": "PALADIO", "it": "PALLADIO", "pt": "PALADIO", "nl": "PALLADIUM"},
    "SPOT PRICE": {"fr": "COURS", "de": "KURS", "es": "PRECIO", "it": "PREZZO", "pt": "PRECO", "nl": "KOERS"},
    # time-since
    "TIME SINCE":   {"fr": "DEPUIS", "de": "SEIT", "es": "DESDE", "it": "DA", "pt": "DESDE", "nl": "SINDS"},
    "NOT YET":      {"fr": "PAS ENCORE", "de": "NOCH NICHT", "es": "AUN NO", "it": "NON ANCORA", "pt": "AINDA NAO", "nl": "NOG NIET"},
    "STARTED":      {"fr": "COMMENCE", "de": "GESTARTET", "es": "EMPEZADO", "it": "INIZIATO", "pt": "INICIADO", "nl": "GESTART"},
    "INVALID DATE": {"fr": "DATE INVALIDE", "de": "UNGULT. DATUM", "es": "FECHA INVALIDA", "it": "DATA NON VALIDA", "pt": "DATA INVALIDA", "nl": "ONGELDIGE DATUM"},
    # wiki-today
    "FEATURED":  {"fr": "A LA UNE", "de": "ARTIKEL", "es": "DESTACADO", "it": "IN VETRINA", "pt": "DESTAQUE", "nl": "UITGELICHT"},
    "MOST READ": {"fr": "TOP LUS", "de": "MEISTGELESEN", "es": "MAS LEIDO", "it": "PIU LETTI", "pt": "MAIS LIDO", "nl": "MEEST GELEZEN"},
    # word-of-the-day
    "WORD OF THE DAY": {"fr": "MOT DU JOUR", "de": "WORT DES TAGES", "es": "PALABRA DEL DIA", "it": "PAROLA DEL GIORNO", "pt": "PALAVRA DO DIA", "nl": "WOORD VAN DE DAG"},
    # holidays
    "NEXT HOLIDAY": {"fr": "PROCHAIN CONGE", "de": "NAECHSTER FEIERTAG", "es": "PROX. FESTIVO", "it": "PROSS. FESTA", "pt": "PROX. FERIADO", "nl": "VOLGENDE FEESTDAG"},
    "IN":           {"fr": "DANS", "de": "IN", "es": "EN", "it": "TRA", "pt": "EM", "nl": "OVER"},
}


def translate(text, lang):
    """English UI string -> localized (or the English key if unknown/English)."""
    if not lang or lang.lower().startswith("en"):
        return text
    return _STRINGS.get(text, {}).get(lang.lower(), text)


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


# Compact duration-unit suffixes ("175D 7H 52M"). The day is where languages truly
# diverge (French JOUR->J, German TAG->T, Italian GIORNO->G, Dutch hour UUR->U); the
# rest are near-universal single letters.
_DURATION_UNITS = {
    "Y": {"fr": "A", "de": "J", "es": "A", "it": "A", "pt": "A", "nl": "J"},   # year (an/Jahr/año…)
    "D": {"fr": "J", "de": "T", "es": "D", "it": "G", "pt": "D", "nl": "D"},
    "H": {"fr": "H", "de": "H", "es": "H", "it": "H", "pt": "H", "nl": "U"},
    "M": {"fr": "M", "de": "M", "es": "M", "it": "M", "pt": "M", "nl": "M"},
    "S": {"fr": "S", "de": "S", "es": "S", "it": "S", "pt": "S", "nl": "S"},
}


def duration_unit(key, lang):
    """Localized single-letter duration suffix (D/H/M/S) -> e.g. J/H/M/S in French."""
    if not lang or lang.lower().startswith("en"):
        return key
    return _DURATION_UNITS.get(key, {}).get(lang.lower(), key)


def uses_24h(lang):
    """AM/PM is essentially an English-language convention; everyone else is 24h."""
    return not (not lang or lang.lower().startswith("en"))


# Non-ASCII group separators CLDR uses (French narrow/no-break spaces) — the flap
# display only speaks Windows-1252, so we fold them back to a plain space.
_GROUP_SPACES = ("\u202f", "\u00a0", "\u2009")


def number(value, lang, decimals=2, grouping=True):
    """Format a number with the locale's own separators: 1,234.5 (en) vs 1.234,5
    (de/es/it/…) vs 1 234,5 (fr). Falls back to English grouping off-companion."""
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


# The "home" currency implied by a language/region, used as the default base for FX:
# US -> USD, UK -> GBP, Australia -> AUD, the rest of Western Europe -> EUR. Users can
# always override the base explicitly.
_BASE_CURRENCY = {
    "en": "USD", "en-us": "USD", "en-gb": "GBP", "en-au": "AUD",
    "fr": "EUR", "de": "EUR", "es": "EUR", "it": "EUR", "pt": "EUR", "nl": "EUR",
}


def base_currency(lang):
    return _BASE_CURRENCY.get((lang or "en").lower(), "USD")


# The country a language/region implies — for holidays (which calendar to show) and
# any other country-scoped data. English splits by region; others use their homeland.
_COUNTRY = {
    "en": "US", "en-us": "US", "en-gb": "GB", "en-au": "AU",
    "fr": "FR", "de": "DE", "es": "ES", "it": "IT", "pt": "PT", "nl": "NL",
}


def country(lang):
    return _COUNTRY.get((lang or "en").lower(), "US")


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

    @property
    def lang_base(self):
        """The 2-letter language without region ('en-GB' -> 'en'), for APIs that
        want a plain language code (Wikipedia editions, weather providers, …)."""
        return self.lang.split("-")[0]
