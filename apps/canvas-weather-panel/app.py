"""Weather Panel — current conditions drawn on the panel with canvas draw-ops.

A canvas app: on a black (unlit) background — bright content reads best on an LED panel — it
blits a CONDITION ICON from a generated sprite atlas and writes the temperature and a three-day
strip with text ops. The icons (sun, moon, cloud, rain, snow, storm, fog) are drawn once with
Pillow and uploaded to the panel's atlas; a wall without the sprite op falls back to a colored disc.

Two things about the on-device text op (both handled by the injected ``canvas``): it draws
CP1252 glyphs (the firmware decodes the UTF-8 we send back to CP1252), so ``canvas.shadow_text``
keeps the degree sign / Latin accents and drops only what the panel can't draw; and ``size``
must be one of the bundled faces {8,9,10,13,18,20} — anything else falls back to a small 6×10
face — so sizes snap via ``canvas.face()``.

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


def _cloud(d, s, col, y):
    """A flat-bottomed cumulus: a wide base with a tall center puff and smaller side puffs, so it
    reads as a cloud (not a blob) even at a small tile size."""
    cy = s * y
    d.rectangle([s * 0.16, cy + s * 0.06, s * 0.84, cy + s * 0.22], fill=col)   # flat base
    d.ellipse([s * 0.10, cy + s * 0.00, s * 0.44, cy + s * 0.26], fill=col)     # left puff
    d.ellipse([s * 0.30, cy - s * 0.22, s * 0.72, cy + s * 0.24], fill=col)     # tall center puff
    d.ellipse([s * 0.56, cy - s * 0.04, s * 0.92, cy + s * 0.26], fill=col)     # right puff


def _wx_tiles(s):
    """The condition-icon atlas: sun, moon, partly, cloud, rain, snow, storm, fog (on magenta),
    bright enough to read on the black background."""
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


# Colors read as their hue on an LED panel only when saturated. Warm high / cool low, a light
# cyan condition word, muted stats — and the big temperature is tinted by how warm it is.
_WARM, _COOL, _WORDC, _MUTE, _DAY = (255, 150, 55), (55, 150, 255), (120, 210, 235), (172, 182, 205), (208, 216, 234)
_TEMP_RAMP = [(5, (60, 120, 255)), (32, (40, 170, 255)), (48, (0, 205, 220)), (60, (40, 215, 130)),
              (72, (150, 215, 55)), (82, (250, 195, 35)), (92, (255, 130, 30)), (104, (255, 70, 40))]


def _ramp(temp_f):
    """A saturated thermal color for a Fahrenheit temperature (whatever unit it's shown in)."""
    if temp_f is None:
        return (236, 240, 250)
    if temp_f <= _TEMP_RAMP[0][0]:
        return _TEMP_RAMP[0][1]
    if temp_f >= _TEMP_RAMP[-1][0]:
        return _TEMP_RAMP[-1][1]
    for (a, ca), (b, cb) in zip(_TEMP_RAMP, _TEMP_RAMP[1:]):
        if a <= temp_f <= b:
            t = (temp_f - a) / (b - a)
            return tuple(int(round(ca[k] + (cb[k] - ca[k]) * t)) for k in range(3))
    return _TEMP_RAMP[-1][1]


def _segs(canvas, x, y, segments, size, maxx):
    """Draw ``(text, color)`` runs left-to-right at ``size``, stopping before ``maxx`` so a
    narrow panel truncates between fields instead of clipping mid-word. Returns the end x."""
    for text, col in segments:
        text = canvas.cp(str(text))
        w = len(text) * canvas.face_width(size)
        if text and x + w > maxx:
            break
        if text:
            canvas.shadow_text(x, y, text, col, size)
        x += w
    return x


def _segs_w(canvas, segments, size):
    return sum(len(canvas.cp(str(t))) * canvas.face_width(size) for t, _ in segments)


def fetch_matrix(settings, canvas, get_weather=None):
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

    use_sprites = bool(getattr(canvas, 'can_sprite', False))
    canvas.clear((0, 0, 0))                     # black — bright content reads best on unlit pixels
    deg = '\N{DEGREE SIGN}'
    word = _WORD.get(tok, tok.title())
    compact = W < 100 or H < 40

    def _icon(x, y, s, sky, is_night):
        if use_sprites:
            canvas.sprite(_icon_for(sky, is_night), x, y)
        else:
            canvas.circle(x + s // 2, y + s // 2, s // 2 - 1,
                          (255, 206, 50) if sky == 'clear' and not is_night else (150, 200, 255), fill=True)

    if compact:                                 # small panel: icon + temp + word/HL on one line
        it = min(tile, max(12, int(H * 0.62))) & ~1
        if use_sprites:
            canvas.upload_atlas(_wx_tiles(it), persist=True)
        _icon(2, 2, it, tok, night)
        tsize = canvas.face(it)
        canvas.shadow_text(2 + it + 3, 2 + (it - tsize) // 2, f'{temp}{deg}' if temp is not None else '--',
                           _ramp(wx.get('temp_f')), tsize)
        x = _segs(canvas, 2, H - 9, [(word + '  ', _WORDC)], 8, W - 2)
        if hi is not None and lo is not None:
            _segs(canvas, x, H - 9, [(f'{hi}{deg}', _WARM), ('/', _MUTE), (f'{lo}{deg}', _COOL)], 8, W)
        canvas.show()
        return 300.0

    # --- wide layout: a compact top that fills the width, and a TALL forecast row -------------
    fc = (wx.get('forecast') or [])[:3]
    fh = max(16, int(H * 0.42)) if fc else 0     # a tall forecast row, so its icons read clearly
    fy = H - fh
    band = (fy - 1) if fc else H                 # the top area

    # The main condition icon and the forecast icons are different sizes, so two atlas sheets —
    # cheap now: each is a named, persisted sheet uploaded once. Main icon sized to the top band.
    it = min(tile, band - 10) & ~1
    if use_sprites:
        canvas.upload_atlas(_wx_tiles(it), persist=True)
    _icon(2, 1, it, tok, night)
    tstr = f'{temp}{deg}' if temp is not None else '--'
    tsize = canvas.face(min(20, it))
    tx = 2 + it + 3
    ty = 1 + (it - tsize) // 2
    canvas.shadow_text(tx, ty, tstr, _ramp(wx.get('temp_f')), tsize)
    tw = len(tstr) * canvas.face_width(tsize)

    # top row, right of the temperature: the condition word (big), then H/L (warm/cool) filling out
    bx = tx + tw + 8
    wsize = canvas.fit(word[:18], W - bx - 2, min(13, tsize))
    canvas.shadow_text(bx, ty, word[:18], _WORDC, wsize)
    hl = [(f'H {hi}{deg}  ', _WARM), (f'L {lo}{deg}', _COOL)] if (hi is not None and lo is not None) else []
    hl_placed = False
    if hl:
        hx = bx + len(canvas.cp(word[:18])) * canvas.face_width(wsize) + 8
        hlsize = canvas.fit(f'H {hi}{deg}  L {lo}{deg}', W - hx - 2, min(13, tsize))
        if _segs_w(canvas, hl, hlsize) <= W - hx - 2:             # room on the top row
            _segs(canvas, hx, ty + max(0, (wsize - hlsize) // 2), hl, hlsize, W - 1)
            hl_placed = True

    # a thin second line spanning the width: H/L (if it didn't fit above), feels / humidity / wind
    row = [] if hl_placed else (hl + [('   ', _MUTE)] if hl else [])
    feels = _conv(wx.get('feels_like_f'), unit)
    if feels is not None:
        row += [(f'Feels {feels}{deg}    ', _MUTE)]
    if wx.get('humidity') is not None:
        row += [(f'Humidity {int(wx["humidity"])}%    ', _MUTE)]
    if wx.get('wind_mph') is not None:
        row += [(f'Wind {int(wx["wind_mph"])} mph    ', _MUTE)]
    if show_city and city:
        row += [(city[:16], _DAY)]
    _segs(canvas, 2, band - 9, row, 8, W - 2)

    # tall 3-day forecast: a BIG clear icon, the day, and warm/cool hi/lo
    if fc:
        canvas.rect(0, fy - 1, W, 1, (44, 52, 68))
        cw = W // len(fc)
        fih = min(fh - 2, 28) & ~1               # big forecast icons
        show_ic = use_sprites and cw >= 62 and fih >= 12
        if show_ic:
            canvas.upload_atlas(_wx_tiles(fih), persist=True)
        for i, day in enumerate(fc):
            if i:
                canvas.vline(i * cw, fy + 2, fh - 4, (44, 52, 68))
            try:
                lbl = datetime.strptime(str(day.get('date'))[:10], '%Y-%m-%d').strftime('%a')
            except Exception:
                lbl = ''
            dhi, dlo = _conv(day.get('hi_f'), unit), _conv(day.get('lo_f'), unit)
            cell = ([(lbl + ' ', _DAY), (f'{dhi}{deg}', _WARM), ('/', _MUTE), (f'{dlo}{deg}', _COOL)]
                    if (dhi is not None and dlo is not None) else [(lbl, _DAY)])
            room = cw - (fih + 3 if show_ic else 0) - 2
            if len(cell) > 1 and _segs_w(canvas, cell, 8) > room:   # too narrow for both — show the high
                cell = [(lbl + ' ', _DAY), (f'{dhi}{deg}', _WARM)]
            gw = _segs_w(canvas, cell, 8) + (fih + 3 if show_ic else 0)
            gx = i * cw + max(1, (cw - gw) // 2)
            if show_ic:
                canvas.sprite(_icon_for(str(day.get('sky') or 'clear'), False), gx, fy + (fh - fih) // 2)
                gx += fih + 3
            _segs(canvas, gx, fy + (fh - 8) // 2, cell, 8, (i + 1) * cw - 1)

    canvas.show()
    return 300.0
