"""paneltext.py — the PIL text/render toolkit every panel app shares.

A canvas app that wants smooth type / gradients renders a whole PIL image and
pushes it with frame(). These methods cover the common needs so each app doesn't
reinvent them: the bundled font, fitters and wrappers (all floored at 8 px —
smaller sizes render wrong-reading glyphs on the panel), the message/text-card
layouts, color mixing, and blank/vgrad backdrops. Mixed into CanvasSurface;
Pillow is imported lazily so the module still loads where it isn't installed.
"""

from __future__ import annotations

import logging
import os

from .canvas_codec import _rgb

log = logging.getLogger("companion.canvas")

_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_FONT_CACHE: dict = {}


class PanelText:
    # A canvas app that wants smooth type / gradients renders a whole PIL image
    # and pushes it with frame(). It covers the common needs so each app doesn't reinvent them. Pillow is imported lazily so the
    # module still loads where it isn't installed.
    def font(self, size, name: str = "DejaVuSans-Bold.ttf"):
        """A cached PIL ImageFont at ``size`` px from the bundled face."""
        from PIL import ImageFont
        key = (name, max(5, int(size)))
        f = _FONT_CACHE.get(key)
        if f is None:
            f = ImageFont.truetype(os.path.join(_FONT_DIR, name), key[1])
            _FONT_CACHE[key] = f
        return f

    # -- PIL text toolkit ------------------------------------------------------
    # The panel-app text vocabulary, hoisted from the (previously per-app) _cv_*
    # helpers so a fix lands once. Every fitter floors at 8 px: smaller sizes
    # render wrong-reading glyphs on the panel (a 6px "2" reads as a "7").
    MIN_READABLE = 8

    def fit_font(self, text, max_w, max_h, min_size=MIN_READABLE):
        """The largest bundled font whose ``text`` fits within max_w x max_h.

        Two-phase: a RATIO jump first (measure once, scale toward the target), then
        a -1 polish. The old -1-only loop was written for 64px panels — from an LCD's
        800px start it could shrink by at most 96 and "fit" nothing."""
        size = max(min_size, int(max_h) + 2)
        font = self.font(size)
        for _ in range(96):
            b = font.getbbox(text or "0")
            w, h = font.getlength(text or "0"), b[3] - b[1]
            if size <= min_size or (w <= max_w and h <= max_h):
                return font
            # jump by the worst overshoot ratio while far off; polish by -1 near it
            ratio = max(w / max_w if max_w > 0 else 1.0, h / max_h if max_h > 0 else 1.0)
            nxt = int(size / ratio) if ratio > 1.15 else size - 1
            size = max(min_size, min(size - 1, nxt))
            font = self.font(size)
        return font

    @staticmethod
    def mix(a, b, t):
        """Linear blend of two RGB colors at ``t`` in [0,1]."""
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    @staticmethod
    def dim(c, k):
        """An RGB color scaled by ``k`` (truncating — the panel's crush direction)."""
        return tuple(int(v * k) for v in c)

    @staticmethod
    def ink(font, text):
        """Ink height of ``text`` in ``font`` (bbox height, not line height)."""
        b = font.getbbox(text or "0")
        return b[3] - b[1]

    @staticmethod
    def wrap(font, text, max_w, max_lines=None):
        """Greedy word-wrap to pixel width ``max_w``. A word wider than the panel is
        hard-split with a visible hyphen — nothing ever draws off the edge. With
        ``max_lines``, the surplus is cut and the last line ellipsized."""
        lines, cur = [], ""
        for word in str(text or "").split():
            w = word
            while font.getlength(w) > max_w and len(w) > 1:
                cut = len(w)
                while cut > 1 and font.getlength(w[:cut] + "-") > max_w:
                    cut -= 1
                if cur:
                    lines.append(cur)
                    cur = ""
                piece = w[:cut]
                lines.append(piece if piece.endswith("-") else piece + "-")
                w = w[cut:]
            cand = f"{cur} {w}".strip()
            if not cur or font.getlength(cand) <= max_w:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        lines = lines or [""]
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and font.getlength(last + "…") > max_w:
                last = last[:-1]
            lines[-1] = (last + "…") if last else "…"
        return lines

    def wrap_fit(self, text, max_w, max_h, max_lines=None, min_size=MIN_READABLE):
        """The largest font at which ``text`` word-wraps inside max_w x max_h (and
        ``max_lines``, when given). A size only qualifies if every WORD fits the width
        whole — otherwise wrap's hyphen-splitting would satisfy the width check at a
        large size and the max_lines cut would then drop words. Returns
        ``(font, lines)``; at the floor, hyphen-splitting (and the max_lines ellipsis)
        is the last resort."""
        words = str(text or "").split()
        size = max(min_size, int(max_h))
        while size >= min_size:
            font = self.font(size)
            if words and max(font.getlength(w) for w in words) > max_w:
                # a word would need a hard split — shrink instead. Ratio-jump while far
                # off (an LCD-height start would otherwise walk down 1px at a time).
                over = max(font.getlength(w) for w in words) / max(1.0, max_w)
                size = max(min_size, min(size - 1, int(size / over))) if over > 1.15 else size - 1
                continue
            lines = self.wrap(font, text, max_w, max_lines)
            b = font.getbbox("Ag")
            lh, gap = b[3] - b[1], max(1, (b[3] - b[1]) // 6)
            if (len(lines) * lh + (len(lines) - 1) * gap <= max_h
                    and max(font.getlength(ln) for ln in lines) <= max_w):
                return font, lines
            size -= 1
        font = self.font(min_size)
        return font, self.wrap(font, text, max_w, max_lines)

    @staticmethod
    def text_top(draw, x, y, text, font, fill):
        """Draw with the ink's TOP at ``y`` (bbox-corrected), left edge at ``x``."""
        draw.text((x, y - font.getbbox(text or "0")[1]), text, font=font, fill=fill,
                  anchor="la")

    def message(self, line1, line2="", color=(238, 238, 244), dim=(150, 150, 158)):
        """A quiet two-line message card on black (offline / no data) — never a
        crash, never a blank panel. Returns the PIL image; push it with frame()."""
        from PIL import ImageDraw
        W, H = self.width, self.height
        img = self.blank((0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        f1 = self.fit_font(line1, W - 4, int(H * 0.32))
        h1 = self.ink(f1, line1)
        f2 = self.fit_font(line2, W - 4, int(H * 0.22)) if line2 else None
        h2 = self.ink(f2, line2) if line2 else 0
        gap = 3 if line2 else 0
        y = (H - (h1 + gap + h2)) / 2.0
        self.text_top(draw, (W - f1.getlength(line1)) / 2.0, y, line1, f1, color)
        if line2:
            self.text_top(draw, (W - f2.getlength(line2)) / 2.0, y + h1 + gap,
                          line2, f2, dim)
        return img

    def card_pages(self, text, max_w, max_h, min_size=MIN_READABLE):
        """``(font, pages, line_h, gap)`` — ``text`` word-wrapped at the largest font
        that holds it on one page, or wrapped at the floor and split into pages to
        rotate through across redraws. The card-family paginator."""
        size = max(min_size, int(max_h))
        while size >= min_size:
            font = self.font(size)
            lines = self.wrap(font, text, max_w)
            b = font.getbbox("Ag")
            lh, gap = b[3] - b[1], max(1, (b[3] - b[1]) // 6)
            if (len(lines) * lh + (len(lines) - 1) * gap <= max_h
                    and max(font.getlength(ln) for ln in lines) <= max_w):
                return font, [lines], lh, gap
            size -= 1
        font = self.font(min_size)
        lines = self.wrap(font, text, max_w)
        b = font.getbbox("Ag")
        lh, gap = b[3] - b[1], max(1, (b[3] - b[1]) // 6)
        per = max(1, (max_h + gap) // (lh + gap))
        return font, [lines[i:i + per] for i in range(0, len(lines), per)] or [[""]], lh, gap

    def _card_header(self, draw, label, accent, motif):
        """Motif + label over a thin accent rule; returns the y where the body starts.
        The motif drops away on small panels, the label never does — an overflowing
        label loses whole words rather than clipping at the edge."""
        W, H = self.width, self.height
        hh = max(7, int(H * 0.19))
        x = 3
        if motif is not None and W >= 96 and H >= 48:
            x += motif(draw, 3, 0, hh + 2) + 4
        f = self.fit_font(label, W - x - 3, hh)
        if f.getlength(label) > W - x - 3:
            f = self.fit_font(label, 10 ** 6, hh)
            words = str(label).split()
            while len(words) > 1 and f.getlength(" ".join(words)) > W - x - 3:
                words.pop()
            label = " ".join(words)
            if f.getlength(label) > W - x - 3:
                f = self.fit_font(label, W - x - 3, hh)
        b = f.getbbox(label)
        draw.text((x, 1 - b[1]), label, font=f, fill=accent)
        ry = 1 + max(hh, b[3] - b[1]) + 2
        draw.line([(3, ry), (W - 4, ry)], fill=tuple(c // 3 for c in accent))
        return ry + 2

    def text_card(self, label, body, page=0, *, accent=(255, 165, 70), motif=None,
                  sub=None, color=(238, 238, 244), dim=(150, 150, 158),
                  dot_off=(70, 70, 76)):
        """One frame of a label+body card — the shared renderer behind the fact/quote
        apps. Header (accent label + rule, ``motif(draw, x, y, s)->width`` drawn where
        the panel is big enough), page ``page`` of the body at the largest fitting
        font (leading-stretched to fill the region, floor-anchored), an optional
        ``sub`` attribution owning the floor, and page dots where there is room. On a
        panel 32 px or shorter the label row is dropped — it would pin the body at
        the 8 px floor. Returns ``(img, page_count)``."""
        from PIL import ImageDraw
        W, H = self.width, self.height
        img = self.blank((0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        top = self._card_header(draw, label, accent, motif) if H > 32 else 1
        if sub and H < 44:
            body = f"{body}  {sub}"       # tiny panel: the author flows with the text
            sub = None
        sf = sb = None
        limit = H                         # the first row past the body's region
        if sub:
            sf = self.fit_font(sub, int(W * 0.72), max(7, int(H * 0.15)))
            sb = sf.getbbox(sub)
            limit = H - (sb[3] - sb[1]) - 2          # the author line owns the floor
        font, pages, lh, gap = self.card_pages(body, W - 6, limit - top)
        dots = 1 < len(pages) <= 8 and H >= 44
        if dots and not sub:              # dots take the bottom rows when no author does
            font, pages, lh, gap = self.card_pages(body, W - 6, limit - top - 3)
            dots = 1 < len(pages) <= 8
        n = len(pages)
        lines = pages[page % n]
        base = font.getbbox("Ag")[1]
        bottom = limit - 4 if (dots and not sub) else limit - 1   # last body ink row
        fb = font.getbbox(lines[0] or "0")
        lb = font.getbbox(lines[-1] or "0")
        step = lh + gap
        if len(lines) > 1:
            # The font is already at its cap, so fill by leading: stretch the line
            # gaps (up to one line-height) until the block spans the whole region.
            span = (len(lines) - 1) * step + (lb[3] - base) - (fb[1] - base)
            step += max(0, min(lh, (bottom + 1 - top - span) // (len(lines) - 1)))
        # Anchor the block to the floor; any leftover rides under the header rule.
        y = bottom + 1 - (lb[3] - base) - step * (len(lines) - 1)
        for ln in lines:
            draw.text(((W - font.getlength(ln)) / 2.0, y - base), ln, font=font, fill=color)
            y += step
        if sub:
            draw.text((W - 3 - sf.getlength(sub), H - (sb[3] - sb[1]) - sb[1]),
                      sub, font=sf, fill=accent)
        if dots:
            for i in range(n):
                c = accent if i == (page % n) else dot_off
                draw.rectangle([3 + i * 5, H - 2, 4 + i * 5, H - 1], fill=c)
        return img, n

    def blank(self, color=(0, 0, 0)):
        """A fresh RGB image the exact size of the panel."""
        from PIL import Image
        return Image.new("RGB", (self.width, self.height), tuple(_rgb(color)))

    def vgrad(self, top, bottom):
        """A panel-sized image with a vertical gradient from ``top`` to ``bottom``
        (each a color name / (r,g,b) / #hex). Built one column then stretched, so
        it's cheap enough to redraw every frame."""
        from PIL import Image
        t, b = _rgb(top), _rgb(bottom)
        col = Image.new("RGB", (1, self.height))
        px = col.load()
        h = max(1, self.height - 1)
        for y in range(self.height):
            r = y / h
            px[0, y] = (int(t[0] + (b[0] - t[0]) * r),
                        int(t[1] + (b[1] - t[1]) * r),
                        int(t[2] + (b[2] - t[2]) * r))
        return col.resize((self.width, self.height))
