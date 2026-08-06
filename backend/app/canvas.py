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
import socket
import struct
import threading
import time
from urllib.parse import urlparse

from . import gateway

log = logging.getLogger("companion.canvas")

# The byte encoders live in canvas_codec; re-exported here so canvas stays the
# one import apps/tests use. paneltext supplies the PIL text toolkit mixin.
from . import paneltext  # noqa: E402
from .paneltext import _FONT_DIR  # noqa: F401,E402  (tests + channel art import it from here)
from .canvas_codec import (_OPCODE, _bi16, _brgb, _bu8, _opc, _rgb,  # noqa: F401,E402
                           _rgb565_be, _rgb565_to_888, _s8, encode_ops_bin,
                           qoi_encode)

# A real anti-aliased font, bundled once in the backend so every canvas app can
# draw smooth text (via canvas.font) without carrying its own copy. Ships with
# the image through `COPY backend/` (see Dockerfile).

_KEYFRAME_EVERY = 20
_READBACK_TTL = 1.0


def _keyframe_every(w: int, h: int) -> int:
    """How many pushes between self-heal keyframes. On an LED-sized panel a full frame is
    ~16-48 KB — cheap enough to resend every 20th push. On an LCD-sized one a full frame is
    megabytes (raw over the stream, ~1 MB as QOI), so keyframes stretch out proportionally:
    drift still heals, just on a tens-of-seconds cadence instead of every couple of seconds."""
    return _KEYFRAME_EVERY if w * h <= 131072 else _KEYFRAME_EVERY * 10


_GTEXT_FONTS: dict = {}


