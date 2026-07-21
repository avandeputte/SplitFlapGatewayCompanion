"""World Time — several cities, their local hour written in light.

A canvas app (surface: canvas). Where the flap World Clock spells three zones
across a character grid, this renders one anti-aliased ROW per city straight
onto the Matrix panel's framebuffer and pushes the whole frame (PUT
/api/canvas/frame): the city on the left with room to be read in full, the local
HH:MM on the right, over a dark row whose faint tint and left-edge stripe carry
the day/night cue — warm by day, cool by night.

Rows stack to fill the wall — two on a 32px panel, up to four on 64px — so a
desk wall can hold New York, London and Tokyo at once. Dark background, bright
type, high contrast, never pink.

Zones come from the same ``world_clock_zones`` setting as the flap World Clock,
so the two apps share their list. The bundled font and the panel-sized helpers
are the injected ``canvas``; Pillow is imported lazily inside fetch.
"""

# Day/night palette. Backgrounds are kept VERY dark (barely-tinted near-black) so
# the bright type carries — the day/night signal is a faint tint plus the vivid
# left stripe, not a bright wash. Nothing here passes through pink.
_DAY_BG = (20, 13, 3)
_NIGHT_BG = (5, 8, 20)
_DAY_TXT = (255, 244, 220)
_NIGHT_TXT = (214, 228, 255)
_DAY_ACCENT = (255, 150, 40)     # the vivid warm cue on the left stripe
_NIGHT_ACCENT = (86, 130, 226)   # the vivid cool cue on the left stripe


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _scale(c, k):
    return tuple(max(0, min(255, int(round(v * k)))) for v in c)


def _day_factor(h):
    """0 at deep night, 1 in full day, ramped across dawn (5-8) and dusk (17-20).
    ``h`` is a float hour in [0, 24)."""
    if h < 5.0 or h >= 20.0:
        return 0.0
    if h < 8.0:
        return (h - 5.0) / 3.0
    if h < 17.0:
        return 1.0
    return 1.0 - (h - 17.0) / 3.0


def _fit_font(canvas, target_cap, max_w, sample):
    """Largest bundled font whose cap height is about ``target_cap`` and whose
    ``sample`` still fits ``max_w``. Returns (font, cap_height, ink_top)."""
    size = max(7, int(round(target_cap / 0.72)))
    font = canvas.font(size)
    for _ in range(48):
        if size <= 7 or font.getlength(sample) <= max_w:
            break
        size -= 1
        font = canvas.font(size)
    l, t, r, b = font.getbbox('8')
    return font, (b - t), t


def _fit_text(font, s, max_w):
    """``s`` trimmed with an ellipsis until it fits ``max_w`` (never past empty)."""
    if max_w <= 0:
        return ''
    if font.getlength(s) <= max_w:
        return s
    while s and font.getlength(s + '…') > max_w:
        s = s[:-1]
    return (s + '…') if s else ''


def _grad(Image, w, h, top, bot):
    """A w x h vertical gradient tile (top -> bot), one column then stretched."""
    h = max(1, h)
    col = Image.new('RGB', (1, h))
    px = col.load()
    m = max(1, h - 1)
    for y in range(h):
        t = y / m
        px[0, y] = (int(top[0] + (bot[0] - top[0]) * t),
                    int(top[1] + (bot[1] - top[1]) * t),
                    int(top[2] + (bot[2] - top[2]) * t))
    return col.resize((max(1, w), h))


def _next_minute_hold():
    """Seconds until the next wall-clock minute. Every zone's minute rolls on the same UTC
    second (offsets are whole minutes), so we redraw exactly on the tick instead of ~60×/min."""
    from datetime import datetime
    now = datetime.now()
    return max(1.0, 60.0 - now.second - now.microsecond / 1_000_000.0)


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None
    from datetime import datetime
    from PIL import Image, ImageDraw
    try:
        import pytz
    except Exception:
        pytz = None

    settings = settings or {}
    W, H = int(canvas.width), int(canvas.height)

    raw = str(settings.get('world_clock_zones') or '').strip()
    zones = [z.strip() for z in raw.split(',') if z.strip()]
    if not zones:
        zones = ['America/New_York', 'Europe/London', 'Asia/Tokyo']

    # How many rows the wall can carry: two on a 32px panel, four on 64px. Fewer
    # zones simply make taller rows that still fill the height.
    rows_fit = max(1, H // 15)
    resolved = []
    for z in zones:
        if len(resolved) >= rows_fit:
            break
        tz = None
        if pytz is not None:
            try:
                tz = pytz.timezone(z)
            except Exception:
                continue                      # skip an invalid zone, keep the rest
        try:
            now = datetime.now(tz) if tz is not None else datetime.now()
        except Exception:
            now = datetime.now()
        label = z.split('/')[-1].replace('_', ' ')   # "America/New_York" -> "New York"
        resolved.append((label, now))

    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"                       # crisp 1-bit text — no anti-aliased fuzz

    if not resolved:
        f, cap, top = _fit_font(canvas, H * 0.34, W - 4, 'No zones')
        msg = 'No zones'
        draw.text(((W - f.getlength(msg)) / 2.0, (H - cap) / 2.0 - top),
                  msg, fill=(230, 230, 235), font=f, anchor='la')
        canvas.frame(img)
        return _next_minute_hold()

    n = len(resolved)
    row_h = H / n

    # Two sizes shared by every row. The CITY leads: it gets the bigger share of
    # the width and a generous cap so a name reads in full — the time is limited
    # to ~a third of the width and sits on the right. (Before, the time claimed
    # half the row and an icon ate the rest, so "New York" clipped to "New Y…".)
    tfont, tcap, ttop = _fit_font(canvas, row_h * 0.52, W * 0.34, '88:88')
    cfont, ccap, ctop = _fit_font(canvas, row_h * 0.46, W * 0.68, 'Los Angeles')

    bar_w = 2
    pad = 4
    for i, (label, now) in enumerate(resolved):
        y0 = int(round(i * H / n))
        y1 = H if i == n - 1 else int(round((i + 1) * H / n))
        rh = y1 - y0

        df = _day_factor(now.hour + now.minute / 60.0)
        bg = _lerp(_NIGHT_BG, _DAY_BG, df)
        txt = _lerp(_NIGHT_TXT, _DAY_TXT, df)
        accent = _lerp(_NIGHT_ACCENT, _DAY_ACCENT, df)
        city_c = _scale(txt, 0.9)                 # bright, just under the time

        # A dark row: near-black, a whisper lighter at the top so stacked rows
        # read as separate shelves. Deliberately dim, so the type has contrast.
        img.paste(_grad(Image, W, rh, _scale(bg, 1.2), _scale(bg, 0.35)), (0, y0))
        draw.rectangle([0, y0, bar_w - 1, y1 - 1], fill=accent)   # day/night stripe

        hhmm = '%02d:%02d' % (now.hour, now.minute)
        tw = tfont.getlength(hhmm)
        tx = W - pad - tw
        draw.text((tx, y0 + (rh - tcap) / 2.0 - ttop), hhmm, fill=txt, font=tfont, anchor='la')

        cx = bar_w + pad
        city = _fit_text(cfont, label, tx - cx - pad)
        if city:
            draw.text((cx, y0 + (rh - ccap) / 2.0 - ctop), city, fill=city_c, font=cfont, anchor='la')

    canvas.frame(img)
    return _next_minute_hold()                     # HH:MM only changes on the minute
