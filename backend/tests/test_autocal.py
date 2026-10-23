"""Camera auto-calibration (app/autocal.py), proved without a camera or a gateway.

The vision is tested against SYNTHETIC fixtures — tiles rendered with Pillow, assembled into a
reel, a module "sweep" simulated with a KNOWN home-offset error, vertical shift, brightness
variation and noise. The orchestration is tested against a simulated wall whose modules mirror
the REAL calibration contract: ``nudge`` is a LIVE correction (reel moves + offset saved
instantly); an absolute home-offset write only takes effect on a re-home (which is why the loop
must never use it for corrections — that bug shipped in beta ≤6 and is pinned here).
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


def test_tiles_similar_tells_settled_from_moving():
    """Consecutive reads of a SETTLED flap agree (sensor noise only); mid-move they differ."""
    assert autocal.tiles_similar(_glyph_tile("A"), _glyph_tile("A"))
    noisy = np.clip(_glyph_tile("A") + np.random.default_rng(1).normal(0, 1.5,
                    (autocal.TILE_H, autocal.TILE_W)), 0, 255).astype(np.float32)
    assert autocal.tiles_similar(_glyph_tile("A"), noisy)
    assert not autocal.tiles_similar(_glyph_tile("A"), _glyph_tile("W"))


# ===========================================================================
# the simulated wall — mirrors the REAL calibration contract
# ===========================================================================
class FakeModule:
    """A simulated split-flap module: holds a home-offset error (in steps) and renders what the
    camera would see. ``conv`` is the (hardware-dependent) direction a nudge moves the glyph in
    the window — the calibrator must converge for either value.

    Mirrors the real write contract: ``nudge`` moves the reel AND saves the offset instantly
    (the live correction); ``restore`` is the absolute write + re-home that revert uses."""

    def __init__(self, reel, total_steps, *, err_flaps=0, sub=0.0, conv=1, glued=None):
        self.reel = reel
        self.glued = glued                       # a flap that never presents (sticks to the next)
        self.n = len(reel)
        self.total = total_steps
        self.spf = total_steps / self.n
        self.conv = conv
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
        return _glyph_tile(self.reel[whole], shift=int(round(frac * autocal.TILE_H)))

    def sweep(self):
        out = []
        for i in range(self.n):
            self.show_index(i)
            out.append(self.capture())
        return out


def _fast(mod, **kw):
    def cap(i):
        mod.show_index(i)
        return mod.capture()
    return autocal.calibrate_module_fast(cap, mod.nudge, mod.reel, mod.total, **kw)


def test_an_already_aligned_module_is_left_alone():
    mod = FakeModule(REEL, 2000)
    r = _fast(mod)
    assert r["status"] == "aligned"
    assert r["applied_steps"] == 0 and mod.homeoffset == 1000


@pytest.mark.parametrize("err,conv", [(3, 1), (3, -1), (-5, 1), (12, -1), (1, 1)])
def test_a_whole_flap_error_converges_for_either_nudge_direction(err, conv):
    mod = FakeModule(REEL, 2000, err_flaps=err, conv=conv)
    r = _fast(mod)
    assert r["status"] in ("adjusted", "aligned"), r["history"]
    mod.show_index(3)
    assert autocal.analyze_sweep(mod.sweep(), REEL)["aligned"]


def test_a_sub_flap_residual_is_corrected():
    mod = FakeModule(REEL, 2000, sub=0.25)
    r = _fast(mod)
    assert r["status"] in ("adjusted", "aligned")
    assert autocal.analyze_sweep(mod.sweep(), REEL)["aligned"]


def test_a_combined_whole_and_sub_error_converges():
    mod = FakeModule(REEL, 3600, err_flaps=4, sub=0.3, conv=-1)
    r = _fast(mod)
    assert r["status"] in ("adjusted", "aligned"), r["history"]
    assert autocal.analyze_sweep(mod.sweep(), REEL)["aligned"]


def test_abort_stops_the_loop_without_touching_the_module():
    mod = FakeModule(REEL, 2000, err_flaps=7)
    r = _fast(mod, should_abort=lambda: True)
    assert r["status"] == "failed" and r["reason"] == "aborted"
    assert mod.homeoffset == 1000


def test_events_narrate_measure_and_adjust():
    """The live log the UI renders: the loop must report what it measured and what it did."""
    mod = FakeModule(REEL, 2000, err_flaps=2)
    seen = []
    _fast(mod, notify=lambda kind, data: seen.append((kind, data)))
    kinds = [k for k, _ in seen]
    assert "measured" in kinds and "adjust" in kinds
    first_measure = next(d for k, d in seen if k == "measured")
    assert first_measure["offset_flaps"] == 2


# ===========================================================================
# the fast path (find the blank without sweeping the whole reel)
# ===========================================================================
class _CountingWall:
    """A FakeModule that counts captures (settle cycles) — the thing the fast path minimises."""
    def __init__(self, err_flaps=0, sub=0.0, conv=1):
        self.m = FakeModule(REEL, 2000, err_flaps=err_flaps, sub=sub, conv=conv)
        self.captures = 0

    def capture_index(self, i):
        self.m.show_index(i)
        self.captures += 1
        return self.m.capture()


@pytest.mark.parametrize("err", [0, 1, 4, -3, 9])
def test_find_blank_recovers_the_offset(err):
    w = _CountingWall(err_flaps=err)
    offset, conf, blank_cmd, evidence = autocal.find_blank(w.capture_index, len(REEL), 0)
    assert offset == err
    assert conf > 0.3


def test_find_blank_is_cheap_when_aligned():
    w = _CountingWall()
    autocal.find_blank(w.capture_index, len(REEL), 0)
    assert w.captures <= 6, f"an aligned module should cost a few captures, not a sweep ({w.captures})"


def test_fast_calibrate_is_cheap_when_already_aligned():
    w = _CountingWall()
    r = autocal.calibrate_module_fast(w.capture_index, w.m.nudge, REEL, 2000)
    assert r["status"] == "aligned"
    assert w.captures <= 8 and r["applied_steps"] == 0


def test_fast_calibrate_falls_back_to_full_sweep_when_the_blank_is_ambiguous(monkeypatch):
    """If the blank can't be found confidently, spend one full sweep on that module rather than
    give up or guess."""
    w = _CountingWall(err_flaps=3)
    monkeypatch.setattr(autocal, "find_blank", lambda *a, **k: (3, 0.0, 0, {"baseline": 0.2, "blank_ink": 0.01}))
    r = autocal.calibrate_module_fast(w.capture_index, w.m.nudge, REEL, 2000)
    assert w.captures >= len(REEL)
    assert r["status"] in ("adjusted", "aligned", "needs_review")


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


def test_autocal_run_calibrates_the_whole_wall():
    wall, run = _run_over_fake_wall([(0, 4), (1, 0), (2, -3)])
    run.run_blocking()
    st = run.status
    assert st["finished"] and st["error"] is None and st["completed"] == 3
    for mid in (0, 1, 2):
        wall[mid].show_index(9)
        assert autocal.analyze_sweep(wall[mid].sweep(), REEL)["aligned"]


def test_autocal_run_emits_the_event_narration():
    _, run = _run_over_fake_wall([(0, 4)])
    run.run_blocking()
    kinds = [e["kind"] for e in run.events]            # the full buffer; status serves a window
    assert kinds[0] == "start"
    assert "measured" in kinds and "adjust" in kinds and "result" in kinds and "park_check" in kinds
    assert "verify_start" in kinds                     # the full-reel pass ran (default on)
    probe = next(e for e in run.events if e["kind"] == "probe")
    assert "char" in probe and "ink" in probe          # dialed-vs-saw detail on every look
    assert all("seq" in e and "module" in e for e in run.status["events"])


def test_autocal_run_revert_restores_offsets():
    wall, run = _run_over_fake_wall([(0, 6)])
    run.run_blocking()
    assert wall[0].homeoffset != 1000
    run.revert()
    assert wall[0].homeoffset == 1000


def test_autocal_run_abort_is_a_clean_stop_not_an_error():
    _, run = _run_over_fake_wall([(0, 3)])
    run.session.abort()
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


def test_find_blank_narrates_every_probe():
    """The live log must move WITH the wall: each flap looked at is reported, with its ink —
    "module 1: starting" followed by a minute of silence is what broke the user's trust."""
    w = _CountingWall(err_flaps=2)
    seen = []
    autocal.find_blank(w.capture_index, len(REEL), 0, notify=lambda k, d: seen.append((k, d)))
    probes = [d for k, d in seen if k == "probe"]
    assert len(probes) == w.captures                 # one narration line per actual look
    assert all("flap" in d and "ink" in d for d in probes)