def _gtext_width(s: str, size: int, face: str = "sans", tracking: int = 0) -> float:
    """Pixel width of ``s`` at ``size`` px in the gtext face — measured with the companion's
    copy of the bundled TrueType (the same DejaVuSans-Bold the wall rasterizes), so an app's
    wrap/fit math agrees with the glass. Mono falls back to sans until a mono face is bundled
    (matching the wall's own fallback)."""
    from PIL import ImageFont
    name = "DejaVuSansMono-Bold.ttf" if face == "mono" else "DejaVuSans-Bold.ttf"
    key = (name, max(4, int(size)))
    f = _GTEXT_FONTS.get(key)
    if f is None:
        try:
            f = ImageFont.truetype(os.path.join(_FONT_DIR, name), key[1])
        except Exception:
            f = ImageFont.truetype(os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf"), key[1])
        _GTEXT_FONTS[key] = f
    text = str(s)
    w = f.getlength(text)
    if tracking and len(text) > 1:
        w += int(tracking) * (len(text) - 1)
    return w


def _wrap_gtext(s, max_w, size, max_lines=3, face="sans"):
    """Word-wrap ``s`` to ``max_w`` at ``size`` in the gtext face, honoring explicit newlines,
    capped at ``max_lines`` (extra words are dropped — the caller shrinks the size to fit them)."""
    lines, cur = [], ""
    for para in str(s).split("\n"):
        for word in para.split():
            cand = (cur + " " + word).strip()
            if not cur or _gtext_width(cand, size, face) <= max_w:
                cur = cand
            else:
                lines.append(cur)
                cur = word
                if len(lines) >= max_lines:
                    return lines[:max_lines]
        if cur:
            lines.append(cur)
            cur = ""
            if len(lines) >= max_lines:
                break
    return lines[:max_lines]


def _fit_wrap_gtext(s, max_w, max_h, max_lines, face, text_max, lo=8, lh_frac=1.18):
    """The largest gtext ``size`` at which ``s`` wraps into ≤ ``max_lines`` that fit ``max_h``
    tall AND carry every word — returns ``(size, lines)``. The on-device analogue of wrap_fit."""
    total_words = len(str(s).replace("\n", " ").split())
    size = min(int(max_h), text_max or 512)
    while size >= lo:
        lines = _wrap_gtext(s, max_w, size, max_lines, face)
        fits_h = len(lines) * int(size * lh_frac) <= max_h
        keeps_all = sum(len(ln.split()) for ln in lines) >= total_words
        # A word wider than the column can't be wrapped — _wrap_gtext puts it on its own
        # line still overwide, and without this check the fit ACCEPTED that overflow
        # ("AL-FITR" ran off a 256-wide holiday card). Keep shrinking until every line
        # genuinely fits; at the lo floor the overflow is the caller's to ellipsize.
        fits_w = all(_gtext_width(ln, size, face) <= max_w for ln in lines)
        if fits_h and keeps_all and fits_w:
            return size, lines
        size = size - 1 if size <= lo + 6 else int(size * 0.9)
    return lo, _wrap_gtext(s, max_w, lo, max_lines, face)


class _Wall:
    """Everything the companion believes about ONE gateway's panel, in one object — the last frame
    it pushed, the delta counter, cached readbacks, sim mode, the atlas-library belief, the live
    draw stream and the last push kind. One owner per URL, so every teardown path clears the same
    complete field set instead of remembering which of several module dicts to touch."""

    def __init__(self):
        self.last_frame = None      # (width, height, rgb888 bytes) — also the base for delta frames.
                                    # Only frame-push apps land here; the live preview / HA board
                                    # image read it (they otherwise show the bypassed flap grid).
        self.delta_n = 0            # frames since the last base reset; a full keyframe every
                                    # _KEYFRAME_EVERY pushes self-heals a gateway reboot or drift
        self.readback = {}          # (url, scale, fmt) -> (monotonic, png|None), briefly cached so
                                    # the browser can poll an on-device effect preview freely
        self.sim = False            # sim mode: frames are cached for the preview, nothing is sent
        self.atlas = None           # {"at": monotonic, "rows": {name: library_row}} — see the
                                    # sprite-sheet notes below
        self.stream = None          # the live CanvasStream (persistent draw channel), if open
        self.last_kind = None       # "frame" | "ops" | "opsb" — what the app last pushed
                                    # (the engine's stream-adoption heuristic)

    def forget_frame(self):
        self.last_frame = None
        self.delta_n = 0
        self.readback.clear()       # so an effect switch never previews stale pixels


_WALLS: dict = {}                   # url -> _Wall


def _wall(url: str) -> _Wall:
    w = _WALLS.get(url)
    if w is None:
        w = _WALLS[url] = _Wall()
    return w


def set_sim(url: str, on: bool) -> None:
    if not url:
        return
    _wall(url).sim = bool(on)


def _remember_frame(url: str, w: int, h: int, rgb: bytes) -> None:
    if url and len(rgb) == w * h * 3:
        _wall(url).last_frame = (int(w), int(h), rgb)


def forget_frame(url: str) -> None:
    _wall(url).forget_frame()


# The wall keeps a NAMED library of sprite sheets — several under one budget, addressed by name,
# optionally persisted. We name a sheet by a fingerprint of its own bytes, so "the wall lists this
# name" *is* "those exact tiles are loaded": no hashing on the device, no generation counter,
# nothing to go stale. A sheet is uploaded once and every later draw costs one small
# {"op":"atlas","name":…} bind instead of re-sending ~8 KB of tiles.
#
# The wall can still drop a sheet under us (a reboot, or LRU eviction by other sheets), and a
# `sprite` with nothing bound silently draws nothing — so the belief is re-checked against the
# device's own library on a wall-clock bound. That check is a small JSON GET, not a re-upload.
_ATLAS_VERIFY_S = 60.0
_ATLAS_NAME_MAX = 32            # firmware: [a-z0-9._-]{1,32}


def atlas_name_for(tiles: bytes, tile_w: int, tile_h: int, fmt: str = "rgb888") -> str:
    """A wall-legal sheet name that IS the content fingerprint, so presence in the wall's library
    means exactly these tiles are loaded. Charset/length match the firmware's rule."""
    digest = hashlib.blake2b(bytes(tiles), digest_size=9).hexdigest()
    f = "5" if str(fmt) == "rgb565" else "8"
    return f"c{int(tile_w)}x{int(tile_h)}.{f}{digest}"[:_ATLAS_NAME_MAX]


def forget_atlas(url: str) -> None:
    """Drop what we believe the wall holds — the next draw re-checks and re-uploads if needed."""
    _wall(url).atlas = None


def has_frame(url: str) -> bool:
    return _wall(url).last_frame is not None


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
    f = _wall(url).last_frame
    return _frame_png(*f, scale=scale) if f else None


def readback_png(url: str, scale: int = 1, fmt: str = "rgb565", down: int = 1):
    """Read the lit panel back and return it as PNG bytes, or None. This is what
    lets the live preview show on-device content — an effect, a ticker, an animation — that the
    companion never rendered a frame for, so :func:`last_frame_png` has nothing cached.

    ``fmt`` defaults to rgb565 — a third less over WiFi than rgb888, and the panel's real depth
    anyway. ``down`` requests a 1/N on-wire readback (see :func:`get_frame`) so polling a big panel's
    preview does not saturate the gateway's single worker; the wall returns the image already at
    that size, so there is nothing to downscale here. The result is cached ~1s: this is one gateway
    round-trip and an effect previews fine at ~1 Hz, so a browser polling faster costs nothing extra."""
    key = (url, scale, fmt, down)
    now = time.monotonic()
    hit = _wall(url).readback.get(key)
    if hit and now - hit[0] < _READBACK_TTL:
        return hit[1]
    f = get_frame(url, fmt, down=down)
    png = _frame_png(*f, scale=scale) if f else None
    _wall(url).readback[key] = (now, png)
    return png


def _ok(r) -> bool:
    return getattr(r, "status_code", 500) < 400


def set_active(url: str, active: bool, timeout: float = 5.0) -> bool:
    """Take the panel over from the reel wall (active=True) or hand it back
    (active=False). Ops/effect/frame auto-take-over too, but a driver takes it
    first so the wall is blanked before the first frame lands."""
    if _wall(url).sim:
        return True
    try:
        return _ok(gateway._request("POST", url, "/api/canvas",
                                    json={"active": bool(active)}, timeout=timeout))
    except Exception as e:
        log.debug("canvas set_active(%s) failed: %s", active, e)
        return False


def take_over(url: str, timeout: float = 5.0) -> bool:
    """Take the panel for a NEW app run — ``set_active(True)`` (the firmware clears the
    whole panel on that takeover) with one twist: a device-side renderer left by the
    PREVIOUS app must be stood down first, or it keeps painting over the newcomer. A
    looping animation or exclusive ticker re-claims the panel every frame it renders,
    so the raw takeover alone loses the fight; an effect survives ``canvasEnter``'s
    stand-down whenever canvas mode never dropped between apps.

    The stand-down (``{active: false}`` = the firmware's ``dispReturnToWall``: effect +
    anim + exclusive ticker; an empty ticker text for the overlay ticker that outlives
    even that) repaints the flap wall for a round-trip — a visible blink — so it runs
    ONLY when the state poll says a layer is actually live. The everyday app switch
    stays on the flash-free path and still gets the takeover's full clear."""
    if _wall(url).sim:
        return True
    stand_down(url, timeout=timeout)
    return set_active(url, True, timeout=timeout)


def stand_down(url: str, timeout: float = 5.0) -> bool:
    """Stop any device-side renderer (effect / looping anim / ticker) the previous app left
    running, WITHOUT claiming the panel — the quiet half of ``take_over``.

    This is the switch path's whole need: a frame/ops app's first push claims the panel itself
    (the firmware's ``canvasEnter(false)`` — park the reel renderer, keep the pixels), so pairing
    the stand-down with ``set_active(True)`` bought nothing but its side effects: the firmware
    CLEARS AND PRESENTS on that takeover, which (a) blanks the panel ~a second early — a black
    hole between apps until the newcomer's first frame lands — and (b) adds one more full-panel
    present to the switch, on hardware where every present is a visible blink. Skipping the claim
    makes an app→app switch a clean cut: the old frame holds until the new one paints, exactly
    like the wall's own UI. ``take_over`` (clear + claim) remains for the paths that WANT the
    wipe — the stop-blank, and anything that must leave the panel empty.

    The stand-down half runs only when the state poll says a renderer is actually live, so the
    everyday switch costs one GET and touches nothing."""
    if _wall(url).sim:
        return True
    st = {}
    try:
        r = gateway._request("GET", url, "/api/canvas", timeout=timeout)
        if _ok(r):
            st = r.json() if isinstance(r.json(), dict) else {}
    except Exception as e:
        log.debug("canvas state poll failed: %s", e)
    if str(st.get("effect", "none")) != "none" or st.get("anim") or st.get("ticker"):
        set_active(url, False, timeout=timeout)
        if st.get("ticker"):
            put_ticker(url, "", timeout=timeout)
        forget_frame(url)
    return True


def play_effect(url: str, effect: str, speed: int = 5, hue=None, density=None,
                params=None, timeout: float = 5.0) -> bool:
    """Start an on-device effect (plasma/fire/matrix/…), or "none" to return to the
    wall. The panel renders it itself — the companion sends one request and stops.

    Two calling styles. ``params`` (a dict) is the def-driven one: exactly those keys go on
    the wire (the caller derived them from the wall's own ``effectDefs``, so an effect gets
    the knobs it consumes and nothing else — no implicit speed). Without ``params``, the
    legacy composition applies: ``speed`` always, plus ``hue`` (0–255) / ``density`` (1–100)
    where given; omitted knobs keep the effect's own default look."""
    if _wall(url).sim:               # an on-device effect can't be simulated; don't start it
        return True
    try:
        if params is not None:
            body = {"type": str(effect), **{k: v for k, v in dict(params).items() if v is not None}}
        else:
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
# Frame/ops byte encoding lives in canvas_codec (re-exported above).


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



def _rects_body(rects: list, fmt: int = 2) -> bytes:
    """The ``PUT /api/canvas/rects`` wire body (also the payload of the stream's 0x02 record):
    big-endian ``u16 count, u8 fmt, u8 0``; then per rect ``u16 x,y,w,h`` and its pixels."""
    parts = [len(rects).to_bytes(2, "big"), bytes((int(fmt) & 0xFF, 0))]
    for x, y, w, h, px in rects:
        parts.append(int(x).to_bytes(2, "big") + int(y).to_bytes(2, "big")
                     + int(w).to_bytes(2, "big") + int(h).to_bytes(2, "big"))
        parts.append(bytes(px))
    return b"".join(parts)


# The gateway ignores the declared Content-Length and stops at the end record, but esp_http_server
# needs a body-carrying length; a large placeholder never actually flows.
_STREAM_CONTENT_LENGTH = 1 << 30


def _tlv(rtype: int, payload: bytes = b"") -> bytes:
    """One stream record: big-endian ``u8 type, u24 payloadLength, payload``."""
    return struct.pack(">B", rtype & 0xFF) + len(payload).to_bytes(3, "big") + bytes(payload)


class CanvasStream:
    """A persistent TLV draw channel to the wall (``PUT /api/canvas/stream``): one
    long-lived socket carrying draw records back-to-back, with no per-frame HTTP round trip. The
    caller opens it, sends frames/rects/ops/present as the app draws, and closes it (end record) on
    hand-back — one stream at a time per wall, so drawing REST endpoints answer 409 while it is open.

    Any socket error tears the session down (``alive`` goes False) so the caller falls back to the
    per-frame HTTP path. ``connect`` is a test seam: a zero-arg factory returning a socket-like
    object (``sendall``/``recv``/``close``); left None, it dials the gateway."""

    def __init__(self, url: str, timeout: float = 10.0, connect=None):
        self.url = url
        self.timeout = timeout
        self._connect = connect
        self.sock = None
        self.alive = False
        self.records = 0
        self._head_pending = False

    def _head(self) -> bytes:
        u = urlparse(self.url)
        host = u.hostname or ""
        hostport = f"{host}:{u.port}" if u.port else host
        return (f"PUT /api/canvas/stream HTTP/1.1\r\n"
                f"Host: {hostport}\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"Content-Length: {_STREAM_CONTENT_LENGTH}\r\n"
                f"Connection: close\r\n\r\n").encode()

    def open(self) -> bool:
        """Connect the socket. The request head is NOT sent yet — it rides the FIRST record (a bare
        body-carrying head parse-blocks esp_http_server's worker), so just call a draw method next."""
        u = urlparse(self.url)
        try:
            self.sock = (self._connect() if self._connect
                         else socket.create_connection((u.hostname, u.port or 80), self.timeout))
            self.sock.settimeout(self.timeout)
            # A SMALL send buffer, so ``writable()`` tells the truth. The OS default (hundreds
            # of KB) swallowed ~10 SECONDS of aquarium batches before the gate ever engaged:
            # the wall played a kernel-held backlog — late, and still draining long after a
            # stop (a graceful close flushes the buffer in the background; the wall kept
            # rendering ~14 s of stale frames, re-raising canvas mode over the release).
            # ~16 KB holds a batch or two: at most a moment of the scene is ever in flight.
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16384)
            except Exception:
                pass                                       # best-effort (fakes, exotic stacks)
            self.alive = True
            self._head_pending = True
            return True
        except Exception as e:
            log.debug("canvas stream open failed: %s", e)
            self._kill()
            return False

    def _send(self, rec: bytes) -> bool:
        if not self.alive:
            return False
        try:
            self.sock.sendall((self._head() + rec) if self._head_pending else rec)
            self._head_pending = False
            self.records += 1
            return True
        except Exception as e:
            log.debug("canvas stream send failed: %s", e)
            self._kill()
            return False

    def writable(self) -> bool:
        """Whether the socket can take another record RIGHT NOW without blocking.

        The wall drains the stream at its own render pace (measured ~2 records/s on the
        1280x800 LCD under the aquarium); a producer running faster does not get errors —
        ``sendall`` just parks the excess in the OS socket buffer, which silently holds
        SECONDS of stale frames. The panel then plays a growing backlog: late, jerky, and
        still animating long after the app stopped. A caller that finds the pipe full
        should SKIP its frame — the next one supersedes it — instead of queueing it.
        The head-carrying first record always reports writable (it opens the request);
        errors report True and let the send surface the real failure."""
        if not self.alive or self.sock is None or self._head_pending:
            return True
        try:
            import select
            return bool(select.select([], [self.sock], [], 0)[1])
        except Exception:
            return True

    def frame(self, fmt: int, pixels: bytes) -> bool:            # 0x01 full frame (fmt 2=rgb565, 3=rgb888)
        return self._send(_tlv(0x01, bytes((int(fmt) & 0xFF,)) + bytes(pixels)))

    def rects(self, rects: list, fmt: int = 2) -> bool:          # 0x02 rect deltas
        return self._send(_tlv(0x02, _rects_body(rects, fmt)))

    def ops(self, ops_json: bytes) -> bool:                      # 0x03 ops JSON (presents via its show op)
        return self._send(_tlv(0x03, ops_json))

    def opsb(self, payload: bytes) -> bool:                      # 0x06 binary ops (presents via SHOW)
        return self._send(_tlv(0x06, bytes(payload)))

    def bind(self, name: str) -> bool:                           # 0x04 bind a named atlas sheet
        return self._send(_tlv(0x04, str(name).encode()))

    def present(self) -> bool:                                   # 0x05 present the back buffer
        return self._send(_tlv(0x05))

    def close(self) -> int | None:
        """Send the end record (0x00) and drop the socket — the gateway ends the stream on that
        record, so we don't block on its reply (hand-back must be instant, or the next canvas app's
        draws 409 against a still-open stream). Returns the record count, or None if never opened.
        Safe to call more than once."""
        if not self.sock:
            return None
        sent = self.records
        try:
            if self.alive and not self._head_pending:           # nothing to end if no record ever went
                self.sock.sendall(_tlv(0x00))
        except Exception as e:
            log.debug("canvas stream close: %s", e)
        finally:
            self._kill()
        return sent

    def _kill(self) -> None:
        self.alive = False
        self._head_pending = False
        try:
            if self.sock:
                # ABORTIVE close (SO_LINGER 0 -> RST): anything still unsent dies with the
                # stream. A graceful close instead FLUSHES the buffer in the background —
                # the wall went on rendering seconds of a stopped app's stale frames, each
                # record re-raising canvas mode over the hand-back, so the flap wall never
                # came back. A dead stream's frames must die with it: a stop is final, and
                # a re-adopted stream always opens with a fresh keyframe anyway.
                try:
                    self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                         struct.pack("ii", 1, 0))
                except Exception:
                    pass                               # best-effort (fakes, exotic stacks)
                self.sock.close()
        except Exception:
            pass
        self.sock = None





