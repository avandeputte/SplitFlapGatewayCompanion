"""Weather Sky — the forecast, drawn instead of spelled.

A canvas app (surface: canvas): instead of returning flap pages it paints an
animated sky straight onto a Matrix panel through the injected `canvas` helper —
a sun with slowly turning rays, drifting clouds, rain that falls, snow that
wobbles down, a lightning flash for storms, a moon and stars at night — with the
current temperature in a big colour that runs from icy blue to hot orange. A
flap grid can spell "Rain"; only a framebuffer can show it falling.

The live conditions come from the same shared `get_weather` helper the ordinary
weather app uses (so it honours the wall's configured location and provider),
cached here for ten minutes while the animation redraws several times a second.
The one canonical `sky` token the helper returns picks which scene to draw.

The panel's op set has no circle or diagonal line (only pixels, h/v lines, rects,
text), so discs are filled row by row and every slanted stroke is a tiny
Bresenham line.
"""

import math

# WMO weather code -> the helper's canonical sky token, for the keyless fallback
# used only when get_weather is somehow absent (canvas is companion-only, so the
# helper is normally injected — this just keeps the app a working module alone).
_SKY_OF_WMO = {
    0: 'clear', 1: 'clear', 2: 'pcloudy', 3: 'cloudy', 45: 'fog', 48: 'fog',
    51: 'rainl', 53: 'rainl', 55: 'rain', 56: 'sleet', 57: 'sleet',
    61: 'rainl', 63: 'rain', 65: 'rainh', 66: 'sleet', 67: 'sleet',
    71: 'snowl', 73: 'snow', 75: 'snowh', 77: 'snow', 80: 'shwr',
    81: 'shwr', 82: 'rainh', 85: 'snowl', 86: 'snowh',
    95: 'storm', 96: 'hail', 99: 'hail',
}

# How many streaks/flakes each intensity draws.
_DENSITY = {
    'rainl': 7, 'rain': 13, 'rainh': 20, 'shwr': 12,
    'snowl': 9, 'snow': 15, 'snowh': 22, 'sleet': 13,
}

_GREY = (110, 115, 130)
_DARK = (70, 72, 88)
_RAIN = (90, 145, 255)
_SNOW = (235, 238, 255)


# --- primitives -------------------------------------------------------------
def _disc(canvas, cx, cy, r, color):
    """A filled circle, one horizontal line per row (the panel has no circle op)."""
    cx, cy, r = int(cx), int(cy), int(r)
    for dy in range(-r, r + 1):
        dx = int((r * r - dy * dy) ** 0.5)
        canvas.hline(cx - dx, cy + dy, 2 * dx + 1, color)


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


# --- sky elements -----------------------------------------------------------
def _sun(canvas, cx, cy, r, frame):
    base = frame * 0.10                       # rays turn a little each frame
    for i in range(8):
        a = base + i * (math.pi / 4)
        pulse = 1.0 + 0.25 * math.sin(frame * 0.3 + i)
        _line(canvas, cx + math.cos(a) * (r + 1), cy + math.sin(a) * (r + 1),
              cx + math.cos(a) * (r * 1.7 * pulse), cy + math.sin(a) * (r * 1.7 * pulse),
              (255, 190, 40))
    _disc(canvas, cx, cy, r, (255, 205, 55))
    _disc(canvas, cx, cy, max(1, r - 2), (255, 235, 120))


def _moon(canvas, cx, cy, r):
    _disc(canvas, cx, cy, r, (210, 216, 236))
    _disc(canvas, cx + r * 0.5, cy - r * 0.2, r, (0, 0, 0))   # carve the crescent


