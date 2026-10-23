"""Camera auto-calibration (app/autocal.py), proved without a camera or a gateway.

The vision is tested against SYNTHETIC fixtures — flap tiles rendered with the bundled DejaVu
font, cut by a horizontal seam like a real flap, with solid-colour flaps as uniform luma and a
module "sweep" simulated with a KNOWN home-offset error, vertical shift, brightness variation
and noise. The orchestration is tested against a simulated wall whose modules mirror the REAL
calibration contract: ``nudge`` is a LIVE correction (reel moves + offset saved instantly); an
absolute home-offset write only takes effect on a re-home (which is why the loop must never use
it for corrections — that bug shipped in beta ≤6 and is pinned here).

The recognition contract under test is the user's: dial A, SEE A — every flap verified by
identity against the module's own learned reel, the absolute rotation pinned by TWO independent
anchors (blank flatness + rendered-reference consensus), disagreement flagged, never guessed.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from app import autocal

REEL = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"      # a plain reel; index 0 is the blank
REEL64 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;q:%'.,/?*roygbpw"   # the default set

# what a solid-colour flap reads as in grayscale: uniform luma, as flat as the blank
_COLOR_LUMA = {"r": 76, "o": 173, "y": 226, "g": 150, "b": 29, "p": 105, "w": 245}


@lru_cache(maxsize=None)
def _font():
    return ImageFont.truetype(str(autocal._FONT_PATH), int(autocal.TILE_H * 0.8))


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------
@lru_cache(maxsize=4096)
def _glyph_tile_cached(ch: str, shift: int) -> bytes:
    w, h = autocal.TILE_W, autocal.TILE_H
    if ch in _COLOR_LUMA:
        return np.full((h, w), _COLOR_LUMA[ch], dtype=np.float32).tobytes()
    img = Image.new("L", (w, h), 230)
    if ch != " ":
        d = ImageDraw.Draw(img)
        g = autocal.physical_glyph(ch)
        x0, y0, x1, y1 = d.textbbox((0, 0), g, font=_font())
        d.text(((w - (x1 - x0)) // 2 - x0, (h - (y1 - y0)) // 2 - y0 + shift), g,
               fill=20, font=_font())
    a = np.asarray(img, dtype=np.float32).copy()
    a[h // 2 - 1:h // 2 + 1, :] = 230.0              # the seam, fixed mid-window
    return a.tobytes()


def _glyph_tile(ch: str, w=autocal.TILE_W, h=autocal.TILE_H, shift=0) -> np.ndarray:
    """One flap as the camera sees it: a light flap with a dark DejaVu glyph cut by the seam,
    a near-uniform blank for ' ', or uniform luma for a colour flap. ``shift`` moves the glyph
    down by that many pixels (a sub-flap misalignment)."""
    return np.frombuffer(_glyph_tile_cached(ch, int(shift)), dtype=np.float32).reshape(h, w).copy()


def _block_tile(ch: str) -> np.ndarray:
    """An ALIEN look (chunky blocks, nothing like any font) — for proving the rendered-font
    anchor abstains-or-agrees on a reel whose print it cannot possibly know."""
    img = Image.new("L", (autocal.TILE_W, autocal.TILE_H), 230)
    if ch != " ":
        d = ImageDraw.Draw(img)
        s = ord(ch)
        x = (autocal.TILE_W - 20) // 2 + (s % 5) - 2
        y = (autocal.TILE_H - 20) // 2
        d.rectangle([x, y, x + 20, y + 20], fill=40 + (s % 7) * 10)
    return np.asarray(img, dtype=np.float32)


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
# the blank anchor (analyze_sweep)
# ---------------------------------------------------------------------------
def test_a_perfectly_homed_module_reads_as_aligned():
    r = autocal.analyze_sweep(_sweep(error=0), REEL)
    assert r["offset_flaps"] == 0
    assert r["aligned"] is True
    assert r["blank_cmd_index"] == 0
    assert r["confidence"] > 0.1


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
    r = autocal.analyze_sweep(_sweep(error=0, shift=12), REEL)
    assert r["offset_flaps"] == 0
    assert r["sub_flap"] > 0.08 and not r["aligned"]


def test_a_glyph_riding_high_is_measured_negative():
    r = autocal.analyze_sweep(_sweep(error=0, shift=-12), REEL)
    assert r["sub_flap"] < -0.08


# ---------------------------------------------------------------------------
# blankness: the reliable signal the home search steers on
# ---------------------------------------------------------------------------
def test_blankness_separates_the_blank_from_the_faintest_glyph():
    """The user's wall measured blank INK 0.117 vs '.' at 0.132 — too close. Peak-deviation
    flatness must keep them far apart: a dot has few but STRONG dark pixels."""
    blank = autocal.tile_blankness(_glyph_tile(" "))
    dot = autocal.tile_blankness(_glyph_tile("."))
    assert dot > 4 * blank + 0.05


def test_canonicalize_normalises_white_on_dark():
    """The real wall is often white characters on BLACK flaps — the opposite polarity to what
    every measurement assumes. Canonicalize inverts those (dark background) and leaves an
    already-light flap alone, so both walls land in the same canonical form."""
    light = _glyph_tile("A")                       # dark glyph on a light flap (canonical)
    dark = 255.0 - light                           # white glyph on a dark flap
    assert np.allclose(autocal.canonicalize(dark), light, atol=1.0)
    assert np.allclose(autocal.canonicalize(light), light)       # already canonical: unchanged
    assert autocal.tile_blankness(autocal.canonicalize(255.0 - _glyph_tile(" "))) \
        == pytest.approx(autocal.tile_blankness(_glyph_tile(" ")), abs=0.01)


def test_autocal_run_calibrates_a_white_on_dark_wall():
    """End to end on a white-on-dark wall: the modules render inverted, and the broadcast sweep's
    capture path must canonicalize so the blank detection and consensus work exactly as on a
    light wall."""
    inv = lambda ch: 255.0 - _reel_render(ch)
    wall, run = _board_wall([(0, 23), (1, 24)], render=inv)
    run.run_blocking()
    assert run.status["error"] is None
    assert run.status["summary"]["adjusted"] == 2
    for mid in wall:
        assert autocal.analyze_sweep([255.0 - t for t in wall[mid].sweep()], REEL)["aligned"]


def test_colour_flaps_are_flat_but_most_are_darker_than_the_blank():
    """Why the home search uses brightness, not just flatness: a solid colour flap is flat too,
    so flatness alone can't tell it from the blank — but most colours sit well below the flap
    background, so the grayscale LEVEL separates them (the white flap stays ambiguous, which is
    why the blank is confirmed by its letter neighbour, not by flatness alone)."""
    blank = _glyph_tile(" ")
    assert autocal.tile_blankness(_glyph_tile("w")) < 3 * autocal.tile_blankness(blank) + 0.02
    bg = float(np.percentile(_glyph_tile("A"), 85))
    assert float(np.median(_glyph_tile("b"))) < bg - 35      # blue: clearly a dark colour
    assert float(np.median(blank)) > bg - 35                 # the blank reads at background


# ---------------------------------------------------------------------------
# glyph identity — the LOCAL check (dialed char vs its few neighbours)
# ---------------------------------------------------------------------------
def test_identify_names_every_glyph_under_fresh_noise():
    """Learn each glyph under one noise draw, re-read under ANOTHER: every glyph identifies as
    itself, by a clear margin, against the full set."""
    sweep1 = _sweep(REEL64, error=0, noise=5.0, seed=1)
    templates = {REEL64[i]: autocal.glyph_pool(sweep1[i]) for i in range(len(REEL64))}
    fresh = _sweep(REEL64, error=0, noise=5.0, seed=2)
    for i, ch in enumerate(REEL64):
        if ch == " " or ch in _COLOR_LUMA:
            continue
        got, score, margin = autocal.identify(fresh[i], templates)
        assert got == ch, f"{ch!r} read as {got!r}"
        assert margin >= autocal.GLYPH_MARGIN_MIN, f"{ch!r} margin {margin}"


def test_identify_finds_the_glyph_under_a_vertical_roll():
    """A template learned at one sub-flap shift must still identify a capture at another, once
    the roll search registers them (the seam masked at both its positions)."""
    templates = {c: autocal.glyph_pool(_glyph_tile(c, shift=10)) for c in REEL}
    rolls = tuple(range(-(autocal.TILE_H // 2), autocal.TILE_H // 2 + 1, 2))
    got, _, _ = autocal.identify(_glyph_tile("K"), templates, rolls=rolls)
    assert got == "K"


def test_a_wrong_flap_is_named_not_scored():
    """Dial A, see B: identify must SAY B — the literal check the whole feature hangs on."""
    templates = {c: autocal.glyph_pool(_glyph_tile(c)) for c in REEL}
    got, _, _ = autocal.identify(_glyph_tile("B"), templates)
    assert got == "B"
    got, _, _ = autocal.identify(_glyph_tile("4"), templates)
    assert got == "4"


# ---------------------------------------------------------------------------
# the OCR anchor — reads the reel back as text lines, opinions aligned in bulk
# ---------------------------------------------------------------------------
needs_tesseract = pytest.mark.skipif(not autocal.ocr_available(),
                                     reason="tesseract binary not installed")


@pytest.fixture(autouse=True)
def _ocr_off_unless_marked(request, monkeypatch):
    """Real tesseract costs ~1s per calibrate run: the orchestration tests run with OCR
    disabled (the other anchors carry them, as on an install without the binary), and the
    tests marked ``@pytest.mark.ocr`` exercise the real engine end to end."""
    if "ocr" not in request.keywords:
        monkeypatch.setattr(autocal, "ocr_available", lambda: False)


@pytest.mark.ocr
@needs_tesseract
def test_ocr_tile_opinions_map_back_to_their_tiles():
    """A line read of many tiles, each recognized character box-mapped to its source tile:
    most opinions must name the right tile's character (individually weak is fine — never
    mostly-wrong)."""
    chars = "ABCDEFGHIJKLMNOP"
    tiles_ = [_glyph_tile(c) for c in chars]
    ops = autocal.ocr_tile_opinions(tiles_, REEL64)
    assert len(ops) >= 8
    right = sum(1 for i, v in ops.items() if v == chars[i])
    assert right >= len(ops) * 0.6, ops


@pytest.mark.ocr
@needs_tesseract
@pytest.mark.parametrize("error", [0, 2, -5])
def test_ocr_shift_anchors_the_rotation(error):
    """The OCR anchor on a full default-reel sweep under noise: weak per-glyph reads, decisive
    in aggregate — and it genuinely READS characters, so it needs no font or blank at all."""
    r = autocal.ocr_shift(_sweep(REEL64, error=error, noise=8.0, seed=abs(error) + 3), REEL64)
    assert r["offset_flaps"] == error, r
    assert r["hits"] >= autocal.OCR_ANCHOR_MIN_HITS


def test_align_opinions_abstains_on_weak_or_conflicting_evidence():
    assert autocal.align_opinions({}, REEL64)["offset_flaps"] is None
    few = {0: "A", 1: "B"}                                  # too few hits
    assert autocal.align_opinions(few, REEL64)["offset_flaps"] is None
    # scattered random opinions: no shift dominates
    noise = {i: REEL64[(i * 7 + 3) % 40 + 1] for i in range(0, 60, 2)}
    assert autocal.align_opinions(noise, REEL64)["offset_flaps"] is None


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


def test_tiles_similar_tells_settled_from_moving():
    """Consecutive reads of a SETTLED flap agree (sensor noise only); mid-move they differ."""
    assert autocal.tiles_similar(_glyph_tile("A"), _glyph_tile("A"))
    noisy = np.clip(_glyph_tile("A") + np.random.default_rng(1).normal(0, 1.5,
                    (autocal.TILE_H, autocal.TILE_W)), 0, 255).astype(np.float32)
    assert autocal.tiles_similar(_glyph_tile("A"), noisy)
    assert not autocal.tiles_similar(_glyph_tile("A"), _glyph_tile("W"))


def test_tiles_similar_survives_realistic_camera_noise():
    """Raw webcam pixels flicker (JPEG + auto-exposure) even when nothing moves — the stillness
    check must pool that noise away or every read burns its whole timeout."""
    rng = np.random.default_rng(7)
    a = _glyph_tile("K")
    b = np.clip(a + rng.normal(0, 6.0, a.shape), 0, 255).astype(np.float32)   # heavy sensor noise
    assert autocal.tiles_similar(a, b), "same flap under noise must read as settled"
    assert not autocal.tiles_similar(a, _glyph_tile("W"))


# ---------------------------------------------------------------------------
# whole-board calibration (broadcast sweep -> consensus blank -> set offsets)
# ---------------------------------------------------------------------------
def _color_tile(ch):
    return np.full((autocal.TILE_H, autocal.TILE_W), _COLOR_LUMA[ch], dtype=np.float32)


def _reel_render(ch):
    return _color_tile(ch) if ch in _COLOR_LUMA else _glyph_tile(ch)


def _zeroed_sweep(blank_index, reel=REEL64):
    """A module's broadcast sweep from a zeroed offset: index i shows reel ``(i - blank_index)``,
    so the blank (reel 0) lands at commanded index ``blank_index``."""
    n = len(reel)
    return [_reel_render(reel[(i - blank_index) % n]) for i in range(n)]


@pytest.mark.parametrize("blank_index", [0, 10, 30, 44, 60])
def test_detect_blank_finds_the_blank(blank_index):
    """The blank is the empty flap followed by the alphabet — not a mid-reel letter (also
    followed by letters) nor a colour (empty, but followed by more colours)."""
    b, conf = autocal.detect_blank(_zeroed_sweep(blank_index), REEL64)
    assert b == blank_index
    assert conf > 0


def test_board_consensus_overrules_an_outlier():
    blanks = {0: (44, 0.1), 1: (43, 0.1), 2: (45, 0.1), 3: (10, 0.05)}   # module 3 is wrong
    corrected, median = autocal.board_consensus(blanks)
    assert median in (43, 44, 45)
    assert corrected[3] == median            # the board overrules the outlier
    assert corrected[0] == 44                # a good read is kept


def test_calibrate_board_sets_every_offset_from_one_sweep():
    bidx = {0: 44, 1: 44, 2: 45, 3: 43, 4: 44, 5: 10}   # a consistent board + one outlier (m5)
    spf, n = 64, len(REEL64)
    sweeps = {m: _zeroed_sweep(bidx[m]) for m in bidx}
    state = {"i": 0}
    setoff = {}
    broadcast = lambda i: state.__setitem__("i", i)
    capture_all = lambda: {m: sweeps[m][state["i"]] for m in bidx}
    set_offset = lambda m, steps: setoff.__setitem__(m, steps)

    def peek(m):   # home shows reel[(offset_flaps - true_blank)]; == blank only when they match
        off_flaps = setoff[m] // spf
        return _reel_render(REEL64[(off_flaps - bidx[m]) % n])

    r = autocal.calibrate_board(list(bidx), broadcast=broadcast, capture_all=capture_all,
                                set_offset=set_offset, peek=peek, flap_chars=REEL64,
                                total_steps=spf * n)
    # the consistent modules are calibrated exactly: offset = blank_index * steps-per-flap
    for m in (0, 1, 2, 3, 4):
        assert setoff[m] == bidx[m] * spf, (m, setoff[m])
        assert r["results"][m]["status"] == "adjusted" and r["results"][m]["home_blank"]
    # the genuine outlier (blank far from the board) is overruled toward the board, not left at
    # its own wrong read, and ends up near the consensus rather than at its true-but-lone 10
    assert abs(setoff[5] // spf - r["median"]) <= 4


def test_calibrate_board_aborts_cleanly():
    bidx = {0: 44, 1: 44}
    sweeps = {m: _zeroed_sweep(bidx[m]) for m in bidx}
    state = {"i": 0}
    r = autocal.calibrate_board(
        list(bidx), broadcast=lambda i: state.__setitem__("i", i),
        capture_all=lambda: {m: sweeps[m][state["i"]] for m in bidx},
        set_offset=lambda m, s: None, peek=lambda m: _reel_render(" "),
        flap_chars=REEL64, total_steps=64 * len(REEL64), should_abort=lambda: True)
    assert r.get("aborted")


# ===========================================================================
# the simulated wall — mirrors the REAL calibration contract
# ===========================================================================
class FakeModule:
    """A simulated split-flap module: holds a home-offset error (in steps) and renders what the
    camera would see. ``conv`` is the (hardware-dependent) direction a nudge moves the glyph in
    the window — the calibrator must converge for either value.

    Mirrors the real write contract: ``nudge`` moves the reel AND saves the offset instantly
    (the live correction); ``restore`` is the absolute write + re-home that revert uses."""

    def __init__(self, reel, total_steps, *, err_flaps=0, sub=0.0, conv=1, glued=None,
                 render=_glyph_tile):
        self.reel = reel
        self.glued = glued                       # a flap that never presents (sticks to the next)
        self.n = len(reel)
        self.total = total_steps
        self.spf = total_steps / self.n
        self.conv = conv
        self.render = render
        self.homeoffset = 1000
        self.err_steps = err_flaps * self.spf + sub * self.spf
        self.commanded = 0

    def show_index(self, i):
        self.commanded = int(i) % self.n

    def nudge(self, steps):
        self.homeoffset += steps
        self.err_steps += self.conv * steps

    def restore(self, steps):
        d = steps - self.homeoffset
        self.homeoffset = steps
        self.err_steps += self.conv * d          # applied because restore ends with a re-home

    def capture(self):
        pos = self.commanded + self.err_steps / self.spf      # displayed flap, fractional
        whole = int(round(pos)) % self.n
        if self.glued is not None and whole == self.glued:     # the stuck flap shows the NEXT one
            whole = (whole + 1) % self.n
        frac = pos - round(pos)                                # sub-flap residual, in flaps
        return self.render(self.reel[whole], shift=int(round(frac * autocal.TILE_H)))

    def sweep(self):
        out = []
        for i in range(self.n):
            self.show_index(i)
            out.append(self.capture())
        return out


def _calibrate(mod, **kw):
    def cap(i):
        mod.show_index(i)
        return mod.capture()
    return autocal.calibrate_module(cap, mod.nudge, lambda: mod.show_index(0),
                                    mod.reel, mod.total, **kw)


def _truly_aligned(mod):
    mod.show_index(7)
    return autocal.analyze_sweep(mod.sweep(), mod.reel)["aligned"]


def test_an_already_aligned_module_is_left_alone():
    mod = FakeModule(REEL, 2000)
    r = _calibrate(mod)
    assert r["status"] == "aligned"
    assert r["applied_steps"] == 0 and mod.homeoffset == 1000
    assert r["reel_check"]["ok"] and r["reel_check"]["flagged"] == []


@pytest.mark.parametrize("err,conv", [(2, 1), (3, -1), (-1, 1), (-5, 1), (8, -1), (-7, -1)])
def test_a_whole_flap_error_converges_for_either_nudge_direction(err, conv):
    """Home, read what shows, and drive to aligned — for an offset either side of home and for
    either hardware nudge direction. The reel is circular, so a forward sweep always reaches
    the blank, whatever ``conv`` maps a +nudge to."""
    mod = FakeModule(REEL, 2000, err_flaps=err, conv=conv)
    r = _calibrate(mod)
    assert r["status"] in ("aligned", "adjusted"), r["history"]
    assert _truly_aligned(mod)


def test_a_sub_flap_residual_is_corrected():
    mod = FakeModule(REEL, 2000, sub=0.25)
    r = _calibrate(mod)
    assert r["status"] in ("aligned", "adjusted")
    assert _truly_aligned(mod)


def test_a_combined_whole_and_sub_error_converges():
    mod = FakeModule(REEL, 3600, err_flaps=4, sub=0.3, conv=-1)
    r = _calibrate(mod)
    assert r["status"] in ("aligned", "adjusted"), r["history"]
    assert _truly_aligned(mod)


def test_the_default_reel_with_colour_flaps_calibrates():
    """The real wall: a 64-flap reel with solid-colour flaps. The home search must still find
    the blank (a colour flap is flat too, so brightness + the letter-neighbour decide it)."""
    mod = FakeModule(REEL64, 4096, err_flaps=2)
    r = _calibrate(mod)
    assert r["status"] in ("aligned", "adjusted")
    assert _truly_aligned(mod)


def test_home_lands_on_a_colour_flap_and_still_finds_the_blank():
    """The case the user named: homing shows a colour/symbol, not the blank. The search reads
    that, keeps stepping forward, and lands on the real blank."""
    mod = FakeModule(REEL64, 4096, err_flaps=-2)      # home shows a colour flap (before blank)
    r = _calibrate(mod)
    assert r["status"] in ("aligned", "adjusted")
    assert _truly_aligned(mod)


def test_abort_stops_the_loop_without_touching_the_module():
    mod = FakeModule(REEL, 2000, err_flaps=7)
    r = _calibrate(mod, should_abort=lambda: True)
    assert r["status"] == "failed" and r["reason"] == "aborted"
    assert mod.homeoffset == 1000


def test_events_narrate_the_whole_run():
    """The live log the UI renders: the home search narrates what it saw, every nudge, a
    verify_start, and a dialed-vs-saw verify line per flap, ending on a reel verdict."""
    mod = FakeModule(REEL, 2000, err_flaps=2)
    seen = []
    r = _calibrate(mod, notify=lambda kind, data: seen.append((kind, data)))
    kinds = [k for k, _ in seen]
    assert "adjust" in kinds and "verify_start" in kinds
    assert "reel_verified" in kinds or "reel_issues" in kinds
    verifies = [d for k, d in seen if k == "verify"]
    assert len(verifies) >= len(REEL)
    assert all({"flap", "expected", "saw", "ok"} <= set(d) for d in verifies)
    assert r["status"] in ("aligned", "adjusted")


def test_a_glued_flap_is_caught_and_downgrades():
    mod = FakeModule(REEL, 2000, glued=10)
    seen = []
    r = _calibrate(mod, notify=lambda k, d: seen.append((k, d)))
    assert r["status"] == "needs_review"
    assert 10 in r["reel_check"]["skips"]
    assert any(k == "skip_suspect" and d.get("flap") == 10 for k, d in seen)


def test_verify_names_what_it_saw():
    """The dialed-vs-saw lines: on a healthy module every verify line in the final pass says it
    saw the expected character (letters by name, flat flaps as flat)."""
    mod = FakeModule(REEL, 2000)
    seen = []
    _calibrate(mod, notify=lambda k, d: seen.append((k, d)))
    # the final verify pass is the run of verify events after the last verify_start
    starts = [i for i, (k, _) in enumerate(seen) if k == "verify_start"]
    assert starts
    final = [d for k, d in seen[starts[-1]:] if k == "verify"]
    assert final and all(d["ok"] and d["saw"] == d["expected"] for d in final)


def test_looks_blank_judges_by_the_modules_own_evidence():
    ev = {"baseline": 0.2, "blank_ink": 0.02}
    assert autocal.looks_blank(0.02, ev)
    assert autocal.looks_blank(0.08, ev)
    assert not autocal.looks_blank(0.2, ev)
    assert not autocal.looks_blank(0.19, ev)


@pytest.mark.ocr
@needs_tesseract
def test_ocr_reads_the_reel_back_as_the_final_check():
    mod = FakeModule(REEL64, 4096, err_flaps=2)
    seen = []
    r = _calibrate(mod, notify=lambda k, d: seen.append((k, d)))
    assert r["status"] in ("aligned", "adjusted")
    checks = [d for k, d in seen if k == "ocr_check"]
    assert checks and checks[-1]["ok"] and checks[-1]["shift"] == 0
    assert r["reel_check"]["ocr"]["shift"] == 0


def test_calibration_still_works_without_tesseract(monkeypatch):
    """Bare installs may lack the binary: the home search + per-flap verify carry the run with
    no ocr_check event and a None OCR read-back."""
    monkeypatch.setattr(autocal, "ocr_available", lambda: False)
    mod = FakeModule(REEL, 2000, err_flaps=3)
    seen = []
    r = _calibrate(mod, notify=lambda k, d: seen.append((k, d)))
    assert r["status"] in ("aligned", "adjusted") and r["reel_check"]["ok"]
    assert "ocr_check" not in [k for k, _ in seen]
    assert r["reel_check"]["ocr"]["shift"] is None


# ===========================================================================
# the wall-level session: lazy meta, snapshot, park, revert, summary
# ===========================================================================
def _capture_index(mods):
    def cap(mid, i):
        mods[mid].show_index(i)
        return mods[mid].capture()
    return cap


def _fake_wall(specs):
    """specs: list of (id, err_flaps). Returns (mods, modules, wiring) for a CalibrationSession."""
    mods = {mid: FakeModule(REEL, 2000, err_flaps=err) for mid, err in specs}
    modules = [{"id": mid, "grid_index": mid} for mid, _ in specs]
    wiring = dict(
        meta_fn=lambda mid: {"flap_chars": REEL, "total_steps": 2000,
                             "home_offset": mods[mid].homeoffset},
        capture_index=_capture_index(mods),
        adjust=lambda mid, s: mods[mid].nudge(s),
        restore=lambda mid, s: mods[mid].restore(s),
        park_fn=lambda mid: mods[mid].show_index(0),
    )
    return mods, modules, wiring


def test_the_session_calibrates_every_module_and_snapshots():
    mods, modules, wiring = _fake_wall([(0, 3), (1, 0), (2, -4)])
    sess = autocal.CalibrationSession(modules, **wiring)
    prog = sess.run()
    assert prog["done"] and prog["completed"] == 3
    assert sess.snapshot == {0: 1000, 1: 1000, 2: 1000}   # each module's saved offset, pre-run
    assert {r["status"] for r in prog["results"].values()} <= {"aligned", "adjusted"}
    for mid in (0, 1, 2):
        mods[mid].show_index(7)
        assert autocal.analyze_sweep(mods[mid].sweep(), REEL)["aligned"]


def test_the_session_parks_each_finished_module_on_the_blank():
    mods, modules, wiring = _fake_wall([(0, 2), (1, 0)])
    autocal.CalibrationSession(modules, **wiring).run()
    assert mods[0].commanded == 0 and mods[1].commanded == 0


def test_the_session_reverts_to_the_snapshot():
    mods, modules, wiring = _fake_wall([(0, 5), (1, 8)])
    sess = autocal.CalibrationSession(modules, **wiring)
    sess.run()
    assert mods[0].homeoffset != 1000 or mods[1].homeoffset != 1000   # it moved them
    sess.revert()
    assert mods[0].homeoffset == 1000 and mods[1].homeoffset == 1000


def test_a_module_that_isnt_ready_is_flagged_not_calibrated():
    mods, modules, wiring = _fake_wall([(0, 3), (1, 0)])
    base_meta = wiring["meta_fn"]
    wiring["meta_fn"] = lambda mid: None if mid == 1 else base_meta(mid)
    prog = autocal.CalibrationSession(modules, **wiring).run()
    assert prog["results"][1]["status"] == "needs_review"
    assert prog["results"][0]["status"] in ("aligned", "adjusted")


def test_session_summary_counts_outcomes():
    mods, modules, wiring = _fake_wall([(0, 0), (1, 4), (2, -2)])
    prog = autocal.CalibrationSession(modules, **wiring).run()
    s = prog["summary"]
    assert s["aligned"] + s["adjusted"] == 3 and s["failed"] == 0


def test_session_flags_a_module_whose_home_lands_wrong():
    """The 'module 2 says homed but shows a glyph' failure: after parking, the session LOOKS at
    the module; a printed flap showing means needs_review, never a quiet green check."""
    mods, modules, wiring = _fake_wall([(0, 0)])
    wiring["park_fn"] = lambda mid: mods[mid].show_index(5)      # a BAD home: lands on a letter
    wiring["peek_fn"] = lambda mid: mods[mid].capture()
    prog = autocal.CalibrationSession(modules, **wiring).run()
    assert prog["results"][0].get("park_failed") is True
    assert prog["results"][0]["status"] == "needs_review"


def test_session_confirms_a_good_home():
    mods, modules, wiring = _fake_wall([(0, 2)])
    wiring["peek_fn"] = lambda mid: mods[mid].capture()          # park_fn already shows blank
    prog = autocal.CalibrationSession(modules, **wiring).run()
    assert prog["results"][0].get("park_failed") is None
    assert prog["results"][0]["status"] in ("aligned", "adjusted")
    assert prog["results"][0]["reel_check"]["ok"]                # verify ran and passed


def test_session_verify_catches_the_glued_flap_and_downgrades():
    mods, modules, wiring = _fake_wall([(0, 0)])
    mods[0].glued = 20
    prog = autocal.CalibrationSession(modules, **wiring).run()
    r = prog["results"][0]
    assert r["reel_check"]["ok"] is False and 20 in r["reel_check"]["skips"]
    assert r["status"] == "needs_review"


# ===========================================================================
# AutocalRun — the live manager (injected seams; no gateway, no camera)
# ===========================================================================
def _run_over_fake_wall(specs, **kw):
    wall = {mid: FakeModule(REEL, 2000, err_flaps=err) for mid, err in specs}
    modules = [{"id": mid, "grid_index": mid} for mid, _ in specs]
    run = autocal.AutocalRun(
        gateway_url="http://gw", corners=[(0, 0), (1, 0), (1, 1), (0, 1)],
        rows=1, cols=len(specs), modules=modules, settle_s=0,
        commander=lambda mid, idx: wall[mid].show_index(idx),
        adjuster=lambda mid, steps: wall[mid].nudge(steps),
        restorer=lambda mid, steps: wall[mid].restore(steps),
        capture=lambda gi: wall[gi].capture(),
        meta=lambda mid: {"flap_chars": REEL, "total_steps": 2000,
                          "home_offset": wall[mid].homeoffset}, **kw)
    return wall, run


class BoardFakeModule:
    """A simulated module for the WHOLE-BOARD flow: a fixed mechanical blank position (where the
    blank lands in a ZEROED broadcast sweep), an absolute home offset, and a render of the flap
    the camera would see for any commanded index.

    The display contract matches ``_zeroed_sweep``: with the offset at ``off_flaps`` flaps,
    commanding index ``i`` shows reel ``(i + off_flaps - blank_index) mod N`` — so at a zeroed
    offset the blank sits at commanded index ``blank_index``, and once the offset is set to
    ``blank_index`` flaps the blank lands at home (index 0)."""

    def __init__(self, reel, total_steps, *, blank_index, saved_offset=1000, render=_reel_render):
        self.reel = reel
        self.n = len(reel)
        self.total = total_steps
        self.spf = total_steps / self.n
        self.blank_index = int(blank_index) % self.n
        self.offset = int(saved_offset)          # the "saved" calibration — never trusted
        self.commanded = 0
        self.render = render

    def show_index(self, i):
        self.commanded = int(i) % self.n

    def set_offset(self, steps):                 # absolute write + re-home to the blank
        self.offset = int(steps)
        self.commanded = 0

    def meta(self):
        return {"flap_chars": self.reel, "total_steps": self.total, "home_offset": self.offset}

    def capture(self):
        off_flaps = int(round(self.offset / self.spf))
        shown = (self.commanded + off_flaps - self.blank_index) % self.n
        return self.render(self.reel[shown])

    def sweep(self):
        out = []
        for i in range(self.n):
            self.show_index(i)
            out.append(self.capture())
        return out


def _board_wall(specs, render=_reel_render):
    """specs: list of (id, blank_index). Returns (wall, run) wired for the whole-board flow —
    broadcast sweep via the commander seam, absolute offset writes via the restorer seam."""
    wall = {mid: BoardFakeModule(REEL, 2000, blank_index=bi, render=render) for mid, bi in specs}
    modules = [{"id": mid, "grid_index": mid} for mid, _ in specs]
    run = autocal.AutocalRun(
        gateway_url="http://gw", corners=[(0, 0), (1, 0), (1, 1), (0, 1)],
        rows=1, cols=len(specs), modules=modules, settle_s=0,
        commander=lambda mid, idx: wall[mid].show_index(idx),
        restorer=lambda mid, steps: wall[mid].set_offset(steps),
        capture=lambda gi: wall[gi].capture(),
        meta=lambda mid: wall[mid].meta())
    return wall, run


def test_autocal_run_calibrates_the_whole_wall():
    wall, run = _board_wall([(0, 30), (1, 31), (2, 29)])
    run.run_blocking()
    st = run.status
    assert st["finished"] and st["error"] is None and st["completed"] == 3
    assert st["summary"]["adjusted"] == 3 and st["median"] == 30
    for mid in (0, 1, 2):
        assert autocal.analyze_sweep(wall[mid].sweep(), REEL)["aligned"]
    # every module's offset was written from its own blank, in whole flaps of the measured reel
    for mid, bi in ((0, 30), (1, 31), (2, 29)):
        assert round(wall[mid].offset / wall[mid].spf) == bi


def test_autocal_run_emits_the_board_narration():
    _, run = _board_wall([(0, 30)])
    run.run_blocking()
    kinds = [e["kind"] for e in run.events]            # the full buffer; status serves a window
    assert kinds[0] == "start"
    for k in ("zeroed", "sweep", "consensus", "module_done"):
        assert k in kinds, k
    done = next(e for e in run.events if e["kind"] == "module_done")
    assert "blank" in done and "status" in done and "module" in done   # the per-module result
    sweep = next(e for e in run.events if e["kind"] == "sweep")
    assert "index" in sweep and "of" in sweep          # board-wide, no module
    assert all("seq" in e for e in run.status["events"])


def test_autocal_run_revert_restores_offsets():
    wall, run = _board_wall([(0, 20)])
    run.run_blocking()
    assert wall[0].offset != 1000                      # it moved the offset to calibrate
    run.revert()
    assert wall[0].offset == 1000                      # back to the pre-run saved value


def test_autocal_run_abort_is_a_clean_stop_not_an_error():
    _, run = _board_wall([(0, 30)])
    run.abort()
    run.run_blocking()
    st = run.status
    assert st["finished"] and st["aborted"] and st["error"] is None


def test_abort_interrupts_a_capture_immediately():
    """Stop must land within a step — not after a long sweep's worth of settles."""
    _, run = _run_over_fake_wall([(0, 5)])
    run._commander = None                 # the real-hardware path (sleeps + frames)
    run.session.abort()
    with pytest.raises(RuntimeError, match="aborted"):
        run._capture_index(0, 1)


