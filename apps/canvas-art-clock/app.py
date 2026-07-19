"""Lumina Clock — the time as luminous colour, at the panel's full resolution.

A canvas app (surface: canvas), and the richer descendant of the flap Art Clock.
Where a flap grid can only spell the time in colour-flaps, this renders big
anti-aliased numerals with Pillow — smooth curves, gradient fills, a soft glow —
and pushes the whole frame to the Matrix panel (PUT /api/canvas/frame). Nothing
here is bound to the blocky built-in font.

Four treatments decide how the colour lives in the digits:
  * Glow    — a light-to-colour vertical gradient with a soft halo
  * Aurora  — colour that flows through the numerals, frame by frame
  * Neon    — an even saturated fill with a strong tube-glow
  * Minimal — flat, soft, no glow

…over curated palettes (Amber / Ice / Mint / Duotone / Daylight) that never
touch pink. Daylight rotates the hue through the day along a hand-tuned ramp.
The bundled font and the panel-sized helpers come from the injected `canvas`.
"""

# palettes: each tone is (on, light, glow).
_AMBER = ((255, 150, 25), (255, 244, 214), (255, 95, 0))
_CYAN = ((70, 175, 255), (220, 248, 255), (0, 95, 235))
_GREEN = ((40, 225, 120), (220, 255, 232), (0, 150, 80))
_PAL = {
    'amber':   {'A': _AMBER, 'B': _AMBER, 'colon': (255, 190, 90)},
    'ice':     {'A': _CYAN,  'B': _CYAN,  'colon': (235, 245, 255)},
    'mint':    {'A': _GREEN, 'B': _GREEN, 'colon': (210, 255, 225)},
    'duotone': {'A': _AMBER, 'B': _CYAN,  'colon': (240, 240, 240)},
}
# Daylight hue ramp — anchors chosen so no interpolation passes through pink.
_RAMP = [(0, (70, 120, 235)), (4, (80, 90, 150)), (6, (255, 165, 75)),
         (9, (255, 235, 205)), (12, (255, 255, 255)), (16, (255, 235, 200)),
         (19, (255, 140, 55)), (21, (225, 110, 60)), (23, (70, 75, 120)),
         (24, (70, 120, 235))]
# Glow strength per treatment. Kept restrained: a soft, wide halo that looks smooth on a
# screen reads as a scattering of dim, muddy pixels on an actual LED panel — so the blur is
# tight (see below) and the amount low, giving a clean edge-glow rather than a fuzzy halo.
# "Minimal" is zero glow for anyone who wants none.
_GLOW = {'glow': 0.33, 'neon': 0.6, 'aurora': 0.28, 'minimal': 0.0}


def _lerp(a, b, t):
    return tuple(int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3))


def _ramp(hf):
    for i in range(len(_RAMP) - 1):
        h0, c0 = _RAMP[i]
        h1, c1 = _RAMP[i + 1]
        if h0 <= hf <= h1:
            return _lerp(c0, c1, (hf - h0) / ((h1 - h0) or 1))
    return (255, 255, 255)


def _resolve(palette, hourf):
    """(A_tone, B_tone, colon_rgb) for the chosen palette."""
    if palette == 'daylight':
        c = _ramp(hourf)
        t = (c, _lerp(c, (255, 255, 255), 0.55), tuple(int(v * 0.6) for v in c))
        return t, t, (255, 255, 255)
    p = _PAL.get(palette, _PAL['amber'])
    return p['A'], p['B'], p['colon']


def _fit_font(canvas, W, H):
    """Largest bundled font whose 'HH:MM' fits the panel, plus cap metrics."""
    cap = max(6, int(round(H * 0.80)))
    size = max(6, int(round(cap / 0.75)))
    font = canvas.font(size)
    for _ in range(60):
        if font.getlength('88:88') <= W - 2 or size <= 6:
            break
        size -= 1
        font = canvas.font(size)
    l, t, r, b = font.getbbox('8')          # ink box (anchor 'la': y=0 at ascender)
    return font, t, b - t                    # font, ink_top, cap_height


