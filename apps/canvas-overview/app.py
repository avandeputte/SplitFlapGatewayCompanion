"""Overview — a drawn time + date + weather dashboard for a Matrix panel.

A canvas app (surface: canvas). It renders one whole frame with Pillow and
pushes it (PUT /api/canvas/frame): a big clock as the focal point on the left,
the date beneath it, and — on any panel with room — a weather column on the
right (the temperature coloured by how warm it is, the condition, today's
high/low, and on a large panel feels-like, humidity and wind with a warm sun /
cool moon accent). A thin seconds bar sweeps the bottom. Solid black behind it,
crisp 1-bit text, curated high-contrast colour, no pink.

It fills whatever panel it's on: a small 128x32 shows a compact clock and a
little temperature/condition; a big 256x64 spreads a large clock and full date
on the left against a rich weather column on the right. Fonts are fitted to the
panel's height and shrunk/trimmed to its width, so nothing clips.

The clock ticks every frame. Live conditions come from the shared get_weather
helper (so it honours the wall's configured location/provider), cached ten
minutes while the clock keeps time; if it's missing or fails the clock and date
still show and the weather column is simply left out. Timezone follows the app's
timezone setting (pytz), else the host clock — exactly like the canvas clock.
"""

# The canonical `sky` token -> a condition word (full, and a short form for a
# narrow column). Same tokens the shared weather helper emits.
_SKY_WORD = {'clear': 'Clear', 'pcloudy': 'Partly', 'cloudy': 'Cloudy', 'fog': 'Fog',
             'rainl': 'Light rain', 'rain': 'Rain', 'rainh': 'Heavy rain', 'shwr': 'Showers',
             'snowl': 'Light snow', 'snow': 'Snow', 'snowh': 'Heavy snow', 'sleet': 'Sleet',
             'storm': 'Storm', 'hail': 'Hail'}
_SKY_SHORT = {'clear': 'Clear', 'pcloudy': 'Partly', 'cloudy': 'Cloudy', 'fog': 'Fog',
              'rainl': 'Rain', 'rain': 'Rain', 'rainh': 'Rain', 'shwr': 'Showers',
              'snowl': 'Snow', 'snow': 'Snow', 'snowh': 'Snow', 'sleet': 'Sleet',
              'storm': 'Storm', 'hail': 'Hail'}

_WEEKDAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
_MONTH = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# Curated palette — high contrast on black, and it never passes through pink.
_C_CLOCK = (255, 244, 224)     # the time: a warm white, the focal point
_C_DATE = (150, 164, 192)      # the date: a muted slate, clearly secondary
_C_DIV = (40, 46, 62)          # the faint divider between the two columns
_C_WORD = (198, 216, 238)      # the condition word: a bright cool white
_C_HI_L = (255, 150, 55)       # today's high — warm (label / value); saturated so it reads on LEDs
_C_HI_V = (255, 170, 80)
_C_LO_L = (55, 150, 255)       # today's low — cool; a real blue, not a tinted white
_C_LO_V = (90, 175, 255)
_C_SEP = (96, 104, 124)        # muted separators between fields
_C_MUTE = (150, 164, 190)      # feels/humidity/wind labels
_C_MUTE_V = (206, 216, 236)    # ...and their values
_C_SUN = (255, 200, 70)        # day accent disc
_C_SUN_CORE = (255, 232, 150)
_C_MOON = (216, 226, 248)      # night accent disc
_C_SB_TRK = (24, 26, 34)       # the seconds bar: track / fill / leading pixel
_C_SB_FIL = (222, 168, 92)
_C_SB_HEAD = (255, 250, 240)

# Temperature colour ramp by Fahrenheit — a saturated thermal scale so a mild temperature still
# carries a colour instead of washing out to white on the LED panel: cold blue -> cyan -> green
# -> amber -> hot red. The hue runs through green (never straight blue->red), so no lerp hits pink.
_TEMP_STOPS = [(5, (60, 120, 255)), (32, (40, 170, 255)), (48, (0, 205, 220)),
               (60, (40, 215, 130)), (72, (150, 215, 55)), (82, (250, 195, 35)),
               (92, (255, 130, 30)), (104, (255, 70, 40))]


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _ramp(stops, v):
    if v <= stops[0][0]:
        return stops[0][1]
    if v >= stops[-1][0]:
        return stops[-1][1]
    for i in range(len(stops) - 1):
        x0, c0 = stops[i]
        x1, c1 = stops[i + 1]
        if x0 <= v <= x1:
            return _lerp(c0, c1, (v - x0) / ((x1 - x0) or 1))
    return stops[-1][1]


