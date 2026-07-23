"""Channel-on-canvas: draw a channel app's text on a Matrix panel with a piece of themed art.

A channel app (jokes, quotes, fortunes …) is normally text on the flaps. On a wall with a
framebuffer it can opt in (the per-app "Show on Matrix panel" toggle) to render here instead: the
line of text laid out big on a black panel, with a small bespoke icon beside it that says what the
channel is — a clapperboard for movie quotes, a moon for good-night, a fortune cookie, and so on.

Each channel names its motif in its manifest (``canvas_art``); most icons are drawn with Pillow
primitives (keyed in ``MOTIFS``), a few are a bundled image (the fortune cookie is the 🥠 emoji,
keyed in ``_ASSETS``). ``render`` composes one frame and pushes it through the injected
``CanvasSurface`` — the same transport the canvas apps use.
"""

import math
import os

_INK = (238, 242, 250)     # default warm-white text

# A few motifs are an actual image (the fortune cookie is the 🥠 emoji, which no shape captures);
# these paste a bundled PNG instead of drawing. Keyed the same as MOTIFS, so a manifest just names
# the motif. The panel's rgb565 + downscale flattens it, but it's unmistakably the cookie.
_ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets")
_ASSETS = {"cookie": "fortune-cookie.png"}
_asset_cache: dict = {}


def _asset(motif):
    """The RGBA image for an image-backed motif (cached), or None to fall back to a drawn shape."""
    name = _ASSETS.get(motif)
    if not name:
        return None
    if motif not in _asset_cache:
        try:
            from PIL import Image
            _asset_cache[motif] = Image.open(os.path.join(_ASSET_DIR, name)).convert("RGBA")
        except Exception:
            _asset_cache[motif] = None
    return _asset_cache[motif]


def _shadow(d, x, y, s, f, col, sh=(0, 0, 0)):
    """Text with a 1px dark outline so it reads over any art bleed."""
    for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        d.text((x + dx, y + dy), s, font=f, fill=sh, anchor="la")
    d.text((x, y), s, font=f, fill=col, anchor="la")


def _wrap(font, text, box_w):
    """Word-wrap to box_w, honouring explicit newlines as hard breaks (so a quote's ``- Author``
    keeps its own line) and collapsing any other whitespace."""
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for w in para.split():
            t = (cur + " " + w).strip()
            if not cur or font.getlength(t) <= box_w:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines or [""]


def _fit_block(font_fn, text, box_w, box_h, max_size, min_size=6):
    """Largest bundled font at which ``text`` word-wraps inside box_w × box_h. Returns
    (font, lines, line_height, gap)."""
    for size in range(int(max_size), min_size - 1, -1):
        f = font_fn(size)
        lines = _wrap(f, text, box_w)
        asc, desc = f.getmetrics()
        lh = asc + desc
        gap = max(1, size // 12)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((f.getlength(ln) for ln in lines), default=0)
        if total <= box_h and widest <= box_w:
            return f, lines, lh, gap
    f = font_fn(min_size)
    asc, desc = f.getmetrics()
    return f, _wrap(f, text, box_w), asc + desc, 1


# --- motifs: draw(d, x, y, s) into an s×s box at (x, y) ----------------------------------------

def _sun(d, x, y, s):
    cx, cy, r, w = x + s / 2, y + s / 2, s * 0.24, max(1, int(s * 0.07))
    for a in range(8):
        ang = a * math.pi / 4
        d.line([cx + math.cos(ang) * r * 1.5, cy + math.sin(ang) * r * 1.5,
                cx + math.cos(ang) * r * 2.3, cy + math.sin(ang) * r * 2.3], fill=(255, 206, 62), width=w)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 198, 40))


def _moon(d, x, y, s):
    cx, cy, r = x + s * 0.54, y + s * 0.52, s * 0.36
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(228, 234, 252))
    d.ellipse([cx - r * 0.35, cy - r * 1.05, cx + r * 1.5, cy + r * 0.95], fill=(0, 0, 0))   # crescent bite
    for sx, sy, ss in ((0.14, 0.20, 0.045), (0.30, 0.74, 0.035), (0.82, 0.30, 0.05)):
        d.ellipse([x + s * sx - s * ss, y + s * sy - s * ss, x + s * sx + s * ss, y + s * sy + s * ss],
                  fill=(196, 208, 240))


