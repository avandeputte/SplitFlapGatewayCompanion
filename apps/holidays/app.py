"""Upcoming holidays for your location — from a bundled ten-year dataset.

The data ships WITH the app (data/<locale>.json: 2026-2035 from
python-holidays, one file per language-region locale, Windows-1252-safe;
rebuilt by scripts/extract_holidays.py) — no API, no network, no key. Each
holiday is one record with its ten dates (an estimated lunar date is prefixed
``~``). Which calendar to show is driven by the configured LOCATION (the language
can't tell France from Canada from Switzerland), down to the province/state:
in Quebec you get Quebec's holidays, not the other provinces'. The names come
already localized when a locale exists for the wall's Language in that country
(fr-CA in Quebec); otherwise any locale for the country, with the catalog's
holiday-name translations as a last resort. An explicit Country setting
overrides the location; the Language's country is the last resort.

PUBLIC holidays always show — they are days off whatever their origin, so
Christmas Day never disappears from a calendar it is statutory in. The
"religious observances" toggle ADDS the non-public religious layer (Christmas
Eve, Yom Kippur, Diwali, Eid al-Fitr...), filtered to the traditions picked in
settings. Dates the source computes rather than observes (some lunar-calendar
dates are announced by sighting) are marked estimated and shown with a ~.

Two further layers, each with its own switch and its own files (M/D-keyed — they
recur yearly — and kept OUT of data/, which scripts/extract_holidays.py wipes on a
dataset rebuild):

  * cultural/<locale>.json — genuine curated traditions (April Fools',
    Nikolaus, Burns Night, Día del Amigo), locale-picked like the dataset;
  * fun.json — the international one-a-day novelty calendar ("National Donut
    Day"), English, OFF by default: 366 a year would drown the real holidays.
"""


def _wrap(text, cols, maxlines):
    """Word-wrap, because a holiday name is the whole point of the app and cutting
    it in half ("MARTIN LUTHER KING J") is worse than using another row."""
    words, lines, cur = str(text or '').split(), [], ''
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= cols:
            cur = f'{cur} {w}'.strip()
        else:
            if cur:
                lines.append(cur)
            cur = w[:cols]
            if len(lines) >= maxlines:
                break
    if cur and len(lines) < maxlines:
        lines.append(cur)
    return lines[:maxlines] or ['']


TRADITIONS = ('christian', 'islamic', 'jewish', 'hindu', 'buddhist', 'sikh')


def _dataset(locale):
    """One locale file out of the bundled data/ dir, parsed once per process."""
    import json
    import os
    cache = getattr(_dataset, '_cache', None)
    if cache is None:
        cache = _dataset._cache = {}
    if locale in cache:
        return cache[locale]
    path = os.path.join(os.path.dirname(__file__), 'data', f'{locale}.json')
    with open(path, encoding='utf-8') as f:
        doc = json.load(f)
    cache[locale] = doc
    return doc


def _pick_locale(lang, country):
    """The data/ locale file for (language, country): the wall's own language in
    that country when it exists, else English there, else any locale for the
    country. None when nothing exists for the country at all. The directory
    listing IS the index."""
    import os
    ddir = os.path.join(os.path.dirname(__file__), 'data')
    try:
        have = {f[:-5] for f in os.listdir(ddir)
                if f.endswith('.json') and not f.startswith('_')}
    except OSError:
        return None
    cc = country.lower()
    for want in (f'{lang}-{cc}', f'en-{cc}'):
        if want in have:
            return want
    for loc in sorted(have):
        if loc.endswith(f'-{cc}'):
            return loc
    return None


def _next_recurring(md, today):
    """Next occurrence (>= today) of an ``M/D`` recurring date, or None. Feb 29
    correctly skips non-leap years to the next leap year."""
    from datetime import date
    try:
        m, d = (int(x) for x in md.split('/'))
    except (ValueError, AttributeError):
        return None
    for year in range(today.year, today.year + 9):
        try:
            dt = date(year, m, d)
        except ValueError:
            continue
        if dt >= today:
            return dt
    return None


def _next_date(record, today):
    """When a record next occurs, as (date, estimated), or None. A dataset record
    carries an explicit ten-year ``dates`` list (an ``~`` marks a computed lunar
    date); a cultural record recurs every year on a fixed ``M/D``."""
    from datetime import datetime
    if record.get('dates'):
        for ds in record['dates']:
            estimated = ds.startswith('~')
            try:
                dt = datetime.strptime(ds.lstrip('~'), '%Y-%m-%d').date()
            except ValueError:
                continue
            if dt >= today:
                return dt, estimated
        return None
    if record.get('recurs'):
        dt = _next_recurring(record['recurs'], today)
        return (dt, False) if dt else None
    return None


