"""The canvas capability — a Matrix wall's framebuffer. It can draw ANYTHING
(pixels, lines, rects, text, a raw frame) and run on-device effects, none of
which a split-flap grid can do. These pin: the capability is parsed, the
transport speaks the gateway's canvas endpoints, the injected ``canvas`` helper
batches draw ops, and the engine takes the panel over for a canvas app and hands
it back when you switch away.
"""

import asyncio

import pytest

from app import canvas, device
from app.config import Config
from app.engine import DisplayController
from app.plugin_settings import PluginSettings
from app.plugins import PluginRuntime
from app.state import DisplayState

CANVAS_DOC = {
    "features": ["cells", "colors", "lowercase", "pictographs", "canvas", "effects"],
    "colors": ["red", "green"], "charset": {"uniform": True, "common": "ABC"},
    "canvas": {"formats": ["rgb888", "rgb565"], "width": 128, "height": 32},
    "effects": ["plasma", "fire", "matrix"], "motion": {"kind": "drawn"},
}


# --- capability parsing -----------------------------------------------------

def test_canvas_capability_is_parsed():
    caps = device.from_capabilities(CANVAS_DOC)
    assert caps.has_canvas
    assert (caps.canvas_w, caps.canvas_h) == (128, 32)
    assert caps.canvas_formats == ("rgb888", "rgb565")
    assert caps.effects == ("plasma", "fire", "matrix")


def test_a_physical_wall_has_no_canvas():
    caps = device.from_capabilities({"features": ["cells"], "charset": {"uniform": True, "common": "A"}})
    assert not caps.has_canvas and caps.effects == ()
    assert not device.SPLIT_FLAP.has_canvas


# --- transport + helper -----------------------------------------------------

@pytest.fixture
def gw_calls(monkeypatch):
    calls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"active": True}

    import app.gateway as gateway
    monkeypatch.setattr(gateway, "_request",
                        lambda method, url, path, *, timeout, **kw:
                        (calls.append((method, path, kw.get("json"), kw.get("content"))) or _Resp()))
    return calls


def _surface():
    return canvas.CanvasSurface("http://gw", 128, 32, ("rgb888", "rgb565"),
                                ("plasma", "fire", "matrix"))


def test_draw_ops_are_batched_until_show(gw_calls):
    s = _surface()
    s.clear("black").rect(0, 0, 10, 8, "red", fill=True).text(2, 2, "HI", "green", 8)
    assert gw_calls == []                       # nothing sent yet — batched
    s.show()
    method, path, body, _ = gw_calls[0]
    assert method == "POST" and path == "/api/canvas/ops"
    ops = [o["op"] for o in body]
    assert ops == ["clear", "rect", "text", "show"]
    assert body[1]["color"] == [255, 0, 0]      # "red" resolved


def test_effect_is_one_request(gw_calls):
    _surface().effect("fire", 7)
    method, path, body, _ = gw_calls[0]
    assert path == "/api/canvas/effect" and body == {"type": "fire", "speed": 7}


def test_frame_is_the_panel_sized_raw_buffer(gw_calls):
    _surface().frame(b"\x00" * (128 * 32 * 3))
    method, path, _body, content = gw_calls[-1]
    assert method == "PUT" and path == "/api/canvas/frame"
    assert len(content) == 128 * 32 * 3         # rgb888


def test_colour_names_hex_and_tuples():
    assert canvas._rgb("red") == [255, 0, 0]
    assert canvas._rgb("#00ff00") == [0, 255, 0]
    assert canvas._rgb((10, 20, 30)) == [10, 20, 30]
    assert canvas._rgb("nonsense") == [255, 255, 255]   # safe default


# --- engine drive + release -------------------------------------------------

def test_engine_takes_over_and_releases_the_panel(gw_calls, tmp_path):
    async def run():
        cfg = Config(data_dir=tmp_path)
        cfg.update({"transport": {"gateway_url": "http://gw"}})
        ctl = DisplayController(cfg, DisplayState(45))
        ps = PluginSettings(tmp_path)
        ps.set_installed(["effects", "time"])
        rt = PluginRuntime(cfg, ps, __import__("pathlib").Path(__file__).resolve().parents[2] / "apps")
        rt.load()
        caps = device.from_capabilities(CANVAS_DOC)
        rt.attach_caps(lambda: caps)
        ctl.attach_plugins(rt)
        await ctl.start()

        await ctl.run_app("effects")            # a canvas app
        await asyncio.sleep(0.25)
        assert ctl._canvas_active
        paths = [p for _, p, _, _ in gw_calls]
        assert "/api/canvas" in paths and "/api/canvas/effect" in paths

        gw_calls.clear()
        await ctl.run_app("time")               # a flap app — must release the panel
        await asyncio.sleep(0.1)
        assert not ctl._canvas_active
        assert any("canvas" in p for _, p, _, _ in gw_calls), "panel not released on switch"
        await ctl.stop()

    asyncio.run(run())


def test_a_canvas_app_will_not_run_on_a_non_canvas_wall(tmp_path):
    async def run():
        ctl = DisplayController(Config(data_dir=tmp_path), DisplayState(45))
        ps = PluginSettings(tmp_path)
        ps.set_installed(["effects"])
        rt = PluginRuntime(Config(data_dir=tmp_path), ps,
                           __import__("pathlib").Path(__file__).resolve().parents[2] / "apps")
        rt.load()
        rt.attach_caps(lambda: device.SPLIT_FLAP)   # a physical wall: no canvas
        ctl.attach_plugins(rt)
        with pytest.raises(KeyError):
            await ctl.run_app("effects")

    asyncio.run(run())


def test_the_canvas_apps_are_marked_canvas_surface():
    from pathlib import Path
    apps = Path(__file__).resolve().parents[2] / "apps"
    import json
    for app in ("effects", "canvas-clock", "canvas-image"):
        m = json.loads((apps / app / "manifest.json").read_text())
        assert m.get("surface") == "canvas", app
