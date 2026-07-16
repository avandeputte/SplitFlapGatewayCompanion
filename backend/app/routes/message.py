"""The message surface: compose/send, the plain-text message endpoint, clear,
physical home, and the gateway status probe the Display tab reads.

Split out of main.py (audit E1); the bodies, docstrings and behaviour are
main.py's, verbatim. ``deps`` is the app.main module — see routes/__init__.py.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import renderer, vestaboard

log = logging.getLogger("companion")


class ComposeRequest(BaseModel):
    text: str
    style: str | None = None
    speed: int | None = None
    raw: bool = False


class MessageRequest(BaseModel):
    text: str
    style: str | None = None
    seconds: int | None = None      # >0 = temporary, then revert to what was playing


def build(deps) -> APIRouter:
    # dependency_overrides_provider is what @app.<method> bakes into an APIRoute;
    # these routes join app.routes FLAT (see main._include_flat), so they carry it
    # themselves. deps.app exists by the time main calls build().
    router = APIRouter(dependency_overrides_provider=deps.app)

    @router.post("/api/compose/send")
    async def compose_send(request: Request, req: ComposeRequest):
        d = deps.display_for(request)
        if req.style and req.style not in renderer.ALL_STYLES:
            raise HTTPException(400, f"unknown style: {req.style}")
        # A person typed this: on a wall that can show lowercase, show it as they typed it,
        # rather than SHOUTING IT BACK AT THEM — which was all the one-byte protocol could do.
        #
        # …unless it is `raw`, which is the click-to-type GRID: there a lowercase r/o/y/g/b/p/w
        # is a COLOUR CELL the user placed, not a letter they typed.
        target = d.controller.send_text_bg(req.text, style=req.style, speed=req.speed,
                                           frame=req.raw)
        return {"ok": True, "target": target}

    @router.post("/api/message")
    async def show_message(request: Request, req: MessageRequest):
        """Show a plain-text message, centred and word-wrapped onto the grid — the same layout
        the apps and the Vestaboard endpoint use. Unlike /api/compose/send (which takes a raw
        grid string from the click-to-type editor), this takes ordinary text.

        `seconds` makes it temporary: after that long the display reverts to whatever was
        playing (or blanks if nothing was). This is what the Home Assistant integration and a
        `rest_command` use — no Vestaboard key needed."""
        d = deps.display_for(request)
        if req.style and req.style not in renderer.ALL_STYLES:
            raise HTTPException(400, f"unknown style: {req.style}")
        g = d.config.grid
        rows, cols = int(g["rows"]), int(g["cols"])
        page = vestaboard.layout_text(req.text, rows, cols, d.controller.caps)
        if req.seconds and req.seconds > 0:
            running = d.controller.show_temporary(page, req.seconds, style=req.style or "ltr")
            d.ha.publish_state()
            return {"ok": True, "seconds": req.seconds,
                    "reverts_to": "app/playlist" if running else "blank"}
        d.controller.send_text_bg(page, style=req.style)
        d.ha.publish_state()
        return {"ok": True}

    @router.post("/api/display/clear")
    async def display_clear(request: Request):
        d = deps.display_for(request)
        await d.controller.clear()
        return {"ok": True}

    @router.post("/api/display/home")
    async def display_home(request: Request):
        """Physically home every module (gateway broadcast), stop any running
        app/playlist, and blank the live preview. Best-effort: reports the reason on
        failure rather than raising, so the UI can surface it inline."""
        d = deps.display_for(request)
        try:
            ok = await d.controller.home_all()
            return {"ok": ok, "error": None if ok else "gateway rejected the home command"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @router.get("/api/gateway/status")
    async def gateway_status(request: Request):
        """Probe the gateway's /api/status and return its URL (for the Display tab).

        ``tabs`` is the gateway's own tab list as it advertised it when we registered
        (Gateway 3.4+); empty means it never did — an older firmware, or we haven't
        reached it yet — and the UI falls back to its built-in list. See tabs.py.
        """
        d = deps.display_for(request)
        import httpx

        tabs = list(d.gateway_tabs)
        url = d.config.transport.get("gateway_url", "").rstrip("/")
        if not url:
            return {"ok": False, "url": "", "tabs": tabs, "error": "no gateway_url configured"}
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(f"{url}/api/status")
                return {"ok": r.status_code < 400, "url": url, "tabs": tabs,
                        "status_code": r.status_code, "data": r.json()}
        except Exception as e:
            return {"ok": False, "url": url, "tabs": tabs, "error": str(e)}

    return router
