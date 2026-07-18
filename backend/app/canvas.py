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

# The last full frame a canvas app rendered, kept per gateway URL so the live
# preview (web UI) and the Home Assistant board image can show what a Matrix panel
# is drawing — both otherwise render the flap grid, which a canvas app bypasses.
# Only frame-push apps land here (rgb888); an on-device effect has no pixels the
# companion ever sees. Cleared on release().
_LAST_FRAME: dict = {}   # url -> (width, height, rgb888 bytes)


def _remember_frame(url: str, w: int, h: int, rgb: bytes) -> None:
    if url and len(rgb) == w * h * 3:
        _LAST_FRAME[url] = (int(w), int(h), rgb)


def forget_frame(url: str) -> None:
    _LAST_FRAME.pop(url, None)


def has_frame(url: str) -> bool:
    return url in _LAST_FRAME


def last_frame_png(url: str, scale: int = 1):
    """The cached frame as PNG bytes (optionally nearest-neighbour upscaled), or None."""
    f = _LAST_FRAME.get(url)
    if not f:
        return None
    try:
        import io

        from PIL import Image
        w, h, rgb = f
        img = Image.frombytes("RGB", (w, h), rgb)
        if scale > 1:
            img = img.resize((w * scale, h * scale), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception as e:
        log.debug("last_frame_png failed: %s", e)
        return None


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


def play_effect(url: str, effect: str, speed: int = 5, hue=None, density=None,
                timeout: float = 5.0) -> bool:
    """Start an on-device effect (plasma/fire/matrix/…), or "none" to return to the
    wall. The panel renders it itself — the companion sends one request and stops.
    Optional ``hue`` (0–255) and ``density`` (1–100) tint/seed effects that support them
    (see caps.effect_params); omitted, each effect keeps its own default look."""
    try:
        body = {"type": str(effect), "speed": max(1, min(10, int(speed)))}
        if hue is not None:
            body["hue"] = max(0, min(255, int(hue)))
        if density is not None:
            body["density"] = max(1, min(100, int(density)))
        return _ok(gateway._request("POST", url, "/api/canvas/effect", json=body, timeout=timeout))
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


# --- QOI encode (qoiformat.org) — a full frame, lossless, 2–4× smaller than raw, so the
# same picture crosses far less WiFi (the board's panel DMA and radio share one bus). The
# firmware decodes it straight to the panel. Pure Python, no dependency: the standard
# run/index/diff coder, one pass over the pixels. ------------------------------------------
def _s8(v: int) -> int:
    """A byte difference as a signed 8-bit value (the wraparound QOI diffs use)."""
    v &= 0xFF
    return v - 256 if v >= 128 else v


def qoi_encode(rgb: bytes, w: int, h: int) -> bytes:
    """Encode a row-major rgb888 buffer (``w*h*3`` bytes) as a QOI image (3 channels, sRGB)."""
    out = bytearray(b"qoif")
    out += w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes((3, 0))
    index = [0] * 64                       # seen-pixel table, keyed r<<24|g<<16|b<<8|a
    pr = pg = pb = 0                       # previous pixel; alpha is a constant 255
    run = 0
    mv = memoryview(rgb)
    n = w * h
    app = out.append
    for i in range(n):
        j = i * 3
        r, g, b = mv[j], mv[j + 1], mv[j + 2]
        if r == pr and g == pg and b == pb:
            run += 1
            if run == 62 or i == n - 1:
                app(0xC0 | (run - 1)); run = 0
            continue
        if run:
            app(0xC0 | (run - 1)); run = 0
        ih = (r * 3 + g * 5 + b * 7 + 255 * 11) & 63
        key = (r << 24) | (g << 16) | (b << 8) | 255
        if index[ih] == key:
            app(ih)                                                    # QOI_OP_INDEX
        else:
            index[ih] = key
            vr, vg, vb = _s8(r - pr), _s8(g - pg), _s8(b - pb)
            vgr, vgb = _s8(vr - vg), _s8(vb - vg)
            if -2 <= vr <= 1 and -2 <= vg <= 1 and -2 <= vb <= 1:
                app(0x40 | ((vr + 2) << 4) | ((vg + 2) << 2) | (vb + 2))  # QOI_OP_DIFF
            elif -32 <= vg <= 31 and -8 <= vgr <= 7 and -8 <= vgb <= 7:
                app(0x80 | (vg + 32)); app(((vgr + 8) << 4) | (vgb + 8))  # QOI_OP_LUMA
            else:
                app(0xFE); app(r); app(g); app(b)                        # QOI_OP_RGB
        pr, pg, pb = r, g, b
    out += bytes((0, 0, 0, 0, 0, 0, 0, 1))                              # end marker
    return bytes(out)


def put_qoi(url: str, data: bytes, timeout: float = 15.0) -> bool:
    """Push a QOI-encoded full frame (PUT /api/canvas/qoi)."""
    try:
        return _ok(gateway._request("PUT", url, "/api/canvas/qoi", content=bytes(data),
                                    headers={"Content-Type": "application/octet-stream"},
                                    timeout=timeout))
    except Exception as e:
        log.debug("canvas put_qoi(%d bytes) failed: %s", len(data), e)
        return False


def put_rect(url: str, x: int, y: int, w: int, h: int, rgb: bytes, timeout: float = 15.0) -> bool:
    """Update ONE rectangle (PUT /api/canvas/rect): an 8-byte header [x, y, w, h] (u16 BE)
    then ``w*h`` rgb888 pixels, drawn over the live frame — animating a small area costs
    only that area's bytes, not the whole panel's."""
    try:
        head = (int(x).to_bytes(2, "big") + int(y).to_bytes(2, "big")
                + int(w).to_bytes(2, "big") + int(h).to_bytes(2, "big"))
        return _ok(gateway._request("PUT", url, "/api/canvas/rect", content=head + bytes(rgb),
                                    headers={"Content-Type": "application/octet-stream"},
                                    timeout=timeout))
    except Exception as e:
        log.debug("canvas put_rect failed: %s", e)
        return False


def put_ticker(url: str, text: str, color=(255, 255, 255), speed: int = 2,
               timeout: float = 5.0) -> bool:
    """Scroll one line of text across the panel ON-DEVICE (POST /api/canvas/ticker) — smooth,
    nothing streamed. Empty text hands the panel back. Speed 1–20."""
    try:
        body = {"text": str(text), "color": list(_rgb(color)),
                "speed": max(1, min(20, int(speed)))}
        return _ok(gateway._request("POST", url, "/api/canvas/ticker", json=body, timeout=timeout))
    except Exception as e:
        log.debug("canvas put_ticker failed: %s", e)
        return False


def put_anim(url: str, frames: list, w: int, h: int, fps: int = 12, loop: bool = True,
             timeout: float = 30.0) -> bool:
    """Upload a looping animation that plays ON-DEVICE from PSRAM (PUT /api/canvas/anim), so
    the companion sends it once and can stop. ``frames`` is a list of rgb888 buffers (each
    ``w*h*3`` bytes). Header (14 B, BE): MPGA · ver=1 · fmt=3(rgb888) · fps · flags(bit0=loop)
    · w · h · frames."""
    try:
        fr = len(frames)
        hdr = (b"MPGA" + bytes((1, 3, max(1, min(60, int(fps))), 1 if loop else 0))
               + int(w).to_bytes(2, "big") + int(h).to_bytes(2, "big") + fr.to_bytes(2, "big"))
        body = bytearray(hdr)
        for f in frames:
            body += bytes(f)
        return _ok(gateway._request("PUT", url, "/api/canvas/anim", content=bytes(body),
                                    headers={"Content-Type": "application/octet-stream"},
                                    timeout=timeout))
    except Exception as e:
        log.debug("canvas put_anim(%d frames) failed: %s", len(frames), e)
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
    forget_frame(url)                      # the preview is no longer live
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
                 formats: tuple = (), effects: tuple = (),
                 rect: bool = False, anim: bool = False, ticker: bool = False,
                 effect_params: tuple = ()):
        self.url = url
        self.width = int(width)
        self.height = int(height)
        self.formats = tuple(formats)
        self.effects = tuple(effects)
        # Newer-firmware canvas extras (see device.Capabilities). An app checks these before
        # reaching for ticker()/anim()/paste() so it can fall back on an older wall.
        self.can_qoi = "qoi" in self.formats
        self.can_rect = bool(rect)
        self.can_anim = bool(anim)
        self.can_ticker = bool(ticker)
        self.effect_params = tuple(effect_params)
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
    def effect(self, name, speed: int = 5, hue=None, density=None) -> bool:
        """Play an on-device effect (from ``canvas.effects``); "none" returns to
        the wall. The panel renders it — one request, then nothing. ``hue`` (0–255) and
        ``density`` (1–100) tint/seed effects that support them (``canvas.effect_params``)."""
        return play_effect(self.url, name, speed, hue, density)

    def _push_rgb(self, b: bytes) -> bool:
        _remember_frame(self.url, self.width, self.height, b)       # for the previews
        # QOI where the wall takes it: the same picture over far less WiFi. Any encode
        # hiccup falls back to the raw frame, so a frame is never lost to compression.
        if self.can_qoi:
            try:
                return put_qoi(self.url, qoi_encode(b, self.width, self.height))
            except Exception as e:
                log.debug("canvas.frame QOI encode failed, sending raw: %s", e)
        return put_frame(self.url, b)

    def frame(self, image) -> bool:
        """Push a full frame. ``image`` is a PIL image (resized/converted to the panel) or
        raw rgb888 bytes already sized for the wall. Sent QOI-compressed where the wall
        advertises it, raw otherwise — transparently, so apps never think about it."""
        if isinstance(image, (bytes, bytearray)):
            return self._push_rgb(bytes(image))
        try:
            b = image.convert("RGB").resize((self.width, self.height)).tobytes()
        except Exception as e:
            log.debug("canvas.frame render failed: %s", e)
            return False
        return self._push_rgb(b)

    def ticker(self, text, color=(255, 255, 255), speed: int = 2) -> bool:
        """Scroll one line of text across the panel ON-DEVICE — smooth, nothing streamed.
        Empty text hands the panel back. Needs ``canvas.can_ticker``."""
        forget_frame(self.url)                 # a scrolling ticker has no single still frame
        return put_ticker(self.url, text, color, speed)

    def anim(self, images, fps: int = 12, loop: bool = True) -> bool:
        """Upload a short loop that plays ON-DEVICE from PSRAM (sent once, then nothing on the
        network). ``images`` is a list of PIL images. Needs ``canvas.can_anim``."""
        try:
            frames = [im.convert("RGB").resize((self.width, self.height)).tobytes()
                      for im in images]
        except Exception as e:
            log.debug("canvas.anim render failed: %s", e)
            return False
        if not frames:
            return False
        _remember_frame(self.url, self.width, self.height, frames[0])   # preview = frame 0
        return put_anim(self.url, frames, self.width, self.height, fps, loop)

    def paste(self, x, y, image) -> bool:
        """Update just a RECTANGLE of the live panel (cheap partial animation). ``image`` is
        a PIL image drawn with its top-left at (x, y). Needs ``canvas.can_rect``."""
        try:
            img = image.convert("RGB")
            return put_rect(self.url, int(x), int(y), img.width, img.height, img.tobytes())
        except Exception as e:
            log.debug("canvas.paste failed: %s", e)
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