def test_autocal_run_frame_to_tile_extracts_the_right_module():
    """The real capture path: a streamed JPEG is rectified with the clicked corners and the
    requested module's tile comes back."""
    import io
    rows, cols = 2, 3
    W, H = cols * autocal.TILE_W, rows * autocal.TILE_H
    wall_img = Image.new("L", (W, H), 230)
    d = ImageDraw.Draw(wall_img)
    gx, gy = 2 * autocal.TILE_W, 1 * autocal.TILE_H            # module 5 (row 1, col 2)
    d.rectangle([gx + 8, gy + 8, gx + autocal.TILE_W - 8, gy + autocal.TILE_H - 8], fill=20)
    buf = io.BytesIO(); wall_img.convert("RGB").save(buf, "JPEG", quality=90)

    run = autocal.AutocalRun(gateway_url="x", corners=[(0, 0), (W, 0), (W, H), (0, H)],
                             rows=rows, cols=cols, modules=[], loop=None)
    run.push_frame(buf.getvalue())
    inks = [autocal.tile_ink(run._capture_tile(i)) for i in range(rows * cols)]
    assert int(np.argmax(inks)) == 5


def test_fresh_tile_raises_when_the_camera_stream_stops():
    """The watchdog: no fresh frames (user left the page / closed the camera) must HALT the run,
    not let it keep flipping the wall against a frozen image."""
    import time as _t
    run = autocal.AutocalRun(gateway_url="x", corners=[(0, 0), (1, 0), (1, 1), (0, 1)],
                             rows=1, cols=1, modules=[], loop=None)
    with pytest.raises(RuntimeError, match="camera stream stopped"):
        run._fresh_tile(0, after=_t.time(), timeout=0.3)


