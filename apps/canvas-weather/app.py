"""Weather Sky — the weather as a rich, colourful scene on a Matrix panel.

A canvas app (surface: canvas). It renders a whole frame with Pillow and pushes
it (PUT /api/canvas/frame): a sky whose colour is the time of day *and* the
conditions — deep blue nights with a glowing moon and coloured stars, warm dawn
and dusk, bright day blue, greying over for cloud and rain — with a glowing sun
or moon, drifting cloud, and rain or snow that falls. Over it sits the numbers:
a big temperature, the condition, today's high/low, and the place.

Live conditions come from the shared `get_weather` helper (so it honours the
wall's configured location/provider), cached ten minutes while the scene
animates. The canonical `sky` token picks the scene.
"""

_SKY_OF_WMO = {
    0: 'clear', 1: 'clear', 2: 'pcloudy', 3: 'cloudy', 45: 'fog', 48: 'fog',
    51: 'rainl', 53: 'rainl', 55: 'rain', 56: 'sleet', 57: 'sleet',
    61: 'rainl', 63: 'rain', 65: 'rainh', 66: 'sleet', 67: 'sleet',
    71: 'snowl', 73: 'snow', 75: 'snowh', 77: 'snow', 80: 'shwr',
    81: 'shwr', 82: 'rainh', 85: 'snowl', 86: 'snowh',
    95: 'storm', 96: 'hail', 99: 'hail',
}
_WORD = {'clear': 'Clear', 'pcloudy': 'Partly', 'cloudy': 'Cloudy', 'fog': 'Fog',
         'rainl': 'Light rain', 'rain': 'Rain', 'rainh': 'Heavy rain', 'shwr': 'Showers',
         'snowl': 'Light snow', 'snow': 'Snow', 'snowh': 'Heavy snow', 'sleet': 'Sleet',
         'storm': 'Storm', 'hail': 'Hail'}
_RAIN = {'rainl': 8, 'rain': 14, 'rainh': 22, 'shwr': 13, 'sleet': 10}
_SNOW = {'snowl': 10, 'snow': 16, 'snowh': 24}
_WET = ('rainl', 'rain', 'rainh', 'shwr', 'sleet')
_CLOUDY = ('pcloudy', 'cloudy', 'fog', 'rainl', 'rain', 'rainh', 'shwr', 'sleet',
           'snowl', 'snow', 'snowh', 'storm', 'hail')


def _mix(a, b, t):
    return tuple(int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3))


def _sky_colors(hour, sky, night):
    """(top, bottom) gradient for the panel — time of day, then greyed by cloud."""
    if night:
        top, bot = (10, 16, 44), (3, 5, 16)
    elif hour < 7:
        top, bot = (66, 86, 158), (240, 150, 96)          # dawn
    elif hour < 17:
        top, bot = (52, 120, 226), (150, 196, 250)         # day
    elif hour < 20:
        top, bot = (44, 56, 120), (238, 126, 66)           # dusk
    else:
        top, bot = (10, 16, 44), (3, 5, 16)
    if sky in ('cloudy', 'fog') or sky in _WET:
        top, bot = _mix(top, (78, 84, 100), 0.5), _mix(bot, (54, 58, 72), 0.5)
    if sky in ('snowl', 'snow', 'snowh'):
        top, bot = _mix(top, (120, 130, 150), 0.5), _mix(bot, (86, 96, 116), 0.5)
    if sky in ('storm', 'hail'):
        top, bot = _mix(top, (34, 36, 48), 0.72), _mix(bot, (20, 22, 30), 0.72)
    return top, bot


def _glow(ImageChops, Image, ImageDraw, ImageFilter, img, shapes, blur):
    """Additive glow: draw bright `shapes` on black, blur, add onto img."""
    layer = Image.new('RGB', img.size, (0, 0, 0))
    d = ImageDraw.Draw(layer)
    for box, col in shapes:
        d.ellipse(box, fill=col)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return ImageChops.add(img, layer)


