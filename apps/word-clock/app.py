"""Word clock — spells the time out ("It's half past ten").

Localized into the major Western-European languages when the companion injects
i18n (honoring the global Language). Each language has its own grammar — Romance
languages name the hour first (Il est dix heures et quart), Germanic ones lead
with the minutes (Viertel nach zehn), and German/Dutch "half" points at the *next*
hour (halb drei = 2:30). Anything without a builder (or a bare host) falls back to
the original English behavior.
"""

# =============================================================================
# SHARED — the phrase itself: vocabulary, per-language grammar builders and the
# rounding rule. Both surfaces speak the same sentence; only the typography
# differs (flap rows there, a lit-words face here).
# =============================================================================

# Hour words 1..12 (index 0 unused) and minute-number words 1..29, accent-free to
# match the display's Windows-1252 rendering. Gender follows each language (es
# una/uno for hour/minute, pt uma/um).
_HOURS = {
    'fr': ['', 'une', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf', 'dix', 'onze', 'douze'],
    'de': ['', 'eins', 'zwei', 'drei', 'vier', 'funf', 'sechs', 'sieben', 'acht', 'neun', 'zehn', 'elf', 'zwolf'],
    'es': ['', 'una', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve', 'diez', 'once', 'doce'],
    'it': ['', 'una', 'due', 'tre', 'quattro', 'cinque', 'sei', 'sette', 'otto', 'nove', 'dieci', 'undici', 'dodici'],
    'pt': ['', 'uma', 'duas', 'tres', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove', 'dez', 'onze', 'doze'],
    'nl': ['', 'een', 'twee', 'drie', 'vier', 'vijf', 'zes', 'zeven', 'acht', 'negen', 'tien', 'elf', 'twaalf'],
}
_MINS = {
    'fr': ['', 'une', 'deux', 'trois', 'quatre', 'cinq', 'six', 'sept', 'huit', 'neuf', 'dix', 'onze', 'douze',
           'treize', 'quatorze', 'quinze', 'seize', 'dix-sept', 'dix-huit', 'dix-neuf', 'vingt', 'vingt et une',
           'vingt-deux', 'vingt-trois', 'vingt-quatre', 'vingt-cinq', 'vingt-six', 'vingt-sept', 'vingt-huit', 'vingt-neuf'],
    'de': ['', 'eins', 'zwei', 'drei', 'vier', 'funf', 'sechs', 'sieben', 'acht', 'neun', 'zehn', 'elf', 'zwolf',
           'dreizehn', 'vierzehn', 'funfzehn', 'sechzehn', 'siebzehn', 'achtzehn', 'neunzehn', 'zwanzig', 'einundzwanzig',
           'zweiundzwanzig', 'dreiundzwanzig', 'vierundzwanzig', 'funfundzwanzig', 'sechsundzwanzig', 'siebenundzwanzig',
           'achtundzwanzig', 'neunundzwanzig'],
    'es': ['', 'uno', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete', 'ocho', 'nueve', 'diez', 'once', 'doce',
           'trece', 'catorce', 'quince', 'dieciseis', 'diecisiete', 'dieciocho', 'diecinueve', 'veinte', 'veintiuno',
           'veintidos', 'veintitres', 'veinticuatro', 'veinticinco', 'veintiseis', 'veintisiete', 'veintiocho', 'veintinueve'],
    'it': ['', 'uno', 'due', 'tre', 'quattro', 'cinque', 'sei', 'sette', 'otto', 'nove', 'dieci', 'undici', 'dodici',
           'tredici', 'quattordici', 'quindici', 'sedici', 'diciassette', 'diciotto', 'diciannove', 'venti', 'ventuno',
           'ventidue', 'ventitre', 'ventiquattro', 'venticinque', 'ventisei', 'ventisette', 'ventotto', 'ventinove'],
    'pt': ['', 'um', 'dois', 'tres', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove', 'dez', 'onze', 'doze',
           'treze', 'catorze', 'quinze', 'dezesseis', 'dezessete', 'dezoito', 'dezenove', 'vinte', 'vinte e um',
           'vinte e dois', 'vinte e tres', 'vinte e quatro', 'vinte e cinco', 'vinte e seis', 'vinte e sete',
           'vinte e oito', 'vinte e nove'],
    'nl': ['', 'een', 'twee', 'drie', 'vier', 'vijf', 'zes', 'zeven', 'acht', 'negen', 'tien', 'elf', 'twaalf',
           'dertien', 'veertien', 'vijftien', 'zestien', 'zeventien', 'achttien', 'negentien', 'twintig', 'eenentwintig',
           'tweeentwintig', 'drieentwintig', 'vierentwintig', 'vijfentwintig', 'zesentwintig', 'zevenentwintig',
           'achtentwintig', 'negenentwintig'],
}


def _ctx(h):
    """Displayed current hour (1..12) and next hour (1..12)."""
    h12 = h % 12
    disp = 12 if h12 == 0 else h12
    n12 = (h12 + 1) % 12
    dispn = 12 if n12 == 0 else n12
    return disp, dispn


def _b_fr(h, rounded):
    if h == 0 and rounded == 0:
        return ['Il', 'est', 'minuit']
    if h == 12 and rounded == 0:
        return ['Il', 'est', 'midi']
    disp, dispn = _ctx(h)
    H, M = _HOURS['fr'], _MINS['fr']
    heure = lambda d: 'heure' if d == 1 else 'heures'
    if rounded == 0:
        return ['Il', 'est', H[disp], heure(disp)]
    if rounded <= 30:
        head = ['Il', 'est', H[disp], heure(disp)]
        if rounded == 15:
            return head + ['et', 'quart']
        if rounded == 30:
            return head + ['et', 'demie']
        return head + ['et', M[rounded]]
    mm = 60 - rounded
    head = ['Il', 'est', H[dispn], heure(dispn)]
    if mm == 15:
        return head + ['moins', 'le', 'quart']
    return head + ['moins', M[mm]]


def _b_de(h, rounded):
    if h == 0 and rounded == 0:
        return ['Es', 'ist', 'Mitternacht']
    if h == 12 and rounded == 0:
        return ['Es', 'ist', 'Mittag']
    disp, dispn = _ctx(h)
    H, M = _HOURS['de'], _MINS['de']
    if rounded == 0:
        return ['Es', 'ist', 'ein' if disp == 1 else H[disp], 'Uhr']
    if rounded <= 30:
        if rounded == 15:
            return ['Es', 'ist', 'Viertel', 'nach', H[disp]]
        if rounded == 30:
            return ['Es', 'ist', 'halb', H[dispn]]      # halb points at the next hour
        return ['Es', 'ist', M[rounded], 'nach', H[disp]]
    mm = 60 - rounded
    if mm == 15:
        return ['Es', 'ist', 'Viertel', 'vor', H[dispn]]
    return ['Es', 'ist', M[mm], 'vor', H[dispn]]


def _b_es(h, rounded):
    if h == 0 and rounded == 0:
        return ['Es', 'medianoche']
    if h == 12 and rounded == 0:
        return ['Es', 'mediodia']
    disp, dispn = _ctx(h)
    H, M = _HOURS['es'], _MINS['es']
    pre = lambda d: ['Es', 'la'] if d == 1 else ['Son', 'las']
    if rounded == 0:
        return pre(disp) + [H[disp]]
    if rounded <= 30:
        head = pre(disp) + [H[disp]]
        if rounded == 15:
            return head + ['y', 'cuarto']
        if rounded == 30:
            return head + ['y', 'media']
        return head + ['y', M[rounded]]
    mm = 60 - rounded
    head = pre(dispn) + [H[dispn]]
    if mm == 15:
        return head + ['menos', 'cuarto']
    return head + ['menos', M[mm]]


def _b_it(h, rounded):
    if h == 0 and rounded == 0:
        return ["E'", 'mezzanotte']
    if h == 12 and rounded == 0:
        return ["E'", 'mezzogiorno']
    disp, dispn = _ctx(h)
    H, M = _HOURS['it'], _MINS['it']
    named = lambda d: ["E'", "l'una"] if d == 1 else ['Sono', 'le', H[d]]
    if rounded == 0:
        return named(disp)
    if rounded <= 30:
        head = named(disp)
        if rounded == 15:
            return head + ['e', 'un', 'quarto']
        if rounded == 30:
            return head + ['e', 'mezza']
        return head + ['e', M[rounded]]
    mm = 60 - rounded
    head = named(dispn)
    if mm == 15:
        return head + ['meno', 'un', 'quarto']
    return head + ['meno', M[mm]]


def _b_pt(h, rounded):
    if h == 0 and rounded == 0:
        return ["E'", 'meia-noite']
    if h == 12 and rounded == 0:
        return ["E'", 'meio-dia']
    disp, dispn = _ctx(h)
    H, M = _HOURS['pt'], _MINS['pt']
    if rounded == 0:
        return ["E'", 'uma', 'hora'] if disp == 1 else ['Sao', H[disp], 'horas']
    if rounded <= 30:
        head = ["E'", 'uma'] if disp == 1 else ['Sao', H[disp]]
        if rounded == 15:
            return head + ['e', 'quinze']
        if rounded == 30:
            return head + ['e', 'meia']
        return head + ['e', M[rounded]]
    mm = 60 - rounded
    tail = 'quinze' if mm == 15 else M[mm]
    # Unlike every other branch, this one has no copula: the minute word OPENS the sentence
    # ("Quinze para as dez"). The vocabulary stores each word in its mid-sentence form —
    # it is the same word the branch above uses after "e" — so capitalize it here, where
    # we know it is first, rather than keeping a second capitalized copy of the numbers.
    tail = tail[:1].upper() + tail[1:]
    if dispn == 1:
        return [tail, 'para', 'a', 'uma']
    return [tail, 'para', 'as', H[dispn]]


def _b_nl(h, rounded):
    if h == 0 and rounded == 0:
        return ['Het', 'is', 'middernacht']
    if h == 12 and rounded == 0:
        return ['Het', 'is', 'middag']
    disp, dispn = _ctx(h)
    H, M = _HOURS['nl'], _MINS['nl']
    if rounded == 0:
        return ['Het', 'is', H[disp], 'uur']
    if rounded <= 30:
        if rounded == 15:
            return ['Het', 'is', 'kwart', 'over', H[disp]]
        if rounded == 30:
            return ['Het', 'is', 'half', H[dispn]]      # half points at the next hour
        return ['Het', 'is', M[rounded], 'over', H[disp]]
    mm = 60 - rounded
    if mm == 15:
        return ['Het', 'is', 'kwart', 'voor', H[dispn]]
    return ['Het', 'is', M[mm], 'voor', H[dispn]]


_BUILDERS = {'fr': _b_fr, 'de': _b_de, 'es': _b_es, 'it': _b_it, 'pt': _b_pt, 'nl': _b_nl}


def _tznow(settings):
    from datetime import datetime
    import pytz
    try:
        tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone('US/Eastern')
    return datetime.now(tz)


def _rounded(now, interval):
    """(display_hour_24, rounded_minute) after snapping to the interval — rolling
    into the next hour when the snap lands on :60."""
    h, m = now.hour, now.minute
    rounded = round(m / interval) * interval
    if rounded == 60:
        rounded = 0
        h = (h + 1) % 24
    return h, rounded


def _interval(settings):
    try:
        interval = int(settings.get('interval', '5'))
    except (TypeError, ValueError):
        interval = 5
    return max(1, min(30, interval))


def _english(h, rounded, cols):
    h12 = h % 12
    hours = ['twelve', 'one', 'two', 'three', 'four', 'five',
             'six', 'seven', 'eight', 'nine', 'ten', 'eleven']
    ones = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
            'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
    tens = ['', '', 'twenty', 'thirty', 'forty', 'fifty']

    def minute_word(n):
        if n == 0:
            return ''
        if n == 15:
            return 'a quarter'
        if n == 30:
            return 'half'
        if n < 20:
            return ones[n]
        t, o = divmod(n, 10)
        return (tens[t] + ' ' + ones[o]).strip() if o else tens[t]

    hour_word = hours[h12]
    next_word = hours[(h12 + 1) % 12]

    if h == 0 and rounded == 0:
        return ["It's", 'midnight', '']
    if h == 12 and rounded == 0:
        return ["It's", 'noon', '']
    if rounded == 0:
        return ["It's", hour_word, "o'clock"]
    if rounded <= 30:
        mw, direction, target = minute_word(rounded), 'past', hour_word
    else:
        mw, direction, target = minute_word(60 - rounded), 'to', next_word

    combined = mw + ' ' + direction
    if len(combined) <= cols:
        return ["It's", combined, target]
    return ["It's", mw, direction + ' ' + target]


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def _wrap(words, cols):
    """Greedily pack words into lines no wider than the display."""
    lines, cur = [], ''
    for w in words:
        cand = w if not cur else f'{cur} {w}'
        if len(cand) <= cols or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit(words, cols, rows):
    lines = _wrap(words, cols)
    if rows and rows >= 1 and len(lines) > rows:      # collapse overflow into the last visible row
        lines = lines[:rows - 1] + [' '.join(lines[rows - 1:])]
    return lines


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    h, rounded = _rounded(_tznow(settings), _interval(settings))
    cols, rows = get_cols(), get_rows()
    lang = (i18n.lang if i18n is not None else 'en')[:2].lower()

    if lang in _BUILDERS:
        lines = _fit(_BUILDERS[lang](h, rounded), cols, rows)
    else:
        lines = _english(h, rounded, cols)
    return [format_lines(*lines)]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The classic word-clock face: a grid of words with the current phrase lit
# bright (hour word in amber) and the rest dimly visible — QLOCKTWO-style, on a
# panel with room for it (English face). Other languages, and panels too small
# for six readable rows, get the same phrase as a lit sentence instead, its
# copula dimmed. Both read the SAME builders as the flap view. Black background.
# =============================================================================

_LIT_COL = (245, 245, 248)      # a lit word
_HOUR_COL = (255, 178, 44)      # the hour word, in amber
_OFF_COL = (44, 47, 56)         # an unlit word — there, but asleep

# The English face: each cell is (word, role, key); _lit_keys decides which
# (role, key) pairs light for a given time. FIVE and TEN appear twice — once as
# minutes, once as hours — and light independently, like the real faces do.
_GRID_EN = [
    [("IT'S", 'on', 0), ('QUARTER', 'min', 15), ('TWENTY', 'min', 20)],
    [('FIVE', 'min', 5), ('TEN', 'min', 10), ('HALF', 'min', 30)],
    [('PAST', 'dir', 'past'), ('TO', 'dir', 'to'), ('ONE', 'hr', 1), ('TWO', 'hr', 2)],
    [('THREE', 'hr', 3), ('FOUR', 'hr', 4), ('FIVE', 'hr', 5), ('SIX', 'hr', 6)],
    [('SEVEN', 'hr', 7), ('EIGHT', 'hr', 8), ('NINE', 'hr', 9), ('TEN', 'hr', 10)],
    [('ELEVEN', 'hr', 11), ('TWELVE', 'hr', 12), ("O'CLOCK", 'min', 0)],
]

# Leading copulas dimmed in the sentence fallback (IT'S / IL EST / ES IST ...).
_COPULAS = {"it's", 'il', 'est', 'es', 'ist', 'son', 'sono', 'le', 'la', 'las',
            'sao', 'het', 'is', "e'"}


def _lit_keys(h, rounded):
    """Which (role, key) cells light for this time — the grid's reading of the
    same sentence the builders speak."""
    disp, dispn = _ctx(h)
    keys = {('on', 0)}
    if rounded == 0:
        keys |= {('min', 0), ('hr', disp)}
        return keys
    mm = rounded if rounded <= 30 else 60 - rounded
    keys.add(('dir', 'past' if rounded <= 30 else 'to'))
    keys.add(('hr', disp if rounded <= 30 else dispn))
    if mm == 25:
        keys |= {('min', 20), ('min', 5)}
    else:
        keys.add(('min', mm))
    return keys


def _grid_font(canvas, W, H):
    """One size for the whole face: the largest at which every row fits the
    width and six rows of ink (with a pixel of breathing each) fill the height.
    Returns (font, ink_height, word_gap, top_offset)."""
    size = max(5, H // 5)
    while size > 5:
        font = canvas.font(size)
        b = font.getbbox('AG')
        ih = b[3] - b[1]
        gap = font.getlength(' ') * 1.4
        widest = max(sum(font.getlength(w) for w, _, _ in row) + gap * (len(row) - 1)
                     for row in _GRID_EN)
        if widest <= W - 4 and len(_GRID_EN) * ih + (len(_GRID_EN) - 1) <= H - 2:
            return font, ih, gap, b[1]
        size -= 1
    font = canvas.font(5)
    b = font.getbbox('AG')
    return font, b[3] - b[1], font.getlength(' '), b[1]


def _draw_grid(canvas, draw, h, rounded):
    W, H = canvas.width, canvas.height
    lit = _lit_keys(h, rounded)
    font, ih, gap, top_off = _grid_font(canvas, W, H)
    rows = len(_GRID_EN)
    # The face rides the edges: the first row's ink on row 1, the last row's on
    # H-2 (the bbox can under-report a pixel — hence not 0 and H-1), the slack
    # spread evenly between the word bands.
    lead = (H - 2 - rows * ih) / (rows - 1)
    for r, row in enumerate(_GRID_EN):
        row_w = sum(font.getlength(w) for w, _, _ in row) + gap * (len(row) - 1)
        x = (W - row_w) / 2.0
        y = 1 + r * (ih + lead) - top_off
        for word, role, key in row:
            on = (role, key) in lit
            col = _OFF_COL if not on else (_HOUR_COL if role == 'hr' else _LIT_COL)
            draw.text((x, y), word, font=font, fill=col)
            x += font.getlength(word) + gap
    return True


def _measure_lines(font, lines):
    """Per-line real ink heights (uppercase lines carry no descender — 'Ag'
    metrics would bank phantom slack) and the inter-line breathing gap."""
    inks = [(lambda b: b[3] - b[1])(font.getbbox(' '.join(ln) or 'A')) for ln in lines]
    return inks, max(1, max(inks) // 5)


def _cv_wrap_fit(canvas, words, max_w, max_h, max_lines):
    """Largest font at which ``words`` pack into <= ``max_lines`` lines fitting
    the box, measured by the lines' real ink. Returns (font, lines, inks, gap)."""
    def pack(font):
        lines, cur, cur_w = [], [], 0.0
        space = font.getlength(' ')
        for w in words:
            ww = font.getlength(w)
            if cur and cur_w + space + ww > max_w:
                lines.append(cur)
                cur, cur_w = [w], ww
                if len(lines) >= max_lines:
                    break
            else:
                cur_w += (space if cur else 0) + ww
                cur.append(w)
        if cur and len(lines) < max_lines:
            lines.append(cur)
        return lines[:max_lines] or [['']]

    size = max(5, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = pack(font)
        inks, gap = _measure_lines(font, lines)
        total = sum(inks) + (len(lines) - 1) * gap
        packed = sum(len(ln) for ln in lines)
        widest = max((sum(font.getlength(w) for w in ln)
                      + font.getlength(' ') * (len(ln) - 1) for ln in lines), default=0)
        if size <= 5 or (total <= max_h and widest <= max_w and packed >= len(words)):
            return font, lines, inks, gap
        size -= 1
    font = canvas.font(5)
    lines = pack(font)
    inks, gap = _measure_lines(font, lines)
    return font, lines, inks, gap


def _draw_sentence(canvas, draw, words):
    """The phrase as a lit sentence: copula dim, the words that carry the time
    bright — the fallback face for other languages and small panels."""
    W, H = canvas.width, canvas.height
    words = [w.upper() for w in words if w]
    font, lines, inks, gap = _cv_wrap_fit(canvas, words, W - 4, H - 2, 3)

    if len(lines) == 1 and len(words) >= 2 and inks[0] < 0.7 * (H - 2):
        # A short phrase ("IT'S NOON") fit one modest line — split it into two
        # width-balanced lines instead and let them grow to fill the height.
        ref = canvas.font(20)
        cut = min(range(1, len(words)),
                  key=lambda c: max(ref.getlength(' '.join(words[:c])),
                                    ref.getlength(' '.join(words[c:]))))
        cand = [words[:cut], words[cut:]]
        size = H - 2
        while size > 5:
            f2 = canvas.font(size)
            inks2, gap2 = _measure_lines(f2, cand)
            widest = max(sum(f2.getlength(w) for w in ln)
                         + f2.getlength(' ') * (len(ln) - 1) for ln in cand)
            if widest <= W - 4 and sum(inks2) + gap2 <= H - 2:
                break
            size -= 1
        if min(inks2) > inks[0]:
            font, lines, inks, gap = f2, cand, inks2, gap2

    # Vertically justified: the first line's ink on row 1, the last line's
    # ending on H-2, the slack shared between the lines (a lone line centers).
    n = len(lines)
    lead = ((H - 2 - sum(inks)) / (n - 1)) if n > 1 else 0.0
    y = 1 if n > 1 else (H - inks[0]) / 2.0
    idx = 0
    for ln, ih in zip(lines, inks):
        line_w = sum(font.getlength(w) for w in ln) + font.getlength(' ') * (len(ln) - 1)
        x = (W - line_w) / 2.0
        for w in ln:
            dim = idx < 2 and w.casefold() in _COPULAS
            b = font.getbbox(w)
            draw.text((x, y - b[1]), w, font=font,
                      fill=(96, 100, 112) if dim else _LIT_COL)
            x += font.getlength(w) + font.getlength(' ')
            idx += 1
        y += ih + lead
    return True


def fetch_matrix(settings, canvas, i18n=None):
    from PIL import ImageDraw

    now = _tznow(settings)
    lang = (i18n.lang if i18n is not None else 'en')[:2].lower()
    interval = _interval(settings)

    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    W, H = canvas.width, canvas.height
    if lang not in _BUILDERS and W >= 110 and H >= 48:
        # The full face. Its words exist at five-minute resolution — a finer
        # interval snaps to the nearest five for the grid, as every real word
        # clock does; coarser intervals (15/30) it honors exactly.
        h, rounded = _rounded(now, max(5, interval))
        _draw_grid(canvas, draw, h, rounded)
    else:
        h, rounded = _rounded(now, interval)
        if lang in _BUILDERS:
            words = _BUILDERS[lang](h, rounded)
        else:
            words = [w for chunk in _english(h, rounded, 10 ** 6) for w in chunk.split()]
        _draw_sentence(canvas, draw, words)

    canvas.frame(img)
    # The sentence can only change on a minute boundary.
    return max(1.0, 60.0 - now.second - now.microsecond / 1e6)
