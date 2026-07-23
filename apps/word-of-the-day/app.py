"""Word of the Day — one characterful word, chosen by the date (no definition).

Localized: when the companion injects i18n, the word is drawn from a curated list in
the current Language (falling back to English), and the header is translated. The
word is picked by the calendar day, so it's stable for the day and cycles over time.
"""


# =============================================================================
# SHARED — the word DATA: the per-language lists and the date-picked choice.
# Both surfaces show the same word on the same day.
# =============================================================================

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

# A short gloss per ENGLISH word — the Matrix panel has the pixels to show it (the flap
# wall stays word-only). part-of-speech + a one-line definition; other languages have no
# gloss layer yet and the panel simply shows the word alone.
DEFS = {
    "ephemeral": ("adj.", "lasting a very short time"),
    "ubiquitous": ("adj.", "present everywhere at once"),
    "serendipity": ("n.", "luck that finds good things unsought"),
    "eloquent": ("adj.", "fluent and persuasive in speech"),
    "resilient": ("adj.", "quick to recover from setbacks"),
    "pragmatic": ("adj.", "guided by practice, not theory"),
    "candor": ("n.", "open, honest sincerity"),
    "tenacious": ("adj.", "holding firm; persistent"),
    "mellifluous": ("adj.", "sweetly smooth to hear"),
    "luminous": ("adj.", "full of or shedding light"),
    "quintessential": ("adj.", "the purest example of its kind"),
    "effervescent": ("adj.", "bubbling with enthusiasm"),
    "perspicacious": ("adj.", "sharp in noticing and judging"),
    "succinct": ("adj.", "brief and clearly expressed"),
    "gregarious": ("adj.", "fond of company; sociable"),
    "ineffable": ("adj.", "too great for words"),
    "labyrinthine": ("adj.", "winding like a maze"),
    "magnanimous": ("adj.", "generous, above pettiness"),
    "nebulous": ("adj.", "hazy, vague, unformed"),
    "obstinate": ("adj.", "stubbornly set in one's ways"),
    "panacea": ("n.", "a cure-all remedy"),
    "querulous": ("adj.", "complaining, petulant"),
    "resplendent": ("adj.", "dazzling in appearance"),
    "sanguine": ("adj.", "cheerfully optimistic"),
    "taciturn": ("adj.", "saying little by nature"),
    "umbrage": ("n.", "offense taken; resentment"),
    "voracious": ("adj.", "devouring with great appetite"),
    "wistful": ("adj.", "quietly yearning; pensive"),
    "zealous": ("adj.", "fervently devoted"),
    "ambivalent": ("adj.", "of two minds at once"),
    "benevolent": ("adj.", "kindly and well-meaning"),
    "cacophony": ("n.", "a harsh clash of sounds"),
    "diligent": ("adj.", "steady, careful, hard-working"),
    "enigmatic": ("adj.", "mysterious, hard to read"),
    "fastidious": ("adj.", "exacting about details"),
    "garrulous": ("adj.", "talkative to a fault"),
    "halcyon": ("adj.", "calm, golden, idyllic"),
    "idiosyncrasy": ("n.", "a quirk all one's own"),
    "juxtapose": ("v.", "to set side by side"),
    "lethargic": ("adj.", "sluggish, drained of energy"),
    "mercurial": ("adj.", "quick to change mood"),
    "nonchalant": ("adj.", "coolly unconcerned"),
    "ostentatious": ("adj.", "showy to impress"),
    "penchant": ("n.", "a strong liking or habit"),
    "quixotic": ("adj.", "nobly impractical; dreamy"),
    "recalcitrant": ("adj.", "resisting authority or control"),
    "tranquil": ("adj.", "free of disturbance; calm"),
    "venerable": ("adj.", "honored by age and wisdom"),
    "whimsical": ("adj.", "playfully fanciful"),
    "zenith": ("n.", "the highest point"),
}