def _disc(draw, cx, cy, r, col):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)


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
            'current': 'temperature_2m,weather_code',
            'daily': 'temperature_2m_max,temperature_2m_min',
            'temperature_unit': 'fahrenheit', 'timezone': 'auto', 'forecast_days': 1,
        }, timeout=8).json()
        cur, daily = d.get('current', {}), d.get('daily', {})
        return {'ok': True, 'temp_f': cur.get('temperature_2m'), 'city': city,
                'sky': _SKY_OF_WMO.get(cur.get('weather_code'), 'cloudy'),
                'hi_f': (daily.get('temperature_2m_max') or [None])[0],
                'lo_f': (daily.get('temperature_2m_min') or [None])[0]}
    except Exception:
        return {'ok': False}


def _num(v, unit):
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    if unit == 'c':
        return int(round((f - 32) * 5 / 9))
    if unit == 'k':
        return int(round((f - 32) * 5 / 9 + 273.15))
    return int(round(f))


def fetch(settings, format_lines, get_rows, get_cols, canvas=None, get_weather=None):
    if canvas is None:
        return None
    import math
    from datetime import datetime
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    st = getattr(fetch, '_state', None)
    if st is None:
        st = {'frame': 0, 'wx': None, 'at': None}
        setattr(fetch, '_state', st)
    st['frame'] += 1
    frame = st['frame']

    tzname = str(settings.get('timezone') or '').strip()
    try:
        now = datetime.now(__import__('pytz').timezone(tzname)) if tzname else datetime.now()
    except Exception:
        now = datetime.now()
    hour = now.hour
    night = hour < 6 or hour >= 20

    nowt = datetime.now()
    stale = st['at'] is None or (nowt - st['at']).total_seconds() > 600
    if st['wx'] is None or stale:
        try:
            wx = get_weather(days=1, air=False) if get_weather is not None else None
            if wx and wx.get('ok'):
                st['wx'], st['at'] = wx, nowt
            elif st['wx'] is None:
                st['wx'], st['at'] = _fallback(settings), nowt
        except Exception:
            if st['wx'] is None:
                st['wx'], st['at'] = {'ok': False}, nowt
    wx = st['wx'] or {}

    unit = str(settings.get('temperature_unit', 'f') or 'f').lower()
    if unit not in ('f', 'c', 'k'):
        unit = 'f'
    show_city = str(settings.get('show_city', 'yes') or 'yes') != 'no'
    sky = wx.get('sky') or 'cloudy'

    W, H = canvas.width, canvas.height
    top, bot = _sky_colors(hour, sky, night)
    img = canvas.vgrad(top, bot).copy()
    draw = ImageDraw.Draw(img)

    # --- celestial: a glowing sun (day) or moon + coloured stars (night) --------
    icx, icy, ir = W - int(H * 0.42) - 1, int(H * 0.40), max(4, int(H * 0.26))
    if night:
        stars = [(0.06, 0.18, (200, 210, 255)), (0.16, 0.42, (255, 240, 200)),
                 (0.30, 0.12, (180, 220, 255)), (0.40, 0.55, (255, 220, 220)),
                 (0.52, 0.24, (210, 235, 255)), (0.63, 0.10, (255, 245, 210))]
        for i, (fx, fy, col) in enumerate(stars):
            if (frame // 7 + i) % 4:
                draw.point((int(W * fx), int(H * fy)), fill=col)
        if sky in ('clear', 'pcloudy'):
            img = _glow(ImageChops, Image, ImageDraw, ImageFilter, img,
                        [([icx - ir, icy - ir, icx + ir, icy + ir], (120, 130, 180))], ir * 0.8)
            draw = ImageDraw.Draw(img)
            _disc(draw, icx, icy, ir, (232, 236, 250))
            _disc(draw, icx + int(ir * 0.55), icy - int(ir * 0.2), ir, _mix(top, bot, 0.3))
    else:
        if sky in ('clear', 'pcloudy'):
            img = _glow(ImageChops, Image, ImageDraw, ImageFilter, img,
                        [([icx - ir, icy - ir, icx + ir, icy + ir], (255, 200, 60))], ir * 1.1)
            draw = ImageDraw.Draw(img)
            _disc(draw, icx, icy, ir, (255, 226, 120))
            _disc(draw, icx, icy, max(1, ir - 2), (255, 240, 175))

    # --- clouds -----------------------------------------------------------------
    if sky in _CLOUDY:
        dark = sky in ('storm', 'hail')
        cc = (70, 74, 88) if dark else (150, 156, 172)
        cx = int((frame * 0.4) % (W + 40) - 20)
        for dx, dy, rr in ((0, 0, ir), (ir, 5, int(ir * 0.8)), (-ir, 4, int(ir * 0.7))):
            _disc(draw, icx + dx - int(W * 0.1), icy + dy, rr, cc)
        _disc(draw, cx, int(H * 0.30), max(3, int(ir * 0.7)), _mix(cc, (255, 255, 255), 0.06))

    # --- precipitation ----------------------------------------------------------
    if sky in _RAIN:
        for i in range(_RAIN[sky]):
            x = (i * 53 + 7) % W
            y = int(H * 0.30) + (frame * 3 + i * 11) % max(1, int(H * 0.7))
            draw.line([(x, y), (x - 1, min(H - 1, y + 3))], fill=(120, 170, 255))
    elif sky in _SNOW:
        for i in range(_SNOW[sky]):
            x = (i * 41 + 5 + int(2 * math.sin(frame * 0.15 + i))) % W
            y = int(H * 0.28) + (frame + i * 9) % max(1, int(H * 0.72))
            draw.point((x, y), fill=(238, 244, 255))
    if sky in ('storm', 'hail') and frame % 22 < 2:
        bx = int(W * 0.3)
        draw.line([(bx, int(H * 0.3)), (bx - 3, int(H * 0.55)), (bx + 2, int(H * 0.55)),
                   (bx - 2, int(H * 0.8))], fill=(255, 255, 170), width=1)

    # --- the text -----------------------------------------------------------
    # ALL text lives in a left column over a dark scrim, so it always reads —
    # even on a bright day sky — while the sky itself (sun/moon/cloud/rain) stays
    # bright on the right. The column stacks: place, big temperature, then the
    # condition with today's high/low. Nothing overlaps, nothing sits on the sky.
    pad = 2
    text_w = int(W * 0.66)
    scrim = Image.new('L', (W, H), 0)
    ImageDraw.Draw(scrim).rectangle([0, 0, text_w, H], fill=180)
    img = Image.composite(Image.new('RGB', (W, H), (0, 0, 0)), img,
                          scrim.filter(ImageFilter.GaussianBlur(6)))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"                         # crisp 1-bit text — no anti-aliased fuzz

    def text(x, y, s, font, col):
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):   # a dark outline for contrast
            draw.text((x + dx, y + dy), s, font=font, fill=(0, 0, 0), anchor='la')
        draw.text((x, y), s, font=font, fill=col, anchor='la')

    temp = _num(wx.get('temp_f'), unit)
    hi, lo = _num(wx.get('hi_f'), unit), _num(wx.get('lo_f'), unit)
    deg = '\N{DEGREE SIGN}'
    tiny = canvas.font(max(6, int(H * 0.17)))
    small = canvas.font(max(7, int(H * 0.24)))
    word = _WORD.get(sky, 'Weather')

    top = 0
    if show_city and wx.get('city'):
        cs = str(wx['city'])
        while cs and tiny.getlength(cs) > text_w - pad:
            cs = cs[:-1]
        if cs:
            text(pad, 0, cs, tiny, (216, 226, 244))
            top = tiny.size + 1

    info_y = H - (small.size + 1)
    band = info_y - top
    big = canvas.font(max(10, int(band * 0.96)))
    s = f'{temp}{deg}' if temp is not None else '--'
    ty = top + max(0, (band - big.size) // 2)
    text(pad, ty, s, big, (255, 255, 255) if temp is not None else (200, 200, 200))

    # The info line: condition, then high (warm) / low (cool). The condition is
    # shown only when the whole line fits the column; the numbers always are.
    hi_s = f'{hi}{deg}' if hi is not None else ''
    lo_s = f'{lo}{deg}' if lo is not None else ''
    x = pad
    if word and small.getlength(word + '  ' + hi_s + '/' + lo_s) <= text_w - pad:
        text(x, info_y, word, small, (214, 226, 246))
        x += small.getlength(word + '  ')
    if hi_s:
        text(x, info_y, hi_s, small, (255, 202, 140))
        x += small.getlength(hi_s)
    if hi_s and lo_s:
        text(x, info_y, '/', small, (186, 196, 216))
        x += small.getlength('/')
    if lo_s:
        text(x, info_y, lo_s, small, (150, 200, 255))

    canvas.frame(img)
    return 0.16
