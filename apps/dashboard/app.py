"""Dashboard plugin: time + current weather (via the shared weather helper).

On a small wall it rotates two pages (time, then weather). On a TALL wall (five
rows or more) it drops both onto one dense page that actually uses the rows and
the width — weekday/date and city/temperature spread to the edges, the time and
condition centered, then high/low and (with a sixth row) feels-like, humidity and
wind — instead of two sparse three-line pages floating in a big grid.
"""


# =============================================================================
# SHARED — the dashboard's data: the local clock and the shared weather helper.
# Both surfaces read the same instant and the same conditions dict.
# =============================================================================

def _local_now(settings):
    """The wall's local time — the configured timezone, US/Eastern when it is unset or bad."""
    from datetime import datetime
    import pytz
    try:
        tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone('US/Eastern')
    return datetime.now(tz)


def _weather(get_weather):
    """The shared helper's current conditions, or None (no helper on a bare host, or no data)."""
    if get_weather is None:
        return None
    try:
        w = get_weather()
    except Exception:
        return None
    return w if w and w.get('ok') else None


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, get_weather=None, i18n=None):
    dt = _local_now(settings)
    if i18n is not None:                     # localized names, locale date order, 24h outside English
        weekday = i18n.weekday(dt)
        date_line = i18n.date(dt, short=True, year=True)
        time_str = i18n.time(dt)
    else:
        weekday = dt.strftime("%A")
        date_line = dt.strftime("%b %d %Y")
        time_str = dt.strftime("%I:%M %p").lstrip("0")
    time_page = format_lines(weekday, date_line, time_str)

    # Weather comes from the companion's shared helper (global provider + key +
    # location). With no helper (e.g. a bare host), just show the time.
    if get_weather is None:
        return [time_page]
    w = _weather(get_weather)
    rows, c = get_rows(), get_cols()
    if not w:
        return [time_page, format_lines("No weather", "data", "Try later")]

    city = str(w.get('city') or 'Location')
    temp = w.get('temp_f')
    feels = w.get('feels_like_f')
    desc = str(w.get('desc') or '')
    if i18n is not None:                     # translate shared-helper condition text where we can
        desc = i18n.t(desc, "weather")
    high = w.get('hi_f')
    low = w.get('lo_f')
    hum, wind = w.get('humidity'), w.get('wind_mph')

    def row(left, right):
        """One full-width line: `left` flush left, `right` flush right — so a wide
        wall reads edge to edge instead of a short string stranded in the middle."""
        left, right = str(left), str(right)
        if not right:
            return left[:c]
        if len(left) + 1 + len(right) > c:
            return f"{left} {right}"[:c]
        return left + " " * (c - len(left) - len(right)) + right

    # A TALL wall gets one dense page that uses every row and the full width.
    if rows >= 5:
        temp_s = f"{temp}F" if temp is not None else ""
        lines = [row(weekday, date_line), time_str,
                 row(city[:max(1, c - len(temp_s) - 1)], temp_s), desc]
        hl = row(f"H {high}F" if high is not None else "",
                 f"L {low}F" if low is not None else "")
        if hl.strip():
            lines.append(hl)
        if rows >= 6:
            det = []
            if feels is not None:
                det.append(f"Feels {feels}F")
            if hum is not None:
                det.append(f"{int(hum)}% hum")
            if wind is not None:
                det.append(f"{int(wind)}mph")
            if det:
                lines.append("  ".join(det))
        return [format_lines(*lines[:rows])]

    # Compact walls keep the two-page rotation.
    now_t = i18n.time(dt, ampm_space=False) if i18n is not None else dt.strftime("%I:%M%p").lstrip("0")
    mcl = max(1, c - 1 - len(now_t))
    l1 = f"{city[:mcl]} {now_t}".center(c)
    pfx = f"{temp}F ({feels}F) "
    l2 = (pfx + desc[:max(0, c - len(pfx))]).center(c)
    l3 = f"H:{high}F L:{low}F".center(c)
    if rows >= 4:
        bits = []
        if hum is not None:
            bits.append(f"{int(hum)}% hum")
        if wind is not None:
            bits.append(f"{int(wind)}mph")
        l4 = "  ".join(bits).center(c) if bits else ""
        return [time_page, format_lines(l1, l2, l3, l4)]
    return [time_page, format_lines(l1, l2, l3)]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# One glanceable card: the clock large over the weekday/date, and the same
# shared-helper weather beside/below it — the temperature color-coded by band,
# the condition and daily high/low in support. On a small panel the weather
# collapses to temperature + condition; with no weather it is a clean clock.
# Solid black background; redraws on the minute tick.
# =============================================================================

_CV_TIME = (240, 244, 248)            # the clock — near-white
_CV_DIM = (150, 155, 165)             # weekday/date, condition, city
_CV_ACCENT = (90, 200, 255)           # the dashboard's cool cyan accent (rules, H/L labels)
_CV_HI = (255, 170, 70)               # daily high
_CV_LO = (110, 170, 255)              # daily low

# Temperature bands (deg F) -> the value's color: icy blue, mild white, warm amber, hot red.
_CV_BANDS = ((45, (110, 170, 255)), (75, (240, 244, 248)),
             (90, (255, 180, 60)), (10 ** 9, (255, 105, 70)))


def _cv_temp_color(t):
    try:
        f = float(t)
    except (TypeError, ValueError):
        return _CV_TIME
    for hi, col in _CV_BANDS:
        if f < hi:
            return col
    return _CV_TIME


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


