# =============================================================================
# SHARED — the elapsed time itself: timezone + event parsing used by both
# surfaces, and the milestone trigger (surface-independent).
# =============================================================================

def _tz(settings):
    import pytz
    try:
        return pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        return pytz.utc


def _event(settings):
    """(event_name, start_datetime_or_None, now) — both views count from here."""
    from datetime import datetime
    tz = _tz(settings)
    now = datetime.now(tz)
    event = settings.get('event_name', 'The start')
    date_str = settings.get('event_date', '2024-01-01')
    try:
        start = tz.localize(datetime.strptime(date_str, '%Y-%m-%d'))
    except Exception:
        start = None
    return event, start, now


def trigger(settings, conditions):
    """Fire when the elapsed time hits a round milestone."""
    from datetime import datetime
    import pytz

    milestone = conditions.get('milestone', '1y')
    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)
    date_str = settings.get('event_date', '2024-01-01')

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'fired_milestone': None}
        setattr(trigger, '_state', state)

    try:
        start = tz.localize(datetime.strptime(date_str, '%Y-%m-%d'))
        diff = now - start
        if diff.total_seconds() < 0:
            return False
        days = diff.days

        # Map milestone to day windows
        windows = {
            '100d': (100, 101),
            '365d': (365, 366),
            '1y':   (365, 366),
            '2y':   (730, 731),
            '5y':   (1825, 1826),
            '10y':  (3650, 3651),
        }
        lo, hi = windows.get(milestone, (365, 366))
        in_window = lo <= days < hi
        key = f"{milestone}:{date_str}:{lo}"

        if in_window and state['fired_milestone'] != key:
            state['fired_milestone'] = key
            return True
        if not in_window and state['fired_milestone'] == key:
            state['fired_milestone'] = None
    except Exception:
        raise
    return False


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
    def t(s):
        return i18n.t(s, "time") if i18n is not None else s

    def u(k):                       # localized Y/D/H/M/S suffix
        return i18n.unit(k) if i18n is not None else k

    event, start, now = _event(settings)
    if start is None:
        return [format_lines(event, t('Invalid date'), '')]
    diff = now - start
    if diff.total_seconds() < 0:
        return [format_lines(event, t('Not yet'), t('Started'))]
    days = diff.days
    hrs, rem = divmod(diff.seconds, 3600)
    mins, secs = divmod(rem, 60)
    years = days // 365
    remaining_days = days % 365
    # A live seconds counter means a flip every second, forever. Only a wall that
    # repaints (caps.instant) gets it; a mechanical wall shows minutes and moves
    # once a minute. It must also actually fit: "364D 23H 59M 59S" is 16 wide.
    instant = bool(getattr(caps, 'instant', False))
    if years > 0:
        elapsed = f'{years}{u("Y")} {remaining_days}{u("D")} {hrs}{u("H")}'
    else:
        elapsed = f'{days}{u("D")} {hrs}{u("H")} {mins}{u("M")}'
        with_secs = f'{elapsed} {secs}{u("S")}'
        if instant and len(with_secs) <= get_cols():
            elapsed = with_secs
    lines = [event, elapsed, t('Time since')]
    if get_rows() >= 4 and i18n is not None:
        lines.append(i18n.date(start, year=True))    # the wall has room: since when
    return [format_lines(*lines)]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A big elapsed-time counter: the event name up top, the counter as the hero —
# values in white, unit letters in amber — and "TIME SINCE <date>" as a quiet
# footer where there is room. Same unit ladder as the flap view (years lead once
# past a year; seconds tick only while the count is young enough to fit them).
# Black background.
# =============================================================================

