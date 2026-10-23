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


def find_blank(capture_index, n: int, blank_index: int = 0, notify=None) -> tuple[int, float, int]:
    """Locate the blank flap WITHOUT sweeping the whole reel.

    ``capture_index(i)`` commands the module to flap ``i`` and returns its tile. We sample a
    few spread flaps to learn the "a letter is printed here" ink baseline, then search forward
    from ``blank_index`` and STOP at the first flap whose ink is clearly below that baseline —
    the blank. An already-aligned module is found in a handful of captures instead of a full
    sweep; one that is off by k flaps costs about k more.

    Returns ``(offset_flaps, confidence, blank_cmd_index)`` with the same sign convention as
    ``analyze_sweep``: commanding index ``i`` currently shows reel flap ``(i + offset_flaps)``.
    """
    seen: dict[int, float] = {}
    say = notify or (lambda kind, data: None)

    def ink_at(i):
        i %= n
        if i not in seen:
            seen[i] = tile_ink(capture_index(i))
            say("probe", {"flap": i, "ink": round(seen[i], 3)})   # live: what it looked at + saw
        return seen[i]

    sample = sorted({(blank_index + round(k * n / 4)) % n for k in range(4)})
    letters = sorted(ink_at(i) for i in sample)
    baseline = letters[len(letters) // 2] if letters else 0.0          # median ~ a letter's ink

    best_i, best_ink = blank_index % n, 1e9
    for k in range(n):
        i = (blank_index + k) % n
        v = ink_at(i)
        if v < best_ink:
            best_ink, best_i = v, i
        if baseline > 1e-6 and v < 0.45 * baseline:                     # clearly the blank
            best_ink, best_i = v, i
            break

    conf = max(0.0, min(1.0, (baseline - best_ink) / baseline)) if baseline > 1e-6 else 0.0
    offset = (blank_index - best_i) % n
    signed = offset if offset <= n // 2 else offset - n
    return signed, round(conf, 4), best_i


def calibrate_module_fast(capture_index, adjust, flap_chars: str,
                          total_steps: int, *, max_rounds: int = MAX_ROUNDS,
                          confidence_min: float = CONFIDENCE_MIN, should_abort=None,
                          notify=None) -> dict:
    """Drive ONE module to aligned: locate the blank with ``find_blank`` (no full sweep), read
    the sub-flap shift off the glyph beside it, then correct and re-measure until it's right.

    ``adjust(delta_steps)`` must be a LIVE, RELATIVE correction — the gateway's **nudge**, which
    moves the reel AND saves the new home offset instantly. It must NOT be an absolute
    home-offset write: per the calibration contract, that only takes effect after the next
    re-home, so the camera would see no change, and re-applying the "missing" correction every
    round would silently pile up a huge wrong offset. (Exactly that bug shipped in beta ≤6.)

    The direction a nudge moves the glyph in the window is hardware-dependent, so the loop
    DISCOVERS the sign: if a correction makes the error worse, it flips and continues.
    If the blank can't be found confidently it falls back to one full sweep + ``analyze_sweep``
    for this module."""
    n = len(flap_chars)
    blank_index = flap_chars.index(" ") if " " in flap_chars else 0
    spf = steps_per_flap(total_steps, n)
    sign, applied, prev_err = 1, 0, None
    history: list[dict] = []
    say = notify or (lambda kind, data: None)   # the live event feed the UI narrates from

    for rnd in range(max_rounds):
        if should_abort is not None and should_abort():
            return {"status": "failed", "reason": "aborted", "rounds": rnd,
                    "applied_steps": applied, "history": history}

        offset, conf, blank_cmd = find_blank(capture_index, n, blank_index, notify=say)
        if conf < confidence_min:
            # Ambiguous — spend a full sweep on this one module and trust analyze_sweep.
            say("fallback", {})
            res = analyze_sweep([capture_index(i) for i in range(n)], flap_chars)
            offset, conf, sub = res["offset_flaps"], res["confidence"], res["sub_flap"]
            if conf < confidence_min:
                return {"status": "needs_review", "rounds": rnd + 1, "applied_steps": applied,
                        "final": {"offset_flaps": offset, "sub_flap": sub, "confidence": conf},
                        "history": history}
        else:
            # Sub-flap from the glyph right after the blank (the blank itself has no ink).
            say("glyph_check", {})
            sub = tile_v_centroid(capture_index((blank_cmd + 1) % n)) - 0.5

        err = offset + sub
        aligned = offset == 0 and abs(sub) < 0.08
        say("measured", {"offset_flaps": offset, "sub_flap": round(sub, 3),
                         "confidence": round(conf, 2)})
        history.append({"round": rnd, "offset_flaps": offset, "sub_flap": round(sub, 4),
                        "confidence": conf})
        if aligned:
            return {"status": "aligned" if rnd == 0 else "adjusted", "rounds": rnd + 1,
                    "applied_steps": applied,
                    "final": {"offset_flaps": offset, "sub_flap": round(sub, 4), "confidence": conf},
                    "history": history}
        if prev_err is not None and abs(err) > abs(prev_err) + 0.02:
            sign = -sign
        delta = int(round(sign * -err * spf)) or (-sign if err > 0 else sign)
        say("adjust", {"steps": delta})
        adjust(delta)                          # a live nudge: reel moves now, offset saved now
        applied += delta
        prev_err = err

    offset, conf, blank_cmd = find_blank(capture_index, n, blank_index, notify=say)
    sub = tile_v_centroid(capture_index((blank_cmd + 1) % n)) - 0.5
    aligned = offset == 0 and abs(sub) < 0.08
    return {"status": "adjusted" if aligned else "failed", "rounds": max_rounds,
            "applied_steps": applied,
            "final": {"offset_flaps": offset, "sub_flap": round(sub, 4), "confidence": conf},
            "history": history}


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
      - ``park_fn(id)``        -> leave a finished module on its blank (home).
    ``snapshot`` records each module's saved offset (from its own EEPROM, via ``meta_fn``) as its
    turn comes, so ``revert()`` undoes exactly what the run touched.
    """

    def __init__(self, modules, *, meta_fn, capture_index, adjust, restore, park_fn=None,
                 log_fn=None):
        self.modules = list(modules)
        self._meta = meta_fn
        self._capture_index = capture_index
        self._adjust = adjust
        self._restore = restore
        self._park = park_fn
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
            res = calibrate_module_fast(
                lambda i, mid=mid: self._capture_index(mid, i),
                lambda s, mid=mid: self._adjust(mid, s),
                meta["flap_chars"], meta["total_steps"], should_abort=lambda: self._abort,
                notify=lambda kind, data, mid=mid: self._log(mid, kind, data))
            self.results[mid] = res
            self._log(mid, "result", {"status": res["status"],
                                      "applied_steps": res.get("applied_steps", 0)})
            # A finished module ends on its blank (a home), not whatever flap the last read
            # landed on — the wall goes visibly clean, module by module, and the re-home
            # confirms the hall sync, as the calibration doc's "always finish with Home" says.
            if self._park is not None and not self._abort:
                self._park(mid)
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
            log_fn=self._log_event)

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

    def _capture_index(self, mid, i):
        """Command module ``mid`` to flap ``i`` and return its tile — only once the flap has
        VERIFIABLY settled. After the motion-time estimate, we require two consecutive fresh
        frames whose tile agrees: a flap in flight smears and changes frame to frame, a settled
        one differs only by sensor noise. A timer alone is a guess; this is observation."""
        if self.session is not None and self.session._abort:
            raise RuntimeError("aborted")
        self._say(mid, "reading")
        wait = self._settle_for(mid, i)
        self._command(mid, i)
        gi = self.modules[mid]["grid_index"]
        if self._commander is not None:            # simulated wall — no physics to wait on
            return self._capture_tile(gi)
        self._sleep_abortable(wait)                # the motion-time estimate…
        tile, ts = self._fresh_tile(gi, time.time())
        deadline = time.time() + 2.5               # …then PROOF of stillness
        while time.time() < deadline:
            nxt, ts = self._fresh_tile(gi, ts + 1e-6)
            if tiles_similar(tile, nxt):
                return nxt
            tile = nxt
        return tile          # still changing after 5s — use the last read; the loop re-measures

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
