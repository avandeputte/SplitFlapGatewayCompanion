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


def test_effect_in_a_playlist_is_released_when_its_slot_ends(gw_calls, tmp_path):
    """Regression: an on-device effect dropped into a playlist used to stay lit
    forever — the playlist ran it through the flap path, never took the panel over
    (so _canvas_active stayed False) and never released it. It must be handed back
    when the effect's slot ends and the next entry begins."""
    from pathlib import Path

    async def run():
        cfg = Config(data_dir=tmp_path)
        cfg.update({"transport": {"gateway_url": "http://gw"}})
        ctl = DisplayController(cfg, DisplayState(45))
        ps = PluginSettings(tmp_path)
        ps.set_installed(["effects", "time"])
        rt = PluginRuntime(cfg, ps, Path(__file__).resolve().parents[2] / "apps")
        rt.load()
        rt.attach_caps(lambda: device.from_capabilities(CANVAS_DOC))
        ctl.attach_plugins(rt)
        await ctl.start()

        await ctl.run_playlist([{"type": "app", "app": "effects", "duration": 0.25},
                                {"type": "app", "app": "time", "duration": 0.25}], loop=False)
        await asyncio.sleep(0.7)                 # effect slot -> release -> flap slot

        bodies = [(p, b) for _, p, b, _ in gw_calls]
        assert ("/api/canvas/effect", {"type": "plasma", "speed": 5}) in [
            (p, b) for p, b in bodies if p == "/api/canvas/effect" and b and b.get("type") == "plasma"
        ] or any(p == "/api/canvas/effect" for p, _ in bodies), "effect never started"
        # released: effect set to 'none' AND/OR the panel handed back (active False)
        assert any(p == "/api/canvas/effect" and (b or {}).get("type") == "none" for p, b in bodies) \
            or any(p == "/api/canvas" and (b or {}).get("active") is False for p, b in bodies), \
            "effect was never turned off when its playlist slot ended"
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
    for app in ("effects", "canvas-art-clock", "canvas-image", "canvas-weather",
                "canvas-ticker", "canvas-date", "canvas-world", "canvas-countdown"):
        m = json.loads((apps / app / "manifest.json").read_text())
        assert m.get("surface") == "canvas", app


# --- the frame-push canvas apps (Lumina Clock, Weather Sky) ------------------
# Both render a whole PIL image and push it via canvas.frame() (PUT
# /api/canvas/frame). These drive the REAL CanvasSurface (so canvas.font / blank
# / vgrad / frame all work) against the stubbed gateway, and inspect the pushed
# rgb888 buffer.
from PIL import Image                                                       # noqa: E402


def _load(name):
    from conftest import load_app
    return load_app(name)


def _push(gw_calls, app, W, H, settings, **kw):
    """Render one frame; return (hold, PIL image or None, raw bytes)."""
    cv = canvas.CanvasSurface("http://gw", W, H, ("rgb888",), ())
    hold = app.fetch(settings, None, None, None, canvas=cv, **kw)
    content = gw_calls[-1][3] if gw_calls else None
    img = (Image.frombytes("RGB", (W, H), content)
           if content and len(content) == W * H * 3 else None)
    return hold, img, content


def _bright(img):
    return max(hi for _, hi in img.getextrema())


# -- Lumina Clock --
def test_art_clock_draws_nothing_without_a_panel():
    assert _load("canvas-art-clock").fetch({}, None, None, None, canvas=None) is None


def test_art_clock_pushes_a_frame_with_the_time(gw_calls):
    hold, img, content = _push(gw_calls, _load("canvas-art-clock"), 128, 32,
                               {"treatment": "glow", "palette": "amber", "clock_format": "24h"})
    assert hold == 0.2 and gw_calls[-1][1] == "/api/canvas/frame"
    assert len(content) == 128 * 32 * 3 and _bright(img) > 40         # digits are lit


@pytest.mark.parametrize("treatment", ["glow", "aurora", "neon", "minimal"])
@pytest.mark.parametrize("palette", ["amber", "ice", "mint", "duotone", "daylight"])
def test_art_clock_every_treatment_and_palette(gw_calls, treatment, palette):
    _, img, content = _push(gw_calls, _load("canvas-art-clock"), 128, 32,
                            {"treatment": treatment, "palette": palette})
    assert len(content) == 128 * 32 * 3 and img is not None


def test_art_clock_adapts_to_panel_size(gw_calls):
    app = _load("canvas-art-clock")
    for W, H in ((64, 32), (128, 64)):
        _, _img, content = _push(gw_calls, app, W, H, {})
        assert len(content) == W * H * 3


def test_art_clock_12h_and_24h_both_render(gw_calls):
    app = _load("canvas-art-clock")
    for fmt in ("12h", "24h"):
        _, img, _c = _push(gw_calls, app, 128, 32, {"clock_format": fmt})
        assert _bright(img) > 40