def test_autocal_run_status_before_start_is_empty_progress():
    _, run = _run_over_fake_wall([(0, 0)])
    st = run.status
    assert st["completed"] == 0 and st["total"] == 1 and not st["finished"]


def test_settle_time_is_proportional_to_the_forward_move():
    """The dominant cost was a flat per-capture settle. A split-flap only moves forward, and
    settle_s is the full-revolution time, so the wait must scale with the actual move."""
    _, run = _run_over_fake_wall([(0, 0)])
    run.settle_s = 4.0
    run._reel[0] = REEL
    n = len(REEL)
    assert run._settle_for(0, 0) == 4.0                 # first command, position unknown -> full
    assert run._settle_for(0, 1) <= autocal.SETTLE_FLOOR_S + 0.2   # a +1 step: the floor
    assert run._settle_for(0, n - 1) > 4.0 * 0.8        # a near-full forward turn ~ full settle


# ===========================================================================
# gateway orchestration helpers (shapes + parsing)
# ===========================================================================
def test_parse_dump_reads_homeoffset_and_totalsteps():
    from app import gateway
    assert gateway._parse_dump("42:4096:0=210,1=320") == (42, 4096)
    assert gateway._parse_dump("") is None
    assert gateway._parse_dump("garbage") is None


def test_flap_helpers_post_the_right_shapes(monkeypatch):
    import asyncio
    from app import gateway

    calls = []

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"modules": [{"id": 5, "flapCount": 40}]}

    async def fake_arequest(method, url, path, *, timeout, **kw):
        calls.append((method, path, kw.get("json")))
        return _R()

    monkeypatch.setattr(gateway, "_arequest", fake_arequest)

    async def go():
        await gateway.flap_show_index("http://gw", 5, 3)
        await gateway.flap_nudge("http://gw", 5, -40)
        await gateway.flap_set_homeoffset("http://gw", 5, 128)
        await gateway.flap_home("http://gw", 5)
        return await gateway.flap_modules("http://gw")
    mods = asyncio.run(go())

    assert ("POST", "/api/flap/index", {"id": 5, "index": 3}) in calls
    assert ("POST", "/api/flap/nudge", {"id": 5, "steps": -40}) in calls
    assert ("POST", "/api/flap/homeoffset", {"id": 5, "steps": 128}) in calls
    assert ("POST", "/api/flap/home", {"id": 5}) in calls
    assert mods == [{"id": 5, "flapCount": 40}]


