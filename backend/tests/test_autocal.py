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


# ===========================================================================
# Phase 1: the closed-loop orchestration, driven against a simulated wall.
# ===========================================================================
class FakeModule:
    """A simulated split-flap module: it holds a home-offset error (in steps) and renders what
    the camera would see when commanded to an index. ``conv`` is the (unknown, hardware-
    dependent) sign relating a home-offset change to reel movement — calibrate_module must
    converge for either value."""

    def __init__(self, reel, total_steps, *, err_flaps=0, sub=0.0, conv=1):
        self.reel = reel
        self.n = len(reel)
        self.total = total_steps
        self.spf = total_steps / self.n
        self.conv = conv
        self.homeoffset = 1000
        self.err_steps = err_flaps * self.spf + sub * self.spf
        self.commanded = 0

    def show_index(self, i):
        self.commanded = int(i) % self.n

    def set_homeoffset(self, steps):
        self.err_steps += self.conv * (steps - self.homeoffset)
        self.homeoffset = steps

    def capture(self):
        pos = self.commanded + self.err_steps / self.spf      # displayed flap, fractional
        whole = int(round(pos)) % self.n
        frac = pos - round(pos)                                # sub-flap residual, in flaps
        return _glyph_tile(self.reel[whole], shift=int(round(frac * autocal.TILE_H)))

    def sweep(self):
        out = []
        for i in range(self.n):
            self.show_index(i)
            out.append(self.capture())
        return out


def _drive(mod, **kw):
    return autocal.calibrate_module(mod.sweep, lambda: mod.homeoffset, mod.set_homeoffset,
                                    mod.reel, mod.total, **kw)


def test_an_already_aligned_module_is_left_alone():
    mod = FakeModule(REEL, 2000, err_flaps=0, sub=0.0)
    r = _drive(mod)
    assert r["status"] == "aligned"
    assert r["applied_steps"] == 0


@pytest.mark.parametrize("err,conv", [(3, 1), (3, -1), (-5, 1), (12, -1), (1, 1)])
def test_a_whole_flap_error_converges_for_either_hardware_sign(err, conv):
    mod = FakeModule(REEL, 2000, err_flaps=err, conv=conv)
    r = _drive(mod)
    assert r["status"] in ("adjusted", "aligned"), r
    # the module really ends aligned: command index 3, it shows reel flap 3
    mod.show_index(3)
    res = autocal.analyze_sweep(mod.sweep(), REEL)
    assert res["aligned"], res


def test_a_sub_flap_residual_is_corrected():
    mod = FakeModule(REEL, 2000, err_flaps=0, sub=0.25, conv=1)
    r = _drive(mod)
    assert r["status"] in ("adjusted", "aligned")
    assert autocal.analyze_sweep(mod.sweep(), REEL)["aligned"]


def test_a_combined_whole_and_sub_error_converges():
    mod = FakeModule(REEL, 3600, err_flaps=4, sub=0.3, conv=-1)
    r = _drive(mod)
    assert r["status"] in ("adjusted", "aligned"), r["history"]
    assert autocal.analyze_sweep(mod.sweep(), REEL)["aligned"]


def test_abort_stops_the_loop():
    mod = FakeModule(REEL, 2000, err_flaps=7)
    r = _drive(mod, should_abort=lambda: True)
    assert r["status"] == "failed" and r["reason"] == "aborted"
    assert mod.err_steps == 7 * mod.spf     # untouched


def test_an_ambiguous_module_is_flagged_not_calibrated():
    mod = FakeModule(REEL, 2000, err_flaps=2)
    orig_sweep = mod.sweep
    def ambiguous():
        s = orig_sweep()
        s[5] = _glyph_tile(" ")             # a second near-blank -> no clean anchor
        return s
    r = autocal.calibrate_module(ambiguous, lambda: mod.homeoffset, mod.set_homeoffset,
                                 mod.reel, mod.total)
    assert r["status"] == "needs_review"


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
        await gateway.flap_set_homeoffset("http://gw", 5, 128)
        mods = await gateway.flap_modules("http://gw")
        return mods
    mods = asyncio.run(go())

    assert ("POST", "/api/flap/index", {"id": 5, "index": 3}) in calls
    assert ("POST", "/api/flap/homeoffset", {"id": 5, "steps": 128}) in calls
    assert ("GET", "/api/flap/modules", None) in calls
    assert mods == [{"id": 5, "flapCount": 40}]


# ===========================================================================
# the wall-level job: run all modules, abort, revert
# ===========================================================================
def _capture_index(mods):
    def cap(mid, i):
        mods[mid].show_index(i)
        return mods[mid].capture()
    return cap


def _fake_wall(specs):
    """specs: list of (id, err_flaps). Returns (modules, meta, wiring) for a CalibrationSession."""
    mods = {mid: FakeModule(REEL, 2000, err_flaps=err) for mid, err in specs}
    meta = [{"id": mid, "flap_chars": REEL, "total_steps": 2000} for mid, _ in specs]
    wiring = dict(
        capture_index=_capture_index(mods),
        get_offset=lambda mid: mods[mid].homeoffset,
        set_offset=lambda mid, s: mods[mid].set_homeoffset(s),
    )
    return mods, meta, wiring


def test_the_session_calibrates_every_module_and_snapshots():
    mods, meta, wiring = _fake_wall([(0, 3), (1, 0), (2, -4)])
    sess = autocal.CalibrationSession(meta, **wiring)
    assert sess.snapshot == {0: 1000, 1: 1000, 2: 1000}
    prog = sess.run()
    assert prog["done"] and prog["completed"] == 3
    assert {r["status"] for r in prog["results"].values()} <= {"aligned", "adjusted"}
    for mid in (0, 1, 2):
        mods[mid].show_index(7)
        assert autocal.analyze_sweep(mods[mid].sweep(), REEL)["aligned"]