# -- Weather Sky --
def _gw(sky="clear", t=52, hi=61, lo=44, city="Boston"):
    return lambda days=1, air=False: {"ok": True, "sky": sky, "temp_f": t,
                                      "hi_f": hi, "lo_f": lo, "city": city}


def test_weather_draws_nothing_without_a_panel():
    assert _load("canvas-weather").fetch({}, None, None, None, canvas=None) is None


def test_weather_pushes_a_scene_with_the_numbers(gw_calls):
    hold, img, content = _push(gw_calls, _load("canvas-weather"), 128, 32,
                               {"temperature_unit": "f"}, get_weather=_gw("clear"))
    assert hold == 0.16 and gw_calls[-1][1] == "/api/canvas/frame"
    assert len(content) == 128 * 32 * 3 and _bright(img) > 40


@pytest.mark.parametrize("sky", [
    "clear", "pcloudy", "cloudy", "fog", "rainl", "rain", "rainh", "shwr",
    "snowl", "snow", "snowh", "sleet", "storm", "hail", "mystery"])
def test_weather_every_sky_renders(gw_calls, sky):
    app = _load("canvas-weather")
    for _ in range(3):                              # advance the animation frame
        _, img, content = _push(gw_calls, app, 128, 32, {}, get_weather=_gw(sky))
    assert len(content) == 128 * 32 * 3 and img is not None


def test_weather_adapts_to_panel_size(gw_calls):
    app = _load("canvas-weather")
    for W, H in ((64, 32), (128, 64)):
        _, _i, content = _push(gw_calls, app, W, H, {}, get_weather=_gw("rain"))
        assert len(content) == W * H * 3


def test_weather_temperature_conversion():
    app = _load("canvas-weather")
    assert app._num(32, "c") == 0 and app._num(32, "k") == 273 and app._num(32, "f") == 32
    assert app._num(None, "f") is None


def test_weather_missing_temp_still_renders(gw_calls):
    _, _img, content = _push(gw_calls, _load("canvas-weather"), 128, 32, {},
                             get_weather=_gw("cloudy", None))
    assert len(content) == 128 * 32 * 3


def test_weather_caches_the_reading(gw_calls):
    app = _load("canvas-weather")
    n = {"c": 0}

    def gw(days=1, air=False):
        n["c"] += 1
        return {"ok": True, "sky": "rain", "temp_f": 50, "hi_f": 55, "lo_f": 40, "city": "Y"}

    for _ in range(10):
        _push(gw_calls, app, 128, 32, {}, get_weather=gw)
    assert n["c"] == 1                              # fetched once, cached for the animation


# -- the frame-push apps built alongside (Date Card, World Time, Countdown Bars) --
@pytest.mark.parametrize("app_id,settings", [
    ("canvas-date", {}),
    ("canvas-world", {"world_clock_zones": "America/New_York,Europe/London,Asia/Tokyo"}),
    ("canvas-countdown", {"countdown_event": "Launch", "countdown_target": "2027-06-01T00:00"}),
])
@pytest.mark.parametrize("size", [(128, 32), (64, 32), (128, 64)])
def test_new_canvas_apps_push_a_frame(gw_calls, app_id, settings, size):
    W, H = size
    _hold, img, content = _push(gw_calls, _load(app_id), W, H, settings)
    assert gw_calls[-1][1] == "/api/canvas/frame" and len(content) == W * H * 3
    assert img is not None and _bright(img) > 20


@pytest.mark.parametrize("app_id", ["canvas-date", "canvas-world", "canvas-countdown"])
def test_new_canvas_apps_are_none_without_a_panel(app_id):
    assert _load(app_id).fetch({}, None, None, None, canvas=None) is None


def test_countdown_arrived_and_no_target_do_not_crash(gw_calls):
    app = _load("canvas-countdown")
    for settings in ({"countdown_event": "Party", "countdown_target": "2000-01-01T00:00"},  # past
                     {}):                                                                     # unset
        _h, _i, content = _push(gw_calls, app, 128, 32, settings)
        assert len(content) == 128 * 32 * 3


def test_news_ticker_scrolls_headlines(gw_calls, monkeypatch):
    app = _load("canvas-ticker")
    import urllib.request

    class _Resp:
        def read(self):
            return (b"<rss><channel>"
                    b"<item><title>Markets rally to record highs today</title></item>"
                    b"<item><title>Weather warning for the weekend ahead</title></item>"
                    b"</channel></rss>")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    content = None
    for _ in range(6):                              # fetch once, then scroll
        _h, img, content = _push(gw_calls, app, 128, 32, {"feed_url": "http://x"})
    assert len(content) == 128 * 32 * 3 and _bright(img) > 20
    assert app.fetch({}, None, None, None, canvas=None) is None
