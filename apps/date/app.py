# =============================================================================
# SHARED — today, in words: timezone resolution and the localized weekday /
# month-day / year strings both surfaces print.
# =============================================================================

def _tz(settings):
    import pytz
    try:
        return pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        return pytz.timezone('US/Eastern')


def _parts(settings, i18n):
    """(now, time_str, weekday, month_day) — the strings every surface shows.
    When the companion injects i18n, honor the global Language: localized weekday,
    locale-ordered date (9 JUILLET, not JUILLET 9), and 24h time outside English."""
    from datetime import datetime
    now = datetime.now(_tz(settings))
    if i18n is not None:
        return now, i18n.time(now), i18n.weekday(now), i18n.date(now)
    return (now, now.strftime('%I:%M %p').lstrip('0'), now.strftime('%A'),
            f"{now.strftime('%B')} {now.day}")


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    now, time_str, weekday, month_day = _parts(settings, i18n)
    rows = get_rows()
    if rows == 2:
        return [format_lines(month_day, weekday)]
    if rows >= 4:
        return [format_lines(time_str, weekday, month_day, str(now.year))]
    return [format_lines(time_str, month_day, weekday)]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A typographic date card — deliberately simpler than the canvas-date app,
# matching the flap view's content: the weekday small in amber, the date big in
# white, the year quiet underneath where there is room. Black background.
# =============================================================================

_DAY_COL = (255, 178, 44)
_DATE_COL = (245, 245, 248)
_YEAR_COL = (132, 136, 148)


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


def fetch_matrix(settings, canvas, i18n=None):
    from PIL import ImageDraw

    now, _time_str, weekday, month_day = _parts(settings, i18n)
    weekday, month_day = weekday.upper(), month_day.upper()

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    year = str(now.year) if H >= 48 else ''     # the year only where it has a row of its own

    # Weekday ~ a quarter of the height, the date the hero, the year a whisper.
    wf = _cv_fit(canvas, weekday, W - 6, max(8, int(H * 0.24)))
    wb = wf.getbbox(weekday)
    wh = wb[3] - wb[1]
    yf = _cv_fit(canvas, year, W - 6, max(7, int(H * 0.16))) if year else None
    yh = (yf.getbbox(year)[3] - yf.getbbox(year)[1]) if year else 0
    gap = max(2, H // 16)
    df = _cv_fit(canvas, month_day, W - 6, H - wh - yh - gap * (2 if year else 1) - 4)
    db = df.getbbox(month_day)
    dh = db[3] - db[1]

    total = wh + gap + dh + ((gap + yh) if year else 0)
    y = (H - total) / 2.0
    draw.text(((W - wf.getlength(weekday)) / 2.0, y - wb[1]), weekday, font=wf, fill=_DAY_COL)
    y += wh + gap
    draw.text(((W - df.getlength(month_day)) / 2.0, y - db[1]), month_day, font=df, fill=_DATE_COL)
    if year:
        y += dh + gap
        ybx = yf.getbbox(year)
        draw.text(((W - yf.getlength(year)) / 2.0, y - ybx[1]), year, font=yf, fill=_YEAR_COL)

    canvas.frame(img)
    # The face only changes at midnight; a minutely redraw catches it promptly
    # without ever ticking visibly.
    return max(5.0, 60.0 - now.second)
