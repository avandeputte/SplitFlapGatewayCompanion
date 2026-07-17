"""Aurora Clock — time as flowing colour, drawn on a Matrix panel.

A canvas app (surface: canvas), and the richer descendant of the flap "Art Clock"
(which spells the time in the handful of colour-flaps a reel carries). Freed of
the flap grid, this one paints on the framebuffer: a living aurora — bands of
colour whose hue and brightness ripple and drift — behind big two-tone digits
(hours in one colour, minutes in another) with a blinking colon and a smooth
seconds bar sweeping along the bottom.

"Time as colour" is the whole idea, so the palette is not fixed. On the default
**Daylight** theme the hue rotates once through the spectrum over a day, so the
wall's colour alone tells you roughly what time it is; **Spectrum** cycles the
hue continuously; **Ocean** and **Ember** hold a cool or warm palette. Colours
come from a tiny HSV→RGB helper, so a single hue drives the whole cohesive scene.

Cheap on purpose: the background is one horizontal line per row (~h ops, not
per-pixel), so the whole frame is well under a hundred ops and animates smoothly
at the ops path's ceiling.
"""

import math

# Glyph cell width for each panel font size (see the firmware's canvasFace).
_CW = {20: 10, 18: 9, 13: 8, 10: 6, 9: 6, 8: 5}


def _hsv(h, s, v):
    """HSV (h in degrees, s/v in 0..1) -> an (r,g,b) tuple. The one colour source."""
    h = h % 360.0
    s = 0.0 if s < 0 else (1.0 if s > 1 else s)
    v = 0.0 if v < 0 else (1.0 if v > 1 else v)
    c = v * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = v - c
    if h < 60:
        r, g, b = c, x, 0
    elif h < 120:
        r, g, b = x, c, 0
    elif h < 180:
        r, g, b = 0, c, x
    elif h < 240:
        r, g, b = 0, x, c
    elif h < 300:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))


def _base_hue(theme, now, frame):
    """The theme's driving hue (degrees). Daylight rotates with the clock;
    Spectrum spins; Ocean/Ember hold a band and merely breathe."""
    if theme == 'spectrum':
        return (frame * 1.5) % 360.0
    if theme == 'ocean':
        return 200.0 + 22.0 * math.sin(frame * 0.02)
    if theme == 'ember':
        return 16.0 + 16.0 * math.sin(frame * 0.02)
    return ((now.hour + now.minute / 60.0) / 24.0) * 360.0        # daylight


def _aurora(canvas, w, h, hue, frame):
    """The background: a waving colour gradient, one hline per row."""
    for y in range(h):
        t = y / max(1, h - 1)
        hy = hue + 34.0 * math.sin(y * 0.22 + frame * 0.05)
        v = 0.04 + 0.18 * (1 - t) + 0.05 * math.sin(y * 0.5 - frame * 0.06)
        canvas.hline(0, y, w, _hsv(hy, 0.62, v))


def _glow(canvas, x, y, s, text, color):
    """Text with a 1px dark shadow, so the digits stay legible over the colour."""
    canvas.text(x + 1, y + 1, text, (0, 0, 0), s)
    canvas.text(x, y, text, color, s)


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None
    from datetime import datetime

    state = getattr(fetch, '_state', None)
    if state is None:
        state = {'frame': 0}
        setattr(fetch, '_state', state)
    state['frame'] += 1
    frame = state['frame']

    tzname = str(settings.get('timezone') or '').strip()
    try:
        if tzname:
            import pytz
            now = datetime.now(pytz.timezone(tzname))
        else:
            now = datetime.now()
    except Exception:
        now = datetime.now()

    theme = str(settings.get('theme', 'daylight') or 'daylight').lower()
    fmt = str(settings.get('clock_format', '24h') or '24h').lower()

    w, h = canvas.width, canvas.height
    hue = _base_hue(theme, now, frame)
    col_h = _hsv(hue, 0.85, 1.0)                  # hours
    col_m = _hsv(hue + 40, 0.85, 1.0)             # minutes
    accent = _hsv(hue + 20, 0.75, 1.0)            # seconds bar

    if fmt == '12h':
        hour = (now.hour % 12) or 12
    else:
        hour = now.hour
    hh, mm = f'{hour:02d}', f'{now.minute:02d}'

    _aurora(canvas, w, h, hue, frame)

    # The date rides along only on a wall tall enough to spare the rows for it.
    show_date = h >= 40
    size = 18 if h >= 30 else (13 if h >= 20 else 8)
    cw = _CW[size]
    dy = 9 if show_date else (h - size) // 2

    if show_date:
        _glow(canvas, 1, 0, 8, now.strftime('%a %d'), _hsv(hue, 0.35, 0.95))
    if fmt == '12h':
        _glow(canvas, w - 2 * _CW[8] - 1, 0, 8, 'PM' if now.hour >= 12 else 'AM', col_m)

    # HH : MM, laid out in fixed-width glyph cells and centred.
    x0 = (w - 5 * cw) // 2
    _glow(canvas, x0, dy, size, hh, col_h)
    if now.second % 2 == 0:                       # the colon blinks once a second
        _glow(canvas, x0 + 2 * cw, dy, size, ':', (245, 245, 245))
    _glow(canvas, x0 + 3 * cw, dy, size, mm, col_m)

    # A smooth seconds bar along the bottom row, sub-second via the microseconds.
    frac = (now.second + now.microsecond / 1_000_000.0) / 60.0
    bw = int(w * frac)
    if bw > 0:
        canvas.hline(0, h - 1, bw, accent)
    if bw < w:
        canvas.pixel(bw, h - 1, (255, 255, 255))

    canvas.show()
    return 0.1                                    # ~10 fps for a smooth sweep