def _fun_days(today):
    """The one global novelty calendar (fun.json, English) as records — a single
    file, not per-locale, so it stays separate from the localized dataset."""
    import json
    import os
    path = os.path.join(os.path.dirname(__file__), 'fun.json')
    try:
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
    except Exception:
        return []
    out = []
    for md, names in (doc.items() if isinstance(doc, dict) else ()):
        dt = _next_recurring(md, today)
        if dt:
            out += [(dt, {'name': n}, False) for n in names]
    return out


def _upcoming(settings, i18n, get_location):
    """The deduped, date-sorted upcoming holidays as ``[(date, record, estimated), ...]`` plus the
    resolved country code — shared by the flap pages and the canvas desk-calendar view, so the two
    views always agree on WHICH holidays and in what order. Empty list on a broken install."""
    from datetime import date

    def on(key, default=''):
        return str(settings.get(key, default) or default).strip().lower() in \
            {'1', 'true', 'yes', 'on'}

    loc = (get_location() or {}) if get_location is not None else {}
    country = str(settings.get('country', '') or '').strip().upper()[:2]
    if not country:
        country = str(loc.get('country') or '') or (i18n.country() if i18n is not None else 'US')
    # Province/state (e.g. CA-QC) — only trusted when it belongs to the country we're
    # actually showing (an explicit Country setting can differ from the location).
    subdivision = str(loc.get('subdivision') or '')
    if subdivision and not subdivision.startswith(country):
        subdivision = ''

    lang = i18n.lang_base if i18n is not None else 'en'
    want_religious = on('religious_holidays')
    traditions = {tr for tr in TRADITIONS if on(f'tradition_{tr}', 'on')}
    want_cultural = on('cultural_traditions', 'on')
    want_fun = on('fun_days')          # off unless asked: one EVERY day

    locale = _pick_locale(lang, country)
    # No dataset locale for the country is not the end: the cultural and fun
    # layers can still carry the page (and the empty fallback says "None found"
    # if every layer comes up dry).
    entries = _dataset(locale).get('holidays', []) if locale else []

    today = date.today()
    upcoming = []
    for h in entries:
        # Nationwide holidays + our own province/state's; drop other regions'
        # (so Quebec doesn't list British Columbia Day).
        subs = h.get('subdivisions')
        if subs and (not subdivision or subdivision not in subs):
            continue
        # Category gate. Every record is tagged: cultural traditions (April
        # Fools', Nikolaus) show under their own switch; a non-public
        # religious observance needs the religious switch AND its tradition;
        # a public holiday is a day off and always shows.
        if h.get('cultural'):
            if not want_cultural:
                continue
        elif not h.get('public'):
            if not (want_religious and h.get('religious')
                    and h.get('tradition') in traditions):
                continue
        nd = _next_date(h, today)
        if nd:
            upcoming.append((nd[0], h, nd[1]))

    # The one layer that isn't per-locale: the global novelty calendar.
    if want_fun:
        upcoming += _fun_days(today)

    upcoming.sort(key=lambda e: e[0])

    # A tradition can shadow a dataset entry under the same name on the same day
    # (Assomption is both a public holiday and a cultural fixture) — show it once.
    seen, deduped = set(), []
    for dt, h, est in upcoming:
        k = (dt, str(h.get('name', '')).casefold())
        if k not in seen:
            seen.add(k)
            deduped.append((dt, h, est))
    return deduped, country


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_location=None):
    from datetime import date
    rows, cols = get_rows(), get_cols()

    def t(s, ctx="time"):
        return i18n.t(s, ctx) if i18n is not None else s

    try:
        upcoming, country = _upcoming(settings, i18n, get_location)
        today = date.today()

        pages = []
        for dt, h, estimated in upcoming[:4]:
            localized = i18n.holiday(h.get('name')) if i18n is not None else None
            name = str(localized or h.get('name') or '')
            days = (dt - today).days
            cd = t('Today') if days == 0 else f'{t("In")} {days} {t("days")}'
            if i18n is not None:
                dow = i18n.weekday(dt, short=True)
                # "MON SEPTEMBER 7" is already 15 — one more letter and the wall
                # would truncate it. Shorten the month rather than lose the day.
                when = f'{dow} {i18n.date(dt)}'
                if len(when) > cols:
                    when = f'{dow} {i18n.date(dt, short=True)}'
            else:
                when = dt.strftime('%a %b %d')
            if estimated:
                when = f'~{when}'[:cols]     # a computed lunar date, not an announced one

            head = t('Next holiday', 'holidays')
            if rows == 1:
                pages.append(f'{name} {cd}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(name, cd))
            elif rows == 3:
                # Three rows is the common wall, and it has exactly one to spare. A name
                # that fits keeps the header; one that doesn't takes the header's row
                # rather than being truncated — "NEXT HOLIDAY" says less than the name.
                if len(name) <= cols:
                    pages.append(format_lines(head, name, cd))
                else:
                    pages.append(format_lines(*_wrap(name, cols, 2), cd))
            else:
                # A tall wall gives the name as many rows as it needs, and spends what's
                # left on the date rather than on blank flaps.
                lines = [head] + _wrap(name, cols, rows - 2) + [cd]
                if when and len(lines) < rows:
                    lines.insert(-1, when)
                pages.append(format_lines(*lines))
        return pages or [format_lines('Holidays', t('None found', 'holidays'), country)]
    except Exception:
        # The dataset is local — a failure here is a broken install, not weather.
        return [format_lines('Holidays', t('No data', 'holidays'), '')]


# ---------------------------------------------------------------------------
# Canvas view — a rich desk-calendar rendering for a Matrix panel. The same
# upcoming holidays as the flap pages (via _upcoming), shown one at a time as a
# slideshow: a red-banded calendar card (month + big day number) with the
# holiday name and countdown beside it. On a panel too small for the card to sit
# next to the text it drops to a compact stacked layout (a date strip over the
# wrapped name) so it stays legible down to a 64x32 wall.
# ---------------------------------------------------------------------------

_CARD, _CARD_EDGE = (244, 244, 246), (208, 208, 214)   # the desk-calendar card
_BAND = (206, 52, 52)                                  # its classic red month band
_DAY = (0, 0, 0)                                       # the big day number — solid black
_SUB = (150, 150, 158)                                 # the weekday under it
_NAME = (238, 238, 244)                                # the holiday name
_SOON = (255, 180, 60)                                 # "IN N DAYS" amber
_TODAY = (80, 220, 120)                                # "TODAY" green


def _cv_shadow(draw, x, y, text, font, fill):
    """Text with a 1px dark outline on all sides, so it stays legible over any backdrop."""
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0), anchor='la')
    draw.text((x, y), text, font=font, fill=fill, anchor='la')


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


