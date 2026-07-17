"""World Time — several cities, their local hour written in light.

A canvas app (surface: canvas). Where the flap World Clock spells three zones
across a character grid, this renders one anti-aliased ROW per city straight
onto the Matrix panel's framebuffer and pushes the whole frame (PUT
/api/canvas/frame): the city on the left, the local HH:MM large on the right,
and a day/night cue that reads at a glance — the whole row is tinted warm amber
by day and cool blue by night (dim through dawn and dusk), a vivid stripe on the
left edge carries the same cue, and on a panel wide enough to hold it a small
sun or moon is drawn beside the city.

Rows stack to fill the wall — two on a 32px panel, up to four on 64px — so a
desk wall can hold New York, London and Tokyo at once. Dark background, high
contrast, never pink.

Zones come from the same ``world_clock_zones`` setting as the flap World Clock,
so the two apps share their list. The bundled font and the panel-sized helpers
are the injected ``canvas``; Pillow is imported lazily inside fetch, per the
canvas contract.
"""

# Day/night palette. Warm ambers by day, cool blues by night — nothing here (nor
# any interpolation between a day value and its night value) passes through pink.
# Backgrounds stay dark so the bright type carries.
_DAY_BG = (34, 23, 7)
_NIGHT_BG = (6, 11, 28)
_DAY_TXT = (255, 246, 224)
_NIGHT_TXT = (219, 231, 255)
_DAY_ACCENT = (255, 150, 40)     # the vivid warm cue (left stripe / sun halo)
_NIGHT_ACCENT = (74, 116, 214)   # the vivid cool cue (left stripe / moon halo)
_SUN = (255, 196, 66)
_SUN_CORE = (255, 232, 150)
_MOON = (206, 222, 255)


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
    ``sample`` still fits ``max_w``. Returns (font, cap_height, ink_top) — the
    last two from the ink box of '8', for centring a numeral band vertically."""
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


def _sun_tile(Image, ImageDraw, ImageFilter, box, body, core, halo):
    """A small anti-aliased sun (disc + core + rays) with a soft warm halo,
    drawn supersampled then downsampled. Returns an RGBA tile of side ``box``."""
    import math
    S = max(8, box * 4)
    c = S / 2.0
    R = S * 0.22
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rw = max(1, int(S * 0.035))
    for k in range(8):
        a = k * math.pi / 4.0
        d.line([c + math.cos(a) * R * 1.35, c + math.sin(a) * R * 1.35,
                c + math.cos(a) * R * 2.0, c + math.sin(a) * R * 2.0],
               fill=body + (255,), width=rw)
    d.ellipse([c - R, c - R, c + R, c + R], fill=body + (255,))
    d.ellipse([c - R * 0.5, c - R * 0.5, c + R * 0.5, c + R * 0.5], fill=core + (255,))
    hl = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(hl).ellipse([c - R * 1.9, c - R * 1.9, c + R * 1.9, c + R * 1.9],
                               fill=halo + (120,))
    hl = hl.filter(ImageFilter.GaussianBlur(S * 0.09))
    return Image.alpha_composite(hl, layer).resize((box, box), Image.LANCZOS)


def _moon_tile(Image, ImageDraw, ImageFilter, box, body, halo):
    """A small anti-aliased crescent moon (a disc with an offset bite carved out)
    with a soft cool halo. Returns an RGBA tile of side ``box``."""
    S = max(8, box * 4)
    c = S / 2.0
    R = S * 0.30
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([c - R, c - R, c + R, c + R], fill=body + (255,))
    off = R * 0.62
    d.ellipse([c - R + off, c - R - R * 0.06, c + R + off, c + R - R * 0.06],
              fill=(0, 0, 0, 0))
    hl = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(hl).ellipse([c - R * 1.55, c - R * 1.55, c + R * 1.55, c + R * 1.55],
                               fill=halo + (120,))
    hl = hl.filter(ImageFilter.GaussianBlur(S * 0.09))
    return Image.alpha_composite(hl, layer).resize((box, box), Image.LANCZOS)


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None
    from datetime import datetime
    from PIL import Image, ImageDraw, ImageFilter
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

    if not resolved:
        f, cap, top = _fit_font(canvas, H * 0.34, W - 4, 'No zones')
        msg = 'No zones'
        draw.text(((W - f.getlength(msg)) / 2.0, (H - cap) / 2.0 - top),
                  msg, fill=(230, 230, 235), font=f, anchor='la')
        canvas.frame(img)
        return 1.0

    n = len(resolved)
    row_h = H / n
    want_icon = W >= 100                       # a sun/moon needs the width to breathe
    ico = int(min(row_h * 0.62, 20)) if want_icon else 0
    if ico < 8:
        ico = 0

    # One font size for every row (rows share a height): a large time and a
    # smaller city label, both centred on each row's mid-line. The time is width
    # limited so 'HH:MM' fits even a 64px panel; the city is trimmed per row.
    tfont, tcap, ttop = _fit_font(canvas, row_h * 0.56, W * 0.50, '88:88')
    cfont, ccap, ctop = _fit_font(canvas, row_h * 0.34, W, 'Angeles')

    bar_w = 2
    for i, (label, now) in enumerate(resolved):
        y0 = int(round(i * H / n))
        y1 = H if i == n - 1 else int(round((i + 1) * H / n))
        rh = y1 - y0

        df = _day_factor(now.hour + now.minute / 60.0)
        bg = _lerp(_NIGHT_BG, _DAY_BG, df)
        txt = _lerp(_NIGHT_TXT, _DAY_TXT, df)
        city_c = _scale(txt, 0.72)
        accent = _lerp(_NIGHT_ACCENT, _DAY_ACCENT, df)

        # Row backdrop: a gentle dark gradient (lighter top, darker foot) so the
        # stacked rows read as separate shelves without a hard divider.
        img.paste(_grad(Image, W, rh, _scale(bg, 1.5), _scale(bg, 0.72)), (0, y0))
        draw.rectangle([0, y0, bar_w - 1, y1 - 1], fill=accent)   # day/night stripe

        x = bar_w + 3
        if ico:
            tile_y = y0 + (rh - ico) // 2
            if df >= 0.5:
                tile = _sun_tile(Image, ImageDraw, ImageFilter, ico, _SUN, _SUN_CORE, _DAY_ACCENT)
            else:
                tile = _moon_tile(Image, ImageDraw, ImageFilter, ico, _MOON, _NIGHT_ACCENT)
            img.paste(tile, (x, tile_y), tile)
            x += ico + 3

        hhmm = '%02d:%02d' % (now.hour, now.minute)
        tw = tfont.getlength(hhmm)
        tx = W - 3 - tw
        draw.text((tx, y0 + (rh - tcap) / 2.0 - ttop), hhmm, fill=txt, font=tfont, anchor='la')

        city = _fit_text(cfont, label, tx - x - 3)
        if city:
            draw.text((x, y0 + (rh - ccap) / 2.0 - ctop), city, fill=city_c, font=cfont, anchor='la')

    canvas.frame(img)
    return 1.0                                 # times tick each minute; 1s keeps it fresh
