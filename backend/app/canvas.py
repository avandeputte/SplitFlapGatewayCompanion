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

import hashlib
import logging
import os
import time

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
_LAST_FRAME: dict = {}   # url -> (width, height, rgb888 bytes)   [also the base for delta frames]
# Frames pushed since the last base reset, per URL. A full "keyframe" every _KEYFRAME_EVERY pushes
# means a gateway reboot (or any panel drift) self-heals within a few seconds without needing to
# poll the wall's uptime on the frame path.
_DELTA_N: dict = {}
_KEYFRAME_EVERY = 20


def _remember_frame(url: str, w: int, h: int, rgb: bytes) -> None:
    if url and len(rgb) == w * h * 3:
        _LAST_FRAME[url] = (int(w), int(h), rgb)


def forget_frame(url: str) -> None:
    _LAST_FRAME.pop(url, None)
    _DELTA_N.pop(url, None)


# The wall keeps a NAMED library of sprite sheets — several under one budget, addressed by name,
# optionally persisted. We name a sheet by a fingerprint of its own bytes, so "the wall lists this
# name" *is* "those exact tiles are loaded": no hashing on the device, no generation counter,
# nothing to go stale. A sheet is uploaded once and every later draw costs one small
# {"op":"atlas","name":…} bind instead of re-sending ~8 KB of tiles.
#
# The wall can still drop a sheet under us (a reboot, or LRU eviction by other sheets), and a
# `sprite` with nothing bound silently draws nothing — so the belief is re-checked against the
# device's own library on a wall-clock bound. That check is a small JSON GET, not a re-upload.
_ATLAS_KNOWN: dict = {}         # url -> {"at": monotonic, "rows": {name: library_row}}
_ATLAS_VERIFY_S = 60.0
_ATLAS_NAME_MAX = 32            # firmware: [a-z0-9._-]{1,32}


def atlas_name_for(tiles: bytes, tile_w: int, tile_h: int, count: int, fmt: str = "rgb888") -> str:
    """A wall-legal sheet name that IS the content fingerprint, so presence in the wall's library
    means exactly these tiles are loaded. Charset/length match the firmware's rule."""
    digest = hashlib.blake2b(bytes(tiles), digest_size=9).hexdigest()
    f = "5" if str(fmt) == "rgb565" else "8"
    return f"c{int(tile_w)}x{int(tile_h)}.{f}{digest}"[:_ATLAS_NAME_MAX]


def forget_atlas(url: str) -> None:
    """Drop what we believe the wall holds — the next draw re-checks and re-uploads if needed."""
    _ATLAS_KNOWN.pop(url, None)


def has_frame(url: str) -> bool:
    return url in _LAST_FRAME