def _cookie(d, x, y, s):
    # A folded fortune cookie: one pleated shell (a rounded belly pinched up to a folded top) with
    # the fortune slip poking out. A single mound, not two lobes — reads as the cookie, nothing else.
    tan, tan_d, paper = (234, 186, 106), (196, 144, 74), (250, 250, 242)
    cx = x + s / 2
    d.pieslice([x + s * 0.14, y + s * 0.30, x + s * 0.86, y + s * 1.02], 180, 360, fill=tan)   # belly
    d.polygon([(x + s * 0.14, y + s * 0.52), (cx, y + s * 0.22), (x + s * 0.86, y + s * 0.52)], fill=tan)  # fold peak
    d.arc([x + s * 0.14, y + s * 0.30, x + s * 0.86, y + s * 1.02], 180, 360, fill=tan_d, width=max(1, int(s * 0.045)))
    for fx in (0.34, 0.5, 0.66):                                                               # pleats
        d.line([x + s * fx, y + s * 0.30, x + s * fx, y + s * 0.62], fill=tan_d, width=max(1, int(s * 0.035)))
    d.polygon([(cx - s * 0.05, y + s * 0.28), (cx + s * 0.05, y + s * 0.28),
               (cx + s * 0.10, y + s * 0.04), (cx - s * 0.10, y + s * 0.04)], fill=paper)      # fortune slip


def _eightball(d, x, y, s):
    cx, cy, r = x + s / 2, y + s / 2, s * 0.44
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(18, 18, 22), outline=(90, 92, 102))
    wr = r * 0.44
    d.ellipse([cx - wr, cy - wr, cx + wr, cy + wr], fill=(240, 242, 248))     # white spot
    for oy, rr in ((-wr * 0.28, wr * 0.34), (wr * 0.30, wr * 0.40)):          # a tiny 8, two ellipses
        d.ellipse([cx - rr, cy + oy - rr, cx + rr, cy + oy + rr], outline=(20, 20, 24), width=max(1, int(s * 0.03)))


def _quote(d, x, y, s):
    col = (255, 206, 90)
    for ox in (0.14, 0.52):                      # two comma-shaped marks
        bx = x + s * ox
        d.ellipse([bx, y + s * 0.22, bx + s * 0.30, y + s * 0.52], fill=col)
        d.polygon([(bx + s * 0.02, y + s * 0.44), (bx + s * 0.20, y + s * 0.44),
                   (bx + s * 0.02, y + s * 0.74)], fill=col)


def _column(d, x, y, s):
    # A classical pillar: capital, fluted shaft, base.
    st = (222, 226, 236)
    d.rectangle([x + s * 0.14, y + s * 0.10, x + s * 0.86, y + s * 0.20], fill=st)          # capital
    d.rectangle([x + s * 0.12, y + s * 0.80, x + s * 0.88, y + s * 0.92], fill=st)          # base
    for i in range(3):
        fx = x + s * (0.26 + i * 0.20)
        d.rectangle([fx, y + s * 0.22, fx + s * 0.10, y + s * 0.78], fill=st)               # fluting


def _mug(d, x, y, s):
    body = (236, 240, 248)
    d.rounded_rectangle([x + s * 0.20, y + s * 0.28, x + s * 0.68, y + s * 0.82], radius=int(s * 0.06), fill=body)
    d.arc([x + s * 0.60, y + s * 0.34, x + s * 0.86, y + s * 0.66], -80, 80, fill=body, width=max(1, int(s * 0.07)))
    for i in range(3):                            # steam
        sx = x + s * (0.30 + i * 0.13)
        d.line([sx, y + s * 0.10, sx + s * 0.05, y + s * 0.22], fill=(150, 160, 180), width=max(1, int(s * 0.04)))


def _bolt(d, x, y, s):
    d.polygon([(x + s * 0.56, y + s * 0.06), (x + s * 0.24, y + s * 0.56), (x + s * 0.46, y + s * 0.56),
               (x + s * 0.36, y + s * 0.94), (x + s * 0.74, y + s * 0.40), (x + s * 0.50, y + s * 0.40)],
              fill=(255, 214, 64))


def _saber(d, x, y, s):
    # A lit lightsaber on the diagonal: metal hilt, glowing blade.
    x0, y0, x1, y1 = x + s * 0.20, y + s * 0.84, x + s * 0.82, y + s * 0.20
    hx, hy = x0 + (x1 - x0) * 0.28, y0 + (y1 - y0) * 0.28
    d.line([x0, y0, hx, hy], fill=(150, 156, 168), width=max(2, int(s * 0.12)))              # hilt
    d.line([hx, hy, x1, y1], fill=(120, 235, 255), width=max(2, int(s * 0.14)))              # blade glow
    d.line([hx, hy, x1, y1], fill=(240, 252, 255), width=max(1, int(s * 0.05)))              # blade core


