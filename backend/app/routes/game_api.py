"""game_api.py — the low-latency input endpoint for interactive matrix apps.

The web UI's control pad POSTs one action per button/keypress here; it lands in the
``gameinput`` buffer for this display's wall, where the running game reads it on its next
frame (no round trip of its own). The heavy path — the frame back to the panel — is
binary ops over the draw stream; this endpoint only carries the tiny input the other way.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import gameinput


class GameInput(BaseModel):
    action: str = ""      # up / down / left / right / release / start / pause / coin


def build(deps) -> APIRouter:
    router = APIRouter(dependency_overrides_provider=deps.app)

    @router.post("/api/game/input")
    async def game_input(request: Request, req: GameInput):
        d = deps.display_for(request)
        url = str(d.config.transport.get("gateway_url") or "").strip()
        if not url:
            raise HTTPException(400, "no gateway configured")
        if not gameinput.push(url, req.action, now=time.monotonic()):
            raise HTTPException(400, f"unknown action: {req.action!r}")
        return {"ok": True}

    return router
