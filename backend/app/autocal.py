"""autocal.py — camera-based auto-calibration vision, no I/O.

Given a photo of the wall and the four corners the user clicked, it rectifies the wall to a
grid of per-module tiles; given a *sweep* of one module (a capture per commanded flap index
0..N-1), it works out how far that module's home offset is wrong — whole flaps plus sub-flap
vertical shift — and then PROVES the result flap by flap. The orchestration (driving the
gateway's /api/flap/* endpoints) and the SPA/webcam live around this; everything here is pure
functions over PIL images and numpy arrays, testable without a camera or a gateway.

HOW A MODULE IS CALIBRATED — learn, anchor twice, correct, verify every flap:
  1. LEARN: one full sweep captures every flap, and the module's own reel becomes its glyph
     library (``learn_reel``). No font is assumed; lighting, camera, print and the horizontal
     flap seam all match by construction, because the references ARE this wall's flaps.
  2. ANCHOR x3: the library needs one absolute fact — which captured tile is which character.
     Three INDEPENDENT anchors vote: (a) the blank, the one provably uniform flap, found by
     peak-deviation flatness (``tile_blankness``); (b) the cyclic shift that best matches the
     whole sweep against RENDERED seamed references (``consensus_shift``); (c) OCR reading the
     sweep back as text lines (``ocr_shift``). Every confident anchor must agree — a
     disagreement = needs_review, never a guess — and the agreeing anchors can RESCUE an
     ambiguous blank (solid-colour flaps — the white one especially — are exactly as flat as
     the blank in grayscale).
  3. CORRECT: nudges (live, relative — see the write contract at ``_adjust``), spot-checked by
     identity each round until the shift reads zero.
  4. VERIFY: every flap is re-read and identified against the learned library — dialed A must
     SHOW A; "saw B instead" means it went one too far and is said in those words. An unsure
     match is flagged by name, never asserted. OCR then reads the WHOLE verified reel back and
     must agree it sits at zero, and flagged flaps get OCR's reading of them in the log.

WHERE OCR FITS — measured, twice: tesseract on a seam-cut SINGLE glyph is unusable (at best
6/22 on realistic tiles, with high-confidence WRONG reads — a seam-cut E genuinely is two bars
and reads as `=`), so a lone OCR opinion never decides a flap. Reading the sweep as text LINES,
each recognized character box-mapped back to its source tile, lands about half its characters —
and the cyclic alignment of ~50 such opinions picks the rotation with huge margins (measured
18/18, top 20-32 vs runner-up 2-4, under noise and low contrast). OCR therefore anchors and
validates the reel in bulk, while per-flap identity stays with the learned-shape match.
"""

from __future__ import annotations

import io
import time
from functools import lru_cache
from pathlib import Path

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


TILE_STABLE_DIFF = 4.0   # mean |a-b| on POOLED tiles. Pooling first matters: raw webcam pixels
                         # flicker with JPEG noise and auto-exposure even when nothing moves, so a
                         # raw-pixel diff never settles; 4x4 mean-pooling averages that out while a
                         # flap in flight still differs by tens of levels.


