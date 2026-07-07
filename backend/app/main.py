"""
main.py — FastAPI application (Phase 1 slice).

Serves the SPA, the companion API (compose/send, live state, config), and a
best-effort gateway status probe. Later phases add the plugin runtime,
playlists/schedules/triggers, and the gateway reverse-proxy.
"""

from __future__ import annotations

import copy
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, renderer
from .config import Config
from .engine import DisplayController
from .state import DisplayState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("companion")

STATIC_DIR = Path(__file__).resolve().parent / "static"

config = Config()
state = DisplayState(config.module_count())
controller = DisplayController(config, state)


def _redact(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    mqtt = cfg.get("transport", {}).get("mqtt", {})
    if mqtt.get("password"):
        mqtt["password"] = "********"
    return cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("SplitFlapGatewayCompanion v%s starting (transport=%s, grid=%sx%s)",
             __version__, config.transport.get("type"), config.grid["rows"], config.grid["cols"])
    await controller.start()
    yield
    await controller.stop()


app = FastAPI(title="SplitFlapGatewayCompanion", version=__version__, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ComposeRequest(BaseModel):
    text: str
    style: str | None = None
    speed: int | None = None
    raw: bool = False


class ConfigPatch(BaseModel):
    grid: dict | None = None
    transport: dict | None = None
    display: dict | None = None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__}


@app.get("/api/current_state")
async def current_state():
    return state.snapshot()


@app.get("/api/grid")
async def grid():
    g = config.grid
    return {
        "rows": int(g["rows"]),
        "cols": int(g["cols"]),
        "module_count": config.module_count(),
        "module_id_base": int(g.get("module_id_base", 0)),
        "flap_chars": renderer.FLAP_CHARS,
        "styles": list(renderer.ALL_STYLES),
        "color_map": renderer.COLOR_MAP,
        "display": config.display,
    }


@app.get("/api/config")
async def get_config():
    return _redact(config.effective)


@app.post("/api/config")
async def update_config(patch: ConfigPatch):
    body = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not body:
        raise HTTPException(400, "empty config patch")
    config.update(body)
    if "grid" in body:
        controller.resize_grid()
    if "transport" in body:
        await controller.reload_transport()
    return _redact(config.effective)


@app.post("/api/compose/send")
async def compose_send(req: ComposeRequest):
    if req.style and req.style not in renderer.ALL_STYLES:
        raise HTTPException(400, f"unknown style: {req.style}")
    target = controller.send_text_bg(req.text, style=req.style, speed=req.speed, raw=req.raw)
    return {"ok": True, "target": target}


@app.post("/api/display/clear")
async def display_clear():
    await controller.clear()
    return {"ok": True}


@app.get("/api/gateway/status")
async def gateway_status():
    """Best-effort probe of the gateway's own /api/status (for the status pill)."""
    import httpx

    url = config.transport.get("gateway_url", "").rstrip("/")
    if not url:
        return JSONResponse({"ok": False, "error": "no gateway_url configured"}, status_code=200)
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"{url}/api/status")
            return {"ok": r.status_code < 400, "status_code": r.status_code, "data": r.json()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


# ---------------------------------------------------------------------------
# Static SPA (mounted last so /api/* wins)
# ---------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="spa")