def test_the_session_reverts_to_the_snapshot():
    mods, meta, wiring = _fake_wall([(0, 5), (1, 8)])
    sess = autocal.CalibrationSession(meta, **wiring)
    sess.run()
    assert mods[0].homeoffset != 1000 or mods[1].homeoffset != 1000   # it moved them
    sess.revert()
    assert mods[0].homeoffset == 1000 and mods[1].homeoffset == 1000


# ===========================================================================
# AutocalRun — the live manager (injected seams; no gateway, no camera)
# ===========================================================================
def _run_over_fake_wall(specs, **kw):
    wall = {mid: FakeModule(REEL, 2000, err_flaps=err) for mid, err in specs}
    modules = [{"id": mid, "grid_index": mid, "flap_chars": REEL, "total_steps": 2000,
                "home_offset": 1000} for mid, _ in specs]
    run = autocal.AutocalRun(
        gateway_url="http://gw", corners=[(0, 0), (1, 0), (1, 1), (0, 1)],
        rows=1, cols=len(specs), modules=modules, settle_s=0,
        commander=lambda mid, idx: wall[mid].show_index(idx),
        offset_writer=lambda mid, steps: wall[mid].set_homeoffset(steps),
        capture=lambda gi: wall[gi].capture(), **kw)
    return wall, run


def test_autocal_run_calibrates_the_whole_wall():
    wall, run = _run_over_fake_wall([(0, 4), (1, 0), (2, -3)])
    run.run_blocking()
    st = run.status
    assert st["finished"] and st["error"] is None and st["completed"] == 3
    for mid in (0, 1, 2):
        wall[mid].show_index(9)
        assert autocal.analyze_sweep(wall[mid].sweep(), REEL)["aligned"]


def test_autocal_run_revert_restores_offsets():
    wall, run = _run_over_fake_wall([(0, 6)])
    run.run_blocking()
    assert wall[0].homeoffset != 1000
    run.revert()
    assert wall[0].homeoffset == 1000


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


def test_autocal_run_status_before_start_is_empty_progress():
    _, run = _run_over_fake_wall([(0, 0)])
    st = run.status
    assert st["completed"] == 0 and st["total"] == 1 and not st["finished"]


# ===========================================================================
# Phase 3: the fast path (find the blank without sweeping the whole reel)
# ===========================================================================
class _CountingWall:
    """A FakeModule that counts how many captures (settle cycles) it took — the thing the fast
    path exists to reduce."""
    def __init__(self, err_flaps=0, sub=0.0):
        self.m = FakeModule(REEL, 2000, err_flaps=err_flaps, sub=sub)
        self.captures = 0
    def capture_index(self, i):
        self.m.show_index(i)
        self.captures += 1
        return self.m.capture()


@pytest.mark.parametrize("err", [0, 1, 4, -3, 9])
def test_find_blank_recovers_the_offset(err):
    w = _CountingWall(err_flaps=err)
    offset, conf, blank_cmd = autocal.find_blank(w.capture_index, len(REEL), 0)
    assert offset == err
    assert conf > 0.3


def test_find_blank_is_cheap_when_aligned():
    w = _CountingWall(err_flaps=0)
    autocal.find_blank(w.capture_index, len(REEL), 0)
    assert w.captures <= 6, f"an aligned module should cost a few captures, not a full sweep ({w.captures})"


def test_fast_calibrate_converges_and_is_cheap_when_already_aligned():
    w = _CountingWall(err_flaps=0)
    r = autocal.calibrate_module_fast(w.capture_index, lambda: w.m.homeoffset,
                                      w.m.set_homeoffset, REEL, 2000)
    assert r["status"] == "aligned"
    assert w.captures <= 8 and r["applied_steps"] == 0


@pytest.mark.parametrize("err,conv", [(5, 1), (5, -1), (-7, 1)])
def test_fast_calibrate_fixes_a_wrong_letter_module(err, conv):
    w = _CountingWall()
    w.m = FakeModule(REEL, 2000, err_flaps=err, conv=conv)
    r = autocal.calibrate_module_fast(w.capture_index, lambda: w.m.homeoffset,
                                      w.m.set_homeoffset, REEL, 2000)
    assert r["status"] in ("adjusted", "aligned"), r["history"]
    w.m.show_index(11)
    assert autocal.analyze_sweep(w.m.sweep(), REEL)["aligned"]


def test_fast_calibrate_falls_back_to_full_sweep_when_the_blank_is_ambiguous(monkeypatch):
    """If the blank can't be found confidently, it spends one full sweep on that module rather
    than giving up or guessing."""
    w = _CountingWall(err_flaps=3)
    # force find_blank to report no-confidence so the fallback path runs
    monkeypatch.setattr(autocal, "find_blank", lambda *a, **k: (3, 0.0, 0))
    r = autocal.calibrate_module_fast(w.capture_index, lambda: w.m.homeoffset,
                                      w.m.set_homeoffset, REEL, 2000)
    # fallback used analyze_sweep (a full reel's worth of captures in a round)
    assert w.captures >= len(REEL)
    assert r["status"] in ("adjusted", "aligned", "needs_review")


def test_session_summary_counts_outcomes():
    mods, meta, wiring = _fake_wall([(0, 0), (1, 4), (2, -2)])
    sess = autocal.CalibrationSession(meta, **wiring)
    prog = sess.run()
    s = prog["summary"]
    assert s["aligned"] + s["adjusted"] == 3 and s["failed"] == 0
