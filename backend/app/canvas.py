"""canvas.py — drive a Matrix wall's framebuffer (the `canvas` capability).

A physical split-flap wall shows flaps; a Matrix wall can additionally draw
ANYTHING on its LED panel, free of the flap grid. Three ways, all here:

  * on-device EFFECTS — plasma/fire/matrix, rendered by the panel itself at
    ~70 fps with nothing on the network (POST /api/canvas/effect);
  * draw OPS — a batch of clear/pixel/line/rect/text applied then presented
    (POST /api/canvas/ops); auto-takes the panel over from the reel wall;
  * a raw FRAME — a full width×height rgb888/rgb565 buffer (PUT
    /api/canvas/frame), for mirroring an image.

These functions are SYNC: they are called from an app's ``fetch()`` (which runs
in an executor thread) via the injected ``canvas`` helper, and from the engine
via ``asyncio.to_thread``. They ride the same pooled per-gateway HTTP client as
every other gateway call (gateway.py)."""

from __future__ import annotations

import logging
import os

from . import gateway

log = logging.getLogger("companion.canvas")

# A real anti-aliased font, bundled once in the backend so every canvas app can
# draw smooth text (via canvas.font) without carrying its own copy. Ships with
# the image through `COPY backend/` (see Dockerfile).
_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
_FONT_CACHE: dict = {}


def _ok(r) -> bool:
    return getattr(r, "status_code", 500) < 400


def set_active(url: str, active: bool, timeout: float = 5.0) -> bool:
    """Take the panel over from the reel wall (active=True) or hand it back
    (active=False). Ops/effect/frame auto-take-over too, but a driver takes it
    first so the wall is blanked before the first frame lands."""
    try:
        return _ok(gateway._request("POST", url, "/api/canvas",
                                    json={"active": bool(active)}, timeout=timeout))
    except Exception as e:
        log.debug("canvas set_active(%s) failed: %s", active, e)
        return False


def play_effect(url: str, effect: str, speed: int = 5, timeout: float = 5.0) -> bool:
    """Start an on-device effect (plasma/fire/matrix), or "none" to return to the
    wall. The panel renders it itself — the companion sends one request and stops."""
    try:
        sp = max(1, min(10, int(speed)))
        return _ok(gateway._request("POST", url, "/api/canvas/effect",
                                    json={"type": str(effect), "speed": sp}, timeout=timeout))
    except Exception as e:
        log.debug("canvas play_effect(%s) failed: %s", effect, e)
        return False


def draw_ops(url: str, ops: list, timeout: float = 8.0) -> bool:
    """Apply a batch of draw ops, in order, then present. Auto-takes the panel over."""
    try:
        return _ok(gateway._request("POST", url, "/api/canvas/ops",
                                    json=list(ops), timeout=timeout))
    except Exception as e:
        log.debug("canvas draw_ops(%d ops) failed: %s", len(ops), e)
        return False


def put_frame(url: str, data: bytes, timeout: float = 15.0) -> bool:
    """Push a full raw frame. The gateway infers the format from the byte length
    (width*height*3 = rgb888, *2 = rgb565), so ``data`` must be exactly one of
    those sizes for the wall."""
    try:
        return _ok(gateway._request("PUT", url, "/api/canvas/frame",
                                    content=bytes(data),
                                    headers={"Content-Type": "application/octet-stream"},
                                    timeout=timeout))
    except Exception as e:
        log.debug("canvas put_frame(%d bytes) failed: %s", len(data), e)
        return False


def get_state(url: str, timeout: float = 5.0) -> dict:
    """{active, width, height, formats, effect, effects} — the panel's canvas state."""
    try:
        r = gateway._request("GET", url, "/api/canvas", timeout=timeout)
        return r.json() if _ok(r) else {}
    except Exception:
        return {}


def release(url: str, timeout: float = 5.0) -> bool:
    """Return the panel to the reel wall — stop any effect AND drop raw-canvas mode.
    Called when a canvas app is replaced by an ordinary flap app, or stopped."""
    stopped = play_effect(url, "none", timeout=timeout)   # effect none marks the wall dirty
    active_off = set_active(url, False, timeout=timeout)   # and drop raw-canvas takeover
    return stopped or active_off


# The named colours the firmware's colour flaps use, so a canvas app can say
# `canvas.rect(..., color="red")` and match the rest of the ecosystem's palette.
_NAMED = {
    "red": (255, 0, 0), "orange": (255, 96, 0), "yellow": (255, 200, 0),
    "green": (0, 200, 0), "blue": (0, 80, 255), "purple": (150, 0, 255),
    "white": (255, 255, 255), "black": (0, 0, 0), "cyan": (0, 200, 200),
    "magenta": (255, 0, 160), "pink": (255, 100, 160), "gray": (128, 128, 128),
}


