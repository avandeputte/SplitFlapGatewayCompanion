"""zonecanvas.py — an offscreen, Pillow-rendering canvas surface.

Extracted from the screenshot harness's Cap so production code can render an app
without a wall: the Zones feature runs several apps side by side, each into one of
these, and composites the results into a single panel frame. The harness now
subclasses this instead of carrying its own copy — one ops->PIL shim, everywhere.
Approximations are the harness's long-standing ones: 1-bit DejaVu text at the face
size, no compositing/alpha, sprite transforms drawn plain.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

from .canvas import _FONT_DIR

_FONT_CACHE: dict = {}

_FACES = (8, 9, 10, 13, 18, 20)          # == canvas._FACES (bundled on-device faces)
_FACE_W = {8: 5, 9: 6, 10: 6, 13: 8, 18: 9, 20: 10}


def _rgb(c):
    if isinstance(c, (tuple, list)):
        return tuple(int(x) for x in c[:3])
    s = str(c).strip()
    if s.startswith('#'):
        s = s[1:]
        if len(s) == 3:
            s = ''.join(ch * 2 for ch in s)
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    from PIL import ImageColor
    return ImageColor.getrgb(s)


class ZoneCanvas:
    """A CanvasSurface stand-in that renders to PILLOW instead of a wall: the same
    drawing API (ops -> PIL shim, Pillow helpers, the borrowed text toolkit), frames
    captured in ``.frames``. Consumers: the ZONES engine (each zone's app draws here;
    the composite goes out as one panel frame), and the screenshot harness (its Cap
    subclasses this, adding sample-data stubs)."""

    def __init__(self, w, h):
        self.width, self.height = int(w), int(h)
        self.frames = []
        # ops-app surface state
        self.can_sprite = True
        # Scalable on-device text (gtext) + box blur — simulated here with PIL DejaVu (the same
        # face the wall rasterizes) so a converted text app's LCD layout previews offscreen.
        self.can_gtext = True
        self.text_max = 512
        self.text_faces = ("sans", "mono", "custom")
        self.can_blur = True
        self._img = None
        self._atlas = []

    def font(self, size, name='DejaVuSans-Bold.ttf'):
        key = (name, max(5, int(size)))
        f = _FONT_CACHE.get(key)
        if f is None:
            f = ImageFont.truetype(os.path.join(_FONT_DIR, name), key[1])
            _FONT_CACHE[key] = f
        return f

    def blank(self, color=(0, 0, 0)):
        return Image.new('RGB', (self.width, self.height), _rgb(color))

    def vgrad(self, top, bottom):
        t, b = _rgb(top), _rgb(bottom)
        col = Image.new('RGB', (1, self.height))
        px = col.load()
        h = max(1, self.height - 1)
        for y in range(self.height):
            r = y / h
            px[0, y] = (int(t[0] + (b[0] - t[0]) * r),
                        int(t[1] + (b[1] - t[1]) * r),
                        int(t[2] + (b[2] - t[2]) * r))
        return col.resize((self.width, self.height))

    def frame(self, image):
        if isinstance(image, (bytes, bytearray)):
            self.frames.append(Image.frombytes('RGB', (self.width, self.height), bytes(image)))
            return True
        self.frames.append(image.convert('RGB').resize((self.width, self.height)))
        return True

    _OPS = ('clear', 'pixel', 'hline', 'vline', 'line', 'rect', 'circle', 'ellipse',
              'triangle', 'roundrect', 'gradient', 'polyline', 'poly', 'arc', 'clip',
              'origin', 'text', 'textbox', 'image', 'sprite', 'scroll', 'show')


    can_sd = False                       # a zone has no card of its own
    aa_ok = False

    def has_op(self, name):
        return name in self._OPS

    @staticmethod
    def num(settings, key, default, lo=None, hi=None):
        # Mirrors CanvasSurface.num — the raw-string-tolerant clamped settings read.
        try:
            v = float(settings.get(key, default) or default)
        except (TypeError, ValueError, AttributeError):
            v = float(default)
        if lo is not None:
            v = max(float(lo), v)
        if hi is not None:
            v = min(float(hi), v)
        return int(v) if isinstance(default, int) else v

    def gradient(self, x, y, w, h, frm, to, direction='v'):
        d = self._draw()
        f, t = _rgb(frm), _rgb(to)
        n = max(1, (h if direction == 'v' else w) - 1)
        for i in range(h if direction == 'v' else w):
            r = i / n
            col = tuple(int(f[k] + (t[k] - f[k]) * r) for k in range(3))
            if direction == 'v':
                d.line([(int(x), int(y + i)), (int(x + w - 1), int(y + i))], fill=col)
            else:
                d.line([(int(x + i), int(y)), (int(x + i), int(y + h - 1))], fill=col)
        return self

    def pixel(self, x, y, color=(255, 255, 255)):
        self._draw().point((int(x), int(y)), fill=_rgb(color))
        return self

    def rect(self, x, y, w, h, color=(255, 255, 255), fill=False, t=1):
        d = self._draw()
        box = [int(x), int(y), int(x + w - 1), int(y + h - 1)]
        if fill:
            d.rectangle(box, fill=_rgb(color))
        else:
            d.rectangle(box, outline=_rgb(color), width=max(1, int(t)))
        return self

    def circle(self, x, y, r, color=(255, 255, 255), fill=False, t=1, aa=False):
        d = self._draw()
        box = [int(x - r), int(y - r), int(x + r), int(y + r)]
        if fill:
            d.ellipse(box, fill=_rgb(color))
        else:
            d.ellipse(box, outline=_rgb(color), width=max(1, int(t)))
        return self

    def triangle(self, x, y, x1, y1, x2, y2, color=(255, 255, 255), fill=False):
        d = self._draw()
        pts = [(int(x), int(y)), (int(x1), int(y1)), (int(x2), int(y2))]
        if fill:
            d.polygon(pts, fill=_rgb(color))
        else:
            d.polygon(pts, outline=_rgb(color))
        return self

    def polyline(self, points, color=(255, 255, 255), t=1, aa=False):
        d = self._draw()
        pts = [(int(px), int(py)) for px, py in points]
        if len(pts) >= 2:
            d.line(pts, fill=_rgb(color), width=max(1, int(t)), joint="curve")
        return self

    def ellipse(self, x, y, rx, ry, color=(255, 255, 255), fill=False, t=1):
        d = self._draw()
        box = [int(x - rx), int(y - ry), int(x + rx), int(y + ry)]
        if fill:
            d.ellipse(box, fill=_rgb(color))
        else:
            d.ellipse(box, outline=_rgb(color), width=max(1, int(t)))
        return self

    def line(self, x, y, x1, y1, color=(255, 255, 255), t=1):
        self._draw().line([(int(x), int(y)), (int(x1), int(y1))],
                          fill=_rgb(color), width=max(1, int(t)))
        return self

    def arc(self, x, y, r, start, end, color=(255, 255, 255), t=2, fill=False):
        d = self._draw()
        box = [int(x - r), int(y - r), int(x + r), int(y + r)]
        # firmware: 0 deg at 12 o'clock, clockwise; PIL: 0 deg at 3 o'clock, clockwise
        a0, a1 = start - 90, end - 90
        if fill:
            d.pieslice(box, a0, a1, fill=_rgb(color))
        else:
            d.arc(box, a0, a1, fill=_rgb(color), width=max(1, int(t)))
        return self

    def poly(self, points, color=(255, 255, 255), fill=True, t=1):
        d = self._draw()
        pts = [(int(px), int(py)) for px, py in points]
        if fill:
            d.polygon(pts, fill=_rgb(color))
        else:
            d.polygon(pts, outline=_rgb(color), width=max(1, int(t)))
        return self

    # ---- ops -> PIL shim (approximates the on-device renderer) --------------
    def _draw(self):
        if self._img is None:
            self._img = Image.new('RGB', (self.width, self.height), (0, 0, 0))
        d = ImageDraw.Draw(self._img)
        d.fontmode = '1'                     # crisp 1-bit text, like the panel
        return d

    def clear(self, color=(0, 0, 0)):
        self._img = Image.new('RGB', (self.width, self.height), _rgb(color))
        return self

    def roundrect(self, x, y, w, h, r, color=(255, 255, 255), fill=False):
        d = self._draw()
        box = [int(x), int(y), int(x) + int(w) - 1, int(y) + int(h) - 1]
        if fill:
            d.rounded_rectangle(box, radius=int(r), fill=_rgb(color))
        else:
            d.rounded_rectangle(box, radius=int(r), outline=_rgb(color), width=1)
        return self

    def upload_atlas(self, images, fmt='rgb888', persist=False):
        self._atlas = [im.convert('RGB') for im in images]
        return True

    def sprite(self, i, x, y, flip=None, rot=None, scale=1):
        # Fidelity note: this gallery shim honors ``scale`` (NEAREST resize — whole OR fractional,
        # matching the wall's sprite / SPRITE2 blit) but ignores flip/rot and has no compositing/
        # alpha, so those visuals render plain here. It never touches encode_ops_bin or the
        # transport, so it can't surface encoding/routing bugs — a parallel path for the gallery.
        if not (0 <= int(i) < len(self._atlas)):
            return self
        tile = self._atlas[int(i)]
        s = max(1.0, float(scale))
        if s != 1.0:
            tile = tile.resize((max(1, round(tile.size[0] * s)), max(1, round(tile.size[1] * s))),
                               Image.NEAREST)
        mask = Image.new('L', tile.size, 0)
        tp, mp = tile.load(), mask.load()
        for yy in range(tile.size[1]):       # magenta is transparent
            for xx in range(tile.size[0]):
                if tp[xx, yy] != (255, 0, 255):
                    mp[xx, yy] = 255
        self._draw()                         # ensure the panel image exists
        self._img.paste(tile, (int(x), int(y)), mask)
        return self

    @property
    def faces(self):
        return _FACES

    def face(self, size):
        ok = [s for s in _FACES if s <= size]
        return max(ok) if ok else _FACES[0]

    def face_width(self, face):
        return _FACE_W.get(int(face), _FACE_W[_FACES[0]])

    def fit(self, text, maxw, maxh):
        best = _FACES[0]
        for f in _FACES:
            if f <= maxh and len(text) * _FACE_W[f] <= maxw:
                best = f
        return best

    def cp(self, s):
        return str(s).encode('cp1252', 'ignore').decode('cp1252')

    def text(self, x, y, s, color=(255, 255, 255), size=10, align='left', font=None):
        d = self._draw()
        anchor = {'left': 'la', 'center': 'ma', 'right': 'ra'}.get(align, 'la')
        d.text((int(x), int(y)), str(s), font=self.font(int(size)),
               fill=_rgb(color), anchor=anchor)
        return self

    def shadow_text(self, x, y, s, color, size, align='left', shadow=(0, 0, 0)):
        s = self.cp(s)
        if not s:
            return self
        self.text(x + 1, y + 1, s, shadow, size=size, align=align)
        self.text(x, y, s, color, size=size, align=align)
        return self

    def gtext(self, x, y, s, color=(255, 255, 255), size=24, align='left', face='sans',
              aa=True, outline=None, shadow=None, tracking=0):
        # Simulate the wall's scalable gtext op with PIL DejaVu at the exact pixel size — (x,y)
        # is the top-left of the ascent box (PIL anchor 'a'), align shifts about x, matching
        # ttfDrawText. mono falls back to sans (as on the wall until a mono face is bundled).
        s = str(s)
        if not s:
            return self
        name = 'DejaVuSansMono-Bold.ttf' if face == 'mono' else 'DejaVuSans-Bold.ttf'
        try:
            f = self.font(int(size), name)
        except Exception:
            f = self.font(int(size))
        d = self._draw()
        anchor = {'left': 'la', 'center': 'ma', 'right': 'ra'}.get(align, 'la')
        if shadow is not None:
            d.text((int(x) + 1, int(y) + 1), s, font=f, fill=_rgb(shadow), anchor=anchor)
        if outline is not None:
            oc = _rgb(outline)
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                d.text((int(x) + dx, int(y) + dy), s, font=f, fill=oc, anchor=anchor)
        d.text((int(x), int(y)), s, font=f, fill=_rgb(color), anchor=anchor)
        return self

    def text_width(self, s, size=24, face='sans', tracking=0):
        from .canvas import _gtext_width
        return _gtext_width(str(s), int(size), str(face), int(tracking))

    def fit_gtext(self, s, max_w, max_h, face='sans', lo=8):
        from .canvas import _gtext_width
        s = str(s)
        hi = min(int(max_h), self.text_max or 512)
        if hi < lo or not s.strip():
            return max(lo, hi)
        size = hi
        for _ in range(12):
            w = _gtext_width(s, size, face)
            if w <= max_w or size <= lo:
                break
            size = max(lo, min(size - 1, int(size * max_w / max(1.0, w))))
        while size > lo and _gtext_width(s, size, face) > max_w:
            size -= 1
        return size

    def wrap_gtext(self, s, max_w, size, max_lines=3, face='sans'):
        from .canvas import _wrap_gtext
        return _wrap_gtext(str(s), max_w, int(size), int(max_lines), face)

    def fit_wrap_gtext(self, s, max_w, max_h, max_lines=3, face='sans', lo=8):
        from .canvas import _fit_wrap_gtext
        return _fit_wrap_gtext(str(s), max_w, max_h, int(max_lines), face, self.text_max, lo)

    def blur(self, x, y, w, h, r=4):
        from PIL import ImageFilter
        self._draw()
        x, y, w, h = int(x), int(y), int(w), int(h)
        box = (max(0, x), max(0, y), min(self.width, x + w), min(self.height, y + h))
        if box[2] > box[0] and box[3] > box[1]:
            region = self._img.crop(box).filter(ImageFilter.GaussianBlur(max(1, int(r))))
            self._img.paste(region, box)
        return self

    def show(self):
        self._draw()
        self.frames.append(self._img.copy())
        return True


    def take(self):
        """The newest rendered image (and forget the backlog) — what the zones
        compositor pastes. None when the app has not drawn yet."""
        img = self.frames[-1] if self.frames else self._img
        self.frames.clear()
        return img


def _borrow_surface_toolkit():
    """ZoneCanvas borrows the real Surface's PIL text toolkit (fit_font/wrap/message/
    text_card...), so apps render through the exact production text code. MRO-aware:
    the toolkit lives on the paneltext.PanelText mixin."""
    from .canvas import CanvasSurface as _CS
    for name in ('MIN_READABLE', 'fit_font', 'ink', 'wrap', 'wrap_fit', 'text_top',
                 'message', 'card_pages', '_card_header', 'text_card', 'mix', 'dim'):
        desc = next(k.__dict__[name] for k in _CS.__mro__ if name in k.__dict__)
        setattr(ZoneCanvas, name, desc)


_borrow_surface_toolkit()


class LcdSurface(ZoneCanvas):
    """The LCD wall's app-facing surface. Apps written for LED panels draw absolute
    pixels (8px faces, 22px fish) — comically small at 1280x800 — so by default this
    REPORTS a logical LED-style panel (device.lcd_logical, e.g. 256x160) and every
    show()/frame() upscales NEAREST xk into ONE full frame pushed through the real
    CanvasSurface (qoi on the wire where the wall takes it). Sizes land where the
    apps' heuristics expect and the look is coherent chunky-LED.

    ``native=True`` (a manifest's ``lcd_native``) skips the shrink for proportional
    apps — the Stock/Sensor Graph draw with fit-to-height type that comes out
    genuinely crisp at the panel's real resolution.
    """

    def __init__(self, url: str, caps, native: bool = False):
        from . import canvas as canvas_mod
        from . import device as device_mod
        if native:
            lw, lh, k = int(caps.canvas_w), int(caps.canvas_h), 1
        else:
            lw, lh, k = device_mod.lcd_logical(caps)
        super().__init__(lw, lh)
        self._k = int(k)
        self._native_size = (int(caps.canvas_w), int(caps.canvas_h))
        self._panel = canvas_mod.CanvasSurface(url, caps)
        self._url = url
        self.can_sound = bool(caps.can_sound)
        self.can_sd = bool(caps.can_sd)

    def __getattr__(self, name):
        """Anything this logical frame-push surface does not define — the device-side renderers
        (``effect`` / on-device ``anim`` / ``ticker``) and the caps they read (``effects``,
        ``effect_defs``, ``effect_params``) — acts on the PANEL itself, not through the upscale
        path, so delegate it to the underlying live CanvasSurface. Without this an effect app on
        the LCD raised ``AttributeError: 'LcdSurface' object has no attribute 'effects'`` and never
        started. Guard ``_panel`` so a miss during __init__ (before it is set) doesn't recurse."""
        if name == "_panel":
            raise AttributeError(name)
        return getattr(self._panel, name)

    # -- the wall side --------------------------------------------------------
    def _push(self, img) -> bool:
        if img is None:
            return False
        if img.size != self._native_size:
            img = img.resize(self._native_size, Image.NEAREST)
        return self._panel.frame(img)

    def show(self):
        super().show()                        # captures the ops image into .frames
        return self._push(self.take())

    def frame(self, image):
        if isinstance(image, (bytes, bytearray)):
            image = Image.frombytes('RGB', (self.width, self.height), bytes(image))
        return self._push(image.convert('RGB'))

    # -- gateway plumbing apps expect on a real surface -----------------------
    def sd_list(self, path="/"):
        from . import canvas as canvas_mod
        return canvas_mod.sd_list(self._url, path)

    def sd_get(self, path):
        from . import canvas as canvas_mod
        return canvas_mod.sd_get(self._url, path)

    def play_anim_path(self, path):
        from . import canvas as canvas_mod
        return canvas_mod.anim_play(self._url, path=path)