def _cv_text(draw, x, y, text, font, fill):
    """Baseline-corrected text draw (y is the ink top, whatever the glyph bbox says)."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill)


def _cv_hold():
    """Seconds until the next wall-clock minute — the card shows minutes, so tick on them."""
    from datetime import datetime
    now = datetime.now()
    return max(1.0, 60.0 - now.second - now.microsecond / 1_000_000.0)


def fetch_matrix(settings, canvas, get_weather=None, i18n=None):
    from PIL import ImageDraw

    dt = _local_now(settings)
    w = _weather(get_weather)
    W, H = int(canvas.width), int(canvas.height)
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    if i18n is not None:
        time_s = i18n.time(dt, ampm_space=False)
        date_s = f'{i18n.weekday(dt, short=True)} {i18n.date(dt, short=True)}'.upper()
    else:
        time_s = dt.strftime('%I:%M%p').lstrip('0')
        date_s = dt.strftime('%a %b %d').upper()

    def deg(v):
        return f'{round(float(v))}\N{DEGREE SIGN}'

    temp = w.get('temp_f') if w else None
    desc = str(w.get('desc') or '') if w else ''
    if w and i18n is not None:
        desc = i18n.t(desc, 'weather')
    city = str(w.get('city') or '') if w else ''
    hi, lo = (w.get('hi_f'), w.get('lo_f')) if w else (None, None)

    if W >= 100 and H >= 48:
        # Full card: date strip, big clock, cyan rule, then temp + condition + H/L.
        pad = 3
        df = _cv_fit(canvas, date_s, W - 2 * pad, max(7, int(H * 0.14)))
        dh = df.getbbox(date_s)[3] - df.getbbox(date_s)[1]
        _cv_text(draw, pad, pad, date_s, df, _CV_DIM)
        tf = _cv_fit(canvas, time_s, W - 2 * pad, int(H * 0.40))
        tb = tf.getbbox(time_s)
        th = tb[3] - tb[1]
        ty = pad + dh + 2
        _cv_text(draw, pad, ty, time_s, tf, _CV_TIME)
        ry = ty + th + 3
        draw.line([(pad, ry), (W - pad - 1, ry)], fill=_CV_ACCENT)

        by, bh = ry + 3, H - (ry + 3) - pad
        if w is None or temp is None:
            nf = _cv_fit(canvas, 'NO WEATHER DATA', W - 2 * pad, min(bh, max(7, int(H * 0.14))))
            _cv_text(draw, pad, by + max(0, (bh - 8) // 2), 'NO WEATHER DATA', nf, _CV_DIM)
        else:
            ts = deg(temp)
            gf = _cv_fit(canvas, ts, int(W * 0.30), bh)
            gw = gf.getlength(ts)
            _cv_text(draw, pad, by + 1, ts, gf, _cv_temp_color(temp))
            rx = pad + gw + 6
            rw = W - pad - rx
            top_s = desc.upper() or city.upper()
            cf = _cv_fit(canvas, top_s, rw, max(7, int(bh * 0.5)))
            _cv_text(draw, rx, by + 1, top_s, cf, _CV_DIM)
            if hi is not None and lo is not None:
                ch = cf.getbbox(top_s or '0')[3] - cf.getbbox(top_s or '0')[1]
                hs, ls = f'H {deg(hi)}', f'L {deg(lo)}'
                hf = _cv_fit(canvas, f'{hs}  {ls}', rw, max(7, bh - ch - 3))
                hy = by + ch + 4
                _cv_text(draw, rx, hy, hs, hf, _CV_HI)
                _cv_text(draw, rx + hf.getlength(hs + '  '), hy, ls, hf, _CV_LO)
    else:
        # Compact panel: the clock owns the top, one weather line below (temp + condition).
        pad = 2
        tf = _cv_fit(canvas, time_s, W - 2 * pad, int(H * 0.52))
        tb = tf.getbbox(time_s)
        th = tb[3] - tb[1]
        _cv_text(draw, (W - tf.getlength(time_s)) / 2.0, pad + 1, time_s, tf, _CV_TIME)
        by = pad + 1 + th + 3
        if w is not None and temp is not None:
            ts = deg(temp)
            line_h = max(7, H - by - pad)
            gf = _cv_fit(canvas, ts, int(W * 0.4), line_h)
            rest = desc.upper()
            avail = W - 2 * pad - gf.getlength(ts) - 4
            rf = _cv_fit(canvas, rest, max(1, avail), min(line_h, max(7, int(H * 0.28)))) if rest else None
            if rest:
                rb = rf.getbbox(rest)
                # A condition that only "fits" by dropping under ~6px of ink is noise, not
                # information — a small panel shows the temperature alone instead.
                if (rb[3] - rb[1]) < 6 or rf.getlength(rest) > avail:
                    rest = ''
            total = gf.getlength(ts) + (4 + rf.getlength(rest) if rest else 0)
            x = max(pad, (W - total) / 2.0)
            _cv_text(draw, x, by, ts, gf, _cv_temp_color(temp))
            if rest:
                gh = gf.getbbox(ts)[3] - gf.getbbox(ts)[1]
                rh = rf.getbbox(rest)[3] - rf.getbbox(rest)[1]
                _cv_text(draw, x + gf.getlength(ts) + 4, by + max(0, gh - rh - 1), rest, rf, _CV_DIM)
        else:
            df2 = _cv_fit(canvas, date_s, W - 2 * pad, max(7, H - by - pad))
            _cv_text(draw, (W - df2.getlength(date_s)) / 2.0, by, date_s, df2, _CV_DIM)

    canvas.frame(img)
    return _cv_hold()
