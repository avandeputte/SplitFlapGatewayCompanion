"""autocal.py — camera-based auto-calibration vision, no I/O.

PHASE 0 (spike): the recognition engine only. Given a photo of the wall and the four
corners the user clicked, it rectifies the wall to a grid of per-module tiles; given a
*sweep* of one module (a capture per commanded flap index 0..N-1), it works out how far
that module's home offset is wrong — in whole flaps and in sub-flap vertical shift — with a
confidence score. The job orchestration (driving the gateway's /api/flap/* endpoints) and
the SPA/webcam live in later phases; everything here is pure functions over PIL images and
numpy arrays, so the hard part (the vision) is testable without a camera or a gateway.

THE CORE IDEA — anchor on the blank, not on OCR. Every reel's flap set contains a blank
(the space, normally index 0), and a blank tile is the one tile with almost no ink: trivial
to find, nothing to read. So we sweep the module, find which COMMANDED index produced the
blank, and that pins the absolute rotation of the reel with no character recognition at all.
Letter/font recognition is left as a later cross-check; the blank landmark is what makes
"fully automatic" robust.

Assumes a plain (non-colour) reel for the spike: a solid COLOUR flap is also low-ink and
would need a hue test to tell from the blank — a Phase 1 refinement, noted at `tile_ink`.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# The rectified size each module tile is normalised to before analysis. Big enough to keep
# a glyph legible, small enough to stay cheap; the real camera resolution is downscaled to it.
TILE_W, TILE_H = 64, 48


# ---------------------------------------------------------------------------
# Perspective rectification: the four clicked corners -> a flat grid of tiles.
# ---------------------------------------------------------------------------
def _perspective_coeffs(dst_quad, src_quad):
    """The 8 coefficients PIL's ``Image.transform(PERSPECTIVE)`` wants, mapping each point of
    the OUTPUT (the flat rectangle) back to the INPUT (the clicked quad in the photo).

    Both quads are four (x, y) points in the same order (TL, TR, BR, BL). Solving the linear
    system is exact for four correspondences — no least-squares needed.
    """
    A, b = [], []
    for (X, Y), (x, y) in zip(dst_quad, src_quad):
        A.append([X, Y, 1, 0, 0, 0, -x * X, -x * Y]); b.append(x)
        A.append([0, 0, 0, X, Y, 1, -y * X, -y * Y]); b.append(y)
    coeffs = np.linalg.solve(np.array(A, dtype=float), np.array(b, dtype=float))
    return tuple(coeffs.tolist())


def rectify(image: Image.Image, corners, out_w: int, out_h: int) -> Image.Image:
    """Flatten the wall in ``image`` (bounded by ``corners`` = TL, TR, BR, BL in pixels) to a
    straight ``out_w`` x ``out_h`` rectangle, undoing the camera's perspective."""
    dst = [(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)]
    coeffs = _perspective_coeffs(dst, [tuple(c) for c in corners])
    return image.convert("L").transform((out_w, out_h), Image.PERSPECTIVE, coeffs,
                                        resample=Image.BILINEAR)


def tiles(image: Image.Image, corners, rows: int, cols: int) -> list[np.ndarray]:
    """Rectify the wall and slice it into ``rows`` x ``cols`` per-module tiles, row-major
    (module 0 top-left), each a float32 grayscale array of shape (TILE_H, TILE_W)."""
    flat = rectify(image, corners, cols * TILE_W, rows * TILE_H)
    arr = np.asarray(flat, dtype=np.float32)
    out = []
    for r in range(rows):
        for c in range(cols):
            out.append(arr[r * TILE_H:(r + 1) * TILE_H, c * TILE_W:(c + 1) * TILE_W].copy())
    return out