def stream_begin(url: str) -> bool:
    """Open (or reuse) the persistent draw stream for this wall; True if a live stream now exists.
    The caller (engine) opens it for a fast frame-push app on a wall advertising canvas.stream; every ``_push_rgb`` then
    routes through it until ``stream_end``."""
    st = _wall(url).stream
    if st is not None and st.alive:
        return True
    st = CanvasStream(url)
    if st.open():
        _wall(url).stream = st
        # A fresh (re)adopted stream can't trust the last frame as a delta base: the wall may
        # have rebooted while the socket was down. Drop it so the first push over the new stream
        # is a full — the one keyframe a stream needs (the periodic ones are suppressed).
        forget_frame(url)
        log.info("canvas %s: draw stream opened", url)
        return True
    _wall(url).stream = None
    return False


def stream_end(url: str) -> None:
    """Close the wall's draw stream (end record + socket) so the drawing REST endpoints unlock.
    Always called on canvas hand-back; a no-op if none is open."""
    w = _wall(url)
    st, w.stream = w.stream, None
    if st is not None:
        st.close()
        log.info("canvas %s: draw stream closed (%d records)", url, st.records)


def has_stream(url: str) -> bool:
    st = _wall(url).stream
    return st is not None and st.alive


def last_push_was_opsb(url: str) -> bool:
    """Whether the app's last push was a BINARY ops batch — the cue that it can ride the
    draw stream's 0x06 record (its whole vocabulary is stream-representable)."""
    return _wall(url).last_kind == "opsb"


def last_push_was_frame(url: str) -> bool:
    """Whether the app's most recent push was a full/delta frame (vs an ops batch). The engine
    adopts the draw stream for frame-push apps — and, via ``last_push_was_opsb``, for apps whose
    ops batches ride the binary encoding; JSON-ops apps stay on HTTP (an open stream would 409
    their atlas uploads)."""
    return _wall(url).last_kind == "frame"


def put_rects(url: str, rects: list, fmt: int = 2, timeout: float = 10.0):
    """Draw several changed rectangles over the live frame in one request (PUT
    /api/canvas/rects). ``rects`` is ``[(x, y, w, h, pixel_bytes)]`` with pixels in ``fmt`` (2 = rgb565
    big-endian, 3 = rgb888), row-major. Header (big-endian): u16 count, u8 fmt, u8 0; then per
    rect u16 x,y,w,h and the pixels. Returns True (drawn), "toobig" on 413 (send a full frame
    instead), or False on any other error."""
    try:
        r = gateway._request("PUT", url, "/api/canvas/rects", content=_rects_body(rects, fmt),
                             headers={"Content-Type": "application/octet-stream"}, timeout=timeout)
        if getattr(r, "status_code", 0) == 413:
            return "toobig"
        return _ok(r)
    except Exception as e:
        log.debug("canvas put_rects failed: %s", e)
        return False


def diff_rects(old_rgb: bytes, new_rgb: bytes, w: int, h: int, max_frac: float = 0.5):
    """Dirty rectangles between two rgb888 frames: the changed rows group into contiguous
    bands, and within each band the changed columns split into runs (small gaps merged), one
    rect per run — so two sprites moving at opposite edges cost two sprite-sized rects, not
    one band spanning the whole panel width. Returns ``[]`` when the frames are identical,
    ``None`` when more than ``max_frac`` of the panel changed (caller: push a full frame),
    else ``[(x, y, w, h, rgb565_be_bytes)]``. numpy-vectorised; the caller falls back to a
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
    # Column runs closer than this merge into one rect: each rect costs an 8-byte header
    # plus per-row seek on the wall, so hairline gaps aren't worth splitting over.
    gap = max(4, w // 64)
    rects, area = [], 0
    for y0, y1 in zip(starts.tolist(), ends.tolist()):
        colmask = changed[y0:y1 + 1].any(axis=0)
        cols = np.nonzero(colmask)[0]
        cbreaks = np.nonzero(np.diff(cols) > gap)[0]
        cstarts = np.concatenate(([cols[0]], cols[cbreaks + 1]))
        cends = np.concatenate((cols[cbreaks], [cols[-1]]))
        if cstarts.size > 24:                    # rect spam: one band rect beats 25 headers
            cstarts, cends = cstarts[:1], cends[-1:]
        for x0, x1 in zip(cstarts.tolist(), cends.tolist()):
            rw, rh = x1 - x0 + 1, y1 - y0 + 1
            area += rw * rh
            if area > max_frac * w * h or len(rects) >= 128:   # firmware takes ≤256/body
                return None
            rects.append((x0, y0, rw, rh, _rgb565_be(new[y0:y1 + 1, x0:x1 + 1])))
    return rects


def put_ticker(url: str, text: str, color=(255, 255, 255), speed: int = 2,
               overlay: bool = False, band: bool = True, font: str | None = None,
               timeout: float = 5.0) -> bool:
    """Scroll one line of text across the panel ON-DEVICE (POST /api/canvas/ticker) — smooth,
    nothing streamed. Empty text hands the panel back. Speed 1–20.

    ``overlay`` composites the ticker as a lower-third band OVER whatever else is
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


