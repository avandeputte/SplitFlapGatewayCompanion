"""An analog clock, drawn on a Matrix panel's framebuffer.

A canvas app (surface: canvas): it draws straight to the LED panel through the
injected `canvas` helper — a clock face and three sweeping hands — which a
split-flap grid could never show. The engine runs it once a second; each pass
redraws the whole face so the second hand ticks.

The panel's op set has no diagonal-line primitive (only h/v lines, rects, pixels,
text), so the hands are plotted pixel by pixel here — a tiny Bresenham line.
"""

import math


def _line(canvas, x0, y0, x1, y1, color):
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        canvas.pixel(x0, y0, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy; x0 += sx
        if e2 <= dx:
            err += dx; y0 += sy


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None
    from datetime import datetime
    import pytz
    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)

    w, h = canvas.width, canvas.height
    cx, cy = w // 2, h // 2
    r = min(cx, cy) - 1
    hand = str(settings.get('hand_color', 'white') or 'white')

    canvas.clear()
    # 12 tick marks around the dial
    for t in range(12):
        a = math.radians(t * 30 - 90)
        canvas.pixel(cx + r * math.cos(a), cy + r * math.sin(a),
                     'white' if t % 3 == 0 else 'gray')

    def polar(length, turns):                    # turns: 0..1 around the dial
        a = math.radians(turns * 360 - 90)
        return cx + length * math.cos(a), cy + length * math.sin(a)

    sec = now.second
    minute = now.minute + sec / 60.0
    hour = (now.hour % 12) + minute / 60.0
    hx, hy = polar(r * 0.5, hour / 12.0)
    mx, my = polar(r * 0.8, minute / 60.0)
    sx, sy = polar(r * 0.9, sec / 60.0)
    _line(canvas, cx, cy, hx, hy, hand)          # hour
    _line(canvas, cx, cy, mx, my, hand)          # minute
    _line(canvas, cx, cy, sx, sy, 'red')         # second, always red
    canvas.pixel(cx, cy, 'white')
    canvas.show()
    return None
