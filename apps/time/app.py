# =============================================================================
# SHARED — one clock, two faces: timezone resolution and the formatted time
# string (12/24h + seconds rules) that both surfaces show.
# =============================================================================

def _tz(settings):
    import pytz
    try:
        return pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        return pytz.utc


def _drop1zero(s):
    # A single leading zero looks cleaner (9:30, not 09:30) — but drop only ONE, or
    # midnight in 24h (00:30) loses its hour entirely and reads ":30".
    return s[1:] if s[:1] == "0" and s[1:2].isdigit() else s


def _clock(settings, i18n, now, seconds):
    # An explicit Time Format wins; otherwise the Language decides (12h AM/PM
    # for English, 24h elsewhere) via the injected i18n helper.
    tf = settings.get('time_format')
    if tf in ('12hr', '24hr'):
        f24, f12 = ("%H:%M:%S", "%I:%M:%S%p") if seconds else ("%H:%M", "%I:%M%p")
        return _drop1zero(now.strftime(f24 if tf == '24hr' else f12))
    if i18n is not None:
        return i18n.time(now, seconds=seconds, ampm_space=False)
    return _drop1zero(now.strftime("%I:%M:%S%p" if seconds else "%I:%M%p"))


def _want_seconds(settings):
    return (str(settings.get('show_seconds', '')).strip().lower()
            in ('1', 'true', 'yes', 'on'))


def _day_lines(now, i18n):
    """The weekday + date pair a roomy clock face adds under the time."""
    if i18n is not None:
        return i18n.weekday(now), i18n.date(now)
    return now.strftime('%A'), f"{now.strftime('%B')} {now.day}"


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
    from datetime import datetime
    now = datetime.now(_tz(settings))
    # Seconds are opt-in, and only where the wall says sub-second updates are
    # honest (caps.instant — its own motion statement): a mechanical module takes
    # seconds per flip, so a ticking seconds field would keep the wall permanently
    # mid-clatter. They also have to fit the row. getattr, so the app still runs
    # where caps has no such attribute (a bare host).
    want_secs = _want_seconds(settings) and bool(getattr(caps, 'instant', False))

    time_str = _clock(settings, i18n, now, want_secs)
    if want_secs and len(time_str) > get_cols():
        time_str = _clock(settings, i18n, now, False)   # the geometry doesn't support it
    rows = get_rows()
    if rows == 1:
        return [format_lines(time_str)]
    if rows >= 4:
        # Room to spare: a wall clock that also says what day it is.
        weekday, date_line = _day_lines(now, i18n)
        return [format_lines(time_str, '', weekday, date_line)]
    return [format_lines('', time_str, '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A big bold clock: the time as large as the panel allows, an AM/PM tag in
# amber beside it, and — where there is vertical room — the weekday + date in
# quiet gray underneath, mirroring the tall flap wall. Redraws land exactly on
# the next second (seconds shown) or the next minute. Black background.
# =============================================================================

_TIME_COL = (245, 245, 248)
_AMPM_COL = (255, 178, 44)
_DATE_COL = (132, 136, 148)


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


def _split_ampm(time_str):
    """('9:41', 'PM') when the string carries a meridian tag, else (s, '')."""
    tail = time_str[-2:].upper()
    if tail in ('AM', 'PM') and len(time_str) > 2:
        return time_str[:-2].rstrip(), tail
    return time_str, ''


def fetch_matrix(settings, canvas, i18n=None, caps=None):
    from datetime import datetime
    from PIL import ImageDraw

    now = datetime.now(_tz(settings))
    seconds = _want_seconds(settings)           # a panel repaints instantly — no motion gate
    time_str = _clock(settings, i18n, now, seconds)
    main, ampm = _split_ampm(time_str)

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    # Layout: the clock pinned to the top row and grown as large as the panel
    # allows; weekday + date pinned to the bottom row — one quiet line, or two
    # bigger ones when the clock's width-bound leaves the room. (Ink starts and
    # ends 1px inside the edge: the reported bbox can under-report a pixel.)
    gap = 2
    date_min = max(7, int(H * 0.16))
    time_h = H - 3 - date_min - gap

    tf = _cv_fit(canvas, main, W - 2 - (int(W * 0.12) if ampm else 0), time_h)
    if seconds and (lambda b: b[3] - b[1])(tf.getbbox('0')) < 10:
        # Seconds would drive the digits below legible — a small panel shows
        # H:MM big instead (and quietly redraws each minute).
        seconds = False
        main, ampm = _split_ampm(_clock(settings, i18n, now, False))
        tf = _cv_fit(canvas, main, W - 2 - (int(W * 0.12) if ampm else 0), time_h)
    tb = tf.getbbox(main)
    tw, th = tf.getlength(main), tb[3] - tb[1]
    af = _cv_fit(canvas, ampm, int(W * 0.16), max(7, int(th * 0.38))) if ampm else None
    aw = (af.getlength(ampm) + 2) if ampm else 0

    tx = (W - tw - aw) / 2.0
    ty = 1
    draw.text((tx, ty - tb[1]), main, font=tf, fill=_TIME_COL)
    if ampm:
        ab = af.getbbox(ampm)
        # The tag rides the clock's baseline.
        draw.text((tx + tw + 2, ty + th - (ab[3] - ab[1]) - ab[1]), ampm, font=af, fill=_AMPM_COL)

    # The date fills what the clock left, pinned to the bottom edge.
    weekday, date_line = _day_lines(now, i18n)
    leftover = (H - 2) - th - gap               # ink rows left under the clock
    if leftover >= 12 and W >= 96:
        # Room to mirror the tall flap wall: weekday and date, stacked.
        lgap = 2
        per = min((leftover - lgap) / 2.0, max(6, int(H * 0.2)))
        # Reported bottom rides H-1: tiny sizes render a row HIGH, never low,
        # so the last ink row lands on H-1 or H-2 and can't clip.
        y_bot = H - 1
        for line in (date_line.upper(), weekday.upper()):       # bottom-up
            f = _cv_fit(canvas, line, W - 2, per)
            b = f.getbbox(line)
            lh = b[3] - b[1]
            draw.text(((W - f.getlength(line)) / 2.0, y_bot - lh + 1 - b[1]),
                      line, font=f, fill=_DATE_COL)
            y_bot -= lh + lgap
    else:
        if W < 96:
            # Short forms buy a narrow panel a font that stays readable.
            if i18n is not None:
                under = (f'{i18n.weekday(now, short=True)} {now.day} '
                         f'{i18n.month(now, short=True)}').upper()
            else:
                under = f"{now.strftime('%a %b')} {now.day}".upper()
        else:
            under = f'{weekday}  {date_line}'.upper()
        df = _cv_fit(canvas, under, W - 2, max(6, leftover))
        db = df.getbbox(under)
        dh = db[3] - db[1]
        draw.text(((W - df.getlength(under)) / 2.0, H - dh - db[1]),
                  under, font=df, fill=_DATE_COL)

    canvas.frame(img)
    if seconds:
        return max(0.05, 1.0 - now.microsecond / 1e6)       # land on the next second
    return max(1.0, 60.0 - now.second - now.microsecond / 1e6)
