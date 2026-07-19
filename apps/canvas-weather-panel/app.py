"""Weather Panel — current conditions drawn on the panel with canvas draw-ops.

A canvas app: instead of pushing a picture it draws a gradient sky, blits a CONDITION
ICON from a generated sprite atlas, and writes the temperature and a three-day strip with
text ops. The icons (sun, moon, cloud, rain, snow, storm, fog) are drawn once with Pillow
and uploaded to the panel's atlas; a wall without the sprite op falls back to a coloured disc.

Two things the on-device text op demands, learned the hard way:
  * it renders ONE CP1252 byte per glyph with no UTF-8 decode — so text must be ASCII
    (the degree mark is drawn as a little ring, not a "°" character);
  * `size` must be one of the bundled faces {8,9,10,13,18,20} — anything else falls back
    to a small 6×10 face — so sizes are snapped with `_face()`.

Conditions come from the shared ``get_weather`` helper (the wall's configured location and
the ten-minute cache are honoured).
"""

_MAGENTA = (255, 0, 255)
_ICON = {'pcloudy': 2, 'cloudy': 3, 'fog': 7,
         'rainl': 4, 'rain': 4, 'rainh': 4, 'shwr': 4, 'sleet': 4,
         'snowl': 5, 'snow': 5, 'snowh': 5, 'storm': 6, 'hail': 6}
_WORD = {'clear': 'Clear', 'pcloudy': 'Partly', 'cloudy': 'Cloudy', 'fog': 'Fog',
         'rainl': 'Light rain', 'rain': 'Rain', 'rainh': 'Heavy rain', 'shwr': 'Showers',
         'snowl': 'Light snow', 'snow': 'Snow', 'snowh': 'Heavy snow', 'sleet': 'Sleet',
         'storm': 'Storm', 'hail': 'Hail'}
_WET = ('rainl', 'rain', 'rainh', 'shwr', 'sleet')
# The panel's bundled faces and their fixed glyph widths.
_FACE_W = {8: 5, 9: 6, 10: 6, 13: 8, 18: 9, 20: 10}
_FACES = (8, 9, 10, 13, 18, 20)
_SHADOW = (10, 12, 20)


def _face(sz):
    """Snap a wanted height to the largest bundled face that fits (min 8)."""
    ok = [s for s in _FACES if s <= sz]
    return max(ok) if ok else 8


def _ascii(s):
    return ''.join(c if 32 <= ord(c) < 127 else ' ' for c in str(s))


def _txt(canvas, x, y, s, color, size, align='left'):
    """Text with a dark drop-shadow so it reads on any sky. ASCII only (see module docs)."""
    s = _ascii(s)
    canvas.text(x + 1, y + 1, s, _SHADOW, size=size, align=align)
    canvas.text(x, y, s, color, size=size, align=align)


def _mix(a, b, t):
    return tuple(int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3))


def _sky(hour, tok, night):
    if night:
        top, bot = (12, 18, 46), (3, 5, 16)
    elif hour < 7:
        top, bot = (66, 86, 158), (240, 150, 96)
    elif hour < 17:
        top, bot = (36, 96, 200), (86, 150, 224)          # day — kept deep enough for light text
    elif hour < 20:
        top, bot = (44, 56, 120), (238, 126, 66)
    else:
        top, bot = (12, 18, 46), (3, 5, 16)
    if tok in ('cloudy', 'fog') or tok in _WET:
        top, bot = _mix(top, (70, 78, 96), 0.6), _mix(bot, (48, 54, 68), 0.6)
    if tok in ('snowl', 'snow', 'snowh'):
        top, bot = _mix(top, (96, 108, 130), 0.55), _mix(bot, (70, 80, 100), 0.55)
    if tok in ('storm', 'hail'):
        top, bot = _mix(top, (30, 32, 44), 0.74), _mix(bot, (18, 20, 28), 0.74)
    return top, bot


def _cloud(d, s, col, y):
    cy = s * y
    d.ellipse([s * 0.08, cy - s * 0.10, s * 0.52, cy + s * 0.20], fill=col)
    d.ellipse([s * 0.32, cy - s * 0.20, s * 0.82, cy + s * 0.16], fill=col)
    d.ellipse([s * 0.54, cy - s * 0.08, s * 0.92, cy + s * 0.20], fill=col)
    d.rectangle([s * 0.14, cy + s * 0.02, s * 0.86, cy + s * 0.20], fill=col)