def _num(v, unit):
    """A Fahrenheit value -> the chosen unit, as an int (or None)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if unit == 'c':
        return int(round((f - 32) * 5 / 9))
    if unit == 'k':
        return int(round((f - 32) * 5 / 9 + 273.15))
    return int(round(f))


def _wind(v, unit):
    """Wind (native mph) -> (value, suffix); km/h when the unit is metric."""
    if v is None:
        return None, ''
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None, ''
    if unit in ('c', 'k'):
        return int(round(f * 1.60934)), 'kph'
    return int(round(f)), 'mph'


def _fit(canvas, text, max_w, want, lo=6):
    """Largest bundled font <= `want` px whose `text` still fits `max_w`."""
    size = max(lo, int(want))
    f = canvas.font(size)
    guard = 0
    while size > lo and f.getlength(text) > max_w and guard < 240:
        size -= 1
        f = canvas.font(size)
        guard += 1
    return f


def _truncate(font, text, max_w):
    while text and font.getlength(text) > max_w:
        text = text[:-1]
    return text


def _line(font, segs):
    """A drawable line: (font, [(text,colour)...], ink_height, ink_top)."""
    txt = ''.join(s for s, _ in segs) or '8'
    bb = font.getbbox(txt)
    return (font, segs, bb[3] - bb[1], bb[1])


def _draw_stack(draw, x, top, region_h, lines, gap):
    """Left-align `lines` in a column at `x`, the block centred in [top, top+region_h)."""
    total = sum(ln[2] for ln in lines) + gap * max(0, len(lines) - 1)
    y = top + max(0, (region_h - total) // 2)
    for font, segs, ih, itop in lines:
        cx = x
        for s, col in segs:                     # each segment shares the line's top
            draw.text((cx, y - itop), s, font=font, fill=col, anchor='la')
            cx += font.getlength(s)
        y += ih + gap


def _fit_stack(canvas, specs, max_w, budget_h, gap):
    """Build a vertical stack of lines that FITS `budget_h` — no line ever clips.

    `specs` is a list of (segs, want_px, lo_px). Each line uses the largest bundled
    font <= want that still fits `max_w`. If the assembled stack (sum of ink heights
    + gaps) is taller than budget_h, every want is scaled down by a shared factor and
    the stack rebuilt — so the whole column shrinks together, keeping its relative
    sizes, until it fits (or reaches the per-line floor). Returns _line() tuples.

    This is what keeps a crowded weather column (temp, condition, high/low, feels,
    humidity/wind) inside a short panel instead of spilling the last line off the
    bottom edge, whatever the day's numbers happen to be.
    """
    gaps = gap * max(0, len(specs) - 1)
    scale = 1.0
    lines = []
    for _ in range(8):
        lines = [_line(_fit(canvas, ''.join(s for s, _ in segs) or '8', max_w,
                            max(lo, want * scale), lo=lo), segs)
                 for segs, want, lo in specs]
        ink = sum(ln[2] for ln in lines)
        if ink + gaps <= budget_h or scale <= 0.5:
            return lines
        # shrink proportionally to close the overflow (a nudge past 1.0 for rounding)
        scale *= max(0.5, (budget_h - gaps) / max(1, ink)) * 0.97
    return lines


def fetch(settings, format_lines, get_rows, get_cols, canvas=None, get_weather=None):
    if canvas is None:
        return None
    from datetime import datetime
    from PIL import Image, ImageDraw

    st = getattr(fetch, '_state', None)
    if st is None:
        st = {'wx': None, 'at': None, 'tried': None}
        setattr(fetch, '_state', st)

    # --- time (per the app's timezone, else the host clock) --------------------
    tzname = str(settings.get('timezone') or '').strip()
    try:
        now = datetime.now(__import__('pytz').timezone(tzname)) if tzname else datetime.now()
    except Exception:
        now = datetime.now()

    # --- weather: cached ~10 min, retried at most every 30s while it's missing --
    nowt = datetime.now()
    have = st['wx'] is not None
    fresh = st['at'] is not None and (nowt - st['at']).total_seconds() <= 600
    may_retry = st['tried'] is None or (nowt - st['tried']).total_seconds() > 30
    if get_weather is not None and (not have or not fresh) and may_retry:
        st['tried'] = nowt
        try:
            wx = get_weather(days=1, air=False)
            if wx and wx.get('ok'):
                st['wx'], st['at'] = wx, nowt
        except Exception:
            pass
    wx = st['wx'] or {}

    # --- settings --------------------------------------------------------------
    fmt = str(settings.get('clock_format', '24h') or '24h').lower()
    unit = str(settings.get('temperature_unit', 'f') or 'f').lower()
    if unit not in ('f', 'c', 'k'):
        unit = 'f'

    deg = '\N{DEGREE SIGN}'
    hour = (now.hour % 12 or 12) if fmt == '12h' else now.hour
    clock = f'{hour:02d}:{now.minute:02d}'
    h24 = now.hour
    night = h24 < 6 or h24 >= 20

    sky = wx.get('sky')
    tf = wx.get('temp_f')
    have_wx = bool(wx) and (tf is not None or bool(sky))
    temp = _num(tf, unit)
    hi, lo = _num(wx.get('hi_f'), unit), _num(wx.get('lo_f'), unit)
    feels = _num(wx.get('feels_like_f'), unit)
    hum = wx.get('humidity')
    wind_v, wind_u = _wind(wx.get('wind_mph'), unit)

    # --- panel + regions -------------------------------------------------------
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"                          # crisp 1-bit text — no AA fuzz

    pad = 2 if W >= 96 else 1
    sb_h = 2 if H >= 48 else (1 if H >= 40 else 0)   # seconds bar height (0 on tiny)
    region_h = H - sb_h

    two_col = have_wx and W >= 112
    if two_col:
        rcw = max(46, int(W * 0.35))
        rcw = min(rcw, W - 58)                   # always leave the clock a wide column
        rx = W - rcw                             # divider / right-column left edge
    else:
        rx = W
    Lw = rx - 2 * pad                            # usable left width
    gap_l = 2 if H >= 40 else 1
    gap_r = 2 if H >= 48 else 1

    # --- LEFT column: the big clock, the date beneath it -----------------------
    clock_cap = int(H * (0.58 if H >= 48 else 0.54))
    cfont = _fit(canvas, clock, Lw, clock_cap / 0.72, lo=8)

    wd, mo = _WEEKDAY[now.weekday()], _MONTH[now.month - 1]
    if Lw >= 150:                                # room for the year on a big panel
        date_cands = [f'{wd} {now.day} {mo} {now.year}', f'{wd} {now.day} {mo}',
                      f'{wd} {now.day}', wd]
    else:
        date_cands = [f'{wd} {now.day} {mo}', f'{wd} {now.day}', f'{now.day} {mo}', wd]
    dsize = max(7, min(int(H * 0.23), 15))
    dfloor = max(7, int(dsize * 0.6))
    date_str, dfont = None, None
    for c in date_cands:                         # richest that fits, shrinking to a floor
        f = _fit(canvas, c, Lw, dsize, lo=dfloor)
        if f.getlength(c) <= Lw:
            date_str, dfont = c, f
            break
    if date_str is None:
        dfont = canvas.font(dfloor)
        date_str = _truncate(dfont, date_cands[-1], Lw)

    _draw_stack(draw, pad, 0, region_h,
                [_line(cfont, [(clock, _C_CLOCK)]), _line(dfont, [(date_str, _C_DATE)])], gap_l)

    # --- RIGHT column: the weather -------------------------------------------
    if two_col:
        draw.line([(rx, 3), (rx, region_h - 3)], fill=_C_DIV)   # subtle divider
        wx0 = rx + pad + 2
        wxw = max(8, W - pad - wx0)
        rich_h = wxw >= 78                       # room for feels/humidity/wind labels
        rich_v = H >= 48                         # room for many stacked lines

        # a warm sun (day) / cool crescent moon (night) accent, top-right, big panels
        if rich_v and rich_h:
            r = max(3, int(H * 0.095))
            cx, cy = W - pad - r - 1, 2 + r
            if night:
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_C_MOON)
                o = max(1, int(r * 0.55))
                draw.ellipse([cx - r + o, cy - r, cx + r + o, cy + r], fill=(0, 0, 0))
            else:
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_C_SUN)
                draw.ellipse([cx - r + 1, cy - r + 1, cx + r - 1, cy + r - 1], fill=_C_SUN_CORE)

        specs = []
        # the temperature — big, the focus of the column, coloured by how warm
        temp_s = f'{temp}{deg}' if temp is not None else '--'
        temp_col = _ramp(_TEMP_STOPS, float(tf)) if tf is not None else (232, 238, 246)
        specs.append(([(temp_s, temp_col)], int(H * (0.42 if rich_v else 0.46)), 9))

        # the condition word (full where wide, short where narrow)
        if sky:
            word = (_SKY_WORD if rich_h else _SKY_SHORT).get(sky, 'Weather')
            wwant = int(H * (0.18 if rich_v else 0.26))
            wf0 = _fit(canvas, word, wxw, wwant, lo=7)     # trim only if it won't fit at all
            specs.append(([(_truncate(wf0, word, wxw), _C_WORD)], wwant, 7))

        # today's high / low — warm high, cool low
        if (rich_v or wxw >= 52) and (hi is not None or lo is not None):
            if hi is not None and lo is not None and rich_h:
                hl = [('H ', _C_HI_L), (f'{hi}{deg}', _C_HI_V), ('  ', _C_SEP),
                      ('L ', _C_LO_L), (f'{lo}{deg}', _C_LO_V)]
            elif hi is not None and lo is not None:
                hl = [(f'{hi}{deg}', _C_HI_V), (' / ', _C_SEP), (f'{lo}{deg}', _C_LO_V)]
            elif hi is not None:
                hl = [('H ', _C_HI_L), (f'{hi}{deg}', _C_HI_V)]
            else:
                hl = [('L ', _C_LO_L), (f'{lo}{deg}', _C_LO_V)]
            specs.append((hl, int(H * 0.16), 6))

        # feels-like, then humidity + wind — only where the panel is generous
        if rich_v and rich_h:
            if feels is not None:
                specs.append(([('Feels ', _C_MUTE), (f'{feels}{deg}', _C_MUTE_V)],
                              int(H * 0.15), 6))
            # Humidity + wind on one line — the '%' and the mph/km-h unit already
            # say which is which, so drop the word labels: a shorter string fits a
            # much taller, readable font in the narrow column.
            hw = []
            if hum is not None:
                hw += [(f'{hum}%', _C_MUTE_V)]
            if wind_v is not None:
                if hw:
                    hw += [('  ', _C_SEP)]
                hw += [(f'{wind_v}{wind_u}', _C_MUTE_V)]
            if hw:
                specs.append((hw, int(H * 0.20), 8))

        # Fit the whole column to the region so the last line never clips off the
        # bottom edge — shrinks the stack together when the day's numbers make it tall.
        lines = _fit_stack(canvas, specs, wxw, region_h - 1, gap_r)
        _draw_stack(draw, wx0, 0, region_h, lines, gap_r)

    # --- a thin seconds bar sweeping the bottom edge --------------------------
    if sb_h:
        frac = (now.second + now.microsecond / 1_000_000.0) / 60.0
        by = H - sb_h
        draw.rectangle([0, by, W - 1, H - 1], fill=_C_SB_TRK)
        fw = int(round(W * frac))
        if fw > 0:
            draw.rectangle([0, by, fw - 1, H - 1], fill=_C_SB_FIL)
        if 0 <= fw < W:
            draw.rectangle([fw, by, fw, H - 1], fill=_C_SB_HEAD)

    canvas.frame(img)
    return 0.5                                    # it ticks; clock every ~0.5s, weather cached
