"""Today's tide predictions for a NOAA station (keyless: NOAA CO-OPS)."""


# =============================================================================
# SHARED — the tide DATA both surfaces read: one NOAA CO-OPS call for today's
# extremes, and the local-time formatter.
# =============================================================================


def _predictions(station):
    """Today's high/low predictions for a NOAA station, in station-local time —
    [{'t': 'YYYY-MM-DD HH:MM', 'type': 'H'|'L', 'v': feet-string}, ...]."""
    import requests
    data = requests.get(
        'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter',
        params={'product': 'predictions', 'application': 'SplitFlapCompanion',
                'datum': 'MLLW', 'station': station, 'time_zone': 'lst_ldt',
                'units': 'english', 'interval': 'hilo', 'format': 'json', 'date': 'today'},
        timeout=8).json()
    return data.get('predictions') or []


def _fmt_time(hhmm, i18n):
    """NOAA's 24h local time ('15:48') in the wall's own style. AM/PM is
    English-only — everyone else gets 24h."""
    from datetime import datetime
    try:
        dt = datetime.strptime(hhmm, '%H:%M')
    except ValueError:
        return hhmm
    if i18n is not None:
        return i18n.time(dt, ampm_space=False)
    return dt.strftime('%I:%M%p').lstrip('0')


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================