def _wx_tiles(s):
    """The condition-icon atlas: sun, moon, partly, cloud, rain, snow, storm, fog (on magenta).
    A thin dark outline keeps the pale icons legible on a light sky."""
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
    d.ellipse([c - s * 0.24, c - s * 0.24, c + s * 0.24, c + s * 0.24], fill=(255, 170, 20), width=1)
    d.ellipse([c - s * 0.21, c - s * 0.21, c + s * 0.21, c + s * 0.21], fill=(255, 206, 50))
    out.append(im)

    im, d = blank()                                            # 1 moon
    d.ellipse([s * 0.20, s * 0.16, s * 0.80, s * 0.84], fill=(150, 160, 190))
    d.ellipse([s * 0.22, s * 0.18, s * 0.80, s * 0.82], fill=(232, 236, 250))
    d.ellipse([s * 0.36, s * 0.08, s * 0.96, s * 0.76], fill=_MAGENTA)
    out.append(im)

    im, d = blank()                                            # 2 partly
    d.ellipse([s * 0.10, s * 0.08, s * 0.46, s * 0.44], fill=(255, 206, 50))
    _cloud(d, s, (120, 128, 148), 0.585)
    _cloud(d, s, (232, 237, 246), 0.56)
    out.append(im)

    im, d = blank(); _cloud(d, s, (120, 128, 148), 0.52); _cloud(d, s, (232, 237, 246), 0.5)  # 3 cloud
    out.append(im)

    im, d = blank(); _cloud(d, s, (110, 118, 140), 0.42); _cloud(d, s, (214, 220, 234), 0.40)  # 4 rain
    for rx in (0.30, 0.50, 0.70):
        d.line([s * rx, s * 0.64, s * rx - s * 0.06, s * 0.88], fill=(80, 150, 255), width=W)
    out.append(im)

    im, d = blank(); _cloud(d, s, (110, 118, 140), 0.42); _cloud(d, s, (224, 230, 244), 0.40)  # 5 snow
    for sx in (0.30, 0.50, 0.70):
        d.ellipse([s * sx - s * 0.06, s * 0.72 - s * 0.06, s * sx + s * 0.06, s * 0.72 + s * 0.06],
                  fill=(240, 248, 255))
    out.append(im)

    im, d = blank(); _cloud(d, s, (90, 96, 112), 0.42); _cloud(d, s, (150, 156, 172), 0.40)     # 6 storm
    d.polygon([(s * 0.50, s * 0.58), (s * 0.40, s * 0.80), (s * 0.52, s * 0.78),
               (s * 0.42, s * 0.98), (s * 0.70, s * 0.66), (s * 0.56, s * 0.68)], fill=(255, 222, 70))
    out.append(im)

    im, d = blank()                                            # 7 fog
    for i, fy in enumerate((0.34, 0.50, 0.66, 0.82)):
        d.line([s * 0.12, s * fy, s * 0.88, s * fy], fill=(210, 216, 228), width=W)
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

    # The atlas is a SINGLE shared slot on the gateway — another canvas app (the Aquarium!)
    # may have overwritten it since we last drew, so re-assert ours EVERY draw. Cheap: this
    # app redraws only every few minutes, and the tiles are tiny.
    use_sprites = bool(getattr(canvas, 'can_sprite', False))
    if use_sprites:
        canvas.upload_atlas(_wx_tiles(tile))

    top, bot = _sky(hour, tok, night)
    canvas.gradient(0, 0, W, H, top, bot, 'v')

    compact = W < 100 or H < 40
    if compact:
        tile = min(tile, max(12, int(H * 0.62))) & ~1
    iy = 2 if compact else 3

    if use_sprites:
        canvas.sprite(_icon_for(tok, night), 2, iy)
    else:
        canvas.circle(2 + tile // 2, iy + tile // 2, tile // 2 - 1,
                      (255, 206, 50) if tok == 'clear' and not night else (220, 226, 238), fill=True)

    # big temperature, snapped to a real face, with a drawn degree ring (no "°" byte)
    tsize = _face(int(tile * (1.0 if compact else 0.95)))
    tx = 2 + tile + 3
    ty = iy + (tile - tsize) // 2
    digits = str(temp) if temp is not None else '--'
    _txt(canvas, tx, ty, digits, (255, 255, 255), tsize)
    dw = len(digits) * _FACE_W.get(tsize, 6)
    dr = 2 if tsize >= 18 else 1
    canvas.circle(tx + dw + dr + 1, ty + dr + 1, dr, (255, 255, 255))       # the degree mark

    if compact:
        sub = _WORD.get(tok, tok.title())
        if hi is not None and lo is not None:
            sub = f'{hi}/{lo}  {sub}'
        _txt(canvas, 2, H - 9, sub, (232, 238, 248), 8)
        canvas.show()
        return 300.0

    sub = _WORD.get(tok, tok.title())
    if hi is not None and lo is not None:
        sub = f'{sub}  {hi}/{lo}'
    _txt(canvas, tx, iy + tile - 1, sub[:22], (226, 233, 246), 8)
    if show_city and city:
        _txt(canvas, W - 2, 1, city[:16], (232, 240, 250), 8, align='right')

    fc = (wx.get('forecast') or [])[:3]
    if fc:
        fh = max(9, int(H * 0.26))
        fy = H - fh
        canvas.rect(0, fy - 1, W, 1, (235, 240, 250))
        cw = W // len(fc)
        for i, day in enumerate(fc):
            if i:
                canvas.vline(i * cw, fy + 1, fh - 2, (235, 240, 250))
            try:
                lbl = datetime.strptime(str(day.get('date'))[:10], '%Y-%m-%d').strftime('%a')
            except Exception:
                lbl = ''
            dhi, dlo = _conv(day.get('hi_f'), unit), _conv(day.get('lo_f'), unit)
            txt = f'{lbl} {dhi}/{dlo}' if (dhi is not None and dlo is not None) else lbl
            _txt(canvas, i * cw + cw // 2, fy + (fh - 8) // 2, txt, (232, 238, 248), 8, align='center')

    canvas.show()
    return 300.0
