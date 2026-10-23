"""Phase 0 spike for camera auto-calibration (app/autocal.py).

No camera, no gateway: the vision is proved against SYNTHETIC fixtures — tiles rendered with
Pillow, assembled into a reel, then a module "sweep" simulated with a KNOWN home-offset error,
a vertical shift, brightness variation and noise. The engine must recover the error. That is
the whole risk of the feature, so it is the whole of Phase 0.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app import autocal

REEL = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"      # a plain reel; index 0 is the blank


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------
def _glyph_tile(ch: str, w=autocal.TILE_W, h=autocal.TILE_H, shift=0) -> np.ndarray:
    """One flap: a light flap (bg ~230) with a dark glyph, or a near-uniform blank for ' '.
    ``shift`` moves the glyph down by that many pixels (to fake a sub-flap misalignment)."""
    img = Image.new("L", (w, h), 230)
    if ch != " ":
        d = ImageDraw.Draw(img)
        # a chunky block per glyph, distinct per character, centred then shifted
        seed = ord(ch)
        bw, bh = 20, 20
        x = (w - bw) // 2 + (seed % 5) - 2        # a little per-glyph horizontal variety
        y = (h - bh) // 2 + shift
        d.rectangle([x, y, x + bw, y + bh], fill=40 + (seed % 7) * 10)
        d.text((x + 4, y + 2), ch, fill=10)
    return np.asarray(img, dtype=np.float32)


def _reel(flap_chars=REEL) -> list[np.ndarray]:
    return [_glyph_tile(c) for c in flap_chars]


def _sweep(flap_chars=REEL, *, error=0, shift=0, noise=0.0, bright=0.0, seed=0):
    """A module sweep with a known home-offset ``error``: commanding index i shows reel flap
    (i + error) mod N. ``shift`` adds a vertical glyph shift, ``noise``/``bright`` add realism."""
    rng = np.random.default_rng(seed)
    reel = [_glyph_tile(c, shift=shift) for c in flap_chars]
    n = len(reel)
    out = []
    for i in range(n):
        t = reel[(i + error) % n].copy()
        if bright:
            t = np.clip(t * (1.0 + bright * (rng.random() - 0.5)), 0, 255)
        if noise:
            t = np.clip(t + rng.normal(0, noise, t.shape), 0, 255)
        out.append(t.astype(np.float32))
    return out


# ---------------------------------------------------------------------------
# the blank anchor
# ---------------------------------------------------------------------------
def test_a_perfectly_homed_module_reads_as_aligned():
    r = autocal.analyze_sweep(_sweep(error=0), REEL)
    assert r["offset_flaps"] == 0
    assert r["aligned"] is True
    assert r["blank_cmd_index"] == 0
    assert r["confidence"] > 0.3


@pytest.mark.parametrize("error", [1, 3, -2, 7, -10, 17])
def test_a_whole_flap_error_is_recovered(error):
    r = autocal.analyze_sweep(_sweep(error=error, noise=6.0, bright=0.15, seed=error + 99), REEL)
    assert r["offset_flaps"] == error, r
    assert not r["aligned"]


def test_the_blank_need_not_be_index_zero():
    """A reel whose blank sits elsewhere still anchors on it."""
    reel = "ABC DEF"            # blank at index 3
    r = autocal.analyze_sweep(_sweep(reel, error=2), reel)
    # commanding i shows (i+2); blank (reel idx 3) appears at i=1 -> offset=(3-1)%7=2
    assert r["offset_flaps"] == 2
    assert r["blank_cmd_index"] == 1


# ---------------------------------------------------------------------------
# sub-flap vertical shift
# ---------------------------------------------------------------------------
def test_a_glyph_riding_low_is_measured_positive():
    r = autocal.analyze_sweep(_sweep(error=0, shift=6), REEL)
    assert r["offset_flaps"] == 0
    assert r["sub_flap"] > 0.08 and not r["aligned"]


def test_a_glyph_riding_high_is_measured_negative():
    r = autocal.analyze_sweep(_sweep(error=0, shift=-6), REEL)
    assert r["sub_flap"] < -0.08


# ---------------------------------------------------------------------------
# confidence
# ---------------------------------------------------------------------------
def test_an_ambiguous_sweep_reports_low_confidence():
    """Two near-blank tiles (a second almost-empty flap) should not be trusted as a clean
    anchor — the engine flags it rather than guessing."""
    sweep = _sweep(error=0)
    sweep[10] = _glyph_tile(" ")          # a second 'blank' collides with the real one
    r = autocal.analyze_sweep(sweep, REEL)
    assert r["confidence"] < 0.15


# ---------------------------------------------------------------------------
# perspective rectification
# ---------------------------------------------------------------------------
def test_rectify_undoes_a_known_perspective_and_lands_ink_in_the_right_tile():
    rows, cols = 2, 3
    W, H = cols * autocal.TILE_W, rows * autocal.TILE_H
    # a flat wall with a dark square in exactly one module (row 1, col 2 -> module index 5)
    flat = Image.new("L", (W, H), 230)
    d = ImageDraw.Draw(flat)
    x0, y0 = 2 * autocal.TILE_W, 1 * autocal.TILE_H
    d.rectangle([x0 + 10, y0 + 10, x0 + autocal.TILE_W - 10, y0 + autocal.TILE_H - 10], fill=20)
    # warp the flat wall into a trapezoid inside a bigger photo, then rectify with its corners
    photo = Image.new("L", (W + 80, H + 80), 200)
    corners = [(40, 45), (W + 38, 30), (W + 55, H + 52), (25, H + 40)]   # TL,TR,BR,BL, skewed
    coeffs = autocal._perspective_coeffs([tuple(c) for c in corners],
                                         [(0, 0), (W, 0), (W, H), (0, H)])
    warped = flat.transform(photo.size, Image.PERSPECTIVE, coeffs, resample=Image.BILINEAR)
    photo.paste(warped, (0, 0))

    ts = autocal.tiles(photo, corners, rows, cols)
    assert len(ts) == rows * cols
    inked = int(np.argmax([autocal.tile_ink(t) for t in ts]))
    assert inked == 5, "the dark square must land in module 5 after rectification"


def test_tiles_returns_row_major_modules():
    rows, cols = 3, 5
    W, H = cols * autocal.TILE_W, rows * autocal.TILE_H
    flat = Image.new("L", (W, H), 230)
    corners = [(0, 0), (W, 0), (W, H), (0, H)]       # already flat
    assert len(autocal.tiles(flat, corners, rows, cols)) == 15