def get_frame(url: str, fmt: str = "rgb888", timeout: float = 8.0, down: int = 1):
    """Read the lit panel back (GET /api/canvas/frame) — a screenshot of whatever
    is on screen: the flap wall, an effect, an animation, a ticker or a pushed frame. Returns
    ``(width, height, rgb888 bytes)`` or ``None``. In sim, there is no panel to read. Read-only,
    so a live preview can poll it. The panel's real bit depth is baked in (it is what is
    physically lit); brightness is not in the framebuffer, so a dim wall reads back at full value.

    ``down`` (N ≥ 2) asks the wall for a 1/N-scaled readback (``&scale=N`` — every N-th pixel), so a
    1280×800 panel comes back at (1280/N)×(800/N) and ~1/N² the bytes. That matters because the
    gateway serves on a SINGLE worker: a full 2 MB readback holds it for ~2 s and every other
    request (the liveness poll included) queues behind it and the wall reads "offline." The
    downscale is a fast on-device copy of a still-full-res snapshot. The wall reports the ACTUAL
    dimensions in the X-Canvas-* headers, so a gateway that ignores ``scale`` (returns full-res) is
    handled transparently — sending it is always safe."""
    if _wall(url).sim:               # sim: no real panel to screenshot
        return None
    try:
        f = "rgb565" if str(fmt) == "rgb565" else "rgb888"
        q = f"/api/canvas/frame?fmt={f}"
        if int(down) > 1:
            q += f"&scale={int(down)}"
        r = gateway._request("GET", url, q, timeout=timeout)
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


def set_transition(url: str, kind: str = "crossfade", ms: int = 400, timeout: float = 5.0) -> bool:
    """Set how subsequent full-frame PUTs present (POST /api/canvas/transition):
    ``none`` (hard cut), ``crossfade``, ``wipe`` or ``slide``, tweened on-device over ``ms``
    (100–2000). Sticky and persisted on the gateway (restored at boot). rect/qoi/anim are
    unaffected."""
    try:
        k = kind if kind in ("none", "crossfade", "wipe", "slide") else "crossfade"
        body = {"type": k, "ms": max(100, min(2000, int(ms)))}
        return _ok(gateway._request("POST", url, "/api/canvas/transition", json=body, timeout=timeout))
    except Exception as e:
        log.debug("canvas set_transition failed: %s", e)
        return False


def put_gif(url: str, data: bytes, timeout: float = 30.0):
    """Import an animated GIF (PUT /api/canvas/gif) — decoded ON-DEVICE into the
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


def _anim_op(url: str, op: str, name: str | None = None, timeout: float = 15.0, *,
             path: str | None = None):
    """POST /api/canvas/anim/<op> — save/play/delete a library animation by ``name``, or
    stream one straight off the microSD card by ``path``. Returns the JSON reply
    (``play`` reports ``frames``) or ``{}``."""
    try:
        body = {"path": str(path)} if path else {"name": str(name)}
        r = gateway._request("POST", url, f"/api/canvas/anim/{op}",
                             json=body, timeout=timeout)
        return r.json() if _ok(r) else {}
    except Exception as e:
        log.debug("canvas anim/%s(%s) failed: %s", op, path or name, e)
        return {}


def anim_save(url: str, name: str) -> bool:
    """Persist whatever animation is loaded to the on-device library as ``name``."""
    return bool(_anim_op(url, "save", name).get("ok"))


def anim_play(url: str, name: str | None = None, *, path: str | None = None):
    """Load and play a saved library animation — or, with ``path=``, stream
    an MPGA off the microSD card. Returns ``{ok, frames}`` or ``{}``."""
    return _anim_op(url, "play", name, path=path)


def anim_delete(url: str, name: str) -> bool:
    """Delete a library animation."""
    return bool(_anim_op(url, "delete", name).get("ok"))


def anim_list(url: str, timeout: float = 8.0) -> list:
    """The on-device animation library (GET /api/canvas/anims): a list of
    ``{name, bytes, frames, w, h, fps, loop}``. ``[]`` on any wall that lacks it."""
    try:
        r = gateway._request("GET", url, "/api/canvas/anims", timeout=timeout)
        doc = r.json() if _ok(r) else []
        return doc if isinstance(doc, list) else []
    except Exception:
        return []


def put_font(url: str, data: bytes, timeout: float = 10.0):
    """Install a packed ``MPFT`` font into the wall's ``custom`` slot (PUT /api/canvas/font,
    ). Returns ``{ok, font, w, h, ascent}`` or ``{}``. Persist it with
    :func:`font_save`; then name it in a ticker or the ``text`` op's ``font`` field."""
    try:
        r = gateway._request("PUT", url, "/api/canvas/font", content=bytes(data),
                             headers={"Content-Type": "application/octet-stream"}, timeout=timeout)
        return r.json() if _ok(r) else {}
    except Exception as e:
        log.debug("canvas put_font(%d bytes) failed: %s", len(data), e)
        return {}


def font_save(url: str, name: str) -> bool:
    """Persist the loaded custom font to the library as ``name``."""
    try:
        r = gateway._request("POST", url, "/api/canvas/font/save", json={"name": str(name)}, timeout=8.0)
        return _ok(r)
    except Exception as e:
        log.debug("canvas font_save(%s) failed: %s", name, e)
        return False


def font_delete(url: str, name: str) -> bool:
    """Delete a library font."""
    try:
        r = gateway._request("POST", url, "/api/canvas/font/delete", json={"name": str(name)}, timeout=8.0)
        return _ok(r)
    except Exception as e:
        log.debug("canvas font_delete(%s) failed: %s", name, e)
        return False


def font_list(url: str, timeout: float = 8.0) -> list:
    """The on-device font library (GET /api/canvas/fonts): ``{name, bytes, w, h,
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
    """Upload one NAMED sheet into the wall's atlas library (PUT /api/canvas/atlas/<name>). Same MPTA body as the unnamed route; the name is the address a later
    ``{"op":"atlas"}`` binds."""
    try:
        # The upload is one of the REST drawing endpoints the firmware 409s while a draw
        # stream is open. A sprite app CAN be streaming (binds ride record 0x04) — so if it
        # suddenly needs a re-upload (its sheet was evicted), close the stream first; the
        # engine re-adopts on its next tick and the stream picks back up.
        if has_stream(url):
            log.info("canvas %s: closing draw stream for atlas '%s' upload", url, name)
            stream_end(url)
        body = _atlas_body(tiles, tile_w, tile_h, count, fmt)
        ok = _ok(gateway._request("PUT", url, f"/api/canvas/atlas/{name}",
                                  content=body,
                                  headers={"Content-Type": "application/octet-stream"},
                                  timeout=timeout))
        if ok:
            log.info("canvas %s: atlas '%s' sprites uploaded, %d tile(s) %d B", url, name, count, len(body))
            w = _wall(url)
            if w.atlas is None:
                w.atlas = {"at": time.monotonic(), "rows": {}}
            rows = w.atlas["rows"]
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
    if has_stream(url):                    # same REST-vs-stream 409 as the upload above
        stream_end(url)
    ok = _ok(gateway._request("POST", url, f"/api/canvas/atlas/{name}/save", timeout=timeout))
    if ok:
        row = ((_wall(url).atlas or {}).get("rows") or {}).get(name)
        if row:
            row["persisted"] = True
    return ok


def _atlas_lib(url: str) -> dict:
    """``{name: row}`` of the wall's atlas library from what we last saw, re-reading it when the
    belief is older than the verify window — a sheet can be evicted or lost to a reboot under us.

    NOT while a draw stream is open, though: the re-read is a GET, and the drawing REST endpoints
    answer 409 while streaming, so the GET comes back empty and would wrongly drop every sheet's
    residency — forcing a full re-upload (a few hundred KB to megabytes on an LCD) and a stream
    close/reopen churn every verify window. A streaming sprite app (the aquarium re-asserting its
    sheet each frame) keeps the cached belief until its stream closes; the sheet is persisted, so
    it can't actually vanish under us mid-stream."""
    e = _wall(url).atlas
    now = time.monotonic()
    if (e is None or now - e["at"] > _ATLAS_VERIFY_S) and not has_stream(url):
        rows = {str(a["name"]): a for a in atlas_list(url) if isinstance(a, dict) and a.get("name")}
        _wall(url).atlas = e = {"at": now, "rows": rows}
    return e["rows"] if e is not None else {}


def _atlas_row(url: str, name: str):
    """The library row for ``name`` (with its ``persisted``/``resident`` flags), or None if the
    wall doesn't have it — in which case the caller uploads."""
    return _atlas_lib(url).get(name)


def release(url: str, timeout: float = 5.0) -> bool:
    """Return the panel to the reel wall — stop any effect AND drop raw-canvas mode.
    Called when a canvas app is replaced by an ordinary flap app, or stopped."""
    forget_frame(url)                      # the preview is no longer live
    # The atlas library is NOT touched: the wall keeps its sheets across uses, so a playlist
    # cycling back to a canvas app re-binds by name rather than re-uploading.
    stopped = play_effect(url, "none", timeout=timeout)   # effect none marks the wall dirty
    active_off = set_active(url, False, timeout=timeout)   # and drop raw-canvas takeover
    return stopped or active_off




