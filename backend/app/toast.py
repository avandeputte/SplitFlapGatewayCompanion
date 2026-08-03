"""toast.py — drawn notification cards for the Matrix panel.

A toast is what a trigger or a timed message looks like on a wall that can draw:
an accent bar, an icon, and the text fitted large — slid up from the bottom edge
and held, instead of the flap-cell text a reel wall gets. Rendering happens on a
ZoneCanvas (the shared offscreen surface), so the text runs through the exact
production fit/wrap toolkit; the engine pushes the returned frames as panel
frames and the running app repaints when the interrupt gate reopens.
"""

from __future__ import annotations

from .zonecanvas import ZoneCanvas

_BG = (14, 16, 24)
_INK = (238, 242, 250)

# icon -> (draw function name, default accent). Accents stay LED-legible (>=40/ch).
ACCENTS = {
    "bell": (168, 120, 255),
    "info": (80, 168, 240),
    "alert": (255, 176, 48),
    "check": (64, 208, 112),
    "cross": (255, 82, 82),
    "heart": (255, 96, 160),
}
_SLIDE_STEPS = 4                        # pre-frames easing the card up from the bottom


def _draw_icon(z: ZoneCanvas, kind: str, cx: int, cy: int, r: int, color) -> None:
    """The icon glyphs, PIL primitives only — sized to radius ``r`` around (cx, cy).
    Every internal stroke/feature scales with r, so the same glyph reads on a 32px
    LED card (r~10) and a 800px LCD card (r~160)."""
    t = max(2, r // 5)                  # the stroke weight
    if kind == "bell":
        rim = max(2, r // 6)
        z.poly([(cx - r, cy + r // 2), (cx - r // 2, cy - r // 2), (cx - r // 4, cy - r),
                (cx + r // 4, cy - r), (cx + r // 2, cy - r // 2), (cx + r, cy + r // 2)],
               color, fill=True)
        z.rect(cx - r, cy + r // 2, 2 * r + 1, rim, color, fill=True)
        z.circle(cx, cy + r // 2 + rim + max(2, r // 5), max(1, r // 4), color, fill=True)
    elif kind == "alert":
        stem = max(1, r // 8)
        z.triangle(cx, cy - r, cx - r, cy + r, cx + r, cy + r, color, fill=True)
        z.rect(cx - stem // 2, cy - r // 3, max(1, stem), r, _BG, fill=True)
        z.circle(cx, cy + r - max(2, r // 5), max(1, r // 10), _BG, fill=True)
    elif kind == "check":
        z.line(cx - r, cy, cx - r // 3, cy + r - 1, color, t=t)
        z.line(cx - r // 3, cy + r - 1, cx + r, cy - r + 1, color, t=t)
    elif kind == "cross":
        z.line(cx - r, cy - r, cx + r, cy + r, color, t=t)
        z.line(cx - r, cy + r, cx + r, cy - r, color, t=t)
    elif kind == "heart":
        z.circle(cx - r // 2, cy - r // 3, r // 2, color, fill=True)
        z.circle(cx + r // 2, cy - r // 3, r // 2, color, fill=True)
        z.triangle(cx - r, cy - r // 8, cx + r, cy - r // 8, cx, cy + r, color, fill=True)
    else:                               # info: the dot-and-stem ℹ in a ring
        w = max(2, r // 5)
        z.circle(cx, cy, r, color, t=max(2, r // 7))
        z.rect(cx - w // 2, cy - r // 2, w, w, color, fill=True)
        z.rect(cx - w // 2, cy - r // 2 + w + max(1, r // 10), w, r - w, color, fill=True)


def render(W: int, H: int, text: str, icon: str = "bell", accent=None) -> list:
    """The toast's frames, slide-in included: a few pre-frames easing the card up
    from the bottom edge, then the final card to hold. PIL images, panel-sized."""
    icon = icon if icon in ACCENTS else "bell"
    color = tuple(accent) if accent else ACCENTS[icon]

    z = ZoneCanvas(W, H)
    z.clear(_BG)
    bar = max(3, W // 90)                               # sizes scale with the panel: a
    pad = max(4, H // 10)                               # 64px LED card and a 800px LCD
    icon_r = max(6, H // 5)                             # card carry the same proportions
    z.rect(0, 0, bar, H, color, fill=True)              # the accent bar
    icx = bar + pad + icon_r
    _draw_icon(z, icon, icx, H // 2, icon_r, color)
    tx = icx + icon_r + pad
    tw = W - tx - pad
    # The text, as large as fits — up to three lines on a tall panel, two on a short one.
    lines = z.wrap_fit(str(text or ""), tw, H - 8, max_lines=3 if H >= 48 else 2) \
        if callable(getattr(z, "wrap_fit", None)) else None
    if lines:
        font, rows = lines
        lh = font.size + 2
        y = (H - lh * len(rows)) // 2
        d = z._draw()
        for row in rows:
            d.text((tx, y), row, font=font, fill=_INK)
            y += lh
    else:                                               # toolkit fallback: single fitted line
        f = z.fit_font(str(text or ""), tw, H - 8)
        d = z._draw()
        d.text((tx, (H - f.size) // 2), str(text or ""), font=f, fill=_INK)
    z.show()
    card = z.take()

    frames = []
    from PIL import Image
    for k in range(1, _SLIDE_STEPS + 1):
        off = int(H * (1 - k / _SLIDE_STEPS) ** 2)      # ease-out from the bottom
        if off <= 0:
            continue
        fr = Image.new("RGB", (W, H), (0, 0, 0))
        fr.paste(card, (0, off))
        frames.append(fr)
    frames.append(card)
    return frames