def _columns(pairs, cols, gap=3):
    """Two aligned columns — time flush left, height flush right — kept together as
    one CENTERED block rather than pinned to the wall's edges.

    format_lines centers each line, so the block is only as wide as its content plus a
    small gap: on a wide wall the time and its height sit together in the middle, not
    stranded at opposite ends. The heights still line up in a column you can read down
    (every line the same width). A narrow wall falls back to the full width."""
    pairs = [(str(left), str(right)) for left, right in pairs]
    rw = max((len(r) for _, r in pairs), default=0)
    lw = max((len(l) for l, _ in pairs), default=0)
    inner = min(cols, lw + gap + rw)
    lspace = max(1, inner - rw)                       # time column width, incl. the gap
    out = []
    for left, right in pairs:
        if len(left) > lspace - 1:
            left = left[:max(0, lspace - 1)]
        out.append((left.ljust(lspace) + right.rjust(rw))[:cols])
    return out


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "tides") if i18n is not None else s

    def fmt_time(hhmm):
        return _fmt_time(hhmm, i18n)

    station = str(settings.get('tide_station', '8443970') or '8443970').strip()
    try:
        preds = _predictions(station)
        if not preds:
            return [format_lines(t('Tides'), t('No data'), t('Check station'))]
        # Four rows or more: today's tides are a LIST, and a list belongs on one page.
        # One tide per page meant waiting through four page turns to answer "when is high
        # tide?" — a question the whole app exists to answer at a glance.
        if rows >= 4:
            # An arrow says high-or-low in ONE cell, which on a 15-wide wall is the
            # difference between "HIGH 9:28AM" not fitting and "↑ 9:28AM  11.2FT" fitting
            # with room to spare. Only where the wall HAS arrows: on a real reel a ↑ falls
            # back to "^", which is not what you want a tide table to say — so there it
            # keeps the word, and shortens it to an initial only if it must.
            arrows = bool(caps and caps.pictographs)
            pairs = []
            for p in preds[:rows - 1]:
                raw = str(p.get('t', ''))
                hhmm = fmt_time(raw.split(' ')[-1] if ' ' in raw else raw)
                is_high = p.get('type') == 'H'
                height = f"{p.get('v', '')}FT"
                if arrows:
                    kind = '\u2191' if is_high else '\u2193'
                else:
                    kind = t('High') if is_high else t('Low')
                left = f'{kind} {hhmm}'
                if len(left) + len(height) + 1 > cols:   # narrow wall: initial will do
                    left = f'{kind[:1]} {hhmm}'
                pairs.append((left, height))
            return [format_lines(t('Tides'), *_columns(pairs, cols))]

        pages = []
        for p in preds[:6]:
            raw = str(p.get('t', ''))
            hhmm = fmt_time(raw.split(' ')[-1] if ' ' in raw else raw)
            is_high = p.get('type') == 'H'
            v = str(p.get('v', ''))
            if rows == 1:
                # Compact single row: the short generic high/low word keeps the
                # time + height on one line (the full "X TIDE" would crowd them out).
                kind = t('High') if is_high else t('Low')
                pages.append(f'{kind} {hhmm} {v}FT'[:cols].center(cols))
            elif rows == 2:
                kind = t('High tide') if is_high else t('Low tide')
                pages.append(format_lines(kind, f'{hhmm}  {v}FT'))
            else:
                kind = t('High tide') if is_high else t('Low tide')
                pages.append(format_lines(kind, hhmm, f'{v} FT'))
        return pages or [format_lines(t('Tides'), t('No data'), '')]
    except Exception:
        return [format_lines(t('Tides'), t('Offline'), '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The day's tide as a drawn curve (cosine between the NOAA extremes, water
# filled below), the next high/low called out above it, 'now' marked on the
# water. Solid black background; adaptive down to 64x32.
# =============================================================================


_CV_TEXT = (238, 238, 244)                 # primary text
_CV_DIM = (150, 150, 158)                  # secondary text
_CV_SEA = (64, 186, 250)                   # the curve
_CV_SEA_FILL = (10, 42, 84)                # the water under it
_CV_NOW = (255, 255, 255)                  # the 'now' dot


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 8px —
    smaller sizes render wrong-reading glyphs on the panel)."""
    size = max(8, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 8 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_ink(font, text):
    """Ink height of ``text`` in ``font``."""
    b = font.getbbox(text or '0')
    return b[3] - b[1]


def _cv_text(draw, x, y, text, font, fill):
    """Draw with the ink's TOP at ``y`` (bbox-corrected), left edge at ``x``."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill, anchor='la')


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message on black (offline / bad station) — never a crash,
    never a blank panel."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    h1 = _cv_ink(f1, line1)
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = _cv_ink(f2, line2) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    _cv_text(draw, (W - f1.getlength(line1)) / 2.0, y, line1, f1, _CV_TEXT)
    if line2:
        _cv_text(draw, (W - f2.getlength(line2)) / 2.0, y + h1 + gap, line2, f2, _CV_DIM)
    return img


def _cv_events(preds):
    """Today's extremes as [(minute_of_day, height_ft, is_high, 'HH:MM'), ...]."""
    out = []
    for p in preds:
        raw = str(p.get('t', ''))
        hhmm = raw.split(' ')[-1] if ' ' in raw else raw
        try:
            hh, mm = (int(x) for x in hhmm.split(':'))
            v = float(p.get('v'))
        except (TypeError, ValueError):
            continue
        out.append((hh * 60 + mm, v, p.get('type') == 'H', hhmm))
    return out


def _cv_curve(draw, events, x0, y0, x1, y1, now_min):
    """The day's tide as a cosine curve through its extremes — water filled below,
    a dot at each high/low, a white marker where 'now' sits. Phantom extremes are
    mirrored past midnight so the curve doesn't flatline at the edges."""
    import math
    pts = [(m, v) for m, v, _hi, _t in events]
    if len(pts) < 2:
        mid = (y0 + y1) // 2
        draw.line([(x0, mid), (x1, mid)], fill=_CV_SEA)
        return
    ext = ([(pts[0][0] - (pts[1][0] - pts[0][0]), pts[1][1])] + pts
           + [(pts[-1][0] + (pts[-1][0] - pts[-2][0]), pts[-2][1])])
    lo = min(v for _m, v in ext)
    hi = max(v for _m, v in ext)
    span = (hi - lo) or 1.0

    def height_at(minute):
        for (ma, va), (mb, vb) in zip(ext, ext[1:]):
            if ma <= minute <= mb:
                u = (minute - ma) / max(1.0, float(mb - ma))
                return va + (vb - va) * (1 - math.cos(math.pi * u)) / 2.0
        return ext[0][1] if minute < ext[0][0] else ext[-1][1]

    def ypix(v):
        return y1 - 1 - (v - lo) / span * (y1 - y0 - 2)

    prev = None
    for x in range(x0, x1 + 1):
        minute = (x - x0) / max(1, x1 - x0) * 1439.0
        y = int(round(ypix(height_at(minute))))
        draw.line([(x, min(y + 2, y1)), (x, y1)], fill=_CV_SEA_FILL)
        # a 2px-thick stroke, joined to the previous column so steps don't dot
        y_from = y if prev is None else min(y, prev)
        draw.line([(x, y_from), (x, min(y + 1, y1))], fill=_CV_SEA)
        prev = y
    for m, v, is_high, _t in events:
        x = x0 + round(m / 1439.0 * (x1 - x0))
        y = int(round(ypix(v)))
        draw.rectangle([x - 1, y - 1, x + 1, y + 1],
                       fill=_CV_TEXT if is_high else _CV_DIM)
    if now_min is not None:
        x = x0 + round(min(1439.0, max(0.0, now_min)) / 1439.0 * (x1 - x0))
        y = int(round(ypix(height_at(now_min))))
        draw.line([(x, y0), (x, y1)], fill=(60, 66, 78))
        draw.rectangle([x - 1, y - 1, x + 1, y + 1], fill=_CV_NOW)
    return


def fetch_matrix(settings, canvas, i18n=None):
    """Today's tide curve with the next high/low called out above it (and the one
    after, where the width allows), 'now' marked on the curve. Predictions are
    station-local and cached for an hour; the marker only crawls, so a redraw
    every five minutes is plenty."""
    import time
    from datetime import datetime
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "tides") if i18n is not None else s

    station = str(settings.get('tide_station', '8443970') or '8443970').strip()
    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'preds': None, 'ts': 0.0, 'station': None}
        setattr(fetch_matrix, '_state', st)
    now = time.time()
    if st['preds'] is None or st['station'] != station or (now - st['ts']) >= 3600.0:
        try:
            st['preds'], st['station'] = _predictions(station), station
        except Exception:
            if st['station'] != station:
                st['preds'] = None         # another station's tides are not these
        st['ts'] = now                     # even after a failure: no hammering
    events = _cv_events(st['preds'] or [])
    if not events:
        canvas.frame(_cv_message(canvas, ImageDraw, t('Tides').upper(), t('No data').upper()))
        return 300.0

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    # The wall's clock stands in for station time — a tide wall lives by its water.
    local = datetime.now()
    now_min = local.hour * 60 + local.minute
    upcoming = [e for e in events if e[0] >= now_min] or [events[-1]]

    def tide_line(x, y, event, bright, with_height=True):
        m, v, is_high, hhmm = event
        arrow = '↑' if is_high else '↓'
        when = _fmt_time(hhmm, i18n)
        ht = f'{v:.1f}FT'
        f = head_f
        yb = y - f.getbbox('0')[1]
        draw.text((x, yb), arrow, font=f, fill=_CV_SEA)
        x += f.getlength(arrow + ' ')
        draw.text((x, yb), when, font=f, fill=_CV_TEXT if bright else _CV_DIM)
        if not with_height:
            return x + f.getlength(when)
        x += f.getlength(when + ' ')
        draw.text((x, yb), ht, font=f, fill=_CV_SEA if bright else _CV_DIM)
        return x + f.getlength(ht)

    def tide_w(event, with_height=True):
        _m, v, is_high, hhmm = event
        arrow = '↑' if is_high else '↓'
        when = _fmt_time(hhmm, i18n)
        w = head_f.getlength(arrow + ' ') + head_f.getlength(when)
        if with_height:
            w += head_f.getlength(' ') + head_f.getlength(f'{v:.1f}FT')
        return w

    sample = '↑ 12:28PM 11.2FT'
    head_h = max(8, min(12, int(H * 0.26)))
    head_f = _cv_fit(canvas, sample, int(W * 0.52) if W >= 128 else W - 4, head_h)
    # Nothing below the 8px floor: what can't fit whole is dropped instead — the
    # SECOND (next-tide) group goes first, then the first group's height figure.
    two = W >= 128 and len(upcoming) > 1 and \
        tide_w(upcoming[0]) + max(8, W // 16) + tide_w(upcoming[1]) <= W - 6
    if not two:                            # one group: give it the full width
        head_f = _cv_fit(canvas, sample, W - 4, head_h)
    hh = _cv_ink(head_f, sample)
    end = tide_line(3, 1, upcoming[0], True,
                    with_height=tide_w(upcoming[0]) <= W - 6)
    if two:
        tide_line(end + max(8, W // 16), 1, upcoming[1], False)

    top = 1 + hh + 2
    _cv_curve(draw, events, 2, top, W - 3, H - 1, float(now_min))
    canvas.frame(img)
    return 300.0
