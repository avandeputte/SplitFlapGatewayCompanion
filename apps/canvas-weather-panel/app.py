"""Weather Panel — current conditions drawn on the panel with canvas draw-ops.

A canvas app: instead of pushing a picture, it draws a gradient sky, blits a
CONDITION ICON from a generated sprite atlas, and writes the temperature and a
three-day strip with text ops — a couple of dozen ops a frame. The icons (sun,
moon, cloud, rain, snow, storm, fog) are drawn once with Pillow and uploaded to
the panel's atlas; a wall without the sprite op falls back to a coloured disc.

Conditions come from the shared ``get_weather`` helper, so the wall's configured
location/provider and the ten-minute cache are honoured.
"""

_MAGENTA = (255, 0, 255)
# sky token -> icon index; "clear" splits day/night below.
_ICON = {'pcloudy': 2, 'cloudy': 3, 'fog': 7,
         'rainl': 4, 'rain': 4, 'rainh': 4, 'shwr': 4, 'sleet': 4,
         'snowl': 5, 'snow': 5, 'snowh': 5, 'storm': 6, 'hail': 6}
_WORD = {'clear': 'Clear', 'pcloudy': 'Partly', 'cloudy': 'Cloudy', 'fog': 'Fog',
         'rainl': 'Light rain', 'rain': 'Rain', 'rainh': 'Heavy rain', 'shwr': 'Showers',
         'snowl': 'Light snow', 'snow': 'Snow', 'snowh': 'Heavy snow', 'sleet': 'Sleet',
         'storm': 'Storm', 'hail': 'Hail'}
_WET = ('rainl', 'rain', 'rainh', 'shwr', 'sleet')


def _mix(a, b, t):
    return tuple(int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3))


def _sky(hour, tok, night):
    """(top, bottom) sky gradient — time of day, greyed by cloud/rain."""
    if night:
        top, bot = (12, 18, 46), (3, 5, 16)
    elif hour < 7:
        top, bot = (66, 86, 158), (240, 150, 96)
    elif hour < 17:
        top, bot = (52, 120, 226), (150, 196, 250)
    elif hour < 20:
        top, bot = (44, 56, 120), (238, 126, 66)
    else:
        top, bot = (12, 18, 46), (3, 5, 16)
    if tok in ('cloudy', 'fog') or tok in _WET:
        top, bot = _mix(top, (78, 84, 100), 0.55), _mix(bot, (54, 58, 72), 0.55)
    if tok in ('snowl', 'snow', 'snowh'):
        top, bot = _mix(top, (120, 130, 150), 0.5), _mix(bot, (86, 96, 116), 0.5)
    if tok in ('storm', 'hail'):
        top, bot = _mix(top, (34, 36, 48), 0.72), _mix(bot, (20, 22, 30), 0.72)
    return top, bot


def _cloud(d, s, col, y):
    cy = s * y
    d.ellipse([s * 0.08, cy - s * 0.10, s * 0.52, cy + s * 0.20], fill=col)
    d.ellipse([s * 0.32, cy - s * 0.20, s * 0.82, cy + s * 0.16], fill=col)
    d.ellipse([s * 0.54, cy - s * 0.08, s * 0.92, cy + s * 0.20], fill=col)
    d.rectangle([s * 0.14, cy + s * 0.02, s * 0.86, cy + s * 0.20], fill=col)


def _wx_tiles(s):
    """The condition-icon atlas, on magenta: sun, moon, partly, cloud, rain, snow, storm, fog."""
    import math
    from PIL import Image, ImageDraw
    W = max(1, int(s * 0.06))
    out = []

    def blank():
        im = Image.new('RGB', (s, s), _MAGENTA)
        return im, ImageDraw.Draw(im)

    im, d = blank()                                            # 0 sun
    c = s / 2.0
    for a in range(8):
        ang = a * math.pi / 4
        d.line([c + math.cos(ang) * s * 0.30, c + math.sin(ang) * s * 0.30,
                c + math.cos(ang) * s * 0.46, c + math.sin(ang) * s * 0.46], fill=(255, 208, 60), width=W)
    d.ellipse([c - s * 0.22, c - s * 0.22, c + s * 0.22, c + s * 0.22], fill=(255, 202, 46))
    out.append(im)

    im, d = blank()                                            # 1 moon
    d.ellipse([s * 0.20, s * 0.16, s * 0.80, s * 0.84], fill=(232, 236, 250))
    d.ellipse([s * 0.36, s * 0.08, s * 0.96, s * 0.76], fill=_MAGENTA)
    out.append(im)

    im, d = blank()                                            # 2 partly
    d.ellipse([s * 0.10, s * 0.08, s * 0.46, s * 0.44], fill=(255, 202, 46))
    _cloud(d, s, (228, 233, 244), 0.56)
    out.append(im)

    im, d = blank(); _cloud(d, s, (228, 233, 244), 0.5); out.append(im)   # 3 cloud

    im, d = blank(); _cloud(d, s, (210, 216, 230), 0.40)                  # 4 rain
    for rx in (0.30, 0.50, 0.70):
        d.line([s * rx, s * 0.64, s * rx - s * 0.06, s * 0.88], fill=(90, 160, 255), width=W)
    out.append(im)

    im, d = blank(); _cloud(d, s, (222, 228, 242), 0.40)                  # 5 snow
    for sx in (0.30, 0.50, 0.70):
        d.ellipse([s * sx - s * 0.06, s * 0.72 - s * 0.06, s * sx + s * 0.06, s * 0.72 + s * 0.06],
                  fill=(240, 248, 255))
    out.append(im)

    im, d = blank(); _cloud(d, s, (150, 156, 172), 0.40)                  # 6 storm
    d.polygon([(s * 0.50, s * 0.58), (s * 0.40, s * 0.80), (s * 0.52, s * 0.78),
               (s * 0.42, s * 0.98), (s * 0.70, s * 0.66), (s * 0.56, s * 0.68)], fill=(255, 222, 70))
    out.append(im)

    im, d = blank()                                            # 7 fog
    for fy in (0.34, 0.50, 0.66, 0.82):
        d.line([s * 0.12, s * fy, s * 0.88, s * fy], fill=(200, 206, 218), width=W)
    out.append(im)
    return out


