"""canvas_codec.py — the pure byte encoders for the Matrix panel wire formats.

No HTTP, no state: every function here turns pixels/ops into the exact bytes the
firmware decodes — QOI frames, rgb565 conversion, and the opsBin batch format
(with its opcode table). canvas.py re-exports these names, so callers and
tests import them from either module; the firmware lockstep lives here.
"""

from __future__ import annotations

import struct


# Named colors a canvas app can pass as strings (`canvas.rect(..., color="red")`).
# The first seven match the flap-side color palette (renderer.COLOR_NAMES); the rest
# are RGB conveniences for canvas drawing only — flaps have no cyan/magenta/pink/gray.
_NAMED = {
    "red": (255, 0, 0), "orange": (255, 96, 0), "yellow": (255, 200, 0),
    "green": (0, 200, 0), "blue": (0, 80, 255), "purple": (150, 0, 255),
    "white": (255, 255, 255), "black": (0, 0, 0), "cyan": (0, 200, 200),
    "magenta": (255, 0, 160), "pink": (255, 100, 160), "gray": (128, 128, 128),
}


def _rgb(color):
    """A color → an [r,g,b] list. Accepts a name, an (r,g,b)/[r,g,b], or a
    #RRGGBB string. Defaults to white for anything unrecognized."""
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
        vals = [max(0, min(255, int(v))) for v in color]
        if len(vals) == 4:                             # rgba — per-color alpha (compositing)
            return vals
        if len(vals) == 3:
            return vals
        return [255, 255, 255]
    except (TypeError, ValueError):
        return [255, 255, 255]



def _s8(v: int) -> int:
    """A byte difference as a signed 8-bit value (the wraparound QOI diffs use)."""
    v &= 0xFF
    return v - 256 if v >= 128 else v


def qoi_encode(rgb: bytes, w: int, h: int) -> bytes:
    """Encode a row-major rgb888 buffer (``w*h*3`` bytes) as a QOI image (3 channels, sRGB).

    numpy-vectorised when numpy is importable (an LCD-sized frame encodes in ~15 ms
    instead of ~800 ms), the original pure-Python coder otherwise. Both emit valid QOI;
    the firmware decodes either."""
    try:
        return _qoi_encode_np(rgb, w, h)
    except ImportError:
        return _qoi_encode_py(rgb, w, h)


def _qoi_encode_np(rgb: bytes, w: int, h: int) -> bytes:
    """The vectorised coder: RUN/DIFF/LUMA/RGB ops only (INDEX is an optional encoder
    optimisation — a stream without it is still spec-valid QOI and decodes identically).
    Every pixel's contribution is computed as an (up to 4-byte, length) pair, run bytes
    landing exactly where the streaming coder would put them (each 62nd member and the
    group tail), then the whole stream is flattened in one pass — no per-pixel loop."""
    import numpy as np
    n = w * h
    px = np.frombuffer(rgb, np.uint8)[:n * 3].reshape(n, 3)
    prev = np.empty_like(px)
    prev[0] = 0
    prev[1:] = px[:-1]
    dw = ((px.astype(np.int16) - prev.astype(np.int16) + 128) & 0xFF) - 128   # wraparound diffs
    same = (dw == 0).all(axis=1)
    dr, dg, db = dw[:, 0], dw[:, 1], dw[:, 2]
    vgr = ((dr - dg + 128) & 0xFF) - 128
    vgb = ((db - dg + 128) & 0xFF) - 128
    is_diff = ~same & (dr >= -2) & (dr <= 1) & (dg >= -2) & (dg <= 1) & (db >= -2) & (db <= 1)
    is_luma = ~same & ~is_diff & (dg >= -32) & (dg <= 31) & \
        (vgr >= -8) & (vgr <= 7) & (vgb >= -8) & (vgb <= 7)
    is_rgb = ~same & ~is_diff & ~is_luma

    lens = np.zeros(n, np.int64)
    cols = np.zeros((n, 4), np.uint8)
    lens[is_diff] = 1
    cols[is_diff, 0] = (0x40 | ((dr[is_diff] + 2) << 4)
                        | ((dg[is_diff] + 2) << 2) | (db[is_diff] + 2)).astype(np.uint8)
    lens[is_luma] = 2
    cols[is_luma, 0] = (0x80 | (dg[is_luma] + 32)).astype(np.uint8)
    cols[is_luma, 1] = (((vgr[is_luma] + 8) << 4) | (vgb[is_luma] + 8)).astype(np.uint8)
    lens[is_rgb] = 4
    cols[is_rgb, 0] = 0xFE
    cols[is_rgb, 1:4] = px[is_rgb]

    if same.any():
        # Member index within each run group: position minus the group head's position
        # (heads propagated forward by a running maximum). A run byte lands on every
        # 62nd member (0xC0|61) and on the group tail when a remainder is left over —
        # the same placements the streaming coder produces.
        idx = np.arange(n)
        starts = same & np.concatenate(([True], ~same[:-1]))
        head = np.maximum.accumulate(np.where(starts, idx, -1))
        m = idx - head
        last = same & np.concatenate((~same[1:], [True]))
        chunk_done = same & ((m + 1) % 62 == 0)
        rem_tail = last.copy()
        rem_tail[last] = (m[last] + 1) % 62 != 0
        lens[chunk_done] = 1
        cols[chunk_done, 0] = 0xC0 | 61
        lens[rem_tail] = 1
        cols[rem_tail, 0] = (0xC0 | ((m[rem_tail] % 62))).astype(np.uint8)

    total = int(lens.sum())
    reps = np.repeat(np.arange(n), lens)
    within = np.arange(total) - np.repeat(np.cumsum(lens) - lens, lens)
    body = cols[reps, within]
    head_b = b"qoif" + w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes((3, 0))
    return head_b + body.tobytes() + bytes((0, 0, 0, 0, 0, 0, 0, 1))