def test_flap_all_posts_the_id_and_returns_detail(monkeypatch):
    import asyncio
    from app import gateway

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"ok": True, "id": 5, "dump": "42:4096:0=1",
                                "flapChars": " ABC", "flapCount": 4}
    seen = {}
    async def fake_arequest(method, url, path, *, timeout, **kw):
        seen.update(method=method, path=path, json=kw.get("json")); return _R()
    monkeypatch.setattr(gateway, "_arequest", fake_arequest)

    detail = asyncio.run(gateway.flap_all("http://gw", 5))
    assert seen == {"method": "POST", "path": "/api/flap/all", "json": {"id": 5}}
    assert gateway._parse_dump(detail["dump"]) == (42, 4096)
    assert detail["flapChars"] == " ABC"


# ===========================================================================
# routes: the wall-module listing (the list endpoint has no reel — read lazily)
# ===========================================================================
def test_wall_modules_filters_to_provisioned_in_grid(monkeypatch):
    import asyncio
    from app import gateway
    from app.routes import autocal as ac

    class _Cfg:
        transport = {"gateway_url": "http://gw"}
        grid = {"rows": 1, "cols": 4, "module_id_base": 0}
    class _D:
        config = _Cfg

    async def fake_modules(url, timeout=10.0):
        return [{"id": 0, "provisioned": True}, {"id": 1, "provisioned": True},
                {"id": 255}, {"id": 9, "provisioned": True},   # 255 = slot; 9 = out of grid
                {"id": 2, "provisioned": False}]               # not provisioned
    monkeypatch.setattr(gateway, "flap_modules", fake_modules)

    url, mods = asyncio.run(ac._wall_modules(_D))
    assert url == "http://gw"
    assert [m["id"] for m in mods] == [0, 1]                   # the ready wall cells, in order
    assert mods[0]["grid_index"] == 0 and mods[1]["grid_index"] == 1
