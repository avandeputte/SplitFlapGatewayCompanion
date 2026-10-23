"""Camera auto-calibration routes (physical split-flap walls only).

The browser owns the webcam: it clicks the four wall corners once, then streams frames while
this backend drives the calibration. The vision + the per-module loop live in ``app.autocal``
(tested headless); these endpoints are the thin HTTP surface that wires that loop to a
display's gateway and to the live frames.

Only ONE run exists at a time (one camera, one wall being moved). ``deps`` is ``app.main``.
"""

from __future__ import annotations

import asyncio
import io
import logging

import numpy as np
from fastapi import APIRouter, HTTPException, Request, Response
from PIL import Image
from pydantic import BaseModel

from .. import autocal, gateway

log = logging.getLogger("companion")

_DEBUG_ROLLS = tuple(range(-(autocal.TILE_H // 2), autocal.TILE_H // 2 + 1, 2))


class StartReq(BaseModel):
    corners: list[list[float]]           # [[x,y]×4] — TL, TR, BR, BL, in the streamed frame


class DebugCmdReq(BaseModel):
    id: int                              # module id
    index: int | None = None             # flap index to command (None = read as-is)


class DebugNudgeReq(BaseModel):
    id: int
    steps: int                           # flap_nudge steps (+/-)


class DebugModReq(BaseModel):
    id: int


def analyze_tile(tile, flap_chars: str) -> dict:
    """Everything the calibration algorithm measures on one tile, for debugging on the real
    wall: ink, blankness (the home-search signal), the vertical centroid (sub-flap), the local
    glyph identification and its margin, and OCR's single-tile opinion."""
    refs = autocal.reference_templates(flap_chars)
    if refs:
        ch, score, margin = autocal.identify(tile, refs, rolls=_DEBUG_ROLLS)
    else:
        ch, score, margin = "?", 0.0, 0.0
    ops = autocal.ocr_tile_opinions([tile], flap_chars)
    med = float(np.median(tile))
    return {"ink": round(autocal.tile_ink(tile), 4),
            "blankness": round(autocal.tile_blankness(tile), 4),
            "stroke_energy": round(autocal.stroke_energy(tile), 4),
            "median_level": round(med, 1),
            "shape": list(tile.shape),
            "centroid": round(autocal.tile_v_centroid(tile), 4),
            "identify": {"char": ch, "score": score, "margin": margin},
            "ocr": ops.get(0)}


def _tile_png(tile) -> bytes:
    im = Image.fromarray(np.clip(tile, 0, 255).astype("uint8"), "L")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _is_physical(caps) -> bool:
    """A mechanical split-flap wall — the only thing with a home offset to calibrate. A Matrix/
    LCD wall draws its cells, so it has no reel and no canvas-less flap grid to align."""
    return caps is not None and not caps.has_canvas


async def _wall_modules(d) -> tuple[str, list[dict]]:
    """(gateway_url, modules) — the PROVISIONED modules on this wall as ``{id, grid_index}``,
    from the list endpoint. Their reels/offsets are read later, per module, during the run
    (the list endpoint carries neither). Raises HTTPException on a missing/unreachable gateway."""
    url = (d.config.transport.get("gateway_url") or "").strip()
    if not url:
        raise HTTPException(400, "no gateway_url configured")
    base = int(d.config.grid.get("module_id_base", 0))
    count = int(d.config.grid["rows"]) * int(d.config.grid["cols"])
    try:
        raw = await gateway.flap_modules(url)
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(502, f"gateway unreachable: {e}")
    out = []
    for m in raw:
        mid = int(m.get("id", 255))
        if mid == 255 or not m.get("provisioned", True):     # unprovisioned slot
            continue
        gi = mid - base
        if 0 <= gi < count:                                  # only cells on this wall
            out.append({"id": mid, "grid_index": gi})
    out.sort(key=lambda m: m["grid_index"])
    return url, out


def build(deps):
    router = APIRouter(dependency_overrides_provider=deps.app)

    @router.get("/api/autocal/plan")
    async def plan(request: Request):
        """What the UI needs to decide whether to offer auto-calibration and how to lay the
        module grid over the camera view."""
        d = deps.display_for(request)
        caps = d.controller.caps
        if not _is_physical(caps):
            return {"available": False, "reason": "not a physical split-flap wall"}
        g = d.config.grid
        _, mods = await _wall_modules(d)
        settle = (caps.settle_ms / 1000.0) if getattr(caps, "settle_ms", None) else 4.0
        return {
            "available": bool(mods),
            "reason": "" if mods else "no provisioned modules on this wall yet",
            "rows": int(g["rows"]), "cols": int(g["cols"]),
            "settle_s": settle,
            "modules": mods,
        }

    @router.post("/api/autocal/start")
    async def start(req: StartReq, request: Request):
        run = deps.autocal_run
        if run is not None and not run.status["finished"]:
            raise HTTPException(409, "a calibration run is already in progress")
        if len(req.corners) != 4:
            raise HTTPException(422, "corners must be four [x, y] points (TL, TR, BR, BL)")
        d = deps.display_for(request)
        caps = d.controller.caps
        if not _is_physical(caps):
            raise HTTPException(400, "not a physical split-flap wall")
        g = d.config.grid
        url, mods = await _wall_modules(d)
        if not mods:
            raise HTTPException(409, "no provisioned modules on this wall")
        settle = (caps.settle_ms / 1000.0) if getattr(caps, "settle_ms", None) else 4.0
        run = autocal.AutocalRun(
            gateway_url=url, corners=[tuple(c) for c in req.corners],
            rows=int(g["rows"]), cols=int(g["cols"]), modules=mods, settle_s=settle,
            loop=asyncio.get_running_loop())
        deps.autocal_run = run
        asyncio.create_task(asyncio.to_thread(run.run_blocking))
        log.info("autocal: started on %d modules", len(mods))
        return {"ok": True, "modules": [m["id"] for m in mods]}

    @router.post("/api/autocal/frame")
    async def frame(request: Request):
        """The latest camera frame (a JPEG body). Streamed continuously while a run is live."""
        run = deps.autocal_run
        if run is None or run.status["finished"]:
            raise HTTPException(409, "no calibration run is in progress")
        run.push_frame(await request.body())
        return {"ok": True}

    @router.get("/api/autocal/status")
    async def status():
        run = deps.autocal_run
        return run.status if run is not None else {"active": False}

    @router.post("/api/autocal/abort")
    async def abort():
        run = deps.autocal_run
        if run is not None:
            run.abort()
            if getattr(run, "debug", False):
                run.finished = True        # a debug holder never loops; free the single-run slot
        return {"ok": True}

    @router.post("/api/autocal/revert")
    async def revert():
        """Put every module's home offset back to what it was before this run."""
        run = deps.autocal_run
        if run is None:
            raise HTTPException(409, "no calibration run to revert")
        run.revert()
        return {"ok": True}

    # -- debug on the real wall -------------------------------------------------
    # The automatic loop can't be tuned against a simulator — the real font, camera and lighting
    # are what matter. These let a human set the corners + stream frames from the page while the
    # loop stays OFF, and then drive the wall and read back the ACTUAL captured tiles (image +
    # every measurement) step by step, over REST or the MCP tools that wrap them.
    def _debug_run():
        run = deps.autocal_run
        if run is None or not getattr(run, "debug", False) or run.finished:
            raise HTTPException(409, "no debug session — start one from the Calibrate page")
        return run

    def _grid_index(run, mid: int) -> int:
        m = run.modules.get(int(mid))
        if m is None:
            raise HTTPException(404, f"module {mid} is not on this wall")
        return m["grid_index"]

    @router.post("/api/autocal/debug/start")
    async def debug_start(req: StartReq, request: Request):
        if len(req.corners) != 4:
            raise HTTPException(422, "corners must be four [x, y] points (TL, TR, BR, BL)")
        d = deps.display_for(request)
        caps = d.controller.caps
        if not _is_physical(caps):
            raise HTTPException(400, "not a physical split-flap wall")
        g = d.config.grid
        url, mods = await _wall_modules(d)
        if not mods:
            raise HTTPException(409, "no provisioned modules on this wall")
        settle = (caps.settle_ms / 1000.0) if getattr(caps, "settle_ms", None) else 4.0
        run = autocal.AutocalRun(
            gateway_url=url, corners=[tuple(c) for c in req.corners],
            rows=int(g["rows"]), cols=int(g["cols"]), modules=mods, settle_s=settle,
            loop=asyncio.get_running_loop())
        run.debug = True                     # a frame/corner holder; the loop never runs
        deps.autocal_run = run
        log.info("autocal: debug session on %d modules", len(mods))
        return {"ok": True, "modules": [m["id"] for m in mods],
                "rows": int(g["rows"]), "cols": int(g["cols"]), "settle_s": settle}

    @router.get("/api/autocal/debug/frame.png")
    async def debug_frame():
        run = _debug_run()
        if run._frame is None:
            raise HTTPException(409, "no camera frame yet")
        return Response(run._frame[0], media_type="image/jpeg")

    @router.get("/api/autocal/debug/tile.png")
    async def debug_tile_png(module: int):
        run = _debug_run()
        tile = run._capture_tile(_grid_index(run, module))
        return Response(_tile_png(tile), media_type="image/png")

    async def _reel_chars(run, mid: int) -> str:
        chars = run._reel.get(int(mid))
        if chars:
            return chars
        raw = await gateway.flap_all(run.gateway_url, int(mid))
        chars = raw.get("flapChars") or ""
        if chars:
            run._reel[int(mid)] = chars
        return chars

    @router.post("/api/autocal/debug/command")
    async def debug_command(req: DebugCmdReq):
        """Command a flap (or read as-is), wait the settle, and return the captured tile's
        measurements. The tile image itself is at /api/autocal/debug/tile.png?module=ID."""
        run = _debug_run()
        gi = _grid_index(run, req.id)
        if req.index is not None:
            await gateway.flap_show_index(run.gateway_url, req.id, int(req.index))
            await asyncio.sleep(run.settle_s)
        tile = run._capture_tile(gi)
        chars = await _reel_chars(run, req.id)
        return {"ok": True, "id": req.id, "index": req.index,
                "flap_chars": chars, "analysis": analyze_tile(tile, chars)}

    @router.post("/api/autocal/debug/broadcast")
    async def debug_broadcast(req: DebugCmdReq):
        """Broadcast 'show flap ``index``' to EVERY module at once (gateway ``id:-1``), wait the
        settle, and return nothing — the caller then reads each module's tile. One broadcast
        sweep captures the whole board, so 15 modules cost one sweep, not fifteen."""
        run = _debug_run()
        if req.index is None:
            raise HTTPException(422, "index required for a broadcast")
        await gateway.flap_show_index(run.gateway_url, -1, int(req.index))
        await asyncio.sleep(run.settle_s)
        return {"ok": True, "index": int(req.index)}

    @router.post("/api/autocal/debug/nudge")
    async def debug_nudge(req: DebugNudgeReq):
        run = _debug_run()
        await gateway.flap_nudge(run.gateway_url, req.id, int(req.steps))
        await asyncio.sleep(run.settle_s)
        return {"ok": True}

    @router.post("/api/autocal/debug/home")
    async def debug_home(req: DebugModReq):
        run = _debug_run()
        await gateway.flap_home(run.gateway_url, req.id)
        await asyncio.sleep(run.settle_s)
        return {"ok": True}

    @router.post("/api/autocal/debug/calibrate")
    async def debug_calibrate(req: DebugModReq):
        run = _debug_run()
        await gateway.flap_calibrate_start(run.gateway_url, req.id)
        for _ in range(25):
            await asyncio.sleep(1.0)
            st = await gateway.flap_calibrate_status(run.gateway_url)
            if st.get("state") == "done":
                return {"ok": True, "stepsPerRev": st.get("stepsPerRev")}
            if st.get("state") in ("timeout", "idle"):
                return {"ok": False, "state": st.get("state")}
        return {"ok": False, "state": "timeout"}

    @router.post("/api/autocal/debug/meta")
    async def debug_meta(req: DebugModReq):
        """The module's raw EEPROM read + parsed home offset / total steps / flap set — exactly
        what the run uses, so a 'not ready' is diagnosable."""
        run = _debug_run()
        raw = await gateway.flap_all(run.gateway_url, req.id)
        dump = gateway._parse_dump(raw.get("dump", ""))
        return {"ok": True, "id": req.id, "stale": raw.get("stale"),
                "flap_chars": raw.get("flapChars") or "", "flap_count": raw.get("flapCount"),
                "home_offset": dump[0] if dump else None,
                "total_steps": dump[1] if dump else None, "raw_dump": raw.get("dump", "")}

    return router