def _fill(Image, treatment, tone, W, H, y0, y1, frame):
    """A panel-sized RGB image to show through a glyph mask, per treatment."""
    on, light, _ = tone
    if treatment == 'minimal':
        return Image.new('RGB', (W, H), tuple(int(v * 0.92) for v in on))
    if treatment == 'neon':
        return Image.new('RGB', (W, H), on)
    if treatment == 'aurora':
        import math
        row = Image.new('RGB', (W, 1))
        px = row.load()
        phase = (frame * 0.06) % 1.0
        for x in range(W):
            k = 0.5 + 0.5 * math.sin((x / max(1, W - 1) * 2.2 + phase * 2) * math.pi)
            px[x, 0] = _lerp(on, light, k)
        return row.resize((W, H))
    # glow: vertical light -> on across the cap band
    col = Image.new('RGB', (1, H))
    px = col.load()
    for y in range(H):
        t = min(1.0, max(0.0, (y - y0) / max(1, (y1 - y0))))
        px[0, y] = _lerp(light, on, t)
    return col.resize((W, H))


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None
    from datetime import datetime
    from PIL import Image, ImageDraw, ImageFilter

    st = getattr(fetch, '_state', None)
    if st is None:
        st = {'frame': 0}
        setattr(fetch, '_state', st)
    st['frame'] += 1
    frame = st['frame']

    tzname = str(settings.get('timezone') or '').strip()
    try:
        if tzname:
            import pytz
            now = datetime.now(pytz.timezone(tzname))
        else:
            now = datetime.now()
    except Exception:
        now = datetime.now()

    fmt = str(settings.get('clock_format', '24h') or '24h').lower()
    treatment = str(settings.get('treatment', 'glow') or 'glow').lower()
    palette = str(settings.get('palette', 'amber') or 'amber').lower()
    show_seconds = str(settings.get('show_seconds', 'yes') or 'yes') != 'no'

    hour = (now.hour % 12 or 12) if fmt == '12h' else now.hour
    hh, mm = f'{hour:02d}', f'{now.minute:02d}'

    W, H = canvas.width, canvas.height
    A, B, colon_c = _resolve(palette, now.hour + now.minute / 60.0)
    font, ink_top, cap = _fit_font(canvas, W, H)

    # Horizontal layout via the font's own advances, so HH:MM reads naturally.
    total = font.getlength(f'{hh}:{mm}')
    x0 = (W - total) / 2.0
    x_colon = x0 + font.getlength(hh)
    x_min = x0 + font.getlength(hh + ':')
    y = int(round((H - cap) / 2.0 - ink_top))      # top of the ascender box
    cap_y0, cap_y1 = (H - cap) / 2.0, (H - cap) / 2.0 + cap

    def mask(text, x):
        m = Image.new('L', (W, H), 0)
        md = ImageDraw.Draw(m)
        md.fontmode = "1"                       # crisp 1-bit glyph mask — no AA edges
        md.text((x, y), text, fill=255, font=font, anchor='la')
        return m

    m_hh, m_mm = mask(hh, x0), mask(mm, x_min)
    m_c = mask(':', x_colon) if now.second % 2 == 0 else Image.new('L', (W, H), 0)

    base = canvas.blank((0, 0, 0))
    amt = _GLOW.get(treatment, 0.0)
    if amt > 0:
        # A tight blur: on a low-res LED panel a wide gaussian just smears dim pixels far
        # from the stroke ("weird halo"); a small radius keeps the glow hugging the digits.
        blur = ImageFilter.GaussianBlur(max(1.0, H * 0.05))
        for m, tone in ((m_hh, A), (m_mm, B)):
            gl = m.filter(blur).point(lambda v: int(v * amt))
            base = Image.composite(Image.new('RGB', (W, H), tone[2]), base, gl)

    base = Image.composite(_fill(Image, treatment, A, W, H, cap_y0, cap_y1, frame), base, m_hh)
    base = Image.composite(_fill(Image, treatment, B, W, H, cap_y0, cap_y1, frame), base, m_mm)
    base = Image.composite(Image.new('RGB', (W, H), colon_c), base, m_c)

    if show_seconds:
        frac = (now.second + now.microsecond / 1_000_000.0) / 60.0
        bw = int(round(W * frac))
        acc = B[0]
        d = ImageDraw.Draw(base)
        d.rectangle([0, H - 2, W - 1, H - 1], fill=tuple(int(v * 0.16) for v in acc))
        if bw > 0:
            d.rectangle([0, H - 2, bw - 1, H - 1], fill=acc)
        if bw < W:
            d.rectangle([bw, H - 2, bw, H - 1], fill=(255, 255, 255))

    canvas.frame(base)
    return 0.2                                      # ~5 fps: smooth colon + sweep
