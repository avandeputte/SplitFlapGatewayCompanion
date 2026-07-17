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
    for app in ("effects", "canvas-art-clock", "canvas-image", "canvas-weather"):
        m = json.loads((apps / app / "manifest.json").read_text())
        assert m.get("surface") == "canvas", app


# --- the canvas-weather app: the sky, drawn ---------------------------------

class _FakeCanvas:
    """Records the draw calls a canvas app makes, standing in for CanvasSurface."""

    def __init__(self, w=64, h=32):
        self.width, self.height = w, h
        self.ops = []
        self.shown = 0

    def clear(self, color=(0, 0, 0)):
        self.ops.append(("clear",)); return self

    def pixel(self, x, y, color=(255, 255, 255)):
        self.ops.append(("pixel", x, y)); return self

    def hline(self, x, y, w, color=(255, 255, 255)):
        self.ops.append(("hline", x, y, w)); return self

    def vline(self, x, y, h, color=(255, 255, 255)):
        self.ops.append(("vline",)); return self

    def rect(self, x, y, w, h, color=(255, 255, 255), fill=False):
        self.ops.append(("rect",)); return self

    def text(self, x, y, s, color=(255, 255, 255), size=10):
        self.ops.append(("text", x, y, s, size)); return self

    def show(self):
        self.shown += 1; return True

    def _texts(self):
        return "".join(o[3] for o in self.ops if o[0] == "text")


def _weather_app():
    from conftest import load_app
    return load_app("canvas-weather")


def _gw(sky="clear", temp_f=72, city="Boston"):
    return lambda days=0, air=False: {"ok": True, "sky": sky, "temp_f": temp_f, "city": city}


def test_canvas_weather_draws_nothing_without_a_panel():
    assert _weather_app().fetch({}, None, None, None, canvas=None) is None


def test_canvas_weather_draws_the_sky_and_temperature():
    app = _weather_app()
    cv = _FakeCanvas(64, 32)
    hold = app.fetch({"temperature_unit": "f", "show_city": "yes"}, None, None, None,
                     canvas=cv, get_weather=_gw("clear", 72, "Boston"))
    assert cv.shown == 1
    assert cv.ops[0] == ("clear",)              # a fresh frame every redraw
    assert "72" in cv._texts() and "Boston" in cv._texts()
    assert hold and hold > 0                     # a short hold => the engine reanimates


@pytest.mark.parametrize("sky", [
    "clear", "pcloudy", "cloudy", "fog", "rainl", "rain", "rainh", "shwr",
    "snowl", "snow", "snowh", "sleet", "storm", "hail", "mystery"])
def test_canvas_weather_every_sky_animates_without_error(sky):
    app = _weather_app()
    cv = _FakeCanvas(128, 32)
    gw = _gw(sky, 5, "X")
    for _ in range(30):                          # enough frames to hit the bolt flash / twinkle
        assert app.fetch({}, None, None, None, canvas=cv, get_weather=gw) == 0.12
    assert cv.shown == 30


def test_canvas_weather_night_clear_draws_a_moon(monkeypatch):
    app = _weather_app()
    monkeypatch.setattr(app, "_is_night", lambda s: True)
    cv = _FakeCanvas(64, 32)
    app.fetch({}, None, None, None, canvas=cv, get_weather=_gw("clear", 30, ""))
    assert cv.shown == 1                          # a night scene drew without error


def test_canvas_weather_units_convert():
    app = _weather_app()
    for unit, want in (("c", "0"), ("k", "273"), ("f", "32")):
        cv = _FakeCanvas(64, 32)
        _weather_app_fetch = _weather_app()       # fresh module => fresh cache per unit
        _weather_app_fetch.fetch({"temperature_unit": unit}, None, None, None,
                                 canvas=cv, get_weather=_gw("clear", 32, ""))
        assert want in cv._texts(), unit


def test_canvas_weather_missing_temp_shows_dashes():
    app = _weather_app()
    cv = _FakeCanvas(64, 32)
    app.fetch({}, None, None, None, canvas=cv, get_weather=_gw("cloudy", None, ""))
    assert "--" in cv._texts()


def test_canvas_weather_caches_the_reading():
    app = _weather_app()
    cv = _FakeCanvas(64, 32)
    calls = {"n": 0}

    def gw(days=0, air=False):
        calls["n"] += 1
        return {"ok": True, "sky": "rain", "temp_f": 50, "city": "Y"}

    for _ in range(20):
        app.fetch({}, None, None, None, canvas=cv, get_weather=gw)
    assert calls["n"] == 1                         # fetched once, cached for the animation


# --- the aurora art clock ---------------------------------------------------

def _clock_app():
    from conftest import load_app
    return load_app("canvas-art-clock")


def test_art_clock_draws_nothing_without_a_panel():
    assert _clock_app().fetch({}, None, None, None, canvas=None) is None


def test_art_clock_draws_the_time_over_an_aurora():
    app = _clock_app()
    cv = _FakeCanvas(128, 32)
    hold = app.fetch({"theme": "daylight", "clock_format": "24h"}, None, None, None, canvas=cv)
    assert cv.shown == 1 and hold and hold > 0
    # the aurora is one hline per row => a full-height gradient
    assert len([o for o in cv.ops if o[0] == "hline"]) >= 32
    # HH and MM are drawn as text (two digits each)
    digits = "".join(o[3] for o in cv.ops if o[0] == "text")
    assert sum(ch.isdigit() for ch in digits) >= 4


def test_art_clock_hsv_is_a_valid_rgb():
    app = _clock_app()
    for hue in (0, 90, 200, 359):
        r, g, b = app._hsv(hue, 0.85, 1.0)
        assert all(0 <= c <= 255 for c in (r, g, b))
    assert app._hsv(0, 0, 0) == (0, 0, 0)


@pytest.mark.parametrize("theme", ["daylight", "spectrum", "ocean", "ember", "mystery"])
def test_art_clock_every_theme_animates(theme):
    app = _clock_app()
    cv = _FakeCanvas(64, 32)
    for _ in range(20):                            # advance the animation frame
        assert app.fetch({"theme": theme}, None, None, None, canvas=cv) == 0.1
    assert cv.shown == 20


def test_art_clock_12h_shows_a_meridiem():
    app = _clock_app()
    cv = _FakeCanvas(64, 48)                        # tall enough for the date row
    app.fetch({"clock_format": "12h"}, None, None, None, canvas=cv)
    text = "".join(o[3] for o in cv.ops if o[0] == "text")
    assert "AM" in text or "PM" in text
