"""Word clock — spells the time out ("It's half past ten").

Localized into the major Western-European languages when the companion injects
i18n (honoring the global Language). Each language has its own grammar — Romance
languages name the hour first (Il est dix heures et quart), Germanic ones lead
with the minutes (Viertel nach zehn), and German/Dutch "half" points at the *next*
hour (halb drei = 2:30). Anything without a builder (or a bare host) falls back to
the original English behavior.
"""

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


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    from datetime import datetime
    import pytz
    try:
        tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone('US/Eastern')
    now = datetime.now(tz)
    h, m = now.hour, now.minute

    try:
        interval = int(settings.get('interval', '5'))
    except (TypeError, ValueError):
        interval = 5
    interval = max(1, min(30, interval))

    rounded = round(m / interval) * interval
    if rounded == 60:
        rounded = 0
        h = (h + 1) % 24

    cols, rows = get_cols(), get_rows()
    lang = (i18n.lang if i18n is not None else 'en')[:2].lower()

    if lang in _BUILDERS:
        lines = _fit(_BUILDERS[lang](h, rounded), cols, rows)
    else:
        lines = _english(h, rounded, cols)
    return [format_lines(*lines)]
