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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# A dictionary card: the header small in violet, the word as LARGE as the panel
# allows, and (for the English list) its part of speech + one-line gloss wrapped
# beneath. The word changes once a day, so the hold is long. Solid black.
# =============================================================================

_CV_LABEL = (168, 148, 255)           # the dictionary-violet accent
_CV_WORD = (240, 242, 246)
_CV_POS = (168, 148, 255)
_CV_DEF = (150, 156, 166)


def _cv_word_ops(canvas, header, word, pos, gloss, W, H):
    """The dictionary card as on-device DRAW OPS — the gtext-era twin of the tall
    PIL path below, for the LCD (manifest ``lcd_ops``): the violet label, the word
    in huge scalable type, the gloss beneath, rendered by the wall at native
    resolution instead of a 256x160 frame upscaled x5. Same composition as the
    pixel card, every measure a fraction of the panel."""
    canvas.clear((0, 0, 0))
    pad = max(8, int(H * 0.05))
    hsz = canvas.fit_gtext(header, W - 2 * pad, max(8, int(H * 0.11)))
    canvas.gtext(W // 2, pad, header, color=_CV_LABEL, size=hsz, align='center')

    # The gloss at a comfortable read — whole words, up to two lines. Its block
    # height counts the descenders (~1.12 of the size), so the last line's ink
    # lands on the pad line the way the PIL path's floor-anchored block does.
    dsz, dlines, dstep, def_block = 0, [], 0, 0
    if gloss:
        dsz, dlines = canvas.fit_wrap_gtext(f'{pos} {gloss}'.strip(), W - 2 * pad,
                                            int(H * 0.24), max_lines=2)
        dstep = int(dsz * 1.18)
        def_block = (len(dlines) - 1) * dstep + int(dsz * 1.12)

    # The WORD — hero — centered in the band between the label and the gloss
    # (by its ink, roughly 0.18..1.05 of the size down the ascent box).
    word_top = pad + hsz + max(6, int(H * 0.05))
    word_bot = H - pad - (def_block + max(8, int(H * 0.06)) if def_block else 0)
    wsz = canvas.fit_gtext(word, W - 2 * pad, word_bot - word_top)
    wy = word_top + max(0, int((word_bot - word_top - wsz * 1.23) / 2))
    canvas.gtext(W // 2, wy, word, color=_CV_WORD, size=wsz, align='center')

    # part of speech + definition, the pos picked out in the accent.
    y = H - pad - def_block
    for i, ln in enumerate(dlines):
        x = int((W - canvas.text_width(ln, dsz)) / 2)
        if i == 0 and pos and ln.startswith(pos):
            canvas.gtext(x, y, pos, color=_CV_POS, size=dsz)
            canvas.gtext(x + canvas.text_width(pos + ' ', dsz), y,
                         ln[len(pos):].strip(), color=_CV_DEF, size=dsz)
        else:
            canvas.gtext(x, y, ln, color=_CV_DEF, size=dsz)
        y += dstep
    canvas.show()


def fetch_canvas(settings, canvas, i18n=None):
    from PIL import ImageDraw

    lang, word = _todays_word(i18n)
    pos, gloss = DEFS.get(word, ('', '')) if lang == 'en' else ('', '')
    header = (i18n.t('Word of the day', 'vocab') if i18n is not None else 'Word of the day').upper()

    W, H = int(canvas.width), int(canvas.height)
    if getattr(canvas, 'can_gtext', False) and H >= 96:
        # The big-panel path: live ops at native resolution (crisp TTF label/word/gloss).
        _cv_word_ops(canvas, header, word, pos, gloss, W, H)
        return 300.0
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3

    if H >= 96:                                     # tall LCD — label / WORD / definition
        pad = 8                                     # spread over the panel, no bottom sliver
        hf = canvas.fit_font(header, W - 2 * pad, int(H * 0.11))
        canvas.text_top(draw, (W - hf.getlength(header)) / 2.0, pad, header, hf, _CV_LABEL)

        # The gloss at a comfortable read — whole words, up to two lines.
        def_lines, df = [], None
        if gloss:
            df, def_lines = canvas.wrap_fit(f'{pos} {gloss}'.strip(), W - 2 * pad,
                                            int(H * 0.24), 2)
        dlh = canvas.ink(df, 'Ag') if def_lines else 0
        dgap = max(2, dlh // 5)
        def_block = (len(def_lines) * dlh + (len(def_lines) - 1) * dgap) if def_lines else 0

        # The WORD — hero — centered in the band between the label and the gloss.
        word_top = pad + canvas.ink(hf, header) + max(6, int(H * 0.05))
        word_bot = H - pad - (def_block + max(8, int(H * 0.06)) if def_block else 0)
        wf = canvas.fit_font(word, W - 2 * pad, word_bot - word_top)
        wy = word_top + max(0, (word_bot - word_top - canvas.ink(wf, word)) // 2)
        canvas.text_top(draw, (W - wf.getlength(word)) / 2.0, wy, word, wf, _CV_WORD)

        # part of speech + definition, the pos picked out in the accent.
        y = H - pad - def_block
        for i, ln in enumerate(def_lines):
            x = (W - df.getlength(ln)) / 2.0
            if i == 0 and pos and ln.startswith(pos):
                canvas.text_top(draw, x, y, pos, df, _CV_POS)
                canvas.text_top(draw, x + df.getlength(pos + ' '), y, ln[len(pos):].strip(),
                                df, _CV_DEF)
            else:
                canvas.text_top(draw, x, y, ln, df, _CV_DEF)
            y += dlh + dgap
        canvas.frame(img)
        return 300.0

    # Header label — only where it doesn't crowd the word off a short panel.
    top = 1
    if H >= 48:
        hf = canvas.fit_font(header, W - 2 * pad, max(6, int(H * 0.12)))
        canvas.text_top(draw, (W - hf.getlength(header)) / 2.0, 1, header, hf, _CV_LABEL)
        top = 1 + (hf.getbbox(header)[3] - hf.getbbox(header)[1]) + 3

    # The gloss block first (its height decides how much the word may take).
    df = canvas.font(9)
    dl = df.getbbox('Ag')
    dlh = dl[3] - dl[1]
    def_lines = []
    if gloss:
        full = f'{pos} {gloss}'.strip()
        # canvas.wrap already ellipsizes a gloss the line budget cuts short.
        def_lines = canvas.wrap(df, full, W - 2 * pad, 2 if H >= 48 else 1)
        if W < 100 and sum(len(ln.split()) for ln in def_lines) < len(full.split()):
            # No honest room for the gloss — drop the block entirely; an
            # orphaned "n." under the word explains nothing.
            def_lines = []

    # The gloss sits ON the panel floor: its last line's own ink ends at H-1.
    floor = H                                   # the first row past the word's room
    if def_lines:
        glb = df.getbbox(def_lines[-1])
        def_top = H - (glb[3] - glb[1]) - (len(def_lines) - 1) * (dlh + 1)
        floor = def_top - 2

    # The word, as large as what's left allows.
    wf = canvas.fit_font(word, W - 2 * pad, floor - top)
    wh = wf.getbbox(word)[3] - wf.getbbox(word)[1]
    if not def_lines:
        wy = top + max(0, (H - 1 - top - wh) // 2)  # nothing beneath: center the word
    elif H >= 48:
        wy = top + max(0, (floor - top - wh) // 2)
    else:
        wy = top                                # short panel: the word pins the top edge
    canvas.text_top(draw, (W - wf.getlength(word)) / 2.0, wy, word, wf, _CV_WORD)

    # part of speech + gloss, the pos picked out in the accent.
    if def_lines:
        y = def_top
        for i, ln in enumerate(def_lines):
            x = (W - df.getlength(ln)) / 2.0
            if i == 0 and pos and ln.startswith(pos):
                canvas.text_top(draw, x, y, pos, df, _CV_POS)
                canvas.text_top(draw, x + df.getlength(pos + ' '), y, ln[len(pos):].strip(), df, _CV_DEF)
            else:
                canvas.text_top(draw, x, y, ln, df, _CV_DEF)
            y += dlh + 1

    canvas.frame(img)
    return 300.0                       # the word changes once a day — no need to hurry