# The panel's bundled text faces and their fixed glyph widths. A `text` op with a `size`
# outside this set falls back to a small 6x10 face on-device, so apps snap to these.
_FACES = (8, 9, 10, 13, 18, 20)
_FACE_W = {8: 5, 9: 6, 10: 6, 13: 8, 18: 9, 20: 10}


def play_sound(url: str, notes=None, freq=None, ms: int = 120, vol: int = 70,
               wav: str | None = None) -> bool:
    """Play a tone or note sequence on the wall's speaker (POST /api/sound) — or,
    with ``wav=``, stream a WAV file off the wall's microSD card (strict 16-bit
    16 kHz PCM). Fire and FORGET: the POST runs on a short-lived daemon thread so a game's
    render tick never waits on audio, and any failure (Quiet Time 409, speaker off 403, no
    card 503) is swallowed — sound is never allowed to stall or break a frame. ``notes``
    is ``[[freq, ms], …]`` (freq 0 = rest); or pass a single ``freq``/``ms``. Returns
    whether a play was dispatched (not whether the wall accepted it)."""
    if _wall(url).sim:
        return False
    body = {"vol": max(0, min(100, int(vol)))}
    if wav:
        body["wav"] = str(wav)
    elif notes:
        body["notes"] = [[int(f), int(d)] for f, d in notes]
    elif freq:
        body["freq"], body["ms"] = int(freq), max(1, min(2000, int(ms)))
    else:
        return False

    def _fire():
        try:
            gateway._request("POST", url, "/api/sound", json=body, timeout=2.0)
        except Exception as e:
            log.debug("play_sound failed: %s", e)

    threading.Thread(target=_fire, daemon=True).start()
    return True


def sd_list(url: str, path: str = "/", timeout: float = 8.0) -> list:
    """One directory level of the wall's microSD card (the wall's ``sd`` feature):
    ``[{"name", "dir", "size"}, ...]``, or ``[]`` on any failure (no card, bad path,
    unreachable). Read-only and safe to poll gently — the card is the gateway's."""
    try:
        r = gateway._request("GET", url, "/api/sd/list", params={"path": path or "/"},
                             timeout=timeout)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        log.debug("canvas sd_list(%s) failed: %s", path, e)
        return []


def sd_get(url: str, path: str, timeout: float = 20.0) -> bytes | None:
    """A file's raw bytes off the wall's microSD card, or None. The generous timeout is
    for multi-MB photos over the ESP32's HTTP server."""
    try:
        r = gateway._request("GET", url, "/api/sd/get", params={"path": path},
                             timeout=timeout)
        return r.content if r.status_code == 200 else None
    except Exception as e:
        log.debug("canvas sd_get(%s) failed: %s", path, e)
        return None


def post_ops_bin(url: str, data: bytes, timeout: float = 8.0) -> bool:
    """Apply a binary op batch (POST /api/canvas/opsb)."""
    try:
        return _ok(gateway._request("POST", url, "/api/canvas/opsb", content=bytes(data),
                                    headers={"Content-Type": "application/octet-stream"},
                                    timeout=timeout))
    except Exception as e:
        log.debug("canvas post_ops_bin(%d bytes) failed: %s", len(data), e)
        return False



def _with_t(op, t):
    """Attach stroke thickness to a drawing op when the caller asks for one."""
    if int(t) > 1:
        op["t"] = int(t)
    return op


def _with_aa(op, aa):
    """Attach the anti-alias flag to a stroke op when asked."""
    if aa:
        op["aa"] = True
    return op