def _shower(d, x, y, s):
    head = (206, 214, 230)
    d.rectangle([x + s * 0.30, y + s * 0.10, x + s * 0.40, y + s * 0.30], fill=head)         # pipe
    d.ellipse([x + s * 0.16, y + s * 0.26, x + s * 0.70, y + s * 0.44], fill=head)           # head
    for i, dx in enumerate((0.24, 0.36, 0.48, 0.60)):
        yy = y + s * (0.54 + (i % 2) * 0.12)
        d.line([x + s * dx, y + s * 0.48, x + s * dx, yy], fill=(90, 170, 255), width=max(1, int(s * 0.05)))


def _clap(d, x, y, s):
    # A clapperboard: slate body with a hinged, striped clapper on top.
    body = (32, 34, 40)
    d.rectangle([x + s * 0.12, y + s * 0.36, x + s * 0.88, y + s * 0.86], fill=body, outline=(120, 124, 134))
    d.polygon([(x + s * 0.12, y + s * 0.20), (x + s * 0.84, y + s * 0.10),
               (x + s * 0.88, y + s * 0.32), (x + s * 0.16, y + s * 0.42)], fill=(24, 26, 32), outline=(150, 154, 164))
    for i in range(4):                            # diagonal white stripes on the clapper
        bx = x + s * (0.18 + i * 0.18)
        d.line([bx, y + s * 0.40, bx + s * 0.10, y + s * 0.14], fill=(236, 238, 244), width=max(1, int(s * 0.05)))


def _grin(d, x, y, s):
    cx, cy, r = x + s / 2, y + s / 2, s * 0.42
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 206, 62))
    for ex in (-0.42, 0.42):                      # eyes
        d.ellipse([cx + r * ex - s * 0.05, cy - r * 0.36 - s * 0.05,
                   cx + r * ex + s * 0.05, cy - r * 0.36 + s * 0.05], fill=(40, 34, 18))
    d.chord([cx - r * 0.62, cy - r * 0.30, cx + r * 0.62, cy + r * 0.62], 20, 160, fill=(40, 34, 18))  # grin


def _bubble(d, x, y, s):
    col = (120, 210, 235)
    d.rounded_rectangle([x + s * 0.10, y + s * 0.16, x + s * 0.90, y + s * 0.64],
                        radius=int(s * 0.14), fill=col)
    d.polygon([(x + s * 0.28, y + s * 0.62), (x + s * 0.30, y + s * 0.86), (x + s * 0.48, y + s * 0.62)], fill=col)
    for i in range(3):                            # ellipsis dots
        dx = x + s * (0.32 + i * 0.16)
        d.ellipse([dx - s * 0.05, y + s * 0.34, dx + s * 0.05, y + s * 0.44], fill=(20, 30, 40))


MOTIFS = {
    "sun": _sun, "moon": _moon, "cookie": _cookie, "eightball": _eightball,
    "quote": _quote, "column": _column, "mug": _mug, "bolt": _bolt,
    "saber": _saber, "shower": _shower, "clapperboard": _clap, "grin": _grin,
    "bubble": _bubble,
}


def render(surface, text, motif="quote", accent=_INK):
    """Compose one frame — themed art on the left, the text laid out big beside it — and push it
    through ``surface`` (a canvas.CanvasSurface). Falls back to text-only if the motif is unknown
    or the panel is too small for art."""
    from PIL import Image, ImageDraw
    W, H = int(surface.width), int(surface.height)
    img = surface.blank((0, 0, 0))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"

    art, asset = MOTIFS.get(motif), _asset(motif)
    if (art or asset) and H >= 40 and W >= 112:
        s = min(H - 8, int(W * 0.30))
        ax, ay = 4, (H - s) // 2
        if asset is not None:
            e = asset.resize((s, s), Image.LANCZOS)
            img.paste(e, (ax, ay), e)             # emoji image (alpha-composited on black)
        else:
            art(d, ax, ay, s)
        tx0 = ax + s + 7
    else:
        tx0 = 3                                   # small panel: text only, art won't read
    box_w = max(8, W - tx0 - 3)
    box_h = H - 6
    max_size = min(28, int(box_h * 0.9))
    f, lines, lh, gap = _fit_block(surface.font, str(text), box_w, box_h, max_size)
    total = len(lines) * lh + (len(lines) - 1) * gap
    y = max(2, (H - total) // 2)
    for ln in lines:
        lx = tx0 + max(0, (box_w - int(f.getlength(ln))) // 2)
        _shadow(d, lx, y, ln, f, accent)
        y += lh + gap
    surface.frame(img)
