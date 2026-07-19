"""canvas_api.py — the companion's own controls for a Matrix wall's LED panel, the ones
that have no place on the flap grid: the overlay ticker, frame transitions, the on-device
animation and font libraries, GIF import and the boot splash (Matrix Portal firmware
1.19 / 1.25 / 2.1). Everything here is a thin, gated proxy to the gateway's ``/api/canvas/*``
family — the companion adds the UI, the panel does the work.

Namespaced ``/api/panel/*`` so it never collides with the gateway's own ``/api/canvas/*`` (which
the companion also reaches, through the display driver and the ``/gw`` proxy). Every route is
gated on the wall actually advertising the capability, and each gateway call runs off the event
loop (the canvas client is synchronous)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import canvas, gateway


class Overlay(BaseModel):
    text: str = ""
    color: list[int] = [255, 255, 255]
    speed: int = 2
    band: bool = True
    font: str | None = None


class Transition(BaseModel):
    type: str = "crossfade"
    ms: int = 400


class Named(BaseModel):
    name: str = ""


def build(deps) -> APIRouter:
    router = APIRouter(dependency_overrides_provider=deps.app)

    def _wall(request: Request):
        """(display, gateway_url, caps) — or 404 if this display is not a canvas wall."""
        d = deps.display_for(request)
        caps = d.controller.caps
        if not caps.has_canvas:
            raise HTTPException(404, "not a canvas wall")
        url = str(d.config.transport.get("gateway_url") or "").strip()
        if not url:
            raise HTTPException(400, "no gateway configured")
        return d, url, caps

    def _need(flag: bool, what: str):
        if not flag:
            raise HTTPException(400, f"this wall does not support {what}")

    @router.get("/api/panel/caps")
    async def panel_caps(request: Request):
        """What the panel can do, for the UI to gate its controls on. 404 (via _wall) on a
        non-canvas wall, so the whole Panel tab simply never appears there."""
        _d, _url, caps = _wall(request)
        return {
            "width": caps.canvas_w, "height": caps.canvas_h,
            "fw": ".".join(str(n) for n in caps.fw_version),
            "readback": caps.canvas_readback,
            "ops": list(caps.canvas_ops),
            "effects": list(caps.effects), "effect_params": list(caps.effect_params),
            "overlay": caps.canvas_2_1, "transition": caps.canvas_2_1,
            "anim_library": caps.canvas_2_1, "gif": caps.canvas_2_1,
            "fonts": caps.canvas_2_1, "sprite": caps.canvas_sprite,
        }

    @router.post("/api/panel/overlay")
    async def panel_overlay(request: Request, req: Overlay):
        """Set (or, with empty text, clear) a lower-third ticker that composites OVER whatever the
        wall is showing and survives page/mode changes. `overlay` needs firmware 2.1."""
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "the overlay ticker")
        ok = await asyncio.to_thread(canvas.put_ticker, url, req.text, tuple(req.color),
                                     req.speed, True, req.band, req.font)
        if not ok:
            raise HTTPException(502, "the gateway refused the ticker (Quiet Time?)")
        return {"ok": True, "active": bool(req.text)}

    @router.post("/api/panel/transition")
    async def panel_transition(request: Request, req: Transition):
        """How subsequent canvas frames present on this wall — none/crossfade/wipe/slide. Sticky
        on the gateway until changed. Needs firmware 2.1."""
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "frame transitions")
        ok = await asyncio.to_thread(canvas.set_transition, url, req.type, req.ms)
        if not ok:
            raise HTTPException(502, "the gateway refused the transition")
        return {"ok": True, "type": req.type, "ms": req.ms}

    @router.get("/api/panel/library")
    async def panel_library(request: Request):
        """The on-device animation and font libraries, plus the current boot splash. Needs
        firmware 2.1; an older wall returns empty lists so the UI can hide the section."""
        _d, url, caps = _wall(request)
        if not caps.canvas_2_1:
            return {"anims": [], "fonts": [], "boot": ""}
        anims, fonts, cfg = await asyncio.gather(
            asyncio.to_thread(canvas.anim_list, url),
            asyncio.to_thread(canvas.font_list, url),
            gateway.fetch_gateway_config(url),   # already a coroutine — don't wrap in a thread
        )
        boot = str((cfg or {}).get("bootAnim") or "")
        return {"anims": anims, "fonts": fonts, "boot": boot}

    @router.post("/api/panel/anim/play")
    async def panel_anim_play(request: Request, req: Named):
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "the animation library")
        out = await asyncio.to_thread(canvas.anim_play, url, req.name)
        if not out.get("ok"):
            raise HTTPException(502, "could not play that animation")
        return out

    @router.post("/api/panel/anim/delete")
    async def panel_anim_delete(request: Request, req: Named):
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "the animation library")
        if not await asyncio.to_thread(canvas.anim_delete, url, req.name):
            raise HTTPException(502, "could not delete that animation")
        return {"ok": True}

    @router.post("/api/panel/anim/save")
    async def panel_anim_save(request: Request, req: Named):
        """Persist whatever the panel is currently looping to the library under `name` — the way
        a GIF you just uploaded becomes a keepable, named entry."""
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "the animation library")
        if not await asyncio.to_thread(canvas.anim_save, url, req.name):
            raise HTTPException(502, "nothing loaded to save, or the write failed")
        return {"ok": True}

    @router.put("/api/panel/gif")
    async def panel_gif(request: Request):
        """Upload an animated GIF; the gateway decodes it on-device and plays it at once. Persist
        it afterwards with /api/panel/anim/save. Needs firmware 2.1."""
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "GIF import")
        data = await request.body()
        if not data:
            raise HTTPException(400, "empty upload")
        out = await asyncio.to_thread(canvas.put_gif, url, data)
        if not out.get("ok"):
            raise HTTPException(502, "the gateway could not decode that GIF (larger than the panel?)")
        return out

    @router.post("/api/panel/boot")
    async def panel_boot(request: Request, req: Named):
        """Set (or clear, with an empty name) the boot splash — a library animation the panel
        autoplays at power-on before WiFi. Stored on the gateway (POST /api/config/settings)."""
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "the boot splash")
        r = await asyncio.to_thread(gateway._request, "POST", url, "/api/config/settings",
                                    json={"bootAnim": req.name}, timeout=8.0)
        if getattr(r, "status_code", 500) >= 400:
            raise HTTPException(502, "the gateway refused the boot splash")
        return {"ok": True, "boot": req.name}

    @router.put("/api/panel/font")
    async def panel_font(request: Request):
        """Upload a packed MPFT font into the wall's custom slot. Save it to the library
        afterwards with /api/panel/font/save. Needs firmware 2.1."""
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "custom fonts")
        data = await request.body()
        if not data:
            raise HTTPException(400, "empty upload")
        out = await asyncio.to_thread(canvas.put_font, url, data)
        if not out.get("ok"):
            raise HTTPException(502, "the gateway rejected that font (not a valid MPFT blob?)")
        return out

    @router.post("/api/panel/font/save")
    async def panel_font_save(request: Request, req: Named):
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "custom fonts")
        if not await asyncio.to_thread(canvas.font_save, url, req.name):
            raise HTTPException(502, "no font loaded to save, or the write failed")
        return {"ok": True}

    @router.post("/api/panel/font/delete")
    async def panel_font_delete(request: Request, req: Named):
        _d, url, caps = _wall(request)
        _need(caps.canvas_2_1, "custom fonts")
        if not await asyncio.to_thread(canvas.font_delete, url, req.name):
            raise HTTPException(502, "could not delete that font")
        return {"ok": True}

    return router