def _rgb(color):
    """A colour → an [r,g,b] list. Accepts a name, an (r,g,b)/[r,g,b], or a
    #RRGGBB string. Defaults to white for anything unrecognised."""
    if isinstance(color, str):
        s = color.strip().lower()
        if s in _NAMED:
            return list(_NAMED[s])
        if s.startswith("#") and len(s) == 7:
            try:
                return [int(s[i:i + 2], 16) for i in (1, 3, 5)]
            except ValueError:
                pass
        return [255, 255, 255]
    try:
        r, g, b = color
        return [max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b)))]
    except (TypeError, ValueError):
        return [255, 255, 255]


class CanvasSurface:
    """The drawing surface an app receives as its ``canvas`` helper. Draw calls
    accumulate ops; ``show()`` sends the batch and presents it. The panel is the
    real size — ``canvas.width`` × ``canvas.height`` pixels — not the flap grid,
    so an app draws in pixels.

    On a wall with no canvas (a physical split-flap), the helper is not injected
    at all, so an app that wants it declares ``canvas`` and checks it for None."""

    def __init__(self, url: str, width: int, height: int,
                 formats: tuple = (), effects: tuple = ()):
        self.url = url
        self.width = int(width)
        self.height = int(height)
        self.formats = tuple(formats)
        self.effects = tuple(effects)
        self._ops: list = []

    # -- drawing (batched until show) ----------------------------------------
    def clear(self, color=(0, 0, 0)):
        self._ops.append({"op": "clear", "color": _rgb(color)})
        return self

    def pixel(self, x, y, color=(255, 255, 255)):
        self._ops.append({"op": "pixel", "x": int(x), "y": int(y), "color": _rgb(color)})
        return self

    def hline(self, x, y, w, color=(255, 255, 255)):
        self._ops.append({"op": "hline", "x": int(x), "y": int(y), "w": int(w), "color": _rgb(color)})
        return self

    def vline(self, x, y, h, color=(255, 255, 255)):
        self._ops.append({"op": "vline", "x": int(x), "y": int(y), "h": int(h), "color": _rgb(color)})
        return self

    def rect(self, x, y, w, h, color=(255, 255, 255), fill=False):
        self._ops.append({"op": "rect", "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                          "color": _rgb(color), "fill": bool(fill)})
        return self

    def text(self, x, y, s, color=(255, 255, 255), size=10):
        self._ops.append({"op": "text", "x": int(x), "y": int(y), "s": str(s),
                          "color": _rgb(color), "size": int(size)})
        return self

    def show(self) -> bool:
        """Send the accumulated draw ops and present them. A no-op if nothing was drawn."""
        if not self._ops:
            return True
        ops, self._ops = self._ops + [{"op": "show"}], []
        return draw_ops(self.url, ops)

    # -- whole-panel content -------------------------------------------------
    def effect(self, name, speed: int = 5) -> bool:
        """Play an on-device effect (from ``canvas.effects``); "none" returns to
        the wall. The panel renders it — one request, then nothing."""
        return play_effect(self.url, name, speed)

    def frame(self, image) -> bool:
        """Push a full raw frame. ``image`` is a PIL image (resized/converted to
        the panel and sent as rgb888) or raw bytes already sized for the wall."""
        if isinstance(image, (bytes, bytearray)):
            return put_frame(self.url, bytes(image))
        try:
            img = image.convert("RGB").resize((self.width, self.height))
            return put_frame(self.url, img.tobytes())
        except Exception as e:
            log.debug("canvas.frame render failed: %s", e)
            return False

    # -- rich rendering helpers (Pillow) -------------------------------------
    # A canvas app that wants smooth type / gradients renders a whole PIL image
    # and pushes it with frame(). These three cover the common needs so each app
    # doesn't reinvent them: the bundled font, a blank panel-sized image, and a
    # vertical gradient (a sky, a backdrop). Pillow is imported lazily so the
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

    def blank(self, color=(0, 0, 0)):
        """A fresh RGB image the exact size of the panel."""
        from PIL import Image
        return Image.new("RGB", (self.width, self.height), tuple(_rgb(color)))

    def vgrad(self, top, bottom):
        """A panel-sized image with a vertical gradient from ``top`` to ``bottom``
        (each a colour name / (r,g,b) / #hex). Built one column then stretched, so
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