_NAME_COL = (245, 245, 248)
_VAL_COL = (245, 245, 248)
_UNIT_COL = (255, 178, 44)
_FOOT_COL = (132, 136, 148)


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 8px)."""
    size = max(8, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 8 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_trim(canvas, text, max_w, max_h, min_ink=6):
    """Like _cv_fit, but rather than shrinking a long text into illegibility it
    keeps a readable size and trims with an ellipsis. Returns (font, text)."""
    font = _cv_fit(canvas, text, max_w, max_h)
    b = font.getbbox(text or '0')
    if not text or b[3] - b[1] >= min_ink:
        return font, text
    font = _cv_fit(canvas, 'AG', max_w, max_h)          # height-bound size
    while text and font.getlength(text + '…') > max_w:
        text = text[:-1].rstrip()
    return font, (text + '…') if text else '…'


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (invalid date / not started yet)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    b1 = f1.getbbox(line1)
    h1 = b1[3] - b1[1]
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = (f2.getbbox(line2)[3] - f2.getbbox(line2)[1]) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    draw.text(((W - f1.getlength(line1)) / 2.0, y - b1[1]), line1, font=f1, fill=_NAME_COL)
    if line2:
        y += h1 + gap
        b2 = f2.getbbox(line2)
        draw.text(((W - f2.getlength(line2)) / 2.0, y - b2[1]), line2, font=f2, fill=_FOOT_COL)
    return img


def _segments(diff, u, with_secs):
    """The counter as [(value, UNIT), ...] — the same ladder the flap view prints."""
    days = diff.days
    hrs, rem = divmod(diff.seconds, 3600)
    mins, secs = divmod(rem, 60)
    years = days // 365
    if years > 0:
        return [(str(years), u('Y')), (str(days % 365), u('D')), (str(hrs), u('H'))]
    segs = [(str(days), u('D')), (str(hrs), u('H')), (str(mins), u('M'))]
    if with_secs:
        segs.append((f'{secs}', u('S')))
    return segs


def fetch_matrix(settings, canvas, i18n=None, caps=None):
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "time") if i18n is not None else s

    def u(k):
        return i18n.unit(k) if i18n is not None else k

    event, start, now = _event(settings)
    W, H = canvas.width, canvas.height
    if start is None:
        canvas.frame(_cv_message(canvas, ImageDraw, event.upper(), t('Invalid date').upper()))
        return 60.0
    diff = now - start
    if diff.total_seconds() < 0:
        canvas.frame(_cv_message(canvas, ImageDraw, event.upper(), t('Not yet').upper()))
        return 60.0

    years = diff.days // 365
    # Seconds only while the count is young (no years yet) and the panel is wide
    # enough that they don't crush the digits — the flap view's fit rule, in pixels.
    with_secs = years == 0 and W >= 96
    segs = _segments(diff, u, with_secs)

    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    # Layout: event name pinned to the very top row, "TIME SINCE <date>" pinned
    # to the very bottom (tall panels), and the counter — the hero — grown to
    # fill everything between, splitting onto two lines when a single line
    # would be width-bound and leave the digits small. (Ink starts/ends 1px in:
    # the reported bbox can under-report a pixel.)
    name = event.upper()
    nf, name = _cv_trim(canvas, name, W - 4, max(8, int(H * 0.22)))
    nb = nf.getbbox(name)
    nh = nb[3] - nb[1]

    foot = ''
    if H >= 48:
        since_date = i18n.date(start, year=True) if i18n is not None else start.strftime('%b %d %Y')
        foot = f'{t("Time since")} {since_date}'.upper()
    ff = _cv_fit(canvas, foot, W - 4, max(7, int(H * 0.15))) if foot else None
    fb = ff.getbbox(foot) if foot else None
    fh = (fb[3] - fb[1]) if foot else 0

    gap = max(2, H // 16)
    box_top = 1 + nh + gap                          # counter region, edge to edge
    box_bot = (H - 2 - fh - gap) if foot else H - 2  # last ink row the counter may take
    box_h = max(6, box_bot - box_top + 1)

    def row_text(r):
        return ' '.join(v + s for v, s in r)

    # The counter: fit "12D 4H 33M 21S" as one string for sizing, then draw it
    # segment by segment so the unit letters can take the accent color.
    rows = [segs]
    cf = _cv_fit(canvas, row_text(segs), W - 2, box_h)
    cb = cf.getbbox(row_text(segs))
    rgap = 0
    if len(segs) >= 2 and (cb[3] - cb[1]) < 0.58 * box_h:
        # Width-bound: two balanced lines double the digit size.
        ref = canvas.font(20)
        cut = min(range(1, len(segs)),
                  key=lambda c: max(ref.getlength(row_text(segs[:c])),
                                    ref.getlength(row_text(segs[c:]))))
        rows = [segs[:cut], segs[cut:]]
        rgap = max(1, gap // 2)
        wide = max(rows, key=lambda r: ref.getlength(row_text(r)))
        cf = _cv_fit(canvas, row_text(wide), W - 2, (box_h - rgap) / 2.0)

    heights = [(lambda b: b[3] - b[1])(cf.getbbox(row_text(r))) for r in rows]
    block = sum(heights) + rgap * (len(rows) - 1)
    # Unit letters a step smaller than the values, sharing the baseline — but on
    # a counter already small, one size for both beats two illegible ones.
    ref_h = max(heights)
    uf = _cv_fit(canvas, 'D', int(W * 0.2), int(ref_h * 0.62)) if ref_h >= 12 else cf

    draw.text(((W - nf.getlength(name)) / 2.0, 1 - nb[1]), name, font=nf, fill=_NAME_COL)

    # With a footer the counter centers between the pinned edges; without one it
    # anchors to the bottom so no dark band is left under it.
    y = (box_top + (box_h - block) / 2.0) if foot else (box_bot - block + 1)
    for r, rh in zip(rows, heights):
        widths = [(cf.getlength(v), uf.getlength(s)) for v, s in r]
        space = max(2.0, cf.getlength(' ') * 0.8)
        line_w = sum(vw + uw for vw, uw in widths) + space * (len(r) - 1)
        x = (W - line_w) / 2.0
        base = y + rh                               # shared baseline for values and units
        for (v, s), (vw, uw) in zip(r, widths):
            vb = cf.getbbox(v)
            draw.text((x, base - (vb[3] - vb[1]) - vb[1]), v, font=cf, fill=_VAL_COL)
            x += vw
            sb = uf.getbbox(s)
            draw.text((x, base - (sb[3] - sb[1]) - sb[1]), s, font=uf, fill=_UNIT_COL)
            x += uw + space
        y += rh + rgap

    if foot:
        draw.text(((W - ff.getlength(foot)) / 2.0, H - 1 - fh - fb[1]), foot,
                  font=ff, fill=_FOOT_COL)

    canvas.frame(img)
    if with_secs:
        return max(0.05, 1.0 - now.microsecond / 1e6)       # tick on the second
    # Coarser counters advance on the minute (or the hour, once years lead) —
    # redraw on the minute either way; it is cheap and never visibly ticks.
    return max(1.0, 60.0 - now.second - now.microsecond / 1e6)