def _cv_wrap_fit(canvas, text, max_w, max_h, max_lines):
    """Pick the largest font at which ``text`` word-wraps into <= ``max_lines`` lines that together
    fit ``max_w`` x ``max_h``. Returns (font, lines, line_height, gap)."""
    size = max(5, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = _cv_wrap(font, text, max_w, max_lines)
        b = font.getbbox('Ag')
        lh = b[3] - b[1]
        gap = max(1, lh // 6)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((font.getlength(ln) for ln in lines), default=0)
        if size <= 5 or (total <= max_h and widest <= max_w):
            return font, lines, lh, gap
        size -= 1
    font = canvas.font(5)
    lines = _cv_wrap(font, text, max_w, max_lines)
    b = font.getbbox('Ag')
    return font, lines, b[3] - b[1], 1


def _cv_when(dt, days, i18n):
    """The countdown line and its colour: 'TODAY' (green) or 'IN N DAYS' (amber)."""
    def t(s):
        return i18n.t(s, 'time') if i18n is not None else s
    if days <= 0:
        return t('Today').upper(), _TODAY
    unit = t('day') if days == 1 else t('days')
    return f'{t("In")} {days} {unit}'.upper(), _SOON


def _cv_month_day(dt, i18n):
    """(MONTH short, day number, weekday short) — localized where an i18n is present."""
    if i18n is not None:
        return (str(i18n.month(dt, short=True)).upper(), str(dt.day),
                str(i18n.weekday(dt, short=True)).upper())
    return dt.strftime('%b').upper(), str(dt.day), dt.strftime('%a').upper()


def _cv_card(canvas, ImageDraw, dt, name, days, estimated, i18n):
    """The full desk-calendar view: the calendar card beside the name + countdown on a wide-enough
    panel, else a compact stacked layout (date strip over the wrapped name)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))          # black under the name — no backdrop wash
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    mon, day, dow = _cv_month_day(dt, i18n)
    when, when_col = _cv_when(dt, days, i18n)
    pre = '~' if estimated else ''

    # Side-by-side calendar card only where it earns its space; otherwise stack.
    if W >= 104 and H >= 34:
        m = 3
        cs = H - 2 * m                                   # a square card filling the height
        cs = min(cs, int(W * 0.42))
        x0, y0 = m, m
        band_h = max(9, int(cs * 0.34))
        # card body + red month band + big day number
        draw.rounded_rectangle([x0, y0, x0 + cs, y0 + cs], radius=3, fill=_CARD, outline=_CARD_EDGE)
        draw.rounded_rectangle([x0, y0, x0 + cs, y0 + band_h], radius=3, fill=_BAND)
        draw.rectangle([x0, y0 + band_h - 3, x0 + cs, y0 + band_h], fill=_BAND)   # square off the band's base
        mf = _cv_fit(canvas, mon, cs - 6, band_h - 3)
        mb = mf.getbbox(mon)
        draw.text((x0 + (cs - mf.getlength(mon)) / 2.0,
                   y0 + (band_h - (mb[3] - mb[1])) / 2.0 - mb[1]), mon, font=mf, fill=(255, 255, 255))
        low_h = cs - band_h
        df = _cv_fit(canvas, day, cs - 6, int(low_h * 0.78))
        db = df.getbbox(day)
        draw.text((x0 + (cs - df.getlength(day)) / 2.0,
                   y0 + band_h + (low_h - (db[3] - db[1])) / 2.0 - db[1] - 1), day, font=df, fill=_DAY)

        # Right column: the holiday name (as many lines as fit) over the countdown.
        rx = x0 + cs + 5
        rw = W - 3 - rx
        cf = _cv_fit(canvas, when, rw, max(7, int(H * 0.20)))
        cb = cf.getbbox(when)
        ch = cb[3] - cb[1]
        name_h = H - 4 - ch - 2
        nf, lines, lh, gap = _cv_wrap_fit(canvas, pre + name, rw, name_h, 3)
        block = len(lines) * lh + (len(lines) - 1) * gap
        ny = max(2.0, (name_h - block) / 2.0 + 2)
        for ln in lines:
            _cv_shadow(draw, rx, ny - nf.getbbox(ln)[1], ln, nf, _NAME)
            ny += lh + gap
        _cv_shadow(draw, rx, H - 3 - ch - cb[1], when, cf, when_col)
        return img

    # Compact: a coloured date strip across the top, the wrapped name filling the rest.
    strip = f'{dow} {mon} {day}'
    sf = _cv_fit(canvas, strip, W - 6, max(7, int(H * 0.30)))
    sb = sf.getbbox(strip)
    sh = sb[3] - sb[1]
    _cv_shadow(draw, (W - sf.getlength(strip)) / 2.0, 2 - sb[1], strip, sf, when_col)
    # a thin divider under the strip
    dv = int(2 + sh + 3)
    draw.line([(3, dv), (W - 4, dv)], fill=_BAND)
    body_top = dv + 2
    body_h = H - body_top - 2
    nf, lines, lh, gap = _cv_wrap_fit(canvas, pre + name, W - 6, body_h, 3)
    block = len(lines) * lh + (len(lines) - 1) * gap
    ny = body_top + max(0.0, (body_h - block) / 2.0)
    for ln in lines:
        _cv_shadow(draw, (W - nf.getlength(ln)) / 2.0, ny - nf.getbbox(ln)[1], ln, nf, _NAME)
        ny += lh + gap
    return img


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (no holidays / broken install)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))          # black backdrop
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    b1 = f1.getbbox(line1)
    h1 = b1[3] - b1[1]
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = (f2.getbbox(line2)[3] - f2.getbbox(line2)[1]) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    _cv_shadow(draw, (W - f1.getlength(line1)) / 2.0, y - b1[1], line1, f1, _NAME)
    if line2:
        y += h1 + gap
        _cv_shadow(draw, (W - f2.getlength(line2)) / 2.0, y - f2.getbbox(line2)[1], line2, f2, _SUB)
    return img


def fetch_matrix(settings, canvas, i18n=None, get_location=None):
    """Draw one upcoming holiday as a desk-calendar frame, advancing through the list each redraw
    (a slideshow paced by the app's ``loop_delay``). Panel-adaptive; offline-safe."""
    from datetime import date
    from PIL import ImageDraw
    try:
        upcoming, country = _upcoming(settings, i18n, get_location)
    except Exception:
        canvas.frame(_cv_message(canvas, ImageDraw, 'HOLIDAYS', 'NO DATA'))
        return 30.0
    if not upcoming:
        canvas.frame(_cv_message(canvas, ImageDraw, 'NO HOLIDAYS', country))
        return 30.0

    items = upcoming[:8]
    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'i': 0}
        setattr(fetch_matrix, '_state', st)
    idx = st['i'] % len(items)
    st['i'] = (st['i'] + 1) % len(items)

    dt, rec, estimated = items[idx]
    name = str((i18n.holiday(rec.get('name')) if i18n is not None else None)
               or rec.get('name') or '').upper()
    days = (dt - date.today()).days
    canvas.frame(_cv_card(canvas, ImageDraw, dt, name, days, estimated, i18n))
    try:
        dwell = float(settings.get('loop_delay', 6) or 6)
    except (TypeError, ValueError):
        dwell = 6.0
    return max(3.0, min(30.0, dwell))