def _todays_word(i18n):
    """(lang, word) for today — date-picked from the wall's language list (English when
    the language has none), stable all day and shared by every surface."""
    from datetime import date
    lang = i18n.lang_base if i18n is not None else "en"
    if not WORDS_BY_LANG.get(lang):
        lang = "en"
    words = WORDS_BY_LANG[lang]
    return lang, words[date.today().toordinal() % len(words)]


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    _lang, word = _todays_word(i18n)
    header = i18n.t("Word of the day", "vocab") if i18n is not None else "Word of the day"
    if get_rows() == 1:
        return [format_lines(word)]
    return [format_lines(header, word)]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A dictionary card: the header small in violet, the word as LARGE as the panel
# allows, and (for the English list) its part of speech + one-line gloss wrapped
# beneath. The word changes once a day, so the hold is long. Solid black.
# =============================================================================

_CV_LABEL = (168, 148, 255)           # the dictionary-violet accent
_CV_WORD = (240, 242, 246)
_CV_POS = (168, 148, 255)
_CV_DEF = (150, 156, 166)


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 5px)."""
    size = max(5, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 5 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_wrap(font, text, max_w, max_lines):
    """Greedy word-wrap of ``text`` to pixel width ``max_w``, at most ``max_lines`` lines."""
    words, lines, cur = str(text or '').split(), [], ''
    for w in words:
        cand = f'{cur} {w}'.strip()
        if not cur or font.getlength(cand) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines[:max_lines] or ['']


def _cv_text(draw, x, y, text, font, fill):
    """Baseline-corrected text draw (y is the ink top, whatever the glyph bbox says)."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill)


def fetch_matrix(settings, canvas, i18n=None):
    from PIL import ImageDraw

    lang, word = _todays_word(i18n)
    pos, gloss = DEFS.get(word, ('', '')) if lang == 'en' else ('', '')
    header = (i18n.t('Word of the day', 'vocab') if i18n is not None else 'Word of the day').upper()

    W, H = int(canvas.width), int(canvas.height)
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3

    # Header label — only where it doesn't crowd the word off a short panel.
    top = pad
    if H >= 48:
        hf = _cv_fit(canvas, header, W - 2 * pad, max(6, int(H * 0.12)))
        _cv_text(draw, (W - hf.getlength(header)) / 2.0, pad, header, hf, _CV_LABEL)
        top = pad + (hf.getbbox(header)[3] - hf.getbbox(header)[1]) + 3

    # The gloss block first (its height decides how much the word may take).
    df = canvas.font(9)
    dl = df.getbbox('Ag')
    dlh = dl[3] - dl[1]
    def_lines = []
    if gloss:
        full = f'{pos} {gloss}'.strip()
        def_lines = _cv_wrap(df, full, W - 2 * pad, 2 if H >= 48 else 1)
        if sum(len(ln.split()) for ln in def_lines) < len(full.split()):
            if W < 100:
                # No honest room for the gloss — the part of speech alone, not a stump.
                def_lines = [pos] if pos else []
            else:
                last = def_lines[-1]
                while last and df.getlength(last + '…') > W - 2 * pad:
                    last = last[:-1]
                def_lines[-1] = last + '…'
    def_h = (len(def_lines) * (dlh + 1) + 2) if def_lines else 0

    # The word, as large as what's left allows.
    wf = _cv_fit(canvas, word, W - 2 * pad, H - top - pad - def_h)
    wh = wf.getbbox(word)[3] - wf.getbbox(word)[1]
    free = H - top - pad - def_h - wh
    wy = top + max(0, free // 2)
    _cv_text(draw, (W - wf.getlength(word)) / 2.0, wy, word, wf, _CV_WORD)

    # part of speech + gloss, the pos picked out in the accent.
    if def_lines:
        y = H - pad - len(def_lines) * (dlh + 1) + 1
        for i, ln in enumerate(def_lines):
            x = (W - df.getlength(ln)) / 2.0
            if i == 0 and pos and ln.startswith(pos):
                _cv_text(draw, x, y, pos, df, _CV_POS)
                _cv_text(draw, x + df.getlength(pos + ' '), y, ln[len(pos):].strip(), df, _CV_DEF)
            else:
                _cv_text(draw, x, y, ln, df, _CV_DEF)
            y += dlh + 1

    canvas.frame(img)
    return 300.0                       # the word changes once a day — no need to hurry
