"""The wave-3 platform features: panel toasts (drawn interrupts), the arcade's
persistent high scores (game_store), and the panel GIF recorder. Zones has its own
file (test_zones.py)."""

import asyncio
import io

import pytest

from app import device, toast
from app.config import Config
from app.engine import DisplayController
from app.plugins import _GameStore
from app.state import DisplayState


# --- toasts ------------------------------------------------------------------

def test_toast_renders_slide_frames_and_the_card():
    frames = toast.render(256, 64, "Dinner's ready", icon="bell")
    assert len(frames) >= 3                                # slide-in + the hold card
    card = frames[-1]
    assert card.size == (256, 64)
    assert card.getpixel((1, 32)) == toast.ACCENTS["bell"]  # the accent bar
    # the text ink is somewhere right of the icon
    assert any(card.getpixel((x, y)) == (238, 242, 250)
               for x in range(40, 250, 3) for y in range(8, 56, 3))
    # slide pre-frames sit lower than the final card
    assert frames[0].getpixel((1, 2)) == (0, 0, 0)


def test_toast_icons_and_accent_override():
    for icon in ("info", "alert", "check", "cross", "heart", "bell"):
        card = toast.render(128, 32, "Hi", icon=icon)[-1]
        assert card.getpixel((1, 16)) == toast.ACCENTS[icon]
    card = toast.render(128, 32, "Hi", icon="nonsense", accent=(40, 200, 40))[-1]
    assert card.getpixel((1, 16)) == (40, 200, 40)          # unknown icon -> bell + accent


def test_fire_interrupt_toasts_on_a_canvas_wall(tmp_path, monkeypatch):
    from app import canvas as canvas_mod
    from app import engine as engine_mod

    pushed = []

    class _Panel:
        def __init__(self, url, caps):
            pass

        def frame(self, img):
            pushed.append(img)
            return True

    async def run():
        cfg = Config(data_dir=tmp_path)
        ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
        ctrl._caps = lambda: device.Capabilities(
            lowercase=True, pictographs=True, named_colors=True, indexed=True,
            canvas_w=128, canvas_h=32, canvas_formats=("rgb888",))
        monkeypatch.setattr(canvas_mod, "CanvasSurface", _Panel)

        async def fake_take_panel():
            return "http://gw"

        ctrl._take_panel = fake_take_panel
        await ctrl.fire_interrupt("Door open", 0.01, icon="alert")
        assert pushed, "no toast frames reached the panel"
        assert pushed[-1].size == (128, 32)
        assert ctrl._interrupt_over.is_set()               # the gate reopened

    asyncio.run(run())


def test_fire_interrupt_still_uses_flap_cells_on_a_reel_wall(tmp_path):
    async def run():
        cfg = Config(data_dir=tmp_path)
        ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
        emitted = []

        async def fake_emit(clean, **kw):
            emitted.append(clean)
            return True

        ctrl._emit_page = fake_emit
        await ctrl.fire_interrupt("HELLO", 0.01)
        assert emitted and "HELLO" in emitted[0]

    asyncio.run(run())


# --- the arcade's persistent bests -------------------------------------------

class _Store:
    def __init__(self):
        self.doc = {}

    def get(self, key, default=None):
        return self.doc.get(key, default)

    def set(self, key, value):
        self.doc[key] = value


def test_game_store_records_only_new_bests():
    gs = _GameStore(_Store(), "canvas-snake")
    assert gs.best(120) is True
    assert gs.get("high") == 120
    assert gs.best(90) is False                            # lower never overwrites
    assert gs.get("high") == 120
    assert gs.best("240") is True and gs.get("high") == 240
    assert gs.best("junk") is False


def test_games_want_the_store_and_show_new_best(tmp_path):
    from conftest import canvas_surface, load_app, make_runtime
    from test_canvas_ops35 import OPS35

    rt = make_runtime(tmp_path=tmp_path, installed=["canvas-snake"])
    assert "game_store" in rt._wants_matrix["canvas-snake"]

    class _Ctl:
        dir = None
        events = []
        taps = []
        presses = 1

        def active(self, within=6.0):
            return True

    import app.gateway as gateway
    from conftest import json_response
    calls = []
    gateway._request = lambda m, u, p, *, timeout, **kw: json_response({"ok": True, "active": True})

    app = load_app("canvas-snake")
    cv = canvas_surface("http://gw", 128, 64, ("rgb888",), (), ops=OPS35, ops_bin=True)
    gs = _GameStore(_Store(), "canvas-snake")
    ctl = _Ctl()
    app.fetch_matrix({"speed": "8"}, cv, controls=ctl, game_store=gs)
    st = app._state._st
    st["snake"] = [(1, 1), (1, 2)]
    st["dir"] = "u"
    st["score"] = 70
    app.fetch_matrix({"speed": "8"}, cv, controls=ctl, game_store=gs)      # dies
    ctl.presses = 1
    app.fetch_matrix({"speed": "8"}, cv, controls=ctl, game_store=gs)      # freeze arms
    assert st["phase"] == "gameover"
    assert st["new_best"] is True and gs.get("high") == 70

    # a worse second run keeps the record and shows it
    ctl.presses = 2
    app.fetch_matrix({"speed": "8"}, cv, controls=ctl, game_store=gs)      # redeal
    st = app._state._st
    st["snake"] = [(1, 1), (1, 2)]
    st["dir"] = "u"
    st["score"] = 30
    app.fetch_matrix({"speed": "8"}, cv, controls=ctl, game_store=gs)
    app.fetch_matrix({"speed": "8"}, cv, controls=ctl, game_store=gs)
    assert st["new_best"] is False and st["best_score"] == 70


# --- the GIF recorder --------------------------------------------------------

def test_panel_record_returns_a_gif(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from PIL import Image

    from app import main
    from app.engine import DisplayController

    monkeypatch.setattr(main.config, "_sim", True)
    monkeypatch.setattr(DisplayController, "_caps",
                        lambda self: SimpleNamespace(has_canvas=True))

    tick = {"n": 0}

    def fake_png(self, scale=1):
        # frames must DIFFER: identical frames legally collapse to a 1-frame GIF
        tick["n"] += 1
        buf = io.BytesIO()
        Image.new("RGB", (64, 32), (200, 30 + tick["n"] * 20 % 200, 30)).save(buf, "PNG")
        return buf.getvalue()

    monkeypatch.setattr(DisplayController, "canvas_preview_png", fake_png)
    c = TestClient(main.app)
    r = c.post("/api/panel/record?seconds=1&fps=4")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/gif"
    assert r.content[:6] in (b"GIF87a", b"GIF89a")
    gif = Image.open(io.BytesIO(r.content))
    assert gif.size == (128, 64)                           # 2x chunky-pixel upscale
    assert getattr(gif, "n_frames", 1) >= 3


def test_panel_record_refuses_a_flap_wall(monkeypatch):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from app import main
    from app.engine import DisplayController

    monkeypatch.setattr(main.config, "_sim", True)
    monkeypatch.setattr(DisplayController, "_caps",
                        lambda self: SimpleNamespace(has_canvas=False))
    c = TestClient(main.app)
    assert c.post("/api/panel/record").status_code == 409
