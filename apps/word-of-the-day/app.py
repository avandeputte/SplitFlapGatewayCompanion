"""Word of the Day — one interesting word, chosen by the date (no definition)."""

# A curated list of characterful words. The word is picked by the calendar day,
# so it's stable for the whole day and cycles through the list over time.
WORDS = [
    "EPHEMERAL", "UBIQUITOUS", "SERENDIPITY", "ELOQUENT", "RESILIENT",
    "PRAGMATIC", "CANDOR", "TENACIOUS", "MELLIFLUOUS", "LUMINOUS",
    "QUINTESSENTIAL", "EFFERVESCENT", "PERSPICACIOUS", "SUCCINCT", "GREGARIOUS",
    "INEFFABLE", "LABYRINTHINE", "MAGNANIMOUS", "NEBULOUS", "OBSTINATE",
    "PANACEA", "QUERULOUS", "RESPLENDENT", "SANGUINE", "TACITURN",
    "UMBRAGE", "VORACIOUS", "WISTFUL", "ZEALOUS", "AMBIVALENT",
    "BENEVOLENT", "CACOPHONY", "DILIGENT", "ENIGMATIC", "FASTIDIOUS",
    "GARRULOUS", "HALCYON", "IDIOSYNCRASY", "JUXTAPOSE", "LETHARGIC",
    "MERCURIAL", "NONCHALANT", "OSTENTATIOUS", "PENCHANT", "QUIXOTIC",
    "RECALCITRANT", "TRANQUIL", "VENERABLE", "WHIMSICAL", "ZENITH",
]


def fetch(settings, format_lines, get_rows, get_cols):
    from datetime import date
    word = WORDS[date.today().toordinal() % len(WORDS)]
    if get_rows() == 1:
        return [format_lines(word)]
    return [format_lines("WORD OF THE DAY", word)]
