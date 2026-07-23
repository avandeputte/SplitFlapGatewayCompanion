"""Weather Sky — the weather as a rich, colourful scene on a Matrix panel.

A canvas app (surface: canvas). It renders a whole frame with Pillow and pushes it
(PUT /api/canvas/frame) on a black (unlit) background — bright content reads best on an LED
panel — with a glowing sun by day or a moon and coloured stars by night, drifting cloud, and
rain or snow that falls. Over it sits the numbers: a big temperature, the condition, today's
high/low, and the place.

Live conditions come from the shared `get_weather` helper (so it honours the wall's configured
location/provider), cached ten minutes while the scene animates. The canonical `sky` token
picks the scene.
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


def fetch_matrix(settings, canvas, get_weather=None):
    import math
    from datetime import datetime
    from PIL import Image, ImageDraw, ImageFilter

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'frame': 0, 'wx': None, 'at': None}
        setattr(fetch_matrix, '_state', st)
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
            wx = get_weather(days=3, air=False) if get_weather is not None else None
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
    large = W >= 192 and H >= 48                # a large panel gets the richer layout
    img = canvas.blank((0, 0, 0))               # black — bright weather elements read best on unlit pixels
    draw = ImageDraw.Draw(img)

    # --- celestial: a crisp sun (day) or moon + coloured stars (night) ----------
    # No soft glow — a blurred halo reads as an ugly gradient on the unlit black panel, so the
    # sun and moon are drawn as clean discs.
    icx, icy, ir = W - int(H * 0.42) - 1, int(H * 0.40), max(4, int(H * 0.26))
    if night:
        stars = [(0.06, 0.18, (200, 210, 255)), (0.16, 0.42, (255, 240, 200)),
                 (0.30, 0.12, (180, 220, 255)), (0.40, 0.55, (255, 220, 220)),
                 (0.52, 0.24, (210, 235, 255)), (0.63, 0.10, (255, 245, 210))]
        for i, (fx, fy, col) in enumerate(stars):
            if (frame // 7 + i) % 4:
                draw.point((int(W * fx), int(H * fy)), fill=col)
        if sky in ('clear', 'pcloudy'):
            _disc(draw, icx, icy, ir, (232, 236, 250))
            _disc(draw, icx + int(ir * 0.55), icy - int(ir * 0.2), ir, (0, 0, 0))   # crescent cut
    else:
        if sky in ('clear', 'pcloudy'):
            _disc(draw, icx, icy, ir, (255, 210, 70))
            _disc(draw, icx, icy, max(1, ir - 2), (255, 226, 120))

    # --- clouds: one resting by the sun/moon, one drifting across — both clearly cloud-SHAPED
    # (three overlapping grey discs) so a cloud never reads as a lone white ball on the black sky.
    if sky in _CLOUDY:
        dark = sky in ('storm', 'hail')
        cc = (70, 74, 88) if dark else (150, 156, 172)

        def _puff(px, py, s):
            for dx, dy, rr in ((0, 0, s), (int(s * 0.9), 4, int(s * 0.78)), (-int(s * 0.9), 3, int(s * 0.72))):
                _disc(draw, int(px + dx), int(py + dy), max(2, rr), cc)

        _puff(icx - int(W * 0.1), icy, ir)                       # the cloud beside the sun/moon
        cx = (frame * 0.4) % (W + ir * 6) - ir * 3               # a smaller cloud drifting across
        _puff(cx, H * 0.28, max(3, int(ir * 0.62)))

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
    # A soft scrim blacks out the info column so the scene never crowds the text; the celestial
    # scene keeps the right. A small panel gets a compact left column; a big panel (256x64) gets
    # a full info dashboard, so the space isn't wasted.
    temp = _num(wx.get('temp_f'), unit)
    hi, lo = _num(wx.get('hi_f'), unit), _num(wx.get('lo_f'), unit)
    deg = '\N{DEGREE SIGN}'
    word = _WORD.get(sky, 'Weather')

    def _outline(draw, x, y, s, font, col):
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):   # a dark outline for contrast
            draw.text((x + dx, y + dy), s, font=font, fill=(0, 0, 0), anchor='la')
        draw.text((x, y), s, font=font, fill=col, anchor='la')

    if large:
        # A dark info column on the left holds the place, a big temperature, the
        # condition, high/low, feels-like, humidity, wind and a 3-day forecast; the
        # sky scene fills the right.
        pad, left_w = 3, int(W * 0.58)
        name_f = canvas.font(max(7, int(H * 0.15)))
        temp_f = canvas.font(max(12, int(H * 0.38)))
        info_f = canvas.font(max(8, int(H * 0.15)))
        step = info_f.size + 2
        fy = H - info_f.size - 2                            # the forecast strip's top
        scrim = Image.new('L', (W, H), 0)
        _sd = ImageDraw.Draw(scrim)
        _sd.rectangle([0, 0, left_w, fy], fill=196)          # left info panel
        _sd.rectangle([0, fy - 1, W - 1, H - 1], fill=196)   # full-width forecast strip
        img = Image.composite(Image.new('RGB', (W, H), (0, 0, 0)), img,
                              scrim.filter(ImageFilter.GaussianBlur(6)))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"

        if show_city and wx.get('city'):
            cs = str(wx['city'])
            while cs and name_f.getlength(cs) > left_w - pad:
                cs = cs[:-1]
            _outline(draw, pad, 1, cs, name_f, (216, 226, 244))

        ty = int(H * 0.20)
        ts = f'{temp}{deg}' if temp is not None else '--'
        _outline(draw, pad, ty, ts, temp_f, (255, 255, 255) if temp is not None else (200, 200, 200))
        _outline(draw, pad, ty + temp_f.size, word[:14], info_f, (214, 226, 246))

        dx, dyy = int(pad + temp_f.getlength(ts) + 8), ty
        if hi is not None:
            _outline(draw, dx, dyy, f'H {hi}{deg}', info_f, (255, 150, 55))
            if lo is not None:
                _outline(draw, dx + info_f.getlength(f'H {hi}{deg}') + 7, dyy,
                         f'L {lo}{deg}', info_f, (55, 150, 255))
            dyy += step
        feels = _num(wx.get('feels_like_f'), unit)
        if feels is not None:
            _outline(draw, dx, dyy, f'Feels {feels}{deg}', info_f, (198, 208, 228))
            dyy += step
        extra = []
        if wx.get('humidity') is not None:
            extra.append(f'Hum {int(wx["humidity"])}%')
        if wx.get('wind_mph') is not None:
            extra.append(f'Wind {int(wx["wind_mph"])}')
        if extra:
            _outline(draw, dx, dyy, '  '.join(extra), info_f, (198, 208, 228))

        fc = wx.get('forecast') or []
        if fc:
            from datetime import datetime as _dt
            n = min(3, len(fc))
            cw = W // n                                     # spread across the FULL width
            for i, day in enumerate(fc[:n]):
                dhi, dlo = _num(day.get('hi_f'), unit), _num(day.get('lo_f'), unit)
                try:
                    lbl = _dt.strptime(str(day.get('date'))[:10], '%Y-%m-%d').strftime('%a')
                except Exception:
                    lbl = ''
                fs = f'{lbl}  {dhi}{deg}/{dlo}{deg}' if (dhi is not None and dlo is not None) else (lbl or '')
                _outline(draw, i * cw + pad + 2, fy, fs, info_f, (206, 216, 234))
    else:
        # Compact: place, big temperature, then condition + high/low, all in a left
        # column over the scrim.
        pad = 2
        text_w = int(W * 0.66)
        scrim = Image.new('L', (W, H), 0)
        ImageDraw.Draw(scrim).rectangle([0, 0, text_w, H], fill=180)
        img = Image.composite(Image.new('RGB', (W, H), (0, 0, 0)), img,
                              scrim.filter(ImageFilter.GaussianBlur(6)))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        tiny = canvas.font(max(6, int(H * 0.17)))
        small = canvas.font(max(7, int(H * 0.24)))

        top = 0
        if show_city and wx.get('city'):
            cs = str(wx['city'])
            while cs and tiny.getlength(cs) > text_w - pad:
                cs = cs[:-1]
            if cs:
                _outline(draw, pad, 0, cs, tiny, (216, 226, 244))
                top = tiny.size + 1

        info_y = H - (small.size + 1)
        band = info_y - top
        big = canvas.font(max(10, int(band * 0.96)))
        s = f'{temp}{deg}' if temp is not None else '--'
        ty = top + max(0, (band - big.size) // 2)
        _outline(draw, pad, ty, s, big, (255, 255, 255) if temp is not None else (200, 200, 200))

        hi_s = f'{hi}{deg}' if hi is not None else ''
        lo_s = f'{lo}{deg}' if lo is not None else ''
        x = pad
        if word and small.getlength(word + '  ' + hi_s + '/' + lo_s) <= text_w - pad:
            _outline(draw, x, info_y, word, small, (214, 226, 246))
            x += small.getlength(word + '  ')
        if hi_s:
            _outline(draw, x, info_y, hi_s, small, (255, 150, 55))
            x += small.getlength(hi_s)
        if hi_s and lo_s:
            _outline(draw, x, info_y, '/', small, (186, 196, 216))
            x += small.getlength('/')
        if lo_s:
            _outline(draw, x, info_y, lo_s, small, (55, 150, 255))

    canvas.frame(img)
    return 0.16