def _pool4(t: np.ndarray) -> np.ndarray:
    h, w = (t.shape[0] // 4) * 4, (t.shape[1] // 4) * 4
    return t[:h, :w].reshape(h // 4, 4, w // 4, 4).mean(axis=(1, 3))


def tiles_similar(a: np.ndarray, b: np.ndarray) -> bool:
    """Do two consecutive reads of the same module agree — i.e. has the flap stopped moving?"""
    return float(np.mean(np.abs(_pool4(a) - _pool4(b)))) < TILE_STABLE_DIFF


def tile_blankness(tile: np.ndarray) -> float:
    """How BLANK a tile is, 0 (flat) upward. The 99th-percentile deviation from the median: a
    faint glyph (a dot, an apostrophe) has few but STRONG dark pixels — a huge deviation — while
    a true blank deviates only by sensor noise. Far sharper at "blank vs faintest glyph" than
    the ink spread (std), which once scored a wall's blank 0.117 against a '.' at 0.132."""
    med = float(np.median(tile))
    return float(np.percentile(np.abs(tile - med), 99)) / 255.0


# ---------------------------------------------------------------------------
# Glyph recognition — learned from the module's OWN reel, no font assumed.
#
# One full sweep captures a tile per commanded index. The blank (the one flap that is provably
# uniform) anchors which captured tile is which character; every captured tile then becomes the
# reference for its character, under this exact camera, lighting and print. From then on,
# "dialed 3" must MATCH the learned "3" — if it matches the learned "4", the reel is one flap
# ahead and the offset falls straight out. Identity, not ink, is the verification.
# ---------------------------------------------------------------------------
def _pool2(t: np.ndarray) -> np.ndarray:
    # finer than _pool4: glyph matching needs the shape detail (B vs 8, S vs $) that 4x4
    # pooling blurs into razor-thin margins, while 2x2 still averages sensor noise away
    h, w = (t.shape[0] // 2) * 2, (t.shape[1] // 2) * 2
    return t[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


def _vroll(t: np.ndarray, dy: int) -> np.ndarray:
    """The tile with its content moved ``dy`` pixels down (up when negative), background-filled.
    A home-offset error shifts EVERY glyph by the same amount, so matching under a vertical
    roll is how a capture is compared against a library learned at a different sub-flap."""
    if dy == 0:
        return t
    out = np.full_like(t, float(np.median(t)))
    if dy > 0:
        out[dy:] = t[:-dy]
    else:
        out[:dy] = t[-dy:]
    return out


GLYPH_SEAM_BAND = 3   # pixel rows masked either side of a seam position when comparing


def glyph_pool(tile: np.ndarray) -> np.ndarray:
    """The stored form of a glyph reference: the 2x2-pooled tile, raw (masking and
    normalisation happen per comparison, because the seam mask depends on the roll)."""
    return _pool2(tile)


def _seam_mask(pool_h: int, dys) -> np.ndarray:
    """Which pooled rows to KEEP when comparing tiles. The seam sits mid-window and never
    moves with the glyph, so a capture rolled by ``dy`` carries its seam band to mid+dy while
    the reference keeps its own at mid — both bands are dropped from both sides, or the seam
    scores as (dis)agreement it isn't. When the positions coincide (dy=0) NOTHING is masked:
    identical seams correlate fine, and a glyph that hugs the seam (a dash) needs those rows."""
    keep = np.ones(pool_h, dtype=bool)
    if len(set(dys)) <= 1:
        return keep
    mid = TILE_H // 2
    for dy in dys:
        y = mid + dy
        lo, hi = (y - GLYPH_SEAM_BAND) // 2, (y + GLYPH_SEAM_BAND) // 2
        keep[max(0, lo):min(pool_h, hi + 1)] = False
    return keep


def _masked_glyph(pool: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """A pooled tile as a lighting-independent shape vector: seam rows dropped,
    median-centred, unit-energy — ready for normalized cross-correlation (a dot product)."""
    v = pool[keep].astype(np.float64).ravel()
    v -= np.median(v)
    nrm = float(np.linalg.norm(v))
    return v / nrm if nrm > 1e-9 else v


def learn_reel(tiles: list, flap_chars: str) -> dict:
    """Turn a full commanded-order sweep into this module's own glyph library.

    The blank anchors the labels: if the flattest tile sits at commanded index j while the reel
    keeps its blank at index b, the reel is offset by (b - j) and every captured tile is the
    reference image for the character actually behind it. ``confidence`` is how uniquely flat
    that blank is against the runner-up — LOW on a reel with solid-colour flaps (they are just
    as flat in grayscale), which is what the rendered-consensus anchor is for. ``blankness``
    (each character's learned flatness) travels with the library so later checks judge the flat
    flaps by flatness, not by glyph identity (normalized near-flat tiles are just noise)."""
    n = len(flap_chars)
    blank_index = flap_chars.index(" ") if " " in flap_chars else 0
    flat = [tile_blankness(t) for t in tiles]
    j = int(np.argmin(flat))
    srt = sorted(flat)
    spread = srt[-1] - srt[0]
    confidence = float((srt[1] - srt[0]) / spread) if (n > 1 and spread > 1e-9) else 0.0
    offset = (blank_index - j) % n
    signed = offset if offset <= n // 2 else offset - n
    templates = {flap_chars[(i + signed) % n]: glyph_pool(tiles[i]) for i in range(n)}
    blankness = {flap_chars[(i + signed) % n]: flat[i] for i in range(n)}
    return {"offset_flaps": signed, "confidence": round(confidence, 4),
            "blank_cmd_index": j, "templates": templates, "blankness": blankness}


def identify(tile: np.ndarray, templates: dict, rolls=(0,)) -> tuple[str, float, float]:
    """Which learned character this tile is: ``(char, score, margin)``. ``margin`` is the gap to
    the runner-up — a small margin means "unsure", to be flagged rather than asserted.

    ``rolls`` are the vertical registrations to try (``_vroll``): the library may have been
    learned at a different sub-flap shift than the capture, and the shapes only line up once
    that shared shift is rolled out (the seam, which does NOT move with the glyph, is masked
    at both its positions). The best-scoring roll wins, margin judged within it."""
    best = ("?", -1e9, 0.0)
    for dy in rolls:
        cap = _pool2(_vroll(tile, dy))
        keep = _seam_mask(cap.shape[0], (0, dy))
        g = _masked_glyph(cap, keep)
        scored = sorted(((float(np.sum(g * _masked_glyph(tpl, keep))), ch)
                         for ch, tpl in templates.items()), reverse=True)
        sc, ch = scored[0]
        if sc > best[1]:
            best = (ch, sc, sc - (scored[1][0] if len(scored) > 1 else 0.0))
    ch, sc, margin = best
    return ch, round(sc, 3), round(margin, 3)


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


# ---------------------------------------------------------------------------
# Orchestration: drive ONE module's home offset to aligned (Phase 1).
#
# This is the closed loop the calibration job runs per module. It is pure — every side
# effect is an injected callable — so the whole thing is tested against a simulated wall
# with no camera and no gateway (see test_autocal.py). In production the session wires:
#   capture_index -> command a flap on the gateway, wait out the motion, then read the
#                    module's tile from a PROVEN-settled fresh camera frame;
#   adjust        -> POST /api/flap/nudge (a LIVE correction: moves the reel + saves);
#   restore       -> POST /api/flap/homeoffset + /api/flap/home (revert only).
# ---------------------------------------------------------------------------
CONFIDENCE_MIN = 0.12          # below this, flag the module for a human rather than guess
MAX_ROUNDS = 6                 # a correction + a few refinements; mechanical walls are slow
SETTLE_FLOOR_S = 1.0           # min wait per step: enough for a flap to physically settle AND for
                               # a fresh camera frame to arrive, and slow enough to watch


def steps_per_flap(total_steps: int, flap_count: int) -> float:
    return float(total_steps) / max(1, int(flap_count))


def looks_blank(ink: float, evidence: dict) -> bool:
    """Judge a NEW capture by the module's own measured yardstick (``evidence`` =
    ``{baseline, blank_ink}``, from its learning sweep): closer to its blank's ink than to its
    printed-flap baseline. Used to verify a re-home really landed on the blank."""
    base = float(evidence.get("baseline", 0.0))
    blank = float(evidence.get("blank_ink", 0.0))
    if base <= blank + 1e-6:
        return False
    return ink < blank + 0.5 * (base - blank)


GLYPH_MARGIN_MIN = 0.03   # an identification closer than this to its runner-up is "unsure".
                          # Measured on a 64-glyph DejaVu reel at pool2 with registration
                          # rolls: correct reads keep margins >= ~0.05 even under noise and
                          # +/-2px residual shift, so 0.03 flags genuine ambiguity only.

# ---------------------------------------------------------------------------
# The rendered-reference anchor — the independent cross-check on the blank.
#
# The learned library needs one absolute fact from outside itself: which captured tile is
# which character. The blank gives it, but a solid-colour flap (and the white one especially)
# is exactly as flat as the blank in grayscale — so a SECOND, independent anchor votes:
# match the whole sweep against rendered seamed references and take the cyclic-shift
# consensus. (Single-glyph OCR was measured and rejected for this job — see the module
# docstring: at best 6/22 on seam-cut glyphs, with confidently-wrong reads.)
# ---------------------------------------------------------------------------
COLOR_LETTERS = "roygbpw"  # the reel's solid-colour flaps: no glyph printed on them
FONT_ANCHOR_MIN = 0.12     # consensus margin below this = the font anchor ABSTAINS
FONT_MATCH_MIN = 0.12      # mean per-glyph similarity below this = too alien, abstain too
_FONT_PATH = Path(__file__).resolve().parent / "fonts" / "DejaVuSans-Bold.ttf"


def physical_glyph(ch: str) -> str:
    """What is PRINTED on the flap for a reel character: `q` is the double-quote flap."""
    return '"' if ch == "q" else ch


def render_reference(ch: str) -> np.ndarray:
    """What a flap printed with ``ch`` roughly looks like: a bold dark glyph on a light flap,
    cut by the horizontal seam every real flap has. The font is NOT assumed to match the
    wall's — a reference only has to look more like its own character than like the others,
    and only in aggregate (see ``consensus_shift``)."""
    from PIL import ImageDraw, ImageFont
    img = Image.new("L", (TILE_W, TILE_H), 230)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(_FONT_PATH), int(TILE_H * 0.8))
    g = physical_glyph(ch)
    x0, y0, x1, y1 = d.textbbox((0, 0), g, font=font)
    d.text(((TILE_W - (x1 - x0)) // 2 - x0, (TILE_H - (y1 - y0)) // 2 - y0), g,
           fill=25, font=font)
    a = np.asarray(img, dtype=np.float32).copy()
    a[TILE_H // 2 - 1:TILE_H // 2 + 1, :] = 230.0        # the seam
    return a


@lru_cache(maxsize=8)
def reference_templates(flap_chars: str) -> dict:
    """Rendered references (pooled) for the reel's PRINTABLE glyphs (the blank and the
    solid-colour flaps carry no glyph to render)."""
    return {ch: glyph_pool(render_reference(ch))
            for ch in flap_chars if ch != " " and ch not in COLOR_LETTERS}


# ---------------------------------------------------------------------------
# OCR — reads the reel back, the way OCR is actually strong.
#
# Measured twice: tesseract on a seam-cut SINGLE glyph is unusable (at best 6/22, with
# confidently wrong reads — a seam-cut E really is two bars). Asked to read a LINE of many
# flap tiles, box-mapped back to their source tiles, it lands ~half its characters — and a
# cyclic alignment of ~50 such opinions picks the reel's rotation with huge margins (measured
# 18/18 correct, top score 20-32 vs runner-up 2-4, under noise and low contrast). So OCR here
# anchors and VALIDATES the whole reel's characters; it never vetoes a single flap alone.
# Degrades to the other anchors when the tesseract binary is missing.
# ---------------------------------------------------------------------------
OCR_ANCHOR_MIN_HITS = 8    # an alignment this weak is no opinion…
OCR_ANCHOR_DOMINANCE = 3   # …and the best shift must beat the runner-up by this factor
_OCR_CHUNK = 16            # tiles per line read (keeps the strip a sane width)

_OCR = {"checked": False, "ok": False}


def ocr_available() -> bool:
    if not _OCR["checked"]:
        _OCR["checked"] = True
        try:
            import shutil

            import pytesseract                                  # noqa: F401
            _OCR["ok"] = shutil.which("tesseract") is not None
        except Exception:                                       # noqa: BLE001
            _OCR["ok"] = False
    return _OCR["ok"]


def _vert_filter(a: np.ndarray, k: int, op) -> np.ndarray:
    out = a.copy()
    for s in range(1, k + 1):
        up = np.pad(a, ((s, 0), (0, 0)), mode="edge")[:-s]
        dn = np.pad(a, ((0, s), (0, 0)), mode="edge")[s:]
        out = op(op(out, up), dn)
    return out


def bridge_seam(a: np.ndarray, k: int) -> np.ndarray:
    """Reconnect the two halves of a seam-cut glyph: thicken dark strokes vertically (grey
    closing), then restore their weight. Without this, tesseract sees two half-characters."""
    return _vert_filter(_vert_filter(a, k, np.minimum), k, np.maximum)


def ocr_tile_opinions(tiles: list, flap_chars: str) -> dict:
    """OCR's per-tile opinions: ``{tile_index: reel_char}`` for every tile it read a character
    off. The tiles are composited into text LINES (chunks of ``_OCR_CHUNK``), contrast-
    stretched, seam-bridged, binarized, upscaled, and read with the reel's glyph whitelist;
    each recognized character is mapped back to its source tile by its box position, which
    makes insertions/duplicates harmless. Individual opinions are WEAK (measured ~half right)
    — only their alignment in bulk is trusted (``align_opinions``)."""
    if not ocr_available() or not tiles:
        return {}
    import tempfile

    import pytesseract

    h, w = tiles[0].shape
    upscale = max(2, 288 // h)
    glyphs = "".join(sorted({physical_glyph(c) for c in flap_chars
                             if c != " " and c not in COLOR_LETTERS} - {'"'}))
    if not glyphs:
        return {}
    opinions: dict[int, str] = {}
    for start in range(0, len(tiles), _OCR_CHUNK):
        chunk = tiles[start:start + _OCR_CHUNK]
        gap = max(8, h // 4)
        xs, x = [], gap
        strip = np.full((h, gap + len(chunk) * (w + gap)), 255.0)
        for a in chunk:
            lo, hi = np.percentile(a, 5), np.percentile(a, 95)
            p = np.clip((a - lo) / max(1e-6, hi - lo), 0.0, 1.0) * 255.0
            strip[:, x:x + w] = bridge_seam(p, max(2, h // 8))
            xs.append((x, x + w))
            x += w + gap
        img = Image.fromarray(strip.astype(np.uint8), "L")
        img = img.resize((img.width * upscale, img.height * upscale), Image.LANCZOS)
        arr = np.asarray(img, dtype=np.float64)
        img = Image.fromarray(np.where(arr < 128, 0, 255).astype(np.uint8), "L")
        pad_px = 16
        pad = Image.new("L", (img.width + 2 * pad_px, img.height + 2 * pad_px), 255)
        pad.paste(img, (pad_px, pad_px))
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False) as f:
                f.write(f"tessedit_char_whitelist {glyphs}\n")
                cfg = f.name
            try:
                boxes = pytesseract.image_to_boxes(pad, config=f'--psm 7 --oem 3 "{cfg}"')
            finally:
                import os
                os.unlink(cfg)
        except Exception:                                       # noqa: BLE001
            continue
        for line in boxes.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            ch = parts[0]
            try:
                bx0, bx1 = int(parts[1]), int(parts[3])
            except ValueError:
                continue
            cx = ((bx0 + bx1) / 2 - pad_px) / upscale
            for i, (x0, x1) in enumerate(xs):
                if x0 - gap / 2 <= cx < x1 + gap / 2:
                    ch = "q" if ch == '"' else ch
                    if ch in flap_chars:
                        opinions.setdefault(start + i, ch)      # a split glyph: first wins
                    break
    return opinions


def align_opinions(opinions: dict, flap_chars: str) -> dict:
    """The cyclic shift that best explains OCR's opinions: ``{offset_flaps, hits, votes}``
    (``analyze_sweep`` sign convention). ``offset_flaps`` is None unless the best shift has
    both enough hits and clear dominance over the runner-up — weak evidence ABSTAINS."""
    n = len(flap_chars)
    if not opinions or n < 2:
        return {"offset_flaps": None, "hits": 0, "votes": len(opinions)}
    scores = [sum(1 for k, v in opinions.items() if flap_chars[(k + s) % n] == v)
              for s in range(n)]
    srt = sorted(scores)
    top, second = srt[-1], srt[-2]
    if top < OCR_ANCHOR_MIN_HITS or top < OCR_ANCHOR_DOMINANCE * max(1, second):
        return {"offset_flaps": None, "hits": int(top), "votes": len(opinions)}
    s = int(np.argmax(scores))
    return {"offset_flaps": s if s <= n // 2 else s - n, "hits": int(top),
            "votes": len(opinions)}


def ocr_shift(sweep_tiles: list, flap_chars: str) -> dict:
    """The OCR anchor over a whole sweep: read the tiles as lines, align the opinions."""
    return align_opinions(ocr_tile_opinions(sweep_tiles, flap_chars), flap_chars)


def consensus_shift(sweep_tiles: list, flap_chars: str) -> dict:
    """The INDEPENDENT absolute anchor: which cyclic shift of the reel best explains the whole
    sweep, judged against rendered references. Each per-glyph match is weak (the wall's font
    is unknown), but N votes on one rotation are strong — and the consensus only has to PICK A
    ROTATION, never read a letter, which is exactly why it works where single-glyph OCR
    measurably failed. The sweep may carry the module's whole sub-flap shift, so a few GLOBAL
    vertical registrations are tried and each shift keeps its best. Returns
    ``{offset_flaps, confidence}`` with the ``analyze_sweep`` sign convention;
    ``offset_flaps`` is None when no shift stands out or nothing matches — an alien-enough
    look ABSTAINS rather than guesses, and the blank anchor rules alone."""
    n = len(flap_chars)
    refs = reference_templates(flap_chars)
    if not refs:
        return {"offset_flaps": None, "confidence": 0.0}
    ph = _pool2(sweep_tiles[0]).shape[0]
    scores = np.full(n, -1e9)
    for dy in range(-TILE_H // 2, TILE_H // 2 + 1, 4):
        keep = _seam_mask(ph, (0, dy))
        g = [_masked_glyph(_pool2(_vroll(t, dy)), keep) for t in sweep_tiles]
        r = {ch: _masked_glyph(p, keep) for ch, p in refs.items()}
        for s in range(n):
            tot = sum(float(np.sum(g[i] * r[flap_chars[(i + s) % n]]))
                      for i in range(n) if flap_chars[(i + s) % n] in refs)
            scores[s] = max(scores[s], tot)
    srt = np.sort(scores)
    spread = float(srt[-1] - srt[0])
    conf = float((srt[-1] - srt[-2]) / spread) if (n > 1 and spread > 1e-9) else 0.0
    if conf < FONT_ANCHOR_MIN or float(srt[-1]) / len(refs) < FONT_MATCH_MIN:
        return {"offset_flaps": None, "confidence": round(conf, 4)}
    s = int(np.argmax(scores))
    return {"offset_flaps": s if s <= n // 2 else s - n, "confidence": round(conf, 4)}


def calibrate_module_learned(capture_index, adjust, flap_chars: str, total_steps: int, *,
                             max_rounds: int = MAX_ROUNDS, confidence_min: float = CONFIDENCE_MIN,
                             should_abort=None, notify=None) -> dict:
    """Calibrate ONE module with LEARNED glyph recognition — dial A, verify you see A.

    1. LEARNING SWEEP: read every flap once, in commanded order — the module's own reel becomes
       its glyph library (``learn_reel``). The sweep also catches skipped flaps (consecutive
       identical reads) and gives the sub-flap shift.
    2. ANCHOR x2: the blank (flatness) and the rendered-reference consensus must agree on the
       absolute rotation. Confident disagreement = needs_review; a confident consensus rescues
       an ambiguous blank (solid-colour flaps are exactly as flat); both unsure = needs_review.
    3. CORRECT: nudge, then spot-check (the blank by flatness + up to two glyph witnesses, so a
       single lookalike misread can't steer the loop) until it reads zero. If anything was
       corrected, RE-LEARN: sweep again at the corrected offset — the seam cuts every glyph at
       a fixed window height, so a library learned at the old shift cannot fully verify the new
       one — and the fresh sweep's anchors must independently agree the module now sits at zero.
    4. VERIFY: re-read EVERY flap and identify it against the library. Each line is the literal
       check 'dialed 3 — saw 3' / 'saw 4 instead'; an unsure match (margin below
       ``GLYPH_MARGIN_MIN``) is flagged by name, never asserted. The flat flaps (blank,
       colours) are verified by flatness — grayscale cannot tell WHICH flat flap is showing.
    """
    n = len(flap_chars)
    blank_index = flap_chars.index(" ") if " " in flap_chars else 0
    spf = steps_per_flap(total_steps, n)
    say0 = notify or (lambda kind, data: None)

    def say(kind, data):
        if kind == "probe" and "flap" in data:
            data = {**data, "char": flap_chars[data["flap"] % n]}
        say0(kind, data)

    def aborted():
        return should_abort is not None and should_abort()

    def fail(status, rounds, **extra):
        return {"status": status, "rounds": rounds, "applied_steps": applied, **extra,
                "history": history}

    applied, history = 0, []

    def full_sweep():
        """One narrated pass over every flap: ``(tiles, skips)``, tiles None on abort. A skip
        suspect = two consecutive near-identical reads — a flap that never presented. Each
        sweep reports its OWN suspects: on a misaligned module two adjacent lookalikes can
        genuinely match mid-window (a shifted E loses the bar that tells it from F), so only
        the final, aligned sweep's verdict stands."""
        out, found, prev = [], [], None
        for i in range(n):
            if aborted():
                return None, found
            t = capture_index(i)
            say("probe", {"flap": i, "ink": round(tile_ink(t), 3)})
            if prev is not None and float(np.mean(np.abs(_pool4(prev) - _pool4(t)))) < 2.0:
                found.append(i - 1)
                say("skip_suspect", {"flap": i - 1, "char": flap_chars[(i - 1) % n]})
            out.append(t)
            prev = t
        return out, found

    def learn_and_anchor(tiles):
        """``learn_reel`` + the independent anchors and their agreement rules. Returns
        ``(lib, offset, conf, used)`` — ``used`` says how the rotation was decided: the names
        of the agreeing confident anchors joined by '+' (e.g. "blank+ocr"), or "conflict"
        (confident anchors disagree) / "none" (every anchor abstained) — don't proceed on
        either. When the blank didn't vote (colour flaps are just as flat), the agreeing
        anchors RESCUE the labelling and the library is relabelled to their rotation."""
        lib = learn_reel(tiles, flap_chars)
        shape = consensus_shift(tiles, flap_chars)
        ocr = ocr_shift(tiles, flap_chars)
        votes = {}
        if lib["confidence"] >= confidence_min:
            votes["blank"] = lib["offset_flaps"]
        if shape["offset_flaps"] is not None:
            votes["font"] = shape["offset_flaps"]
        if ocr["offset_flaps"] is not None:
            votes["ocr"] = ocr["offset_flaps"]
        data = {"blank": lib["offset_flaps"], "font": shape["offset_flaps"],
                "ocr": ocr["offset_flaps"], "ocr_hits": ocr["hits"],
                "confidence": lib["confidence"]}
        if not votes:
            used, offset, conf = "none", lib["offset_flaps"], lib["confidence"]
        elif len(set(votes.values())) > 1:
            used, offset, conf = "conflict", lib["offset_flaps"], lib["confidence"]
        else:
            used = "+".join(k for k in ("blank", "font", "ocr") if k in votes)
            offset = next(iter(votes.values()))
            conf = lib["confidence"] if "blank" in votes else \
                (shape["confidence"] if "font" in votes
                 else round(min(1.0, ocr["hits"] / 16), 4))
            if offset != lib["offset_flaps"]:
                lib["templates"] = {flap_chars[(i + offset) % n]: glyph_pool(tiles[i])
                                    for i in range(n)}
                lib["blankness"] = {flap_chars[(i + offset) % n]: tile_blankness(tiles[i])
                                    for i in range(n)}
        say("anchor", {**data, "used": used})
        return lib, offset, conf, used

    # -- 1. the learning sweep ------------------------------------------------
    tiles, skips = full_sweep()
    if tiles is None:
        return fail("failed", 0, reason="aborted")
    inks = sorted(tile_ink(t) for t in tiles)
    evidence = {"baseline": round(inks[n // 2], 4), "blank_ink": round(inks[0], 4)}

    # -- 2. the two anchors must agree on the absolute rotation ----------------
    lib, offset, conf, used = learn_and_anchor(tiles)
    if used in ("conflict", "none"):
        return fail("needs_review", 1,
                    final={"offset_flaps": offset, "sub_flap": 0.0,
                           "confidence": conf, **evidence})

    flats = lib["blankness"]
    flat_cut = flats.get(" ", 0.0) * 2.5 + 0.03      # flat flaps: the blank + the colours

    def is_flat(ch):
        return flats.get(ch, 1.0) <= flat_cut

    j = (blank_index - offset) % n                   # where the blank sat in the sweep
    nb = next(((blank_index + k) % n for k in range(1, n)
               if not is_flat(flap_chars[(blank_index + k) % n])), (blank_index + 1) % n)
    nb2 = next(((nb + k) % n for k in range(1, n)
                if not is_flat(flap_chars[(nb + k) % n])), nb)
    sub = tile_v_centroid(tiles[(j + (nb - blank_index)) % n]) - 0.5
    say("learned", {"offset_flaps": offset, "sub_flap": round(sub, 3), "confidence": conf})
    history.append({"round": 0, "offset_flaps": offset, "sub_flap": round(sub, 4),
                    "confidence": conf})

    # Corrections compare captures against a library learned at ANOTHER sub-flap shift, so the
    # spot-check identifies under a wide vertical roll search (the seam, which does not move
    # with the glyph, is masked at both of its positions — see ``_seam_mask``).
    search_rolls = tuple(range(-(TILE_H // 2), TILE_H // 2 + 1, 2))

    def reel_shift(ch, at):
        s = (flap_chars.index(ch) - at) % n if ch in flap_chars else 0
        return s if s <= n // 2 else s - n

    # -- 3. correct until a FRESH sweep proves zero ----------------------------
    # The library must describe the wall AS IT STANDS when the final verify runs: a correction
    # moves every glyph relative to the seam (which stays fixed mid-window), so after any
    # nudge the module is RE-SWEPT and re-anchored — the fresh anchors must independently
    # agree on zero, and a residual the fresh sweep still shows is corrected against the
    # fresh library, within the shared round budget.
    sign, prev_err = 1, None
    rounds_left, fresh = max_rounds, True            # fresh: lib/tiles match the wall right now
    while True:
        while not (offset == 0 and abs(sub) < 0.08):
            if aborted():
                return fail("failed", len(history), reason="aborted")
            if rounds_left <= 0:
                return fail("failed", len(history),
                            final={"offset_flaps": offset, "sub_flap": round(sub, 4),
                                   "confidence": conf, **evidence})
            rounds_left -= 1
            err = offset + sub
            if prev_err is not None and abs(err) > abs(prev_err) + 0.02:
                sign = -sign
            delta = int(round(sign * -err * spf)) or (-sign if err > 0 else sign)
            say("adjust", {"steps": delta})
            adjust(delta)
            applied += delta
            prev_err = err
            fresh = False
            # spot-check, narrated: the blank back at its slot (by flatness — identity can't
            # tell near-flat tiles apart) plus a glyph witness; a whole-flap claim needs a
            # SECOND witness to agree, so one lookalike misread can't steer the loop
            t0 = capture_index(blank_index)
            blank_ok = tile_blankness(t0) <= flat_cut
            say("verify", {"flap": blank_index, "expected": " ",
                           "saw": " " if blank_ok else "?", "ok": blank_ok, "flat": True,
                           "margin": 1.0 if blank_ok else 0.0})
            t1 = capture_index(nb)
            ch1, _, m1 = identify(t1, lib["templates"], rolls=search_rolls)
            shift1 = reel_shift(ch1, nb)
            say("verify", {"flap": nb, "expected": flap_chars[nb], "saw": ch1,
                           "ok": shift1 == 0, "margin": m1})
            if shift1 == 0 and blank_ok:
                offset = 0
            else:
                t2 = capture_index(nb2)
                ch2, _, m2 = identify(t2, lib["templates"], rolls=search_rolls)
                shift2 = reel_shift(ch2, nb2)
                say("verify", {"flap": nb2, "expected": flap_chars[nb2], "saw": ch2,
                               "ok": shift2 == 0, "margin": m2})
                offset = shift1 if shift1 == shift2 else (0 if blank_ok else shift2)
            sub = tile_v_centroid(t1) - 0.5
            history.append({"round": len(history), "offset_flaps": offset,
                            "sub_flap": round(sub, 4), "confidence": conf})
        if fresh:
            break                                    # the library IS the wall as it stands
        # re-learn at the corrected offset, and prove the correction on the fresh sweep
        # (whose skip verdict replaces the misaligned sweep's — see full_sweep)
        say("relearn", {})
        tiles, skips = full_sweep()
        if tiles is None:
            return fail("failed", len(history), reason="aborted")
        inks = sorted(tile_ink(t) for t in tiles)
        evidence = {"baseline": round(inks[n // 2], 4), "blank_ink": round(inks[0], 4)}
        lib, offset, conf, used = learn_and_anchor(tiles)
        flats = lib["blankness"]
        flat_cut = flats.get(" ", 0.0) * 2.5 + 0.03
        if used in ("conflict", "none"):
            return fail("needs_review", len(history),
                        final={"offset_flaps": offset, "sub_flap": round(sub, 4),
                               "confidence": conf, **evidence})
        j2 = (blank_index - offset) % n
        sub = tile_v_centroid(tiles[(j2 + (nb - blank_index)) % n]) - 0.5
        fresh = True
        history.append({"round": len(history), "offset_flaps": offset,
                        "sub_flap": round(sub, 4), "confidence": conf})

    # -- 4. verify EVERY flap by identity, then OCR reads the reel back --------
    say("verify_start", {"flaps": n})
    verify_rolls = (-1, 0, 1)      # the library is at TODAY's registration; allow jitter only
    shifts, flagged, vtiles = [], [], []
    for i in range(n):
        if aborted():
            return fail("failed", len(history), reason="aborted")
        t = capture_index(i)
        vtiles.append(t)
        expected = flap_chars[i]
        if is_flat(expected):
            # no glyph to identify on the blank/colour flaps; prove a flat flap is presenting
            # (the sweep's skip check covers whether each of them stepped in turn)
            ok = tile_blankness(t) <= flat_cut
            say("verify", {"flap": i, "expected": expected, "saw": expected if ok else "?",
                           "ok": ok, "flat": True, "margin": 1.0 if ok else 0.0})
            if not ok:
                flagged.append(i)
            continue
        ch, score, margin = identify(t, lib["templates"], rolls=verify_rolls)
        ok = ch == expected and margin >= GLYPH_MARGIN_MIN
        say("verify", {"flap": i, "expected": expected, "saw": ch, "ok": ok,
                       "margin": margin})
        if ch in flap_chars and not is_flat(ch):
            shifts.append(reel_shift(ch, i))
        if not ok:
            flagged.append(i)
    consistent_shift = int(np.median(shifts)) if shifts else 0
    blank_ok = blank_index not in flagged

    # OCR validates WHICH characters the verified reel is showing — in bulk, where it is
    # measured-strong (a lone glyph opinion never vetoes a flap; the alignment of many does).
    ops = ocr_tile_opinions(vtiles, flap_chars)
    ver = align_opinions(ops, flap_chars)
    ocr_ok = ver["offset_flaps"] in (None, 0)
    if ocr_available():
        say("ocr_check", {"shift": ver["offset_flaps"], "hits": ver["hits"],
                          "votes": ver["votes"], "ok": ocr_ok})
    for i in flagged:
        if i in ops:            # a disputed flap: give OCR's reading of it too
            say("verify_ocr", {"flap": i, "expected": flap_chars[i], "ocr": ops[i]})

    say("reel_verified" if (not flagged and not skips and ocr_ok) else "reel_issues",
        {"skips": len(skips) + len(flagged), "blank_ok": blank_ok})

    aligned = (consistent_shift == 0 and not flagged and not skips
               and abs(sub) < 0.08 and ocr_ok)
    status = ("aligned" if applied == 0 else "adjusted") if aligned else \
             ("failed" if (consistent_shift != 0 or not ocr_ok) else "needs_review")
    out = fail(status, len(history),
               final={"offset_flaps": consistent_shift, "sub_flap": round(sub, 4),
                      "confidence": conf, **evidence})
    out["reel_check"] = {"ok": aligned, "skips": skips, "blank_ok": blank_ok,
                         "flagged": flagged,
                         "ocr": {"shift": ver["offset_flaps"], "hits": ver["hits"],
                                 "votes": ver["votes"]}}
    return out


class CalibrationSession:
    """The wall-level job: calibrate every module in turn, one at a time, abortably, and be
    able to put every offset back the way it was.

    Side effects are injected, so the whole run is tested against the simulated wall. Each
    ``module`` is just ``{id, grid_index}``; its reel is read LAZILY when its turn comes (the
    gateway's list endpoint doesn't carry the reel/offset — only the per-module detail does, so
    fetching it up front for the whole wall would stall the start):
      - ``meta_fn(id)``        -> ``{flap_chars, total_steps, home_offset}`` or None when the
                                  module isn't ready (still learning its reel / no blank);
      - ``capture_index(id, i)`` -> command module ``id`` to flap ``i`` and return its tile;
      - ``adjust(id, steps)``  -> a LIVE relative correction (the gateway's nudge: moves the
                                  reel and saves the offset instantly);
      - ``restore(id, steps)`` -> write an ABSOLUTE home offset back and re-home so it takes
                                  effect (only used by ``revert``);
      - ``park_fn(id)``        -> leave a finished module on its blank (home);
      - ``peek_fn(id)``        -> the module's CURRENT tile, commanding nothing — how the
                                  post-home check confirms the blank is really showing.
    ``snapshot`` records each module's saved offset (from its own EEPROM, via ``meta_fn``) as its
    turn comes, so ``revert()`` undoes exactly what the run touched. Every module's run ends with
    a per-flap identity verification — nothing is inferred from the blank alone.
    """

    def __init__(self, modules, *, meta_fn, capture_index, adjust, restore, park_fn=None,
                 peek_fn=None, log_fn=None):
        self.modules = list(modules)
        self._meta = meta_fn
        self._capture_index = capture_index
        self._adjust = adjust
        self._restore = restore
        self._park = park_fn
        self._peek = peek_fn
        self._log = log_fn or (lambda mid, kind, data: None)
        self._abort = False
        self.snapshot: dict[int, int] = {}
        self.results: dict[int, dict] = {}
        self.done = False

    def abort(self) -> None:
        self._abort = True

    @property
    def progress(self) -> dict:
        return {"total": len(self.modules), "completed": len(self.results),
                "aborted": self._abort, "done": self.done,
                "summary": self._summary(), "results": dict(self.results)}

    def _summary(self) -> dict:
        s = {"aligned": 0, "adjusted": 0, "needs_review": 0, "failed": 0}
        for r in self.results.values():
            s[r["status"]] = s.get(r["status"], 0) + 1
        return s

    def run(self) -> dict:
        for m in self.modules:
            if self._abort:
                break
            mid = m["id"]
            self._log(mid, "start", {})
            meta = self._meta(mid)
            if not meta:
                self.results[mid] = {"status": "needs_review", "reason": "module not ready"}
                self._log(mid, "not_ready", {})
                continue
            self.snapshot[mid] = meta["home_offset"]           # its own saved value, pre-run
            # The learned flow: one sweep teaches this module's own glyphs, corrections are
            # spot-checked, and EVERY flap is then verified by identity — dialed A must match
            # the learned A. Nothing is inferred from the blank alone: a move that overshoots
            # after a perfect blank is exactly what the per-flap verify exists to catch.
            res = calibrate_module_learned(
                lambda i, mid=mid: self._capture_index(mid, i),
                lambda s, mid=mid: self._adjust(mid, s),
                meta["flap_chars"], meta["total_steps"], should_abort=lambda: self._abort,
                notify=lambda kind, data, mid=mid: self._log(mid, kind, data))
            self.results[mid] = res
            self._log(mid, "result", {"status": res["status"],
                                      "applied_steps": res.get("applied_steps", 0)})
            # A finished module ends on its blank (a home) — and the run then LOOKS at it: a
            # re-home that lands on a printed flap is reported, never assumed fine. (A module
            # once reported "homed" while visibly showing a glyph; this check is why.)
            if self._park is not None and not self._abort:
                self._park(mid)
                if self._peek is not None:
                    ink = tile_ink(self._peek(mid))
                    ok = looks_blank(ink, res.get("final", {}))
                    self._log(mid, "park_check", {"ok": ok, "ink": round(ink, 3)})
                    if not ok:
                        res["status"] = "needs_review"
                        res["park_failed"] = True
                else:
                    self._log(mid, "parked", {})
        self.done = not self._abort
        return self.progress

    def revert(self) -> None:
        """Restore every touched module's home offset to its pre-run value — an ABSOLUTE write
        plus a re-home, because a written offset only takes effect when the reel re-homes."""
        for mid, steps in self.snapshot.items():
            self._restore(mid, steps)


# ---------------------------------------------------------------------------
# AutocalRun — the live job: the browser streams camera frames, this drives the
# CalibrationSession server-side (so all the logic stays in the tested Python).
#
# The browser owns the camera; it POSTs the latest frame continuously and clicks the four
# wall corners once. This object commands the gateway, waits for the mechanical settle, reads
# whatever frame is current (settled by then), rectifies this module's tile and feeds the
# session. Gateway calls are async; the session loop is synchronous and runs in a worker
# thread, bridging back to the event loop via run_coroutine_threadsafe. Side effects are
# injectable (``commander`` / ``capture``) so the whole run is tested without a camera or a
# gateway — see test_autocal.py.
# ---------------------------------------------------------------------------
class AutocalRun:
    def __init__(self, *, gateway_url, corners, rows, cols, modules, settle_s=4.0,
                 loop=None, commander=None, adjuster=None, restorer=None, capture=None, meta=None):
        # modules: [{id, grid_index}] — the reel/offset is fetched lazily per module (meta).
        self.gateway_url = gateway_url
        self.corners = corners
        self.rows, self.cols = rows, cols
        self.settle_s = settle_s
        self.modules = {m["id"]: m for m in modules}
        self._loop = loop
        self._commander = commander          # test seam: commander(id, index)
        self._adjuster = adjuster            # test seam: adjuster(id, steps) — the live nudge
        self._restorer = restorer            # test seam: restorer(id, steps) — absolute + home
        self._capture = capture              # test seam: capture(grid_index) -> tile
        self._meta_seam = meta               # test seam: meta(id) -> {flap_chars,total_steps,home_offset}
        self._frame = None                   # (jpeg bytes, ts)
        self._reel: dict[int, str] = {}      # flap_chars per module (settle timing, parking)
        self._pos: dict[int, int] = {}       # last commanded flap index per module (settle timing)
        self.activity = None                 # {module, action} — what it's doing right now
        self.events: list[dict] = []         # the live narration the UI renders (bounded)
        self._seq = 0
        self.error = None
        self.finished = False
        self.session = CalibrationSession(
            [{"id": m["id"], "grid_index": m["grid_index"]} for m in modules],
            meta_fn=self._meta, capture_index=self._capture_index,
            adjust=self._adjust, restore=self._restore, park_fn=self._park,
            peek_fn=self._peek, log_fn=self._log_event)

    def _log_event(self, mid, kind, data) -> None:
        """One line of the live narration: what the run measured / did on which module. The UI
        turns these structured events into readable text — see the Calibrate page's log."""
        self._seq += 1
        self.events.append({"seq": self._seq, "module": self.modules[mid]["grid_index"],
                            "kind": kind, **(data or {})})
        if len(self.events) > 400:
            del self.events[:100]

    def _park(self, mid):
        """Leave a finished module on its blank by RE-HOMING it — which both lands on flap 0
        (the blank) and confirms the hall sync, per the calibration doc's "always finish with
        Home Motor"."""
        self._say(mid, "parking")
        self._pos[mid] = 0
        if self._commander is not None:
            reel = self._reel.get(mid, "")
            self._commander(mid, reel.index(" ") if " " in reel else 0)
            return
        from . import gateway
        self._run_coro(gateway.flap_home(self.gateway_url, mid))

    def _meta(self, mid):
        """Read module ``mid``'s reel + SAVED calibration (``/api/flap/all``): its flap set,
        steps/rev and current home offset, straight from its own EEPROM. Returns None when the
        module isn't ready (no dump / no blank to anchor on)."""
        if self._meta_seam is not None:
            m = self._meta_seam(mid)
        else:
            from . import gateway
            raw = self._run_coro(gateway.flap_all(self.gateway_url, mid))
            dump = gateway._parse_dump(raw.get("dump", ""))
            chars = raw.get("flapChars") or ""
            if dump is None or not chars or " " not in chars:
                return None
            m = {"flap_chars": chars, "total_steps": dump[1], "home_offset": dump[0]}
        if not m:
            return None
        self._reel[mid] = m["flap_chars"]
        return dict(m)

    def _say(self, mid, action) -> None:
        """Record what the run is doing right now, for the UI's live line — a coarse PHASE
        (``reading`` the reel / ``adjusting`` the offset), per module. Deliberately NOT the flap
        character: on an un-calibrated module the glyph on the wall is NOT the one we commanded
        (that's what we're fixing), and we never read the actual glyph — so naming a character
        would be a lie, and the per-flap churn is faster than the screen refresh anyway."""
        self.activity = {"module": self.modules[mid]["grid_index"], "action": action}

    # -- frames --------------------------------------------------------------
    def push_frame(self, data: bytes) -> None:
        self._frame = (data, time.time())

    # -- gateway bridges (called from the worker thread) ---------------------
    def _run_coro(self, coro):
        import asyncio
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=20)

    def _command(self, mid, index):
        if self._commander is not None:
            self._commander(mid, index)
        else:
            from . import gateway
            self._run_coro(gateway.flap_show_index(self.gateway_url, mid, index))

    def _sleep_abortable(self, seconds: float) -> None:
        """Sleep in slices so a Stop lands within ~a step, never after a minutes-long wait."""
        end = time.time() + seconds
        while time.time() < end:
            if self.session is not None and self.session._abort:
                raise RuntimeError("aborted")
            time.sleep(min(0.2, max(0.0, end - time.time())))

    def _adjust(self, mid, steps):
        """A LIVE correction: the gateway's NUDGE moves the reel by ``steps`` AND saves the new
        home offset instantly — so the very next camera read sees the change. (An absolute
        home-offset write is invisible until the next re-home; using it here was the beta ≤6
        bug.) The nudge builds on the module's SAVED offset, and its per-flap Character Map is
        untouched — a hand-tuned reel keeps its tuning and just shifts as a whole."""
        self._say(mid, "adjusting")
        if self._adjuster is not None:
            self._adjuster(mid, steps)
            return
        from . import gateway
        self._run_coro(gateway.flap_nudge(self.gateway_url, mid, steps))
        # A negative nudge may wrap most of a revolution forward — allow the full settle.
        self._sleep_abortable(self.settle_s)

    def _restore(self, mid, steps):
        """Put a module's SAVED offset back (revert): an absolute write plus a re-home, because
        a written offset only takes effect when the reel re-homes."""
        if self._restorer is not None:
            self._restorer(mid, steps)
            return
        from . import gateway
        self._run_coro(gateway.flap_set_homeoffset(self.gateway_url, mid, steps))
        self._run_coro(gateway.flap_home(self.gateway_url, mid))

    def _capture_tile(self, grid_index):
        if self._capture is not None:
            return self._capture(grid_index)
        if self._frame is None:
            raise RuntimeError("no camera frame yet")
        img = Image.open(io.BytesIO(self._frame[0]))
        return tiles(img, self.corners, self.rows, self.cols)[grid_index]

    def _settle_for(self, mid, i) -> float:
        """How long to wait after commanding flap ``i`` — proportional to how far the reel
        actually turns, not a flat worst-case. A split-flap only moves FORWARD, so the move is
        ``(i - last) mod N`` flaps; ``settle_s`` is the FULL-revolution time, so one flap is a
        fraction of it. A +1 step waits a moment; only a near-full turn waits the whole time.
        The first command to a module (position unknown) waits the full settle."""
        n = len(self._reel.get(mid, "")) or 1
        last = self._pos.get(mid)
        self._pos[mid] = i
        if last is None:
            return self.settle_s
        dist = (i - last) % n
        return max(SETTLE_FLOOR_S, min(self.settle_s, SETTLE_FLOOR_S + (dist / n) * self.settle_s))

    def _fresh_tile(self, grid_index, after: float, timeout: float = 6.0):
        """This module's tile from a camera frame that arrived AFTER ``after`` — never a stale
        one from before/mid-move. Returns ``(tile, frame_ts)``.

        If no fresh frame arrives within ``timeout`` the stream has stopped — the user left the
        page, closed it, or the webcam went away — so RAISE rather than calibrate against a
        frozen image. The run then ends and the wall stops, instead of flipping on blind."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            fr = self._frame
            if fr is not None and fr[1] >= after:
                img = Image.open(io.BytesIO(fr[0]))
                return tiles(img, self.corners, self.rows, self.cols)[grid_index], fr[1]
            if self.session is not None and self.session._abort:
                raise RuntimeError("aborted")
            time.sleep(0.1)
        raise RuntimeError("camera stream stopped — calibration halted")

    def _settled_tile(self, gi):
        """A read PROVEN settled: two consecutive fresh frames whose tile agrees. A flap in
        flight smears and changes frame to frame; a settled one differs only by sensor noise."""
        tile, ts = self._fresh_tile(gi, time.time())
        deadline = time.time() + 2.5
        while time.time() < deadline:
            nxt, ts = self._fresh_tile(gi, ts + 1e-6)
            if tiles_similar(tile, nxt):
                return nxt
            tile = nxt
        return tile      # still changing — use the last read; the loop re-measures anyway

    def _capture_index(self, mid, i):
        """Command module ``mid`` to flap ``i`` and return its tile — only once the flap has
        VERIFIABLY settled (motion-time estimate, then proof of stillness). A timer alone is a
        guess; this is observation."""
        if self.session is not None and self.session._abort:
            raise RuntimeError("aborted")
        self._say(mid, "reading")
        wait = self._settle_for(mid, i)
        self._command(mid, i)
        gi = self.modules[mid]["grid_index"]
        if self._commander is not None:            # simulated wall — no physics to wait on
            return self._capture_tile(gi)
        self._sleep_abortable(wait)                # the motion-time estimate…
        return self._settled_tile(gi)              # …then PROOF of stillness

    def _peek(self, mid):
        """The module's CURRENT tile, commanding nothing — how the post-home check confirms a
        re-home genuinely landed on the blank rather than assuming it did."""
        gi = self.modules[mid]["grid_index"]
        if self._capture is not None:              # simulated wall
            return self._capture(gi)
        self._sleep_abortable(self.settle_s)       # a home can be a near-full revolution
        return self._settled_tile(gi)

    # -- lifecycle -----------------------------------------------------------
    def run_blocking(self) -> None:
        try:
            self.session.run()
        except Exception as e:               # noqa: BLE001 - surfaced in status
            if str(e) != "aborted":          # a Stop is a clean outcome, not an error
                self.error = str(e)
        finally:
            self.finished = True

    def abort(self) -> None:
        self.session.abort()

    def revert(self) -> None:
        self.session.revert()

    @property
    def status(self) -> dict:
        return {**self.session.progress, "finished": self.finished, "error": self.error,
                "settle_s": self.settle_s,
                "activity": None if self.finished else self.activity,
                "events": list(self.events)[-80:]}