def _icon_for(tok, night):
    if tok == 'clear':
        return 1 if night else 0
    return _ICON.get(tok, 3)


def _conv(f, unit):
    if f is None:
        return None
    if unit == 'c':
        return round((f - 32) * 5 / 9)
    if unit == 'k':
        return round((f - 32) * 5 / 9 + 273.15)
    return round(f)


def fetch(settings, format_lines, get_rows, get_cols, canvas=None, get_weather=None):
    if canvas is None:
        return None
    from datetime import datetime

    W, H = canvas.width, canvas.height
    tile = max(14, min(30, int(H * 0.52))) & ~1
    unit = str(settings.get('temperature_unit', 'f') or 'f').lower()
    show_city = str(settings.get('show_city', 'yes') or 'yes') != 'no'

    tzname = str(settings.get('timezone') or '').strip()
    try:
        now = datetime.now(__import__('pytz').timezone(tzname)) if tzname else datetime.now()
    except Exception:
        now = datetime.now()
    hour = now.hour
    night = hour < 6 or hour >= 20

    wx = None
    try:
        wx = get_weather(days=3, air=False) if get_weather else None
    except Exception:
        wx = None
    wx = wx if isinstance(wx, dict) and wx.get('ok') else {}

    tok = str(wx.get('sky') or 'clear').lower()
    temp = _conv(wx.get('temp_f'), unit)
    hi, lo = _conv(wx.get('hi_f'), unit), _conv(wx.get('lo_f'), unit)
    city = str(wx.get('city') or '').strip()

    # atlas: (re)upload on size change, and re-assert occasionally in case another app clobbered it
    st = getattr(fetch, '_state', None)
    if st is None:
        st = {'atlas': None, 'n': 0}
        setattr(fetch, '_state', st)
    st['n'] += 1
    use_sprites = bool(getattr(canvas, 'can_sprite', False))
    if use_sprites and (st['atlas'] != tile or st['n'] % 30 == 1):
        canvas.upload_atlas(_wx_tiles(tile))
        st['atlas'] = tile

    top, bot = _sky(hour, tok, night)
    canvas.gradient(0, 0, W, H, top, bot, 'v')

    # A narrow or short panel can't hold the place name and a three-day strip without
    # colliding — drop to a compact icon + temperature + one condition line there.
    compact = W < 100 or H < 40
    if compact:
        tile = min(tile, max(12, int(H * 0.62))) & ~1
    iy = 2 if compact else 3

    if use_sprites:
        canvas.sprite(_icon_for(tok, night), 2, iy)
    else:
        canvas.circle(2 + tile // 2, iy + tile // 2, tile // 2 - 1,
                      (255, 202, 46) if tok == 'clear' and not night else (220, 226, 238), fill=True)

    tsize = max(10, min(22, int(tile * (0.95 if compact else 0.9))))
    tx = 2 + tile + 3
    ts = f'{temp}°' if temp is not None else '--°'
    canvas.text(tx, iy + (tile - tsize) // 2, ts, (255, 255, 255), size=tsize)

    if compact:
        sub = _WORD.get(tok, tok.title())
        if hi is not None and lo is not None:
            sub = f'{hi}/{lo}  {sub}'
        canvas.text(2, H - 9, sub, (214, 224, 240), size=8)
        canvas.show()
        return 300.0

    # condition word + hi/lo under the temperature, place top-right
    sub = _WORD.get(tok, tok.title())
    if hi is not None and lo is not None:
        sub = f'{sub}  {hi}/{lo}'
    canvas.text(tx, iy + tile - 1, sub[:22], (206, 216, 234), size=8)
    if show_city and city:
        canvas.text(W - 2, 1, city[:16], (210, 222, 240), size=8, align='right')

    # 3-day strip along the bottom
    fc = (wx.get('forecast') or [])[:3]
    if fc:
        fh = max(9, int(H * 0.26))
        fy = H - fh
        canvas.rect(0, fy - 1, W, 1, (255, 255, 255))          # a hairline divider
        cw = W // len(fc)
        for i, day in enumerate(fc):
            if i:
                canvas.vline(i * cw, fy + 1, fh - 2, (255, 255, 255))
            try:
                lbl = datetime.strptime(str(day.get('date'))[:10], '%Y-%m-%d').strftime('%a')
            except Exception:
                lbl = ''
            dhi, dlo = _conv(day.get('hi_f'), unit), _conv(day.get('lo_f'), unit)
            txt = f'{lbl} {dhi}/{dlo}' if (dhi is not None and dlo is not None) else lbl
            canvas.text(i * cw + cw // 2, fy + (fh - 8) // 2, txt, (224, 232, 246), size=8, align='center')

    canvas.show()
    return 300.0                                               # conditions move slowly