class CanvasSurface(paneltext.PanelText):
    """The drawing surface an app receives as its ``canvas`` helper. Draw calls
    accumulate ops; ``show()`` sends the batch and presents it. The panel is the
    real size — ``canvas.width`` × ``canvas.height`` pixels — not the flap grid,
    so an app draws in pixels.

    On a wall with no canvas (a physical split-flap), the helper is not injected
    at all, so an app that wants it declares ``canvas`` and checks it for None."""

    def __init__(self, url: str, caps):
        """``caps`` is the wall's ``device.Capabilities`` — the surface derives its size, formats
        and every ``can_*`` gate from it in this one place (rather than threading ~16 booleans
        through a constructor). Apps keep reading the plain ``can_*`` attributes."""
        self.url = url
        self.caps = caps
        self.width = int(caps.canvas_w)
        self.height = int(caps.canvas_h)
        self.formats = tuple(caps.canvas_formats)
        self.effects = tuple(caps.effects)
        # Newer-firmware canvas extras (see device.Capabilities). An app checks these before
        # reaching for ticker()/anim()/paste() so it can fall back on an older wall.
        self.can_qoi = "qoi" in self.formats
        self.can_rect = bool(caps.canvas_rect)
        self.can_rects = bool(caps.canvas_rects)   # 3.1: frame() sends only the changed rects (delta)
        self.can_stream = bool(caps.canvas_stream)  # 3.2: PUT /api/canvas/stream — persistent TLV channel
        self.can_anim = bool(caps.canvas_anim)
        self.can_ticker = bool(caps.canvas_ticker)
        self.effect_params = tuple(caps.effect_params)
        self.effect_defs = tuple(caps.effect_defs or ())
        # The wall's draw-op vocabulary, verbatim (capabilities canvas.ops). has_op() is
        # the per-op gate; can_text_styles keys off ``textbox`` in the vocabulary
        # (the styled-text ops travel with it).
        self.op_names = tuple(caps.canvas_ops)
        self.can_text_styles = "textbox" in self.op_names
        # Scalable on-device TrueType text (the gtext op) + the box-blur op — what lets a text
        # app draw as ops rather than pushing pixel frames. text_max is the px size ceiling.
        self.can_gtext = bool(getattr(caps, "canvas_gtext", False))
        self.text_max = int(getattr(caps, "canvas_text_max", 0) or 0)
        self.text_faces = tuple(getattr(caps, "canvas_text_faces", ()) or ())
        self.can_blur = bool(getattr(caps, "canvas_blur", False))
        self.can_ops_bin = bool(caps.canvas_ops_bin)     # the binary ops encoding, whole
        self.can_composite = bool(caps.canvas_composite)   # canvas.compositing: alpha, blend, AA
        self.can_sd = bool(caps.can_sd)                    # "sd" feature: a microSD card is mounted
        # MPGA animations stream straight off the card (no PSRAM cap) — needs the card.
        self.can_sd_anim = self.can_sd and bool(caps.canvas_anim)
        # aa renders wherever the wall composites; the binary format carries it natively,
        # so it never costs the fast path. (Kept as an attribute — it is the app ABI.)
        self.aa_ok = bool(caps.canvas_composite)
        # 1.19 / 1.25 / 2.1. `can_ops` is "any draw ops at all" (the vocabulary itself is
        # op_names above, consulted via has_op()). The 2.1 endpoint families aren't flagged
        self.can_ops = bool(self.op_names)
        self.can_readback = bool(caps.canvas_readback)
        # The overlay/transition/anim-library/GIF/font endpoint families: every current
        # Matrix firmware has them, so they simply come with the framebuffer. (Kept as
        # attributes — they are the app ABI.)
        self.can_overlay = self.can_transition = True
        self.can_anim_library = self.can_gif = self.can_fonts = True
        self.can_sprite = bool(caps.canvas_sprite)
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

    def rect(self, x, y, w, h, color=(255, 255, 255), fill=False, t=1):
        self._ops.append(_with_t({"op": "rect", "x": int(x), "y": int(y), "w": int(w),
                                  "h": int(h), "color": _rgb(color), "fill": bool(fill)}, t))
        return self

    def line(self, x, y, x1, y1, color=(255, 255, 255), t=1, aa=False):
        self._ops.append(_with_aa(_with_t({"op": "line", "x": int(x), "y": int(y), "x1": int(x1),
                                          "y1": int(y1), "color": _rgb(color)}, t), aa))
        return self

    def circle(self, x, y, r, color=(255, 255, 255), fill=False, t=1, aa=False):
        self._ops.append(_with_aa(_with_t({"op": "circle", "x": int(x), "y": int(y), "r": int(r),
                                          "color": _rgb(color), "fill": bool(fill)}, t), aa))
        return self

    def ellipse(self, x, y, rx, ry, color=(255, 255, 255), fill=False, t=1):
        self._ops.append(_with_t({"op": "ellipse", "x": int(x), "y": int(y), "rx": int(rx),
                                  "ry": int(ry), "color": _rgb(color), "fill": bool(fill)}, t))
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

    def polyline(self, points, color=(255, 255, 255), t=1, aa=False):
        """Connect ``points`` — a list of (x, y) — with lines."""
        self._ops.append(_with_aa(_with_t({"op": "polyline", "color": _rgb(color),
                                          "points": [[int(px), int(py)] for px, py in points]}, t), aa))
        return self

    def sprite(self, i, x, y, flip=None, rot=None, scale=1):
        """Blit tile ``i`` of the uploaded atlas (see :meth:`upload_atlas`) at (x, y). Magenta is
        transparent. Needs ``canvas.can_sprite``. Transforms: ``flip`` "h"/"v"/"hv",
        ``rot`` 90/180/270, ``scale`` ≥ 1 — one sheet serves every orientation. A whole 1–4 takes
        the fast integer blit; a fractional value (e.g. 2.5) scales smoothly via the SPRITE2 op."""
        op = {"op": "sprite", "i": int(i), "x": int(x), "y": int(y)}
        if flip in ("h", "v", "hv"):
            op["flip"] = flip
        if rot in (90, 180, 270):
            op["rot"] = int(rot)
        s = float(scale)
        if s != 1.0:
            # keep a whole 1–4 as an int (the JSON/fast-path form); a fractional or larger value
            # stays a float so the codec emits SPRITE2 and the JSON op carries the exact scale.
            op["scale"] = int(s) if (s.is_integer() and 1 <= s <= 4) else s
        self._ops.append(op)
        return self

    def blend(self, mode="over"):
        """Set the batch compositing mode for the ops that follow — "over" (normal),
        "add" (additive; the LED-glow mode where overlapping lights sum), "multiply",
        "screen" or "max". Batch-scoped: reset to "over" when done.

        No-op on a wall without ``can_composite``: its binary decoder has no blend opcode
        and cannot length-skip an unknown one, so emitting it would desync the batch.
        Callers can therefore use blend() unconditionally."""
        if self.can_composite:
            self._ops.append({"op": "blend", "mode": str(mode)})
        return self

    # -- transform stack / layers / macros / bezier (a bare host can gate on
    # ``has_op("save")``) ----------------------------------------------------------

    def save(self):
        """Push the current transform (translate/scale/rotate) — pair with restore()."""
        self._ops.append({"op": "save"})
        return self

    def restore(self):
        """Pop the transform save() pushed."""
        self._ops.append({"op": "restore"})
        return self

    def translate(self, x, y):
        """Shift the origin of the ops that follow by (x, y) pixels."""
        self._ops.append({"op": "translate", "x": int(x), "y": int(y)})
        return self

    def scale(self, sx, sy=None):
        """Scale the ops that follow (about the current origin); one factor = uniform."""
        op = {"op": "scale", "x": float(sx)}
        if sy is not None:
            op["y"] = float(sy)
        self._ops.append(op)
        return self

    def rotate(self, deg):
        """Rotate the ops that follow by ``deg`` degrees clockwise (about the origin)."""
        self._ops.append({"op": "rotate", "deg": int(deg)})
        return self

    def layer(self):
        """Begin an offscreen group: draws accumulate in a shadow buffer until
        composite() flattens them back with one group blend + opacity."""
        self._ops.append({"op": "layer"})
        return self

    def composite(self, x=0, y=0, mode="over", alpha=255):
        """Flatten the open layer() back onto the canvas at (x, y) with ``mode``
        (over/add/multiply/screen/max) and group ``alpha`` (0-255)."""
        self._ops.append({"op": "composite", "x": int(x), "y": int(y),
                          "mode": str(mode), "alpha": max(0, min(255, int(alpha)))})
        return self

    def bezier(self, points, color=(255, 255, 255), t=1, aa=False):
        """A quadratic (3 points) or cubic (4 points) bezier stroke."""
        op = {"op": "bezier", "points": [[int(px), int(py)] for px, py in points],
              "color": _rgb(color), "t": int(t)}
        if aa:
            op["aa"] = True
        self._ops.append(op)
        return self

    class _MacroCapture:
        def __init__(self, surface, name):
            self._s, self._name = surface, name

        def __enter__(self):
            self._saved, self._s._ops = self._s._ops, []
            return self._s

        def __exit__(self, exc_type, exc, tb):
            body, self._s._ops = self._s._ops, self._saved
            if exc_type is None:
                self._s._ops.append({"op": "define", "name": self._name, "ops": body})
            return False

    def define(self, name):
        """Capture a reusable op sequence: ``with canvas.define("star"): canvas.circle(…)``
        then replay it with ``call("star", x, y)`` — the batch sends the body once."""
        return self._MacroCapture(self, str(name))

    def call(self, name, x=0, y=0):
        """Replay a define()d macro translated to (x, y)."""
        self._ops.append({"op": "call", "name": str(name), "x": int(x), "y": int(y)})
        return self

    def scroll(self, dx, dy, color=(0, 0, 0)):
        """Shift the current frame by (dx, dy), filling the vacated pixels with ``color``. Make it
        the FIRST op, then draw the newly-revealed edge — a marquee without resending the panel."""
        self._ops.append({"op": "scroll", "dx": int(dx), "dy": int(dy), "color": _rgb(color)})
        return self

    def text(self, x, y, s, color=(255, 255, 255), size=10, align="left", font=None,
             aa=False, outline=None, shadow=None):
        """Draw a text label. ``size`` selects a bundled CP1252 face (8–20); ``align`` is "left"
        (default) / "center" / "right" about (x, y); ``font`` names an uploaded or
        library face — "custom" or a saved name — falling back to the built-in face if unknown.
        Styled text (see ``can_text_styles``): ``aa=True`` renders the smooth Orbitron
        faces (34/24/13 px, A–Z 0–9 ``:.-+%/`` only, folded to uppercase); ``outline``/``shadow``
        layer a 1px ring / +1,+1 drop in the given color under the bitmap-font path."""
        op = {"op": "text", "x": int(x), "y": int(y), "s": str(s),
              "color": _rgb(color), "size": int(size)}
        if align in ("center", "right"):
            op["align"] = align
        if font:
            op["font"] = str(font)
        if aa:
            op["aa"] = True
        if outline is not None:
            op["outline"] = _rgb(outline)
        if shadow is not None:
            op["shadow"] = _rgb(shadow)
        self._ops.append(op)
        return self

    def gtext(self, x, y, s, color=(255, 255, 255), size=24, align="left", face="sans",
              aa=True, outline=None, shadow=None, tracking=0):
        """Scalable, anti-aliased TrueType text — the on-device stand-in for a fitted PIL label,
        so a text app draws its type as ops instead of pushing a pixel frame. ``size`` is any
        pixel height (4…``caps.text_max``, ~512); ``(x, y)`` is the top-left of the ascent box;
        ``align`` shifts about ``x``. ``face`` is "sans" (default), "mono", or "custom" (an
        uploaded TTF). ``outline`` (1px ring) / ``shadow`` (+1,+1 drop) layer under the fill.
        The bundled face is the companion's own DejaVuSans-Bold, so PIL layout matches the glass.
        Needs ``canvas.can_gtext`` — fall back to a fitted frame where it's absent."""
        op = {"op": "gtext", "x": int(x), "y": int(y), "s": str(s),
              "color": _rgb(color), "size": int(size)}
        if align in ("center", "right"):
            op["align"] = align
        if face and face != "sans":
            op["face"] = str(face)
        if not aa:
            op["aa"] = False
        if outline is not None:
            op["outline"] = _rgb(outline)
        if shadow is not None:
            op["shadow"] = _rgb(shadow)
        if tracking:
            op["tracking"] = int(tracking)
        self._ops.append(op)
        return self

    def text_width(self, s, size=24, face="sans", tracking=0):
        """The pixel width of ``s`` at ``size`` in the gtext face — for the app's own align/wrap
        math. Measured with the companion's copy of the bundled TTF (metrics match the wall)."""
        return _gtext_width(str(s), int(size), str(face), int(tracking))

    def fit_gtext(self, s, max_w, max_h, face="sans", lo=8):
        """The largest gtext ``size`` (≤ ``max_h``, ≥ ``lo``) at which ``s`` fits ``max_w`` wide —
        the on-device analogue of :meth:`fit_font`. Capped at the wall's ``text_max``. A quick
        ratio jump then a short refine, so it works from any starting height."""
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

    def wrap_gtext(self, s, max_w, size, max_lines=3, face="sans"):
        """Word-wrap ``s`` to ``max_w`` at ``size`` in the gtext face (≤ ``max_lines``)."""
        return _wrap_gtext(str(s), max_w, int(size), int(max_lines), face)

    def fit_wrap_gtext(self, s, max_w, max_h, max_lines=3, face="sans", lo=8):
        """Largest gtext size wrapping ``s`` into ≤ ``max_lines`` that fit ``max_w``×``max_h`` and
        keep every word — the gtext analogue of ``wrap_fit``. Returns ``(size, lines)``."""
        return _fit_wrap_gtext(str(s), max_w, max_h, int(max_lines), face, self.text_max, lo)

    def blur(self, x, y, w, h, r=4):
        """Box-blur a rectangle of the back buffer in place — the on-device stand-in for the
        PIL scrim: soften busy art, then draw text on top. Needs ``canvas.can_blur``."""
        self._ops.append({"op": "blur", "x": int(x), "y": int(y), "w": int(w),
                          "h": int(h), "r": int(r)})
        return self

    def arc(self, x, y, r, start, end, color=(255, 255, 255), t=2, fill=False):
        """An arc/annulus segment — or a pie slice with ``fill`` — from ``start`` to ``end``
        degrees, 0° at 12 o'clock, clockwise; ``t`` is the ring thickness. The gauge/meter
        primitive (gate on ``has_op("arc")``)."""
        self._ops.append({"op": "arc", "x": int(x), "y": int(y), "r": int(r),
                          "start": int(start), "end": int(end), "color": _rgb(color),
                          "t": int(t), "fill": bool(fill)})
        return self

    def poly(self, points, color=(255, 255, 255), fill=True, t=1, aa=False):
        """A closed polygon (≤16 vertices): even-odd filled by default, else outlined with
        thickness ``t`` (gate on ``has_op("poly")``)."""
        op = {"op": "poly", "points": [[int(px), int(py)] for px, py in points],
              "color": _rgb(color), "fill": bool(fill)}
        if not fill and int(t) != 1:
            op["t"] = int(t)
        self._ops.append(_with_aa(op, aa))
        return self

    def clip(self, x=None, y=None, w=None, h=None):
        """Clip all later ops in this batch to the window; call with no args to clear.
        Batch-scoped on the wall (gate on ``has_op("clip")``)."""
        if x is None:
            self._ops.append({"op": "clip"})
        else:
            self._ops.append({"op": "clip", "x": int(x), "y": int(y), "w": int(w), "h": int(h)})
        return self

    def origin(self, x=None, y=None):
        """Translate every later coordinate in this batch by (x, y) — placeable components;
        no args resets. Batch-scoped on the wall (gate on ``has_op("origin")``)."""
        if x is None:
            self._ops.append({"op": "origin"})
        else:
            self._ops.append({"op": "origin", "x": int(x), "y": int(y)})
        return self

    def textbox(self, x, y, w, h, s, color=(255, 255, 255), size=10,
                align="left", valign="top", font=None):
        """Word-wrapped text inside a box, aligned on both axes, clipped to the box;
        explicit newlines honored (gate on ``has_op("textbox")``)."""
        op = {"op": "textbox", "x": int(x), "y": int(y), "w": int(w), "h": int(h),
              "s": str(s), "color": _rgb(color), "size": int(size)}
        if align in ("center", "right"):
            op["align"] = align
        if valign in ("middle", "bottom", "center"):
            op["valign"] = valign
        if font:
            op["font"] = str(font)
        self._ops.append(op)
        return self

    def has_op(self, name) -> bool:
        """Whether this wall's ops vocabulary includes ``name`` (capabilities canvas.ops)."""
        return str(name) in self.op_names

    def sd_list(self, path: str = "/") -> list:
        """This wall's microSD directory listing (``[]`` without a card) — see module sd_list."""
        return sd_list(self.url, path) if self.can_sd else []

    def sd_get(self, path: str) -> bytes | None:
        """A file's bytes off this wall's microSD card (None without a card)."""
        return sd_get(self.url, path) if self.can_sd else None

    @staticmethod
    def num(settings, key, default, lo=None, hi=None):
        """Read ``settings[key]`` as a number, tolerant of the raw-string settings contract
        ("" / junk → ``default``), clamped to [lo, hi]. Returns an int when ``default`` is an
        int (truncating, like the ``int(float(...))`` idiom it replaces), else a float.

        Matrix apps use this instead of hand-rolling the try/except clamp — it lives on the
        canvas surface (not an injected parameter) so a test or the screenshot harness can
        call ``fetch_canvas(settings, canvas)`` directly and it is simply there. The flap-side
        ``fetch()`` keeps the hand-rolled idiom: it has no canvas, and its signature is the
        portable upstream ABI (apps/README.md)."""
        try:
            v = float(settings.get(key, default) or default)
        except (TypeError, ValueError, AttributeError):
            v = float(default)
        if lo is not None:
            v = max(float(lo), v)
        if hi is not None:
            v = min(float(hi), v)
        return int(v) if isinstance(default, int) else v

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
        if self.can_text_styles:                 # text styles: the shadow is one op, not two
            return self.text(x, y, s, color, size=size, align=align, shadow=shadow)
        self.text(x + 1, y + 1, s, shadow, size=size, align=align)
        self.text(x, y, s, color, size=size, align=align)
        return self

    def show(self) -> bool:
        """Send the accumulated draw ops and present them. A no-op if nothing was drawn."""
        if not self._ops:
            return True
        ops, self._ops = self._ops + [{"op": "show"}], []
        wall = _wall(self.url)
        if wall.sim:                                  # sim: an ops app renders on-device, so it
            wall.last_kind = "ops"
            log.info("canvas %s: [sim] %d op(s) not sent", self.url, len(ops))
            return True                                 # cannot preview here; just don't drive the panel
        # Split the batch at atlas binds: binary has no bind opcode, but the draw stream
        # carries one natively (record 0x04), so a sprite app's batch is still stream+binary
        # material — [bind, ops...] becomes 0x04 + 0x06 records. Each segment is
        # (name | None, encoded-draw-run); encoding failure of any run voids the lot.
        segments = None
        if self.can_ops_bin:
            segments = []
            run = []
            pending_bind = None
            ok = True
            for op in ops:
                if op.get("op") == "atlas":
                    if run:
                        enc = encode_ops_bin(run)
                        if enc is None:
                            ok = False
                            break
                        segments.append((pending_bind, enc))
                        run, pending_bind = [], None
                    pending_bind = str(op.get("name", ""))
                else:
                    run.append(op)
            if ok and (run or pending_bind is not None):
                enc = encode_ops_bin(run)
                if enc is None:
                    ok = False
                else:
                    segments.append((pending_bind, enc))
            if not ok:
                segments = None
        # A binary-representable batch (binds included) marks the wall "opsb" — the
        # engine's stream heuristic adopts the draw stream for such apps, and every later
        # batch rides 0x04/0x06 records. While a stream is OPEN nothing may go over REST
        # (409), so a JSON-only batch is carried as the stream's 0x03 record instead.
        wall.last_kind = "opsb" if segments is not None else "ops"
        st = wall.stream
        if st is not None and st.alive:
            # Backpressure: the wall renders the stream at its own pace, and a full socket
            # means it is still chewing on earlier records. Queueing more just builds a
            # standing backlog of stale frames (late, jerky, and still playing after a
            # stop) — skip this WHOLE batch instead; the app's next redraw supersedes it.
            # All-or-nothing per batch: a frame's bind+ops segments never split.
            if not st.writable():
                log.debug("canvas %s: stream backlogged, ops batch skipped", self.url)
                return True
            if segments is not None:
                if all((seg_name is None or st.bind(seg_name)) and st.opsb(enc)
                       for seg_name, enc in segments):
                    return True
            else:
                import json
                if st.ops(json.dumps(ops).encode()):
                    return True
            # the stream died mid-send: fall through to the per-batch HTTP path
        if segments is not None and len(segments) == 1 and segments[0][0] is None:
            payload = segments[0][1]
            log.info("canvas %s: opsb %d op(s) %d B", self.url, len(ops), len(payload))
            return post_ops_bin(self.url, payload)
        # Binds (or anything else binary can't carry) over HTTP: the JSON batch, which
        # accepts the bind op inline. The opsb last_kind above still lets the engine
        # adopt the stream, where the binds switch to 0x04 records.
        if log.isEnabledFor(logging.INFO):
            import json
            log.info("canvas %s: ops %d op(s) %d B", self.url, len(ops), len(json.dumps(ops).encode()))
        return draw_ops(self.url, ops)

    # -- whole-panel content -------------------------------------------------
    def effect(self, name, speed: int = 5, hue=None, density=None, params=None) -> bool:
        """Play an on-device effect (from ``canvas.effects``); "none" returns to
        the wall. The panel renders it — one request, then nothing. ``params`` is the
        def-driven style: exactly those keys go on the wire (derived from
        ``canvas.effect_defs``). Without it, ``hue`` (0–255) and ``density`` (1–100)
        tint/seed effects that support them (``canvas.effect_params``)."""
        # An effect draws on-device — there is no frame the companion holds for it. Drop any
        # frame a PREVIOUS frame-push app (a clock, weather) cached, or the live preview would keep
        # showing that stale frame instead of reading the running effect back off the panel.
        forget_frame(self.url)
        return play_effect(self.url, name, speed, hue, density, params=params)

    def _push_full(self, b: bytes) -> bool:
        # QOI where the wall takes it: the same picture over far less WiFi. Any encode
        # hiccup falls back to the raw frame, so a frame is never lost to compression.
        if self.can_qoi:
            try:
                enc = qoi_encode(b, self.width, self.height)
                ok = put_qoi(self.url, enc)
                log.info("canvas %s: full frame (qoi) %d B", self.url, len(enc))
                return ok
            except Exception as e:
                log.debug("canvas.frame QOI encode failed, sending raw: %s", e)
        log.info("canvas %s: full frame (raw) %d B", self.url, len(b))
        return put_frame(self.url, b)

    def _stream_full(self, st, b: bytes) -> bool:
        """A full frame over the open stream: rgb565 (record 0x01, fmt 2) where numpy can pack it,
        raw rgb888 (fmt 3) otherwise — then present. False if the stream send failed (the caller
        then falls back to the HTTP path, and the now-dead stream stays closed)."""
        try:
            import numpy as np
            arr = (np.frombuffer(b, dtype=np.uint8)[:self.width * self.height * 3]
                   .reshape(self.height, self.width, 3))
            ok, fmt = st.frame(2, _rgb565_be(arr)), "rgb565"
        except Exception:
            ok, fmt = st.frame(3, b), "rgb888"
        if ok and st.present():
            log.info("canvas %s: full frame (%s, stream)", self.url, fmt)
            return True
        return False

    def _push_rgb(self, b: bytes) -> bool:
        """Deliver one rgb888 frame, cheapest way first: nothing (unchanged), delta rects, then a
        full frame — each over the persistent stream when one is open, HTTP otherwise. The pieces:
        ``_try_delta`` (the incremental path, or the reasons it can't apply) and ``_push_full_any``
        (stream-full, falling back to HTTP-full)."""
        wall = _wall(self.url)
        if wall.sim:                                       # sim: cache for the preview, drive nothing
            _remember_frame(self.url, self.width, self.height, b)
            log.info("canvas %s: [sim] frame not sent (%d B)", self.url, len(b))
            return True
        wall.last_kind = "frame"                           # this app pushes frames (stream heuristic)

        # Backpressure (see CanvasStream.writable): a full stream socket means the wall is
        # behind — skip this frame rather than queue a stale one. State untouched: the wall
        # still shows the last DELIVERED frame, so the next delta diffs against the truth.
        st = wall.stream
        if st is not None and st.alive and not st.writable():
            log.debug("canvas %s: stream backlogged, frame skipped", self.url)
            return True

        handled = self._try_delta(wall, b)
        if handled is None:                                # delta didn't apply -> a full frame
            handled = self._push_full_any(wall, b)
        if handled:
            _remember_frame(self.url, self.width, self.height, b)
        else:
            forget_frame(self.url)                         # panel state unknown -> next is full
        return handled

    def _try_delta(self, wall, b: bytes) -> bool | None:
        """The incremental path: True = delivered (or identical, nothing to send), False = a delta
        was tried and FAILED (deliver a full frame and reset the base), None = a delta doesn't
        apply here (no rects support, no same-size base, or a due keyframe) — send a full frame.

        A delta needs a same-size base. On the HTTP path a full frame goes every _keyframe_every
        pushes so a gateway reboot or drift self-heals — but NOT over an open stream: TCP delivers
        every delta or breaks the socket (which drops the base, see stream_begin), so a live stream
        can't drift, and a periodic full there is just a ~2 MB raw-rgb565 hitch for nothing. Deltas
        never transition, which is right for an animating app."""
        old = wall.last_frame                              # the base (the last frame we sent)
        wall.delta_n += 1
        st = wall.stream
        streaming = st is not None and st.alive
        keyframe_due = not streaming and wall.delta_n % _keyframe_every(self.width, self.height) == 0
        if not (self.can_rects and old is not None and (old[0], old[1]) == (self.width, self.height)
                and not keyframe_due):
            return None
        try:
            # The delta-vs-full cutover depends on what a full frame would COST here. Rects
            # carry raw rgb565 (2 B/px). Over an open stream a full is also raw rgb565 for the
            # whole panel, so a delta wins right up to half the panel (0.5). Over HTTP a full
            # goes QOI (~0.1 B/px for app content) — on an LCD-sized panel a half-panel delta
            # is ~1 MB against a tens-of-KB full, so past 15% changed the keyframe is cheaper.
            # LED-sized panels stay at 0.5 either way (even a full is only ~16-48 KB there).
            st = wall.stream
            frac = 0.5 if (self.width * self.height <= 131072
                           or (st is not None and st.alive)) else 0.15
            rects = diff_rects(old[2], b, self.width, self.height, max_frac=frac)
        except Exception as e:                             # numpy missing/failed -> full frame
            log.debug("canvas delta failed, full frame: %s", e)
            return None
        if rects == []:                                    # identical -> panel already shows b
            log.debug("canvas %s: frame unchanged, nothing sent", self.url)
            return True
        if not rects:                                      # too much changed -> full frame is cheaper
            return None
        size = 4 + sum(8 + len(px) for *_, px in rects)    # frame header + per-rect header + pixels
        st = wall.stream
        if st is not None and st.alive:
            if st.rects(rects) and st.present():
                log.info("canvas %s: incremental %d rect(s) %d B (stream)", self.url, len(rects), size)
                return True
            return False                                   # stream died mid-send -> full frame next
        if put_rects(self.url, rects) is True:
            log.info("canvas %s: incremental %d rect(s) %d B", self.url, len(rects), size)
            return True
        return False                                       # a 413 / transient error -> full frame

    def _push_full_any(self, wall, b: bytes) -> bool:
        """A full frame by the best available channel: over the open stream first, else (or on a
        dead stream) the HTTP path (QOI where advertised, raw otherwise)."""
        st = wall.stream
        if st is not None and st.alive and self._stream_full(st, b):
            return True
        return self._push_full(b)

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
        """Set how subsequent ``frame()`` pushes present: "none" (hard cut),
        "crossfade", "wipe" or "slide", tweened on-device over ``ms`` (100–2000). Sticky until
        changed. Needs ``canvas.can_transition``."""
        return set_transition(self.url, kind, ms)

    def readback(self, fmt: str = "rgb888"):
        """Read the lit panel back as a PIL image, or ``None`` — a screenshot of
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
        — no client-side unpacking, no frame cap beyond the panel's PSRAM. ``data``
        is the raw GIF bytes. Returns ``{ok, frames, fps}`` (or ``{}``). Needs ``canvas.can_gif``."""
        forget_frame(self.url)
        return put_gif(self.url, bytes(data))

    def save_anim(self, name) -> bool:
        """Persist whatever animation is loaded to the on-device library as ``name`` — it survives
        the reboot and replays by name. Needs ``canvas.can_anim_library``."""
        return anim_save(self.url, name)

    def play_anim(self, name) -> dict:
        """Load and play a saved library animation. Returns ``{ok, frames}``."""
        forget_frame(self.url)
        return anim_play(self.url, name)

    def play_anim_path(self, path) -> dict:
        """Stream an MPGA animation straight off the microSD card (gate on
        ``can_sd_anim``): one frame in memory at a time, so length is bounded only by
        the card. Returns ``{ok, frames}``; the movie loops on-device until replaced."""
        forget_frame(self.url)
        return anim_play(self.url, path=str(path))

    def delete_anim(self, name) -> bool:
        """Delete a saved library animation."""
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
        if _wall(self.url).sim:                          # sim: nothing is drawn, so nothing to upload
            return True
        try:
            imgs = [im.convert("RGB") for im in images]
            if not imgs:
                return False
            tw, th = imgs[0].width, imgs[0].height
            buf = bytearray()
            for im in imgs:
                buf += (im if (im.width, im.height) == (tw, th) else im.resize((tw, th))).tobytes()
            tiles = bytes(buf)
            name = atlas_name_for(tiles, tw, th, fmt)
            row = _atlas_row(self.url, name)                   # what the wall's library says about it
            if row is None:
                if not put_atlas_named(self.url, name, tiles, tw, th, len(imgs), fmt):
                    return False
            else:                                              # no upload — a ~40B bind op rides the draw batch
                log.debug("canvas %s: atlas '%s' already resident, bound only", self.url, name)
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

    # -- PIL text toolkit: inherited from paneltext.PanelText ----------------