def test_tiles_similar_survives_realistic_camera_noise():
    """Raw webcam pixels flicker (JPEG + auto-exposure) even when nothing moves — the stillness
    check must pool that noise away or every read burns its whole timeout."""
    rng = np.random.default_rng(7)
    a = _glyph_tile("K")
    b = np.clip(a + rng.normal(0, 6.0, a.shape), 0, 255).astype(np.float32)   # heavy sensor noise
    assert autocal.tiles_similar(a, b), "same flap under noise must read as settled"
    assert not autocal.tiles_similar(a, _glyph_tile("W"))


# ===========================================================================
# full-reel verification + the post-home check
# ===========================================================================
def test_verify_reel_passes_a_healthy_module():
    w = _CountingWall()
    r = autocal.verify_reel(w.capture_index, REEL)
    assert r["ok"] and r["skips"] == [] and r["blank_ok"]


def test_verify_reel_catches_a_glued_flap():
    """A stuck flap shows the same image on two consecutive commands — the check must name it."""
    w = _CountingWall()
    w.m = FakeModule(REEL, 2000, glued=10)
    seen = []
    r = autocal.verify_reel(w.capture_index, REEL, notify=lambda k, d: seen.append((k, d)))
    assert not r["ok"] and 10 in r["skips"]
    assert any(k == "skip_suspect" and d.get("flap") == 10 for k, d in seen)


def test_find_blank_returns_its_evidence():
    w = _CountingWall()
    _, _, _, ev = autocal.find_blank(w.capture_index, len(REEL), 0)
    assert ev["blank_ink"] < ev["baseline"]
    assert autocal.looks_blank(ev["blank_ink"], ev)
    assert not autocal.looks_blank(ev["baseline"], ev)


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


def test_session_verify_can_be_turned_off():
    mods, modules, wiring = _fake_wall([(0, 0)])
    mods[0].glued = 20
    prog = autocal.CalibrationSession(modules, verify=False, **wiring).run()
    assert "reel_check" not in prog["results"][0]
