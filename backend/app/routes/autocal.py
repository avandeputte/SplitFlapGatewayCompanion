"""Camera auto-calibration routes (physical split-flap walls only).

The browser owns the webcam: it clicks the four wall corners once, then streams frames while
this backend drives the calibration. The vision + the per-module loop live in ``app.autocal``
(tested headless); these endpoints are the thin HTTP surface that wires that loop to a
display's gateway and to the live frames.

Only ONE run exists at a time (one camera, one wall being moved). ``deps`` is ``app.main``.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import autocal, gateway

log = logging.getLogger("companion")


class StartReq(BaseModel):
    corners: list[list[float]]           # [[x,y]×4] — TL, TR, BR, BL, in the streamed frame
    verify: bool = True                  # full-reel check per module (catches skipped flaps)


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
            verify=req.verify, loop=asyncio.get_running_loop())
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
        return {"ok": True}

    @router.post("/api/autocal/revert")
    async def revert():
        """Put every module's home offset back to what it was before this run."""
        run = deps.autocal_run
        if run is None:
            raise HTTPException(409, "no calibration run to revert")
        run.revert()
        return {"ok": True}

    return router