# ---------------------------------------------------------------------------
# Per-tile measurements.
# ---------------------------------------------------------------------------
def tile_ink(tile: np.ndarray) -> float:
    """How much is PRINTED on this tile, 0..1. A letter is dark ink on a light flap, so its
    intensity spread is wide; a blank flap is near-uniform, so its spread is tiny. Standard
    deviation of the (normalised) pixels captures exactly that, and the blank is its minimum.

    NOTE (Phase 1): a solid colour flap is also near-uniform here and would read as low ink.
    Telling a colour flap from the blank needs the original colour (a hue/saturation test on
    the RGB tile); the spike assumes a plain reel.
    """
    return float(np.std(tile) / 255.0)


def tile_v_centroid(tile: np.ndarray) -> float:
    """Vertical centre of the ink, 0 (top) .. 1 (bottom). For a correctly-homed flap the glyph
    sits centred (~0.5); a module a fraction of a flap off shows the glyph high (<0.5) or low
    (>0.5). Background is the light flap, so ink weight is how far each pixel is BELOW the
    tile's bright background. Returns 0.5 for a blank (no ink to weigh)."""
    bg = float(np.percentile(tile, 90))          # the flap colour (bright); robust to a few specks
    weight = np.clip(bg - tile, 0, None)          # darker-than-background = ink
    total = float(weight.sum())
    if total < 1e-6:
        return 0.5
    ys = np.arange(tile.shape[0], dtype=np.float32)[:, None]
    return float((weight * ys).sum() / total) / (tile.shape[0] - 1)


# ---------------------------------------------------------------------------
# The solve: one module's sweep -> its home-offset error.
# ---------------------------------------------------------------------------
def analyze_sweep(sweep: list[np.ndarray], flap_chars: str) -> dict:
    """Work out how far a module's home offset is wrong, from a sweep of its flaps.

    ``sweep[i]`` is the tile captured when the module was commanded to flap index ``i`` (so
    ``len(sweep)`` == the reel's flap count). ``flap_chars`` is the reel order (``/api/flap/
    modules`` ``flapChars``); its blank is the landmark.

    Returns:
      - ``offset_flaps``: signed whole-flap error. Commanding index ``i`` currently displays
        reel flap ``(i + offset_flaps) mod N``; to correct, move the home position DOWN by
        ``offset_flaps`` flaps (0 = already aligned). Phase 1 turns that into a
        ``/api/flap/homeoffset`` nudge (sign confirmed on real hardware).
      - ``sub_flap``: residual vertical shift, -0.5..0.5 of a tile (+ = glyph riding low).
      - ``confidence``: 0..1, how cleanly the blank stood out from every other flap. A low
        value means "don't trust this; flag the module for a human look" rather than guess.
      - ``blank_cmd_index`` / ``inks``: the raw evidence, for the UI and tests.
    """
    n = len(sweep)
    if n == 0:
        raise ValueError("empty sweep")
    inks = np.array([tile_ink(t) for t in sweep], dtype=np.float64)
    blank_cmd = int(np.argmin(inks))
    target = flap_chars.index(" ") if " " in flap_chars else 0

    offset = (target - blank_cmd) % n                       # commanding i shows reel (i+offset)
    signed = offset if offset <= n // 2 else offset - n     # nearest rotation, +/-

    # Confidence: the blank should be a CLEAR minimum. Measure the gap from the lowest ink to
    # the next-lowest, as a fraction of the whole spread — a lone deep minimum ~ 1, a tie ~ 0.
    srt = np.sort(inks)
    spread = srt[-1] - srt[0]
    confidence = float((srt[1] - srt[0]) / spread) if (n > 1 and spread > 1e-6) else 0.0

    # Sub-flap: the typical vertical shift of the real (non-blank) glyphs off centre.
    cents = [tile_v_centroid(t) for i, t in enumerate(sweep) if i != blank_cmd]
    sub_flap = (float(np.median(cents)) - 0.5) if cents else 0.0

    return {
        "n": n,
        "blank_cmd_index": blank_cmd,
        "offset_flaps": int(signed),
        "sub_flap": round(sub_flap, 4),
        "confidence": round(confidence, 4),
        "aligned": signed == 0 and abs(sub_flap) < 0.08,
        "inks": [round(float(x), 4) for x in inks],
    }