def _stars(canvas, w, h, frame):
    pts = [(0.12, 0.10), (0.30, 0.22), (0.52, 0.12), (0.68, 0.28), (0.84, 0.08), (0.44, 0.32)]
    for i, (fx, fy) in enumerate(pts):
        if (frame // 6 + i) % 4:              # each star winks out now and then
            canvas.pixel(int(w * fx), int(h * fy), (200, 200, 175))


def _cloud(canvas, x, y, s, color):
    _disc(canvas, x, y, s, color)
    _disc(canvas, x - s, y + s * 0.3, s * 0.75, color)
    _disc(canvas, x + s, y + s * 0.3, s * 0.8, color)
    canvas.hline(int(x - s * 1.7), int(y + s * 0.9), int(s * 3.4) + 1, color)


def _drift(frame, speed, offset, span):
    """A cloud's x, scrolling right and wrapping around the panel edges."""
    return int((frame * speed + offset) % span - span * 0.25)


def _rain(canvas, w, h, frame, count):
    top = int(h * 0.28)
    span = max(1, h - top)                    # head stays on-panel; the 3px tail flows off
    for i in range(count):
        x = (i * 53 + 7) % w
        y = top + (frame * 3 + i * 11) % span
        _line(canvas, x, y, x - 1, min(h - 1, y + 3), _RAIN)


def _snow(canvas, w, h, frame, count):
    top = int(h * 0.26)
    span = max(1, h - top)
    for i in range(count):
        x = (i * 41 + 5 + int(2 * math.sin(frame * 0.15 + i))) % w
        y = top + (frame + i * 9) % span
        canvas.pixel(x, y, _SNOW)


def _fog(canvas, w, h, frame):
    for band in range(4):
        y = int(h * (0.2 + band * 0.2))
        x = -((frame * 2 + band * 13) % 8)
        while x < w:
            canvas.hline(x, y, 3, (120, 122, 132))
            x += 6


def _bolt(canvas, h, frame, cx, cy):
    if frame % 22 < 2:                        # a brief flash every ~22 frames
        d = int(h * 0.25)
        pts = [(cx, cy), (cx - 3, cy + d), (cx + 2, cy + d), (cx - 2, cy + 2 * d)]
        for a, b in zip(pts, pts[1:]):
            _line(canvas, a[0], a[1], b[0], b[1], (255, 255, 160))


# --- temperature ------------------------------------------------------------
_CHAR_W = {18: 9, 13: 8, 8: 5}


def _temp_color(c):
    if c <= -5:
        return (140, 200, 255)
    if c <= 5:
        return (120, 220, 255)
    if c <= 15:
        return (200, 240, 255)
    if c <= 24:
        return (255, 255, 255)
    if c <= 30:
        return (255, 200, 70)
    return (255, 120, 40)


def _degree(canvas, x, y, color):
    for dx, dy in ((1, 0), (2, 0), (0, 1), (3, 1), (0, 2), (3, 2), (1, 3), (2, 3)):
        canvas.pixel(x + dx, y + dy, color)


def _draw_temp(canvas, w, h, temp_f, unit):
    size = 18 if h >= 30 else (13 if h >= 20 else 8)
    cw = _CHAR_W[size]
    y = h - size
    if temp_f is None:
        canvas.text(1, y, '--', (150, 150, 150), size)
        return
    c = (float(temp_f) - 32.0) * 5.0 / 9.0
    if unit == 'c':
        disp = int(round(c))
    elif unit == 'k':
        disp = int(round(c + 273.15))
    else:
        disp = int(round(float(temp_f)))
    s = str(disp)
    color = _temp_color(c)
    canvas.text(1, y, s, color, size)
    end = 1 + len(s) * cw
    if unit == 'k':
        canvas.text(end + 1, y, 'K', color, size)
    else:
        _degree(canvas, end + 1, y, color)


# --- when the helper is missing (companion always injects it) ---------------
def _fallback(settings):
    try:
        import re
        import requests
        lat = str(settings.get('location_lat', '') or '').strip()
        lon = str(settings.get('location_lon', '') or '').strip()
        city = str(settings.get('location_name', '') or '').split(',')[0].strip()
        if not (lat and lon):
            q = str(settings.get('zip_code', '02118') or '02118').strip()
            p = {'q': q, 'format': 'json', 'limit': 1}
            if re.fullmatch(r'\d{5}', q):
                p['countrycodes'] = 'us'
            g = requests.get('https://nominatim.openstreetmap.org/search', params=p, timeout=5,
                             headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'}).json()
            if g:
                lat, lon = g[0]['lat'], g[0]['lon']
                city = city or g[0].get('display_name', q).split(',')[0].strip()
        d = requests.get('https://api.open-meteo.com/v1/forecast', params={
            'latitude': lat or 42.35, 'longitude': lon or -71.08,
            'current': 'temperature_2m,weather_code', 'temperature_unit': 'fahrenheit',
        }, timeout=8).json().get('current', {})
        return {'ok': True, 'temp_f': d.get('temperature_2m'), 'city': city,
                'sky': _SKY_OF_WMO.get(d.get('weather_code'), 'cloudy')}
    except Exception:
        return {'ok': False}


def _is_night(settings):
    from datetime import datetime
    tzname = str(settings.get('timezone') or '').strip()
    try:
        if tzname:
            import pytz
            hour = datetime.now(pytz.timezone(tzname)).hour
        else:
            hour = datetime.now().hour       # the add-on's clock is the wall's local time
    except Exception:
        hour = datetime.now().hour
    return hour < 6 or hour >= 20


def fetch(settings, format_lines, get_rows, get_cols, canvas=None, get_weather=None):
    if canvas is None:
        return None
    from datetime import datetime

    state = getattr(fetch, '_state', None)
    if state is None:
        state = {'frame': 0, 'wx': None, 'at': None}
        setattr(fetch, '_state', state)
    state['frame'] += 1
    frame = state['frame']

    # The animation redraws several times a second; the weather itself only every
    # ten minutes. Last good reading survives a hiccup — the sky keeps moving.
    now = datetime.now()
    stale = state['at'] is None or (now - state['at']).total_seconds() > 600
    if state['wx'] is None or stale:
        try:
            wx = get_weather(days=0, air=False) if get_weather is not None else None
            if wx and wx.get('ok'):
                state['wx'], state['at'] = wx, now
            elif state['wx'] is None:
                state['wx'], state['at'] = _fallback(settings), now
        except Exception:
            if state['wx'] is None:
                state['wx'], state['at'] = {'ok': False}, now
    wx = state['wx'] or {}

    unit = str(settings.get('temperature_unit', 'f') or 'f').lower()
    if unit not in ('f', 'c', 'k'):
        unit = 'f'
    show_city = str(settings.get('show_city', 'yes') or 'yes') != 'no'

    sky = wx.get('sky') or 'cloudy'
    w, h = canvas.width, canvas.height
    r = max(4, h // 4)
    ox, oy = w - r - 2, r + 1                 # sun / moon, top-right
    night = _is_night(settings)
    span = w + 4 * r

    canvas.clear((0, 0, 0))

    if sky in ('clear', 'pcloudy'):
        if night:
            _stars(canvas, w, h, frame)
            _moon(canvas, ox, oy, r)
        else:
            _sun(canvas, ox, oy, r, frame)
        if sky == 'pcloudy':
            _cloud(canvas, _drift(frame, 0.4, 0, span), int(h * 0.55), max(3, r - 1), _GREY)
    elif sky == 'cloudy':
        _cloud(canvas, _drift(frame, 0.35, 0, span), int(h * 0.34), max(3, r - 1), _GREY)
        _cloud(canvas, _drift(frame, 0.3, span * 0.55, span), int(h * 0.5), max(3, r - 1), _DARK)
    elif sky == 'fog':
        _fog(canvas, w, h, frame)
    elif sky in ('rainl', 'rain', 'rainh', 'shwr'):
        _cloud(canvas, int(w * 0.35), int(h * 0.26), max(3, r - 1), _GREY)
        _cloud(canvas, int(w * 0.72), int(h * 0.3), max(3, r - 2), _DARK)
        _rain(canvas, w, h, frame, _DENSITY.get(sky, 12))
    elif sky in ('snowl', 'snow', 'snowh', 'sleet'):
        _cloud(canvas, int(w * 0.4), int(h * 0.26), max(3, r - 1), _GREY)
        _snow(canvas, w, h, frame, _DENSITY.get(sky, 14))
        if sky == 'sleet':
            _rain(canvas, w, h, frame, 6)
    elif sky in ('storm', 'hail'):
        _cloud(canvas, int(w * 0.38), int(h * 0.26), max(3, r - 1), _DARK)
        _cloud(canvas, int(w * 0.72), int(h * 0.3), max(3, r - 2), _DARK)
        if sky == 'hail':
            _snow(canvas, w, h, frame, 14)
        else:
            _rain(canvas, w, h, frame, 16)
        _bolt(canvas, h, frame, int(w * 0.38), int(h * 0.26) + r)
    else:
        _cloud(canvas, _drift(frame, 0.35, 0, span), int(h * 0.4), max(3, r - 1), _GREY)

    if show_city and wx.get('city') and w >= 64:
        canvas.text(1, 0, str(wx['city'])[:max(1, (w - 2) // 6)], (170, 180, 200), 8)
    _draw_temp(canvas, w, h, wx.get('temp_f'), unit)

    canvas.show()
    return 0.12                               # ~8 fps — the ops path's ceiling
