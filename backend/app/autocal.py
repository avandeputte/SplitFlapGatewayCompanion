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

import io
import time

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


# ---------------------------------------------------------------------------
# Orchestration: drive ONE module's home offset to aligned (Phase 1).
#
# This is the closed loop the calibration job runs per module. It is pure — every side
# effect is an injected callable — so the whole thing is tested against a simulated wall
# with no camera and no gateway (see test_autocal.py). In production the session wires:
#   sweep_fn    -> command each flap index on the gateway, wait to settle, grab the latest
#                  camera frame and rectify this module's tile (one pass per call);
#   get_offset  -> the module's current homeOffset (from /api/flap/modules);
#   set_offset  -> POST /api/flap/homeoffset.
# ---------------------------------------------------------------------------
CONFIDENCE_MIN = 0.12          # below this, flag the module for a human rather than guess
MAX_ROUNDS = 6                 # a correction + a few refinements; mechanical walls are slow


def steps_per_flap(total_steps: int, flap_count: int) -> float:
    return float(total_steps) / max(1, int(flap_count))


def calibrate_module(sweep_fn, get_offset, set_offset, flap_chars: str,
                     total_steps: int, *, max_rounds: int = MAX_ROUNDS,
                     confidence_min: float = CONFIDENCE_MIN, should_abort=None) -> dict:
    """Iterate a single module to aligned, measuring with the camera and writing its home
    offset, until the blank sits at index 0 and glyphs ride centred — or we run out of rounds
    or the measurement is too ambiguous to trust.

    The direction a home-offset change moves the reel is hardware-dependent, so the loop
    DISCOVERS the sign: if a correction makes the error worse, it flips sign and continues.
    That makes the real-hardware sign a non-issue (Phase 3 just confirms it converges).

    Returns ``{status, rounds, applied_steps, final, history}`` where status is
    ``aligned`` (already good), ``adjusted`` (fixed), ``needs_review`` (too ambiguous) or
    ``failed`` (didn't converge / aborted).
    """
    spf = steps_per_flap(total_steps, len(flap_chars))
    sign = 1
    applied = 0
    prev_err = None
    history: list[dict] = []

    for rnd in range(max_rounds):
        if should_abort is not None and should_abort():
            return {"status": "failed", "reason": "aborted", "rounds": rnd,
                    "applied_steps": applied, "history": history}
        res = analyze_sweep(sweep_fn(), flap_chars)
        err = res["offset_flaps"] + res["sub_flap"]            # total error, in flaps
        history.append({"round": rnd, "offset_flaps": res["offset_flaps"],
                        "sub_flap": res["sub_flap"], "confidence": res["confidence"]})
        if res["confidence"] < confidence_min:
            return {"status": "needs_review", "rounds": rnd + 1, "applied_steps": applied,
                    "final": res, "history": history}
        if res["aligned"]:
            return {"status": "aligned" if rnd == 0 else "adjusted", "rounds": rnd + 1,
                    "applied_steps": applied, "final": res, "history": history}
        # A correction that made things worse means our sign guess was wrong; flip it.
        if prev_err is not None and abs(err) > abs(prev_err) + 0.02:
            sign = -sign
        delta = int(round(sign * -err * spf))                  # move to shrink the error
        if delta == 0:                                         # a sub-flap residual < 1 step
            delta = -sign if err > 0 else sign
        set_offset(get_offset() + delta)
        applied += delta
        prev_err = err

    res = analyze_sweep(sweep_fn(), flap_chars)
    return {"status": "adjusted" if res["aligned"] else "failed", "rounds": max_rounds,
            "applied_steps": applied, "final": res, "history": history}


class CalibrationSession:
    """The wall-level job: calibrate every module in turn, one at a time, abortably, and be
    able to put every offset back the way it was.

    Side effects are injected, exactly as for ``calibrate_module``, so the whole run is tested
    against the simulated wall. Each ``module`` is ``{id, flap_chars, total_steps}``:
      - ``sweep_fn(id)``  -> the module's flap sweep (command each index, capture);
      - ``get_offset(id)`` / ``set_offset(id, steps)`` -> its home offset.
    ``snapshot`` records every offset up front so ``revert()`` can undo a run the user dislikes.
    """

    def __init__(self, modules, sweep_fn, get_offset, set_offset):
        self.modules = list(modules)
        self._sweep_fn = sweep_fn
        self._get = get_offset
        self._set = set_offset
        self._abort = False
        self.snapshot = {m["id"]: get_offset(m["id"]) for m in self.modules}
        self.results: dict[int, dict] = {}
        self.done = False

    def abort(self) -> None:
        self._abort = True

    @property
    def progress(self) -> dict:
        return {"total": len(self.modules), "completed": len(self.results),
                "aborted": self._abort, "done": self.done,
                "results": dict(self.results)}

    def run(self) -> dict:
        for m in self.modules:
            if self._abort:
                break
            mid = m["id"]
            self.results[mid] = calibrate_module(
                lambda mid=mid: self._sweep_fn(mid),
                lambda mid=mid: self._get(mid),
                lambda s, mid=mid: self._set(mid, s),
                m["flap_chars"], m["total_steps"], should_abort=lambda: self._abort)
        self.done = not self._abort
        return self.progress

    def revert(self) -> None:
        """Restore every module's home offset to its pre-run value."""
        for mid, steps in self.snapshot.items():
            self._set(mid, steps)


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
                 loop=None, commander=None, offset_writer=None, capture=None):
        # modules: [{id, grid_index, flap_chars, total_steps, home_offset}]
        self.gateway_url = gateway_url
        self.corners = corners
        self.rows, self.cols = rows, cols
        self.settle_s = settle_s
        self.modules = {m["id"]: m for m in modules}
        self._loop = loop
        self._commander = commander          # test seam: commander(id, index)
        self._offset_writer = offset_writer   # test seam: offset_writer(id, steps)
        self._capture = capture               # test seam: capture(grid_index) -> tile
        self._frame = None                    # (jpeg bytes, ts)
        self._offsets = {m["id"]: m["home_offset"] for m in modules}
        self.error = None
        self.finished = False
        self.session = CalibrationSession(
            [{"id": m["id"], "flap_chars": m["flap_chars"], "total_steps": m["total_steps"]}
             for m in modules],
            sweep_fn=self._sweep, get_offset=self._get_offset, set_offset=self._set_offset)

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

    def _get_offset(self, mid):
        return self._offsets[mid]

    def _set_offset(self, mid, steps):
        self._offsets[mid] = steps
        if self._offset_writer is not None:
            self._offset_writer(mid, steps)
        elif self._commander is None:
            from . import gateway
            self._run_coro(gateway.flap_set_homeoffset(self.gateway_url, mid, steps))

    def _capture_tile(self, grid_index):
        if self._capture is not None:
            return self._capture(grid_index)
        if self._frame is None:
            raise RuntimeError("no camera frame yet")
        img = Image.open(io.BytesIO(self._frame[0]))
        return tiles(img, self.corners, self.rows, self.cols)[grid_index]

    def _sweep(self, mid):
        m = self.modules[mid]
        out = []
        for i in range(len(m["flap_chars"])):
            if self.session is not None and self.session._abort:
                break
            self._command(mid, i)
            if self._commander is None:       # real hardware: let the flap settle
                time.sleep(self.settle_s)
            out.append(self._capture_tile(m["grid_index"]))
        return out

    # -- lifecycle -----------------------------------------------------------
    def run_blocking(self) -> None:
        try:
            self.session.run()
        except Exception as e:               # noqa: BLE001 - surfaced in status
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
                "settle_s": self.settle_s}