def _qoi_encode_py(rgb: bytes, w: int, h: int) -> bytes:
    """The original streaming coder — kept whole as the no-numpy fallback."""
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


def _rgb565_be(arr):
    """A numpy (h, w, 3) uint8 array -> rgb565 big-endian bytes, row-major."""
    import numpy as np
    r = arr[:, :, 0].astype(np.uint16)
    g = arr[:, :, 1].astype(np.uint16)
    b = arr[:, :, 2].astype(np.uint16)
    v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return v.astype(">u2").tobytes()



def _rgb565_to_888(data: bytes, w: int, h: int) -> bytes:
    """Big-endian rgb565 → rgb888, expanding each channel to 8 bits (the panel's own quantization
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



# -- binary ops (capabilities "opsBin") ---------------------------------------
# The fixed-layout twin of the JSON ops batch: ~6x smaller and the wall skips JSON
# parsing entirely (measured 1.5-1.9x the frame rate on op-heavy scenes). Big-endian,
# coordinates signed int16. encode_ops_bin returns None only for what the format
# cannot carry (textbox, image, an atlas bind, a named text font, text over 127 UTF-8
# bytes, >16 poly vertices, mixed per-field alphas) — the caller then sends the JSON
# batch instead, pixel-identical. Atlas binds ride the draw stream's own 0x04 record
# (see CanvasSurface.show), so a sprite app streams too.
def _bi16(v):
    return struct.pack(">h", max(-32768, min(32767, int(v))))


def _bu8(v):
    return bytes((max(0, min(255, int(v))),))


def _brgb(c):
    return bytes((int(c[0]) & 255, int(c[1]) & 255, int(c[2]) & 255))


# The opsBin opcode table — the one place the wire numbers live. MUST stay in lockstep
# with the firmware's binary decoder (web.cpp); "polyline" shares poly's opcode (a flag
# bit distinguishes them), and streaming wraps a whole batch in draw-channel record 0x06.
_OPCODE = {
    "clear": 0x01, "pixel": 0x02, "hline": 0x03, "vline": 0x04, "line": 0x05,
    "rect": 0x06, "circle": 0x07, "ellipse": 0x08, "triangle": 0x09, "roundrect": 0x0a,
    "gradient": 0x0b, "arc": 0x0c, "poly": 0x0d, "polyline": 0x0d, "clip": 0x0e,
    "origin": 0x0f, "text": 0x10, "sprite": 0x11, "scroll": 0x12, "show": 0x13,
    "blend": 0x14, "alpha": 0x15,
    # The transform/layer/macro/bezier opcodes, at parity with JSON.
    "save": 0x16, "restore": 0x17, "translate": 0x18, "scale": 0x19, "rotate": 0x1a,
    "layer": 0x1b, "composite": 0x1c, "define": 0x1d, "call": 0x1e, "bezier": 0x1f,
    "aaline": 0x20,
}


def _opc(k):
    return bytes((_OPCODE[k],))


def encode_ops_bin(ops):
    """The batch as opsBin bytes, or None when any op is not representable.

    ONE format — the one the current firmware decodes (capabilities
    ``canvas.opsBin: true``). A 4-component ``[r,g,b,a]`` color encodes as batch
    alpha (``0x15 a`` before the op, restored for the next opaque op — identical
    rendering to the JSON path's per-color alpha); anti-aliased strokes, the
    transform stack, layers, macros (JSON names map to binary slot ids 0-7 in
    first-seen order) and bezier all encode directly. None is reserved for what
    the format genuinely cannot carry: textbox/image/atlas binds, a named text
    font, text over 127 UTF-8 bytes, >16 poly vertices, mixed per-field alphas,
    bad bezier arity, >8 macros — the caller sends JSON instead."""
    out = bytearray()
    _BLEND = {"over": 0, "add": 1, "multiply": 2, "screen": 3, "max": 4}
    cur_alpha = 255                                    # the runner's binAlpha starts opaque
    macro_ids: dict = {}                               # JSON macro name -> slot id 0-7
    for op in ops:
        k = op.get("op")
        # Per-color alpha ([r,g,b,a] on color / gradient from+to / text outline+shadow)
        # maps onto batch alpha 0x15 — but only when every color in THIS op agrees on one
        # alpha (0x15 is per op, not per field). Mixed per-field alphas send the whole
        # batch as JSON so the alpha is never silently truncated to opaque.
        alphas = set()
        for _ck in ("color", "from", "to", "outline", "shadow"):
            _cv = op.get(_ck)
            if isinstance(_cv, (list, tuple)):
                alphas.add(int(_cv[3]) if len(_cv) == 4 else 255)
        op_alpha = alphas.pop() if len(alphas) == 1 else (255 if not alphas else None)
        if op_alpha is None:
            return None                        # mixed per-field alphas: 0x15 is per op
        if op_alpha != cur_alpha and k not in ("blend", "show"):
            out += _opc("alpha") + _bu8(op_alpha)      # 0x15: batch alpha for the ops below
            cur_alpha = op_alpha
        # Anti-aliasing: line becomes the 0x20 AALINE (t is ignored, exactly like the
        # JSON path); circle/poly/polyline carry an aa flag bit in their layouts; text
        # and bezier carry aa themselves. Any other op asking for aa has no binary
        # form -> JSON fallback.
        if op.get("aa") and k not in ("text", "bezier"):
            if k == "line":
                out += _opc("aaline") + _bi16(op["x"]) + _bi16(op["y"]) \
                    + _bi16(op["x1"]) + _bi16(op["y1"]) + _brgb(op["color"])
                continue
            if k not in ("circle", "poly", "polyline"):
                return None
        if k == "clear":
            out += _opc(k) + _brgb(op.get("color", (0, 0, 0)))
        elif k == "pixel":
            out += _opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _brgb(op["color"])
        elif k == "hline":
            out += _opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["w"]) + _brgb(op["color"])
        elif k == "vline":
            out += _opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["h"]) + _brgb(op["color"])
        elif k == "line":
            out += (_opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["x1"])
                    + _bi16(op["y1"]) + _bu8(op.get("t", 1)) + _brgb(op["color"]))
        elif k == "rect":
            out += (_opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["w"]) + _bi16(op["h"])
                    + _bu8(1 if op.get("fill") else 0) + _bu8(op.get("t", 1)) + _brgb(op["color"]))
        elif k == "circle":
            cfl = (1 if op.get("fill") else 0) | (2 if op.get("aa") else 0)   # bit1: aa
            out += (_opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["r"])
                    + _bu8(cfl) + _bu8(op.get("t", 1)) + _brgb(op["color"]))
        elif k == "ellipse":
            out += (_opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["rx"]) + _bi16(op["ry"])
                    + _bu8(1 if op.get("fill") else 0) + _bu8(op.get("t", 1)) + _brgb(op["color"]))
        elif k == "triangle":
            out += (_opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["x1"]) + _bi16(op["y1"])
                    + _bi16(op["x2"]) + _bi16(op["y2"])
                    + _bu8(1 if op.get("fill") else 0) + _brgb(op["color"]))
        elif k == "roundrect":
            out += (_opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["w"]) + _bi16(op["h"])
                    + _bi16(op["r"]) + _bu8(1 if op.get("fill") else 0) + _brgb(op["color"]))
        elif k == "gradient":
            out += (_opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["w"]) + _bi16(op["h"])
                    + _brgb(op["from"]) + _brgb(op["to"])
                    + _bu8(0 if op.get("dir", "v") == "v" else 1))
        elif k == "arc":
            out += (_opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bi16(op["r"])
                    + _bu8(op.get("t", 2)) + _bi16(op.get("start", 0)) + _bi16(op.get("end", 360))
                    + _bu8(1 if op.get("fill") else 0) + _brgb(op["color"]))
        elif k in ("poly", "polyline"):
            pts = op.get("points") or []
            if len(pts) > 16:
                return None
            # bit0 fill (poly), bit1 outline (poly), bit2 aa; a polyline leaves bits
            # 0-1 clear — how the shared 0x0d opcode distinguishes the two.
            flags = (1 if (k == "poly" and op.get("fill", True)) else 0)                 | (2 if (k == "poly" and not op.get("fill", True)) else 0)                 | (4 if op.get("aa") else 0)
            out += (_opc(k) + _bu8(len(pts)) + _bu8(flags) + _bu8(op.get("t", 1))
                    + _brgb(op["color"]))
            for px, py in pts:
                out += _bi16(px) + _bi16(py)
        elif k == "clip":
            out += (_opc(k) + _bi16(op.get("x", 0)) + _bi16(op.get("y", 0))
                    + _bi16(op.get("w", 0)) + _bi16(op.get("h", 0)))
        elif k == "origin":
            out += _opc(k) + _bi16(op.get("x", 0)) + _bi16(op.get("y", 0))
        elif k == "text":
            if op.get("font"):
                return None                        # named faces have no binary form
            raw = str(op.get("s", "")).encode("utf-8")
            if len(raw) > 127:
                return None
            flags = {"center": 1, "right": 2}.get(op.get("align"), 0)
            if op.get("aa"):
                flags |= 0x04
            if op.get("outline") is not None:
                flags |= 0x08
            if op.get("shadow") is not None:
                flags |= 0x10
            out += (_opc(k) + _bi16(op["x"]) + _bi16(op["y"]) + _bu8(op.get("size", 10))
                    + _bu8(flags) + _brgb(op["color"]))
            if op.get("outline") is not None:
                out += _brgb(op["outline"])
            if op.get("shadow") is not None:
                out += _brgb(op["shadow"])
            out += _bu8(len(raw)) + raw
        elif k == "sprite":
            flags = (1 if "h" in str(op.get("flip", "")) else 0)                 | (2 if "v" in str(op.get("flip", "")) else 0)                 | ((int(op.get("rot", 0)) // 90 & 3) << 2)                 | ((max(1, int(op.get("scale", 1))) - 1 & 3) << 4)
            out += (_opc(k) + struct.pack(">H", int(op["i"]) & 0xFFFF)
                    + _bi16(op["x"]) + _bi16(op["y"]) + _bu8(flags))
        elif k == "scroll":
            out += _opc(k) + _bi16(op["dx"]) + _bi16(op["dy"]) + _brgb(op.get("color", (0, 0, 0)))
        elif k == "blend":
            out += _opc(k) + _bu8(_BLEND.get(op.get("mode", "over"), 0))
        elif k == "show":
            out += _opc(k)
        elif k in ("save", "restore", "layer"):
            out += _opc(k)                                             # no payload
        elif k == "translate":
            out += _opc(k) + _bi16(op.get("x", 0)) + _bi16(op.get("y", 0))
        elif k == "scale":
            # u16 8.8 fixed; y absent -> 0 on the wire = uniform (the decoder's rule)
            sx = max(0, min(65535, int(round(float(op.get("x", 1)) * 256))))
            sy = op.get("y")
            syw = 0 if sy is None else max(0, min(65535, int(round(float(sy) * 256))))
            out += _opc(k) + struct.pack(">HH", sx, syw)
        elif k == "rotate":
            out += _opc(k) + _bi16(op.get("deg", 0))
        elif k == "composite":
            out += (_opc(k) + _bi16(op.get("x", 0)) + _bi16(op.get("y", 0))
                    + _bu8(_BLEND.get(op.get("mode", "over"), 0))
                    + _bu8(op.get("alpha", 255)))
        elif k == "define":
            name = str(op.get("name", ""))
            if not name or name in macro_ids or len(macro_ids) >= 8:
                return None                        # slots are 0-7; a redefinition is JSON's
            blob = encode_ops_bin(op.get("ops") or [])
            if blob is None or len(blob) > 65535:
                return None                        # an unencodable body sinks the batch
            macro_ids[name] = len(macro_ids)
            out += _opc(k) + _bu8(macro_ids[name]) + struct.pack(">H", len(blob)) + blob
        elif k == "call":
            mid = macro_ids.get(str(op.get("name", "")))
            if mid is None:
                return None                        # calling a macro this batch never defined
            out += _opc(k) + _bu8(mid) + _bi16(op.get("x", 0)) + _bi16(op.get("y", 0))
        elif k == "bezier":
            pts = op.get("points") or []
            if len(pts) not in (3, 4):
                return None
            out += (_opc(k) + _bu8(len(pts)) + _bu8(op.get("t", 1))
                    + _bu8(1 if op.get("aa") else 0) + _brgb(op["color"]))
            for px, py in pts:
                out += _bi16(px) + _bi16(py)
        else:
            return None                            # textbox / image / atlas: JSON carries those
    return bytes(out)


