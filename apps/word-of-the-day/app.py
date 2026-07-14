"""Word of the Day — one characterful word, chosen by the date (no definition).

Localized: when the companion injects i18n, the word is drawn from a curated list in
the current Language (falling back to English), and the header is translated. The
word is picked by the calendar day, so it's stable for the day and cycles over time.
"""

# Accent-free (Windows-1252-safe) words as they are actually written — German nouns keep
# their capital, everything else is lowercase — one evocative list per language. Walls with
# no lowercase flaps are folded to uppercase downstream; don't shout here.
WORDS_BY_LANG = {
    "en": [
        "ephemeral", "ubiquitous", "serendipity", "eloquent", "resilient",
        "pragmatic", "candor", "tenacious", "mellifluous", "luminous",
        "quintessential", "effervescent", "perspicacious", "succinct", "gregarious",
        "ineffable", "labyrinthine", "magnanimous", "nebulous", "obstinate",
        "panacea", "querulous", "resplendent", "sanguine", "taciturn",
        "umbrage", "voracious", "wistful", "zealous", "ambivalent",
        "benevolent", "cacophony", "diligent", "enigmatic", "fastidious",
        "garrulous", "halcyon", "idiosyncrasy", "juxtapose", "lethargic",
        "mercurial", "nonchalant", "ostentatious", "penchant", "quixotic",
        "recalcitrant", "tranquil", "venerable", "whimsical", "zenith",
    ],
    "fr": [
        "flanerie", "depaysement", "retrouvailles", "crepuscule", "eblouissant",
        "chatoyant", "insouciant", "melancolie", "quietude", "sagacite",
        "perspicace", "opiniatre", "loquace", "volubile", "ephemere",
        "lumineux", "resilient", "tenace", "serenite", "venerable",
        "nonchalant", "enigmatique", "exquis", "impromptu",
    ],
    "de": [
        "Fernweh", "Waldeinsamkeit", "Zeitgeist", "Geborgenheit", "Sehnsucht",
        "vergaenglich", "leuchtend", "beharrlich", "scharfsinnig", "wortkarg",
        "Ueberschwang", "gemuetlich", "Wehmut", "Gelassenheit", "eigensinnig",
        "besonnen", "verwegen", "anmutig", "unergruendlich", "Augenblick",
        "Daemmerung", "schwelgen", "verschmitzt", "Ehrfurcht",
    ],
    "es": [
        "efimero", "inefable", "serendipia", "elocuente", "resiliente",
        "luminoso", "tenaz", "perspicaz", "locuaz", "obstinado",
        "panacea", "resplandeciente", "tranquilo", "venerable", "sagaz",
        "nostalgia", "crepusculo", "deslumbrante", "melancolia", "quietud",
        "sosiego", "enigmatico", "exquisito", "impetu",
    ],
    "it": [
        "effimero", "ineffabile", "serendipita", "eloquente", "resiliente",
        "luminoso", "tenace", "perspicace", "loquace", "ostinato",
        "panacea", "splendente", "tranquillo", "venerabile", "sagace",
        "nostalgia", "crepuscolo", "abbagliante", "malinconia", "quiete",
        "serenita", "enigmatico", "squisito", "impeto",
    ],
    "pt": [
        "efemero", "inefavel", "serendipia", "eloquente", "resiliente",
        "luminoso", "tenaz", "perspicaz", "loquaz", "obstinado",
        "panaceia", "resplandecente", "tranquilo", "veneravel", "sagaz",
        "saudade", "crepusculo", "deslumbrante", "melancolia", "quietude",
        "sossego", "enigmatico", "requintado", "impeto",
    ],
    "nl": [
        "vluchtig", "onuitsprekelijk", "tijdgeest", "veerkrachtig", "lichtend",
        "vastberaden", "scherpzinnig", "woordkarig", "koppig", "wondermiddel",
        "stralend", "sereen", "eerbiedwaardig", "wijsheid", "weemoed",
        "schemering", "oogverblindend", "melancholie", "gezellig", "raadselachtig",
        "verrukkelijk", "onstuimig", "voorbijgaand", "bedachtzaam",
    ],
}


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    from datetime import date
    lang = i18n.lang_base if i18n is not None else "en"
    words = WORDS_BY_LANG.get(lang) or WORDS_BY_LANG["en"]
    word = words[date.today().toordinal() % len(words)]
    header = i18n.t("Word of the day", "vocab") if i18n is not None else "Word of the day"
    if get_rows() == 1:
        return [format_lines(word)]
    return [format_lines(header, word)]
