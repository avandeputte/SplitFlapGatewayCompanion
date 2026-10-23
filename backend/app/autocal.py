"""autocal.py — camera-based auto-calibration vision, no I/O.

Given a photo of the wall and the four corners the user clicked, it rectifies the wall to a
grid of per-module tiles; given a *sweep* of one module (a capture per commanded flap index
0..N-1), it works out how far that module's home offset is wrong — whole flaps plus sub-flap
vertical shift — and then PROVES the result flap by flap. The orchestration (driving the
gateway's /api/flap/* endpoints) and the SPA/webcam live around this; everything here is pure
functions over PIL images and numpy arrays, testable without a camera or a gateway.

HOW A MODULE IS CALIBRATED — the manual procedure, automated (``calibrate_module``). The reel
ORDER is the compass, so nothing is surveyed:
  1. The FIRMWARE measures Total Steps first (``/api/flap/calibrate`` — the calibration
     manual's golden order), then the module is HOMED and the camera looks: a blank means home
     is right; a LETTER means the home offset is too high (letters follow the blank on the
     reel) — lower it a quarter flap at a time; a symbol or colour flap means too low (they
     precede the blank) — raise it.
  2. EVERY CHARACTER is then dialed and read back, one at a time: seeing the NEXT character
     means a quarter flap down, seeing the blank or an earlier one means a quarter flap up.
     A correction shifts every flap, so the pass repeats until one full pass needs no
     correction. Recognition only ever answers a LOCAL 3-5-way question ("the dialed
     character, a neighbour, or a flat flap?") against rendered references + each character's
     own confirmed camera tile + an OCR vote — far sturdier than whole-reel classification.
  3. Sub-flap centering is polished, and OCR reads the confirmed reel back as a whole, which
     must agree everything sits where it should (``ocr_check``).

WHERE OCR FITS — measured, twice: tesseract on a seam-cut SINGLE glyph is unusable open-ended
(at best 6/22, with high-confidence WRONG reads — a seam-cut E genuinely is two bars and reads
as `=`), so a lone OCR opinion never decides a flap; narrowed to the 3-5 local candidates it
is a useful vote. Reading tiles as text LINES, each recognized character box-mapped back to
its source tile, lands about half its characters — and the cyclic alignment of ~50 such
opinions validates the whole reel with huge margins (measured 18/18, top 20-32 vs runner-up
2-4, under noise and low contrast). That bulk read-back is the run's final check.
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
def canonicalize(tile: np.ndarray) -> np.ndarray:
    """Normalise a tile to the polarity the whole pipeline assumes — a DARK glyph on a LIGHT
    flap. Many real walls are the opposite (white characters on black flaps), which shows up as
    a DARK flap background (a low median), so those tiles are inverted; an already-light flap
    passes through. Measuring ink, blankness, the sub-flap centroid and matching rendered
    references all assume a bright background with darker ink, so this one step makes both kinds
    of wall work. (A solid-colour flap is near-uniform either way, so its flatness is unchanged.)
    """
    return (255.0 - tile) if float(np.median(tile)) < 128.0 else tile


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
_FONT_PATH = Path(__file__).resolve().parent / "fonts" / "DejaVuSans-Bold.ttf"


def physical_glyph(ch: str) -> str:
    """What is PRINTED on the flap for a reel character: `q` is the double-quote flap."""
    return '"' if ch == "q" else ch


def render_reference(ch: str) -> np.ndarray:
    """What a flap printed with ``ch`` roughly looks like: a bold dark glyph on a light flap,
    cut by the horizontal seam every real flap has. The font is NOT assumed to match the
    wall's — a reference only has to look more like its own character than like its few
    neighbours, which is all the local identity check (``identify`` + ``window``) asks."""
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


QUARTER_FLAP_DIV = 4        # the correction quantum: a quarter flap (16 steps on a 64-step flap)
HOME_WALK_MAX = 24          # quarter-flap nudges before the home/landmark search gives up


def calibrate_module(capture_index, adjust, home, flap_chars: str, total_steps: int, *,
                     max_passes: int = 3, should_abort=None, notify=None) -> dict:
    """Calibrate ONE module the way a person does it at the wall — leveraging what the reel
    ORDER already says instead of surveying the reel:

    1. HOME: re-home and look. A blank means home is right. A LETTER means the home offset is
       too high (letters follow the blank on the reel) — lower it a quarter flap at a time
       until the blank shows. A symbol or a colour flap means too low (they precede the blank)
       — raise it. (The white colour flap can pass for the blank here; step 2 catches that.)
    2. EVERY CHARACTER, one at a time: dial it, read it. Seeing the NEXT character means a
       quarter flap back; seeing the blank or an earlier one means a quarter flap up; re-check
       and move on. A correction shifts every flap, so the pass repeats until one full pass
       needs no correction at all.
    3. Polish the sub-flap centering, then OCR reads the confirmed reel back as a whole
       (``align_opinions``) and must agree everything sits where it should.

    Recognition only ever answers a LOCAL question — "is this the dialed character, one of its
    neighbours, or a flat flap?" — a 3-5-way choice against rendered references (plus each
    character's own camera tile once confirmed, plus an OCR vote), far sturdier than any
    whole-reel classification. ``adjust`` must be the gateway NUDGE (moves the reel AND saves
    the offset); ``home`` must be a real re-home (hall re-sync). Firmware step-count
    calibration happens before this is called (the session drives it).
    """
    n = len(flap_chars)
    blank_index = flap_chars.index(" ") if " " in flap_chars else 0
    spf = steps_per_flap(total_steps, n)
    inc = max(1, int(round(spf / QUARTER_FLAP_DIV)))
    say = notify or (lambda kind, data: None)
    refs = reference_templates(flap_chars)
    learned: dict[str, np.ndarray] = {}        # camera-confirmed glyphs, built as we go
    rolls = tuple(range(-14, 15, 2))

    def aborted():
        return should_abort is not None and should_abort()

    def fail(status, **extra):
        # every result carries a reel_check so callers read it uniformly; a path that ends
        # before the full verify (home never found) reports an empty, not-ok one.
        out = {"status": status, "rounds": passes, "applied_steps": applied,
               "reel_check": {"ok": False, "skips": [], "blank_ok": False, "flagged": [],
                              "ocr": {"shift": None, "hits": 0, "votes": 0}},
               "history": history}
        out.update(extra)
        return out

    def is_flat_char(ch):
        return ch == " " or ch in COLOR_LETTERS

    def window(i, span=2):
        """The local printable candidates around flap ``i`` (confirmed camera tiles first,
        rendered references otherwise)."""
        tpl = {}
        for d in range(-span, span + 1):
            ch = flap_chars[(i + d) % n]
            if not is_flat_char(ch):
                p = learned.get(ch, refs.get(ch))
                if p is not None:
                    tpl[ch] = p
        return tpl

    def reel_shift(ch, at):
        s = (flap_chars.index(ch) - at) % n if ch in flap_chars else 0
        return s if s <= n // 2 else s - n

    applied, passes = 0, 0
    history: list[dict] = []

    # -- 1. home, then find the blank by reading what shows --------------------
    # A mid-reel capture first: a certainly-printed flap is the flatness yardstick AND gives the
    # flap-background brightness, which separates the blank (a bright, empty flap) from a
    # solid-colour flap (a uniform fill — flat too, but most are markedly darker).
    probe = capture_index((blank_index + n // 2) % n)
    printed_flat = tile_blankness(probe)
    bg_level = float(np.percentile(probe, 85))     # the light flap background
    home()
    flat_cut = max(0.3 * printed_flat, 0.02)
    flap_steps = max(1, int(round(spf)))
    landmark_i = next(((blank_index + k) % n for k in range(1, n)
                       if not is_flat_char(flap_chars[(blank_index + k) % n])), None)
    lchar = flap_chars[landmark_i] if landmark_i is not None else None

    def home_kind(tile):
        """What is showing at a slot: a ``glyph`` (ink — a letter/symbol), a ``dark`` colour
        flap (flat but far below the flap background), or ``light`` (the blank, or a pale
        colour like white/yellow)."""
        if tile_blankness(tile) > flat_cut:
            return "glyph"
        return "dark" if float(np.median(tile)) < bg_level - 35 else "light"

    # Find the blank by its UNIQUE signature, not by reading a glyph (single-flap glyph reads
    # are too noisy to steer on): the blank is the one bright, flat flap whose NEXT slot shows
    # a glyph (letters follow the blank; a colour's neighbour is another flat colour). The reel
    # is circular, so stepping ONE whole flap at a time in a single direction is guaranteed to
    # bring the blank into the home slot within a revolution, whatever the nudge sign maps to.
    def blank_here():
        th = capture_index(blank_index)
        if home_kind(th) != "light":
            return False, th, home_kind(th)
        if landmark_i is None:
            return True, th, "light"
        return (home_kind(capture_index(landmark_i)) == "glyph"), th, "light"

    def name_at_home(tile):
        """Which letter is showing at the home slot (best effort, against the rendered
        references) — for the log, so the reel is visibly cycling and a skipped blank shows."""
        if not refs:
            return "?"
        return identify(tile, refs, rolls=rolls)[0]

    # Center whatever glyph is at home FIRST. Full-flap steps preserve the reel's fractional
    # offset, so if it sits ~half a flap off, the blank would never land cleanly in the window:
    # it would half-show, read as a letter, and the sweep would step right OVER it. The vertical
    # centroid is a reliable gradient (unlike glyph identity), so a few small nudges — direction
    # discovered by what shrinks the offset — bring the fraction to ~0 and the blank will land
    # cleanly when it comes around.
    t = capture_index(blank_index)
    if home_kind(t) == "glyph":
        cstep, cdir = max(1, inc // 2), 1
        c = tile_v_centroid(t) - 0.5
        for _ in range(8):
            if abs(c) < 0.08 or aborted():
                break
            adjust(cdir * cstep)
            applied += cdir * cstep
            c2 = tile_v_centroid(capture_index(blank_index)) - 0.5
            if abs(c2) > abs(c) + 0.02:            # that way made it worse -> reverse
                cdir = -cdir
            c = c2

    confirmed, blank_t = False, None
    for _ in range(n + 1):
        if aborted():
            return fail("failed", reason="aborted")
        ok, th, kind = blank_here()
        if ok:
            confirmed, blank_t = True, th
            break
        # NAME the letter (so the cycling A,B,C… is visible and a skipped blank is obvious)
        say("seeking", {"saw": "glyph", "char": name_at_home(th)} if kind == "glyph"
            else {"saw": kind})
        delta = flap_steps                         # one whole flap forward; circular -> hits it
        say("adjust", {"steps": delta})
        adjust(delta)
        applied += delta
    if not confirmed:
        return fail("needs_review",
                    final={"offset_flaps": 0, "sub_flap": 0.0, "confidence": 0.0,
                           "baseline": round(tile_ink(probe), 4),
                           "blank_ink": round(tile_ink(capture_index(blank_index)), 4)})
    blank_flat = tile_blankness(blank_t)
    flat_cut = blank_flat * 2.5 + 0.03
    blank_ink = tile_ink(blank_t)
    say("verify", {"flap": blank_index, "expected": " ", "saw": " ", "ok": True,
                   "flat": True, "margin": 1.0})

    # -- 2a. center the sub-flap, using the 'A' landmark glyph -----------------
    # The quarter-flap home search stops at the first blank-ish capture, which can sit a quarter
    # flap low; a reel riding low makes every glyph read as a near-miss, so trim it to centre
    # against the landmark's own centroid before verifying (a trim that worsens it is reversed).
    if landmark_i is not None:
        sub0 = tile_v_centroid(capture_index(landmark_i)) - 0.5
        if abs(sub0) >= 0.06:
            k = int(np.clip(int(round(-sub0 * spf)), -inc, inc)) or (-1 if sub0 > 0 else 1)
            say("adjust", {"steps": k})
            adjust(k)
            applied += k
            s1 = tile_v_centroid(capture_index(landmark_i)) - 0.5
            if abs(s1) > abs(sub0) + 0.02:
                say("adjust", {"steps": -2 * k})
                adjust(-2 * k)
                applied += -2 * k

    # -- 2b. verify every character; only a WHOLE-REEL shift corrects the home -------------
    # The home offset is global: nudging it to chase ONE flap would wreck the flaps already
    # right (individual flaps are the per-flap Character Map's job, which we leave untouched).
    # So each pass READS every flap and flags what is wrong, and the reel only moves when a
    # strong MAJORITY of confident flaps agree on the very same shift — a genuine whole-reel
    # offset — corrected in whole flaps with the learned direction, then re-verified. A lone
    # flap one off is a recognition miss or a bad flap: flagged for a human, never chased.
    tiles_last: list = [None] * n
    skips: list[int] = []
    flagged: set[int] = set()
    letter_inks: list[float] = []
    clean = False
    for pass_no in range(1, max_passes + 1):
        passes = pass_no
        say("verify_start", {"flaps": n})
        skips, flagged, letter_inks, shifts, prev = [], set(), [], [], None
        for i in range(n):
            if aborted():
                return fail("failed", reason="aborted")
            expected = flap_chars[i]
            t = capture_index(i)
            tiles_last[i] = t
            if prev is not None and float(np.mean(np.abs(_pool4(prev) - _pool4(t)))) < 2.0:
                skips.append(i - 1)
                say("skip_suspect", {"flap": i - 1, "char": flap_chars[(i - 1) % n]})
            prev = t
            flat = tile_blankness(t) <= flat_cut
            if is_flat_char(expected):
                say("verify", {"flap": i, "expected": expected,
                               "saw": expected if flat else "?", "ok": flat, "flat": True,
                               "margin": 1.0 if flat else 0.0})
                if not flat:
                    flagged.add(i)
                continue
            if flat:                            # a glyph flap showing a blank/colour flap
                say("verify", {"flap": i, "expected": expected, "saw": " ", "ok": False,
                               "margin": 0.0})
                flagged.add(i)
                continue
            ch, score, margin = identify(t, window(i), rolls=rolls)
            shift = reel_shift(ch, i)
            ocr_vote = ocr_tile_opinions([t], "".join(window(i))).get(0)
            confident = margin >= GLYPH_MARGIN_MIN or ocr_vote == ch
            ok = shift == 0 and (margin >= GLYPH_MARGIN_MIN or ocr_vote == expected)
            say("verify", {"flap": i, "expected": expected, "saw": ch, "ok": ok,
                           "margin": margin})
            if ok:
                learned[expected] = glyph_pool(t)
                letter_inks.append(tile_ink(t))
            else:
                flagged.add(i)
                if confident and abs(shift) <= 2:   # a vote on a possible global shift
                    shifts.append(shift)
        # The blank-home is trusted (found by the direction-agnostic sweep + centering), so a
        # verify pass that still reads a strong whole-reel shift means the blank detection was
        # fooled — land on needs_review and report it, rather than risk a directional nudge off
        # a good home. A scattered handful of misreads just flags those flaps.
        consensus = 0
        if shifts:
            best = max(set(shifts), key=shifts.count)
            if best != 0 and shifts.count(best) >= max(8, int(0.75 * len(shifts))):
                consensus = best
        history.append({"pass": pass_no, "consensus": consensus, "flagged": len(flagged),
                        "skips": len(skips)})
        clean = consensus == 0 and not flagged and not skips
        break

    # -- 3. sub-flap polish, then OCR reads the reel back ----------------------
    sub_src = [i for i in range(n) if not is_flat_char(flap_chars[i]) and i not in flagged
               and tiles_last[i] is not None]
    sub = float(np.median([tile_v_centroid(tiles_last[i]) - 0.5 for i in sub_src]))         if sub_src else 0.0
    if abs(sub) >= 0.08 and sub_src and not aborted():
        # one fractional trim (quarter-flap corrections quantize the offset); the nudge
        # direction is hardware-dependent, so a trim that worsens it is reversed once
        j = sub_src[len(sub_src) // 2]
        k = int(np.clip(int(round(-sub * spf)), -inc, inc)) or (-1 if sub > 0 else 1)
        say("adjust", {"steps": k})
        adjust(k)
        applied += k
        t = capture_index(j)
        s2 = tile_v_centroid(t) - 0.5
        if abs(s2) > abs(sub) + 0.02:
            say("adjust", {"steps": -2 * k})
            adjust(-2 * k)
            applied += -2 * k
            t = capture_index(j)
            s2 = tile_v_centroid(t) - 0.5
        sub = s2
        tiles_last[j] = t

    ops = ocr_tile_opinions(tiles_last, flap_chars)
    ver = align_opinions(ops, flap_chars)
    ocr_ok = ver["offset_flaps"] in (None, 0)
    if ocr_available():
        say("ocr_check", {"shift": ver["offset_flaps"], "hits": ver["hits"],
                          "votes": ver["votes"], "ok": ocr_ok})
    for i in sorted(flagged):
        if i in ops:                           # a disputed flap: give OCR's reading of it too
            say("verify_ocr", {"flap": i, "expected": flap_chars[i], "ocr": ops[i]})
    blank_ok = blank_index not in flagged
    say("reel_verified" if (clean and not flagged and not skips and ocr_ok) else "reel_issues",
        {"skips": len(skips) + len(flagged), "blank_ok": blank_ok})

    baseline = float(np.median(letter_inks)) if letter_inks else tile_ink(probe)
    evidence = {"baseline": round(baseline, 4), "blank_ink": round(blank_ink, 4)}
    good = clean and not flagged and not skips and ocr_ok and abs(sub) < 0.12
    status = ("aligned" if applied == 0 else "adjusted") if good else \
             ("failed" if ver["offset_flaps"] not in (None, 0) else "needs_review")
    out = fail(status,
               final={"offset_flaps": int(ver["offset_flaps"] or 0), "sub_flap": round(sub, 4),
                      "confidence": round(1.0 - len(flagged) / max(1, n), 4), **evidence})
    out["reel_check"] = {"ok": good, "skips": skips, "blank_ok": blank_ok,
                         "flagged": sorted(flagged),
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
                                  post-home check confirms the blank is really showing;
      - ``home_fn(id)``        -> re-home one module mid-run (defaults to ``park_fn``);
      - ``fw_calibrate_fn(id)``-> the FIRMWARE's steps-per-revolution measurement; returns the
                                  measured steps or None. Runs first, per the calibration
                                  manual's golden order: Total Steps, then Home Offset, then
                                  individual characters.
    ``snapshot`` records each module's saved offset (from its own EEPROM, via ``meta_fn``) as its
    turn comes, so ``revert()`` undoes exactly what the run touched. Every module's run ends with
    a per-flap check — every character dialed and read back.
    """

    def __init__(self, modules, *, meta_fn, capture_index, adjust, restore, park_fn=None,
                 peek_fn=None, home_fn=None, fw_calibrate_fn=None, log_fn=None):
        self.modules = list(modules)
        self._meta = meta_fn
        self._capture_index = capture_index
        self._adjust = adjust
        self._restore = restore
        self._park = park_fn
        self._peek = peek_fn
        self._home = home_fn or park_fn
        self._fw_calibrate = fw_calibrate_fn
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
            # Read the reel FIRST, while the module is responsive. Firmware calibration spins the
            # reel a full revolution and leaves the module briefly unable to answer a bus query
            # (an EEPROM read then comes back empty/stale) — so reading before any maintenance is
            # what keeps "not ready" from being a false alarm.
            meta = self._meta(mid)
            if not meta:
                self.results[mid] = {"status": "needs_review", "reason": "module not ready"}
                self._log(mid, "not_ready", {})
                continue
            self.snapshot[mid] = meta["home_offset"]           # its own saved value, pre-run
            # Total Steps first (the golden order), but only MEASURE it when it isn't already
            # known: re-spinning an already-calibrated reel every run is slow and needless. When
            # the saved value is missing/zero, the firmware measures it and we use that.
            if self._fw_calibrate is not None and not meta.get("total_steps"):
                steps = self._fw_calibrate(mid)
                self._log(mid, "fw_calibrate", {"steps": steps, "measured": True})
                if steps:
                    meta["total_steps"] = steps
            else:
                self._log(mid, "fw_calibrate", {"steps": meta.get("total_steps"),
                                                "measured": False})
            # The guided flow: home and read what shows (the reel ORDER says which way to go),
            # walk to the blank in quarter-flap nudges, then dial every character and read it
            # back, correcting the same way, until a full pass needs no correction.
            res = calibrate_module(
                lambda i, mid=mid: self._capture_index(mid, i),
                lambda s, mid=mid: self._adjust(mid, s),
                (lambda mid=mid: self._home(mid)) if self._home is not None else (lambda: None),
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
                 loop=None, commander=None, adjuster=None, restorer=None, capture=None,
                 meta=None, fw_calibrator=None):
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
        self._fw_seam = fw_calibrator        # test seam: fw_calibrator(id) -> measured steps | None
        self._frame = None                   # (jpeg bytes, ts)
        self._reel: dict[int, str] = {}      # flap_chars per module (settle timing, parking)
        self._total: dict[int, int] = {}     # total steps/rev per module (proportional settle)
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
            peek_fn=self._peek, home_fn=self._home_module,
            fw_calibrate_fn=self._fw_calibrate, log_fn=self._log_event)

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
        self._do_home(mid)

    def _home_module(self, mid):
        """A mid-run re-home (the guided flow's step 1: home, then look)."""
        self._say(mid, "homing")
        self._do_home(mid)

    def _do_home(self, mid):
        self._pos[mid] = 0
        if self._commander is not None:
            reel = self._reel.get(mid, "")
            self._commander(mid, reel.index(" ") if " " in reel else 0)
            return
        from . import gateway
        self._run_coro(gateway.flap_home(self.gateway_url, mid))
        self._sleep_abortable(self.settle_s)   # a home can be a near-full revolution

    def _fw_calibrate(self, mid):
        """The firmware's steps-per-revolution measurement: start the async job, poll it out
        (the reel spins a full revolution, up to ~15s), write the measured value back. Returns
        the measured steps, or None when the job never finished (the run then continues on the
        module's saved Total Steps)."""
        self._say(mid, "measuring")
        if self._fw_seam is not None:
            return self._fw_seam(mid)
        if self._commander is not None:        # simulated wall: no firmware to ask
            return None
        from . import gateway
        try:
            self._run_coro(gateway.flap_calibrate_start(self.gateway_url, mid))
        except Exception:                                       # noqa: BLE001 - e.g. 409 busy
            return None
        deadline = time.time() + 25.0
        while time.time() < deadline:
            self._sleep_abortable(1.0)
            st = self._run_coro(gateway.flap_calibrate_status(self.gateway_url))
            state = st.get("state")
            if state == "done":
                steps = int(st.get("stepsPerRev") or 0)
                if steps > 0:
                    self._run_coro(gateway.flap_set_totalsteps(self.gateway_url, mid, steps))
                    return steps
                return None
            if state in ("timeout", "idle"):
                return None
        return None

    def _meta(self, mid):
        """Read module ``mid``'s reel + SAVED calibration (``/api/flap/all``): its flap set,
        steps/rev and current home offset, straight from its own EEPROM. Returns None when the
        module isn't ready (no dump / no blank to anchor on)."""
        if self._meta_seam is not None:
            m = self._meta_seam(mid)
        else:
            from . import gateway
            # A bus query can come back ``stale`` (no reply yet — the module was busy); the read
            # is fetched fresh each call, so a couple of retries clears a transient miss.
            raw, dump, chars = {}, None, ""
            for attempt in range(4):
                raw = self._run_coro(gateway.flap_all(self.gateway_url, mid))
                dump = gateway._parse_dump(raw.get("dump", ""))
                chars = raw.get("flapChars") or ""
                if dump is not None and chars and not raw.get("stale"):
                    break
                self._sleep_abortable(1.0)
            reason = ("no reply from the module (bus busy?)" if raw.get("stale") and dump is None
                      else "no EEPROM dump" if dump is None else
                      "no flap set reported" if not chars else
                      "reel has no blank flap" if " " not in chars else None)
            if reason is not None:
                self._log_event(mid, "meta_missing", {"reason": reason})
                return None
            m = {"flap_chars": chars, "total_steps": dump[1], "home_offset": dump[0]}
        if not m:
            return None
        self._reel[mid] = m["flap_chars"]
        self._total[mid] = int(m.get("total_steps") or 0)
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
        # Wait in proportion to how far the reel actually turns: a POSITIVE nudge moves only
        # that many steps forward (a one-flap home-search step is a fraction of a revolution,
        # so it settles fast — no reason to wait the full-revolution time). A NEGATIVE nudge
        # wraps almost all the way around forward, so it gets the full settle.
        total = self._total.get(mid) or 0
        if steps >= 0 and total > 0:
            frac = min(1.0, steps / total)
            wait = max(SETTLE_FLOOR_S, frac * self.settle_s)
        else:
            wait = self.settle_s
        self._sleep_abortable(wait)

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
            return canonicalize(self._capture(grid_index))
        if self._frame is None:
            raise RuntimeError("no camera frame yet")
        img = Image.open(io.BytesIO(self._frame[0]))
        return canonicalize(tiles(img, self.corners, self.rows, self.cols)[grid_index])

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
                tile = canonicalize(tiles(img, self.corners, self.rows, self.cols)[grid_index])
                return tile, fr[1]
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