def _frame_png(w: int, h: int, rgb: bytes, scale: int = 1):
    """rgb888 bytes → PNG bytes (optionally nearest-neighbour upscaled), or None."""
    try:
        import io

        from PIL import Image
        img = Image.frombytes("RGB", (int(w), int(h)), bytes(rgb))
        if scale > 1:
            img = img.resize((int(w) * scale, int(h) * scale), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    except Exception as e:
        log.debug("_frame_png failed: %s", e)
        return None


def last_frame_png(url: str, scale: int = 1):
    """The cached frame as PNG bytes (optionally nearest-neighbour upscaled), or None."""
    f = _LAST_FRAME.get(url)
    return _frame_png(*f, scale=scale) if f else None


def readback_png(url: str, scale: int = 1, fmt: str = "rgb888"):
    """Read the lit panel back (firmware 1.19) and return it as PNG bytes, or None. This is what
    lets the live preview show on-device content — an effect, a ticker, an animation — that the
    companion never rendered a frame for, so :func:`last_frame_png` has nothing cached. One
    gateway round-trip; the endpoint is read-only and made for polling."""
    f = get_frame(url, fmt)
    return _frame_png(*f, scale=scale) if f else None


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


def put_rects(url: str, rects: list, fmt: int = 2, timeout: float = 10.0):
    """Draw several changed rectangles over the live frame in one request (PUT /api/canvas/rects,
    firmware 3.1). ``rects`` is ``[(x, y, w, h, pixel_bytes)]`` with pixels in ``fmt`` (2 = rgb565
    big-endian, 3 = rgb888), row-major. Header (big-endian): u16 count, u8 fmt, u8 0; then per
    rect u16 x,y,w,h and the pixels. Returns True (drawn), "toobig" on 413 (send a full frame
    instead), or False on any other error."""
    try:
        parts = [len(rects).to_bytes(2, "big"), bytes((int(fmt) & 0xFF, 0))]
        for x, y, w, h, px in rects:
            parts.append(int(x).to_bytes(2, "big") + int(y).to_bytes(2, "big")
                         + int(w).to_bytes(2, "big") + int(h).to_bytes(2, "big"))
            parts.append(bytes(px))
        r = gateway._request("PUT", url, "/api/canvas/rects", content=b"".join(parts),
                             headers={"Content-Type": "application/octet-stream"}, timeout=timeout)
        if getattr(r, "status_code", 0) == 413:
            return "toobig"
        return _ok(r)
    except Exception as e:
        log.debug("canvas put_rects failed: %s", e)
        return False


def _rgb565_be(arr):
    """A numpy (h, w, 3) uint8 array -> rgb565 big-endian bytes, row-major."""
    import numpy as np
    r = arr[:, :, 0].astype(np.uint16)
    g = arr[:, :, 1].astype(np.uint16)
    b = arr[:, :, 2].astype(np.uint16)
    v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return v.astype(">u2").tobytes()


def diff_rects(old_rgb: bytes, new_rgb: bytes, w: int, h: int, max_frac: float = 0.5):
    """Coarse dirty-row bands between two rgb888 frames: group the changed rows into a few bands,
    each one rect spanning the columns that differ across it. Returns ``[]`` when the frames are
    identical, ``None`` when more than ``max_frac`` of the panel changed (caller: push a full
    frame), else ``[(x, y, w, h, rgb565_be_bytes)]``. numpy-vectorised; the caller falls back to a
    full frame if numpy is unavailable."""
    import numpy as np
    old = np.frombuffer(old_rgb, np.uint8).reshape(h, w, 3)
    new = np.frombuffer(new_rgb, np.uint8).reshape(h, w, 3)
    changed = np.any(old != new, axis=2)                       # (h, w) bool
    dirty_rows = np.nonzero(changed.any(axis=1))[0]
    if dirty_rows.size == 0:
        return []
    # Split the dirty rows into contiguous bands.
    breaks = np.nonzero(np.diff(dirty_rows) > 1)[0]
    starts = np.concatenate(([dirty_rows[0]], dirty_rows[breaks + 1]))
    ends = np.concatenate((dirty_rows[breaks], [dirty_rows[-1]]))
    rects, area = [], 0
    for y0, y1 in zip(starts.tolist(), ends.tolist()):
        cols = np.nonzero(changed[y0:y1 + 1].any(axis=0))[0]
        x0, x1 = int(cols[0]), int(cols[-1])
        rw, rh = x1 - x0 + 1, y1 - y0 + 1
        area += rw * rh
        if area > max_frac * w * h:
            return None
        rects.append((x0, y0, rw, rh, _rgb565_be(new[y0:y1 + 1, x0:x1 + 1])))
    return rects


def put_ticker(url: str, text: str, color=(255, 255, 255), speed: int = 2,
               overlay: bool = False, band: bool = True, font: str | None = None,
               timeout: float = 5.0) -> bool:
    """Scroll one line of text across the panel ON-DEVICE (POST /api/canvas/ticker) — smooth,
    nothing streamed. Empty text hands the panel back. Speed 1–20.

    ``overlay`` (firmware 2.1) composites the ticker as a lower-third band OVER whatever else is
    presenting — the flap wall, an effect, an animation, a pushed frame — and it survives page
    and mode changes until an empty text stops it. ``band=False`` drops the black bar and scrolls
    the glyphs straight over the content. ``font`` names an uploaded/library face (``"custom"`` or
    a saved name); an unknown name falls back to the built-in face rather than erroring."""
    try:
        body = {"text": str(text), "color": list(_rgb(color)),
                "speed": max(1, min(20, int(speed)))}
        if overlay:
            body["overlay"] = True
            body["band"] = bool(band)
        if font:
            body["font"] = str(font)
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


def get_frame(url: str, fmt: str = "rgb888", timeout: float = 8.0):
    """Read the lit panel back (GET /api/canvas/frame, firmware 1.19) — a screenshot of whatever
    is on screen: the flap wall, an effect, an animation, a ticker or a pushed frame. Returns
    ``(width, height, rgb888 bytes)`` or ``None``. Read-only: it never disturbs the running mode,
    so a live preview can poll it. The panel's real bit depth is baked in (it is what is
    physically lit); brightness is not in the framebuffer, so a dim wall reads back at full value."""
    try:
        f = "rgb565" if str(fmt) == "rgb565" else "rgb888"
        r = gateway._request("GET", url, f"/api/canvas/frame?fmt={f}", timeout=timeout)
        if not _ok(r):
            return None
        w = int(r.headers.get("X-Canvas-Width") or 0)
        h = int(r.headers.get("X-Canvas-Height") or 0)
        got = (r.headers.get("X-Canvas-Format") or f).lower()
        body = r.content
        if not (w and h):
            return None
        if got == "rgb565":
            body = _rgb565_to_888(body, w, h)
        return (w, h, body) if len(body) == w * h * 3 else None
    except Exception as e:
        log.debug("canvas get_frame failed: %s", e)
        return None


def _rgb565_to_888(data: bytes, w: int, h: int) -> bytes:
    """Big-endian rgb565 → rgb888, expanding each channel to 8 bits (the panel's own quantisation
    is already baked in; this only widens the container)."""
    out = bytearray(w * h * 3)
    n = min(len(data) // 2, w * h)
    for i in range(n):
        v = (data[2 * i] << 8) | data[2 * i + 1]
        r5, g6, b5 = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
        j = i * 3
        out[j] = (r5 << 3) | (r5 >> 2)
        out[j + 1] = (g6 << 2) | (g6 >> 4)
        out[j + 2] = (b5 << 3) | (b5 >> 2)
    return bytes(out)


def set_transition(url: str, kind: str = "crossfade", ms: int = 400, timeout: float = 5.0) -> bool:
    """Set how subsequent full-frame PUTs present (POST /api/canvas/transition, firmware 2.1):
    ``none`` (hard cut), ``crossfade``, ``wipe`` or ``slide``, tweened on-device over ``ms``
    (100–2000). Sticky and runtime-only — a reboot returns to hard cuts. rect/qoi/anim are
    unaffected."""
    try:
        k = kind if kind in ("none", "crossfade", "wipe", "slide") else "crossfade"
        body = {"type": k, "ms": max(100, min(2000, int(ms)))}
        return _ok(gateway._request("POST", url, "/api/canvas/transition", json=body, timeout=timeout))
    except Exception as e:
        log.debug("canvas set_transition failed: %s", e)
        return False


def put_gif(url: str, data: bytes, timeout: float = 30.0):
    """Import an animated GIF (PUT /api/canvas/gif, firmware 2.1) — decoded ON-DEVICE into the
    animation store and played at once, so the companion never unpacks frames itself. Returns the
    reply ``{ok, frames, fps}`` (or ``{}`` on failure). A GIF larger than the panel is a 400; the
    upload is capped at 4 MB. Persist what's playing with :func:`anim_save`."""
    try:
        r = gateway._request("PUT", url, "/api/canvas/gif", content=bytes(data),
                             headers={"Content-Type": "application/octet-stream"}, timeout=timeout)
        return r.json() if _ok(r) else {}
    except Exception as e:
        log.debug("canvas put_gif(%d bytes) failed: %s", len(data), e)
        return {}


def _anim_op(url: str, op: str, name: str, timeout: float = 15.0):
    """POST /api/canvas/anim/<op> {name} — save/play/delete a library animation. Returns the JSON
    reply (``play`` reports ``frames``) or ``{}``."""
    try:
        r = gateway._request("POST", url, f"/api/canvas/anim/{op}",
                             json={"name": str(name)}, timeout=timeout)
        return r.json() if _ok(r) else {}
    except Exception as e:
        log.debug("canvas anim/%s(%s) failed: %s", op, name, e)
        return {}


def anim_save(url: str, name: str) -> bool:
    """Persist whatever animation is loaded to the on-device library as ``name`` (firmware 2.1)."""
    return bool(_anim_op(url, "save", name).get("ok"))


def anim_play(url: str, name: str):
    """Load and play a saved library animation (firmware 2.1). Returns ``{ok, frames}`` or ``{}``."""
    return _anim_op(url, "play", name)


def anim_delete(url: str, name: str) -> bool:
    """Delete a library animation (firmware 2.1)."""
    return bool(_anim_op(url, "delete", name).get("ok"))


def anim_list(url: str, timeout: float = 8.0) -> list:
    """The on-device animation library (GET /api/canvas/anims, firmware 2.1): a list of
    ``{name, bytes, frames, w, h, fps, loop}``. ``[]`` on any wall that lacks it."""
    try:
        r = gateway._request("GET", url, "/api/canvas/anims", timeout=timeout)
        doc = r.json() if _ok(r) else []
        return doc if isinstance(doc, list) else []
    except Exception:
        return []


def put_font(url: str, data: bytes, timeout: float = 10.0):
    """Install a packed ``MPFT`` font into the wall's ``custom`` slot (PUT /api/canvas/font,
    firmware 2.1). Returns ``{ok, font, w, h, ascent}`` or ``{}``. Persist it with
    :func:`font_save`; then name it in a ticker or the ``text`` op's ``font`` field."""
    try:
        r = gateway._request("PUT", url, "/api/canvas/font", content=bytes(data),
                             headers={"Content-Type": "application/octet-stream"}, timeout=timeout)
        return r.json() if _ok(r) else {}
    except Exception as e:
        log.debug("canvas put_font(%d bytes) failed: %s", len(data), e)
        return {}


def font_save(url: str, name: str) -> bool:
    """Persist the loaded custom font to the library as ``name`` (firmware 2.1)."""
    try:
        r = gateway._request("POST", url, "/api/canvas/font/save", json={"name": str(name)}, timeout=8.0)
        return _ok(r)
    except Exception as e:
        log.debug("canvas font_save(%s) failed: %s", name, e)
        return False


def font_delete(url: str, name: str) -> bool:
    """Delete a library font (firmware 2.1)."""
    try:
        r = gateway._request("POST", url, "/api/canvas/font/delete", json={"name": str(name)}, timeout=8.0)
        return _ok(r)
    except Exception as e:
        log.debug("canvas font_delete(%s) failed: %s", name, e)
        return False


def font_list(url: str, timeout: float = 8.0) -> list:
    """The on-device font library (GET /api/canvas/fonts, firmware 2.1): ``{name, bytes, w, h,
    ascent}`` each. ``[]`` on a wall that lacks it."""
    try:
        r = gateway._request("GET", url, "/api/canvas/fonts", timeout=timeout)
        doc = r.json() if _ok(r) else []
        return doc if isinstance(doc, list) else []
    except Exception:
        return []


def _atlas_body(tiles: bytes, tile_w: int, tile_h: int, count: int, fmt: str) -> bytes:
    """The MPTA upload body: 12-byte big-endian header, then the tiles back-to-back."""
    f = 2 if str(fmt) == "rgb565" else 3
    return (b"MPTA" + bytes((1, f))                     # magic, ver=1, fmt (= bytes per pixel)
            + int(tile_w).to_bytes(2, "big") + int(tile_h).to_bytes(2, "big")
            + int(count).to_bytes(2, "big") + bytes(tiles))


def put_atlas_named(url: str, name: str, tiles: bytes, tile_w: int, tile_h: int, count: int,
                    fmt: str = "rgb888", timeout: float = 15.0) -> bool:
    """Upload one NAMED sheet into the wall's atlas library (PUT /api/canvas/atlas/<name>,
    firmware 3.1). Same MPTA body as the unnamed route; the name is the address a later
    ``{"op":"atlas"}`` binds."""
    try:
        ok = _ok(gateway._request("PUT", url, f"/api/canvas/atlas/{name}",
                                  content=_atlas_body(tiles, tile_w, tile_h, count, fmt),
                                  headers={"Content-Type": "application/octet-stream"},
                                  timeout=timeout))
        if ok:
            rows = _ATLAS_KNOWN.setdefault(url, {"at": time.monotonic(), "rows": {}})["rows"]
            rows[name] = {"name": name, "resident": True, "persisted": rows.get(name, {}).get("persisted", False)}
        else:
            forget_atlas(url)                           # we no longer know what's up there
        return ok
    except Exception as e:
        log.debug("canvas put_atlas_named failed: %s", e)
        forget_atlas(url)
        return False


def atlas_list(url: str, timeout: float = 8.0) -> list:
    """The wall's atlas library — ``[{name, tiles, w, h, fmt, bytes, resident, persisted}]``.
    Includes sheets that are only persisted: binding one lazy-loads it, so for "can I bind this?"
    presence in this list is the answer."""
    try:
        r = gateway._request("GET", url, "/api/canvas/atlas", timeout=timeout)
        if not _ok(r):
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        log.debug("canvas atlas_list failed: %s", e)
        return []


def atlas_save(url: str, name: str, timeout: float = 15.0) -> bool:
    """Persist a sheet to the wall's filesystem so it survives a reboot AND an LRU eviction
    (lazy-loaded on the next bind)."""
    ok = _ok(gateway._request("POST", url, f"/api/canvas/atlas/{name}/save", timeout=timeout))
    if ok:
        row = _ATLAS_KNOWN.get(url, {}).get("rows", {}).get(name)
        if row:
            row["persisted"] = True
    return ok


def atlas_delete(url: str, name: str, timeout: float = 10.0) -> bool:
    """Drop a sheet from the wall, resident and persisted."""
    ok = _ok(gateway._request("DELETE", url, f"/api/canvas/atlas/{name}", timeout=timeout))
    if ok:
        _ATLAS_KNOWN.get(url, {}).get("rows", {}).pop(name, None)
    return ok


def _atlas_lib(url: str) -> dict:
    """``{name: row}`` of the wall's atlas library from what we last saw, re-reading it when the
    belief is older than the verify window — a sheet can be evicted or lost to a reboot under us."""
    e = _ATLAS_KNOWN.get(url)
    now = time.monotonic()
    if e is None or now - e["at"] > _ATLAS_VERIFY_S:
        rows = {str(a["name"]): a for a in atlas_list(url) if isinstance(a, dict) and a.get("name")}
        _ATLAS_KNOWN[url] = e = {"at": now, "rows": rows}
    return e["rows"]


def _atlas_row(url: str, name: str):
    """The library row for ``name`` (with its ``persisted``/``resident`` flags), or None if the
    wall doesn't have it — in which case the caller uploads."""
    return _atlas_lib(url).get(name)


def atlas_has(url: str, name: str) -> bool:
    """Is ``name`` bindable on this wall (resident or persisted)?"""
    return name in _atlas_lib(url)


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
    # The atlas library is NOT touched: the wall keeps its sheets across uses, so a playlist
    # cycling back to a canvas app re-binds by name rather than re-uploading.
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


# The panel's bundled text faces and their fixed glyph widths. A `text` op with a `size`
# outside this set falls back to a small 6x10 face on-device, so apps snap to these.
_FACES = (8, 9, 10, 13, 18, 20)
_FACE_W = {8: 5, 9: 6, 10: 6, 13: 8, 18: 9, 20: 10}


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
                 rect: bool = False, rects: bool = False, anim: bool = False, ticker: bool = False,
                 effect_params: tuple = (), readback: bool = False, ops: tuple = (),
                 overlay: bool = False, transition: bool = False, anim_library: bool = False,
                 gif: bool = False, fonts: bool = False, sprite: bool = False):
        self.url = url
        self.width = int(width)
        self.height = int(height)
        self.formats = tuple(formats)
        self.effects = tuple(effects)
        # Newer-firmware canvas extras (see device.Capabilities). An app checks these before
        # reaching for ticker()/anim()/paste() so it can fall back on an older wall.
        self.can_qoi = "qoi" in self.formats
        self.can_rect = bool(rect)
        self.can_rects = bool(rects)            # 3.1: frame() sends only the changed rects (delta)
        self.can_anim = bool(anim)
        self.can_ticker = bool(ticker)
        self.effect_params = tuple(effect_params)
        # 1.19 / 1.25 / 2.1. `ops` is the draw-op vocabulary the wall honours (an app can consult
        # it before reaching for a shape); `can_ops` is "any ops at all". The rest gate the 2.1
        # families — an app checks the flag, then calls the matching helper.
        self.ops = tuple(ops)
        self.can_ops = bool(self.ops)
        self.can_readback = bool(readback)
        self.can_overlay = bool(overlay)
        self.can_transition = bool(transition)
        self.can_anim_library = bool(anim_library)
        self.can_gif = bool(gif)
        self.can_fonts = bool(fonts)
        self.can_sprite = bool(sprite)
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

    def line(self, x, y, x1, y1, color=(255, 255, 255)):
        self._ops.append({"op": "line", "x": int(x), "y": int(y), "x1": int(x1), "y1": int(y1),
                          "color": _rgb(color)})
        return self

    def circle(self, x, y, r, color=(255, 255, 255), fill=False):
        self._ops.append({"op": "circle", "x": int(x), "y": int(y), "r": int(r),
                          "color": _rgb(color), "fill": bool(fill)})
        return self

    def ellipse(self, x, y, rx, ry, color=(255, 255, 255), fill=False):
        self._ops.append({"op": "ellipse", "x": int(x), "y": int(y), "rx": int(rx), "ry": int(ry),
                          "color": _rgb(color), "fill": bool(fill)})
        return self

    def triangle(self, x, y, x1, y1, x2, y2, color=(255, 255, 255), fill=False):
        self._ops.append({"op": "triangle", "x": int(x), "y": int(y), "x1": int(x1), "y1": int(y1),
                          "x2": int(x2), "y2": int(y2), "color": _rgb(color), "fill": bool(fill)})
        return self

    def roundrect(self, x, y, w, h, r, color=(255, 255, 255), fill=False):
        self._ops.append({"op": "roundrect", "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                          "r": int(r), "color": _rgb(color), "fill": bool(fill)})
        return self

    def gradient(self, x, y, w, h, frm, to, direction="v"):
        """Fill a rectangle with a linear gradient ``frm`` → ``to``; ``direction`` "v" (default)
        or "h". Drawn on-device, so a sky or a backdrop costs a dozen bytes, not a frame."""
        self._ops.append({"op": "gradient", "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                          "from": _rgb(frm), "to": _rgb(to), "dir": "h" if direction == "h" else "v"})
        return self

    def polyline(self, points, color=(255, 255, 255)):
        """Connect ``points`` — a list of (x, y) — with lines."""
        self._ops.append({"op": "polyline", "color": _rgb(color),
                          "points": [[int(px), int(py)] for px, py in points]})
        return self

    def sprite(self, i, x, y):
        """Blit tile ``i`` of the uploaded atlas (see :meth:`upload_atlas`) at (x, y). Magenta is
        transparent. Needs ``canvas.can_sprite``."""
        self._ops.append({"op": "sprite", "i": int(i), "x": int(x), "y": int(y)})
        return self

    def scroll(self, dx, dy, color=(0, 0, 0)):
        """Shift the current frame by (dx, dy), filling the vacated pixels with ``color``. Make it
        the FIRST op, then draw the newly-revealed edge — a marquee without resending the panel."""
        self._ops.append({"op": "scroll", "dx": int(dx), "dy": int(dy), "color": _rgb(color)})
        return self

    def text(self, x, y, s, color=(255, 255, 255), size=10, align="left", font=None):
        """Draw a text label. ``size`` selects a bundled CP1252 face (8–20); ``align`` is "left"
        (default) / "center" / "right" about (x, y); ``font`` (firmware 2.1) names an uploaded or
        library face — "custom" or a saved name — falling back to the built-in face if unknown."""
        op = {"op": "text", "x": int(x), "y": int(y), "s": str(s),
              "color": _rgb(color), "size": int(size)}
        if align in ("center", "right"):
            op["align"] = align
        if font:
            op["font"] = str(font)
        self._ops.append(op)
        return self

    # -- text helpers (the bundled faces are fixed-width per size) ------------
    @property
    def faces(self) -> tuple:
        """The panel's bundled text faces, smallest first."""
        return _FACES

    def face(self, size) -> int:
        """Snap `size` to the largest bundled face that fits (min 8) — a ``text`` op with a
        size off this list falls back to a small face on-device."""
        ok = [s for s in _FACES if s <= size]
        return max(ok) if ok else _FACES[0]

    def face_width(self, face) -> int:
        """The fixed glyph width of a bundled face — for laying text out before drawing it."""
        return _FACE_W.get(int(face), _FACE_W[_FACES[0]])

    def fit(self, text, maxw, maxh) -> int:
        """The largest bundled face for which ``text`` fits in ``maxw`` × ``maxh`` (min 8)."""
        best = _FACES[0]
        for f in _FACES:
            if f <= maxh and len(text) * _FACE_W[f] <= maxw:
                best = f
        return best

    def cp(self, s) -> str:
        """Keep only CP1252-representable characters — the on-device font's charset (degree
        sign and Latin accents survive; other scripts drop)."""
        return str(s).encode("cp1252", "ignore").decode("cp1252")

    def shadow_text(self, x, y, s, color, size, align="left", shadow=(0, 0, 0)):
        """A text label with a 1px drop-shadow so it stays legible over any content. ``s`` is
        filtered to the panel's charset (:meth:`cp`); an empty result draws nothing."""
        s = self.cp(s)
        if not s:
            return self
        self.text(x + 1, y + 1, s, shadow, size=size, align=align)
        self.text(x, y, s, color, size=size, align=align)
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
        # An effect draws on-device — there is no frame the companion holds for it. Drop any
        # frame a PREVIOUS frame-push app (a clock, weather) cached, or the live preview would keep
        # showing that stale frame instead of reading the running effect back off the panel.
        forget_frame(self.url)
        return play_effect(self.url, name, speed, hue, density)

    def _push_full(self, b: bytes) -> bool:
        # QOI where the wall takes it: the same picture over far less WiFi. Any encode
        # hiccup falls back to the raw frame, so a frame is never lost to compression.
        if self.can_qoi:
            try:
                return put_qoi(self.url, qoi_encode(b, self.width, self.height))
            except Exception as e:
                log.debug("canvas.frame QOI encode failed, sending raw: %s", e)
        return put_frame(self.url, b)

    def _push_rgb(self, b: bytes) -> bool:
        old = _LAST_FRAME.get(self.url)                             # the base (last frame we sent)
        n = _DELTA_N.get(self.url, 0) + 1
        _DELTA_N[self.url] = n
        # A delta needs a same-size base, and we force a full frame every _KEYFRAME_EVERY pushes so
        # a reboot or drift self-heals. Deltas never transition, which is right for an animating app.
        if (self.can_rects and old is not None and (old[0], old[1]) == (self.width, self.height)
                and n % _KEYFRAME_EVERY != 0):
            try:
                rects = diff_rects(old[2], b, self.width, self.height)
            except Exception as e:                                 # numpy missing/failed -> full frame
                log.debug("canvas delta failed, full frame: %s", e)
                rects = None
            if rects == []:                                        # identical -> panel already shows b
                _remember_frame(self.url, self.width, self.height, b)
                return True
            if rects and put_rects(self.url, rects) is True:       # a small change -> only the rects
                _remember_frame(self.url, self.width, self.height, b)
                return True
            # rects is None (too much changed), a 413, or a transient error -> full frame, which
            # also re-establishes the base after a reboot.
        ok = self._push_full(b)
        if ok:
            _remember_frame(self.url, self.width, self.height, b)
        else:
            forget_frame(self.url)                                 # panel state unknown -> next is full
        return ok

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

    def ticker(self, text, color=(255, 255, 255), speed: int = 2,
               overlay: bool = False, band: bool = True, font=None) -> bool:
        """Scroll one line of text across the panel ON-DEVICE — smooth, nothing streamed.
        Empty text hands the panel back. Needs ``canvas.can_ticker``.

        ``overlay`` (needs ``canvas.can_overlay``) composites the ticker as a lower-third band OVER
        whatever else is presenting, surviving page/mode changes until an empty text stops it;
        ``band=False`` drops the black bar. ``font`` names an uploaded/library face."""
        if not overlay:
            forget_frame(self.url)             # a full-screen ticker has no single still frame
        return put_ticker(self.url, text, color, speed, overlay=overlay, band=band, font=font)

    def transition(self, kind: str = "crossfade", ms: int = 400) -> bool:
        """Set how subsequent ``frame()`` pushes present (firmware 2.1): "none" (hard cut),
        "crossfade", "wipe" or "slide", tweened on-device over ``ms`` (100–2000). Sticky until
        changed. Needs ``canvas.can_transition``."""
        return set_transition(self.url, kind, ms)

    def readback(self, fmt: str = "rgb888"):
        """Read the lit panel back (firmware 1.19) as a PIL image, or ``None`` — a screenshot of
        whatever is on screen, including on-device effects/tickers this side never rendered.
        Read-only. Needs ``canvas.can_readback``."""
        f = get_frame(self.url, fmt)
        if not f:
            return None
        from PIL import Image
        w, h, rgb = f
        return Image.frombytes("RGB", (w, h), rgb)

    def gif(self, data) -> dict:
        """Import an animated GIF, decoded ON-DEVICE into the animation store and played at once
        (firmware 2.1) — no client-side unpacking, no frame cap beyond the panel's PSRAM. ``data``
        is the raw GIF bytes. Returns ``{ok, frames, fps}`` (or ``{}``). Needs ``canvas.can_gif``."""
        forget_frame(self.url)
        return put_gif(self.url, bytes(data))

    def save_anim(self, name) -> bool:
        """Persist whatever animation is loaded to the on-device library as ``name`` — it survives
        the reboot and replays by name (firmware 2.1). Needs ``canvas.can_anim_library``."""
        return anim_save(self.url, name)

    def play_anim(self, name) -> dict:
        """Load and play a saved library animation (firmware 2.1). Returns ``{ok, frames}``."""
        forget_frame(self.url)
        return anim_play(self.url, name)

    def delete_anim(self, name) -> bool:
        """Delete a saved library animation (firmware 2.1)."""
        return anim_delete(self.url, name)

    def upload_atlas(self, images, fmt: str = "rgb888", persist: bool = False) -> bool:
        """Make ``images`` (equal-size PIL images) the sheet the following ``sprite(i)`` calls
        blit from. Magenta (255,0,255) is transparent. Needs ``canvas.can_sprite``.

        Call it on every draw — that is the safe habit, because the library is shared. It costs
        almost nothing to do so: the sheet is named by a fingerprint of its own bytes, so identical
        tiles are uploaded ONCE and each later draw adds just a small bind op.

        ``persist=True`` for a sheet whose CONTENT is stable across the session (an app's own icon
        set, say — but NOT a scoreboard's per-matchup logos): it is saved to the wall's flash once,
        so it survives a reboot AND an LRU eviction by other apps' sheets, lazy-loading on the next
        bind instead of being re-uploaded."""
        try:
            imgs = [im.convert("RGB") for im in images]
            if not imgs:
                return False
            tw, th = imgs[0].width, imgs[0].height
            buf = bytearray()
            for im in imgs:
                buf += (im if (im.width, im.height) == (tw, th) else im.resize((tw, th))).tobytes()
            tiles = bytes(buf)
            name = atlas_name_for(tiles, tw, th, len(imgs), fmt)
            row = _atlas_row(self.url, name)                   # what the wall's library says about it
            if row is None and not put_atlas_named(self.url, name, tiles, tw, th, len(imgs), fmt):
                return False
            if persist and not (row or {}).get("persisted"):   # save once — skip if already on flash
                atlas_save(self.url, name)                      # marks it persisted in the cache
            # Bind for the sprites that follow. Queued with the drawing, so it costs one op rather
            # than a request, and the batch is self-contained: no reliance on a sticky earlier bind.
            self._ops.append({"op": "atlas", "name": name})
            return True
        except Exception as e:
            log.debug("canvas.upload_atlas failed: %s", e)
            return False

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
