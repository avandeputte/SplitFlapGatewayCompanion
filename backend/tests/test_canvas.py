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


# --- named atlas library (firmware 3.1) -------------------------------------

@pytest.fixture
def wall(monkeypatch):
    """A stand-in for a 3.1 wall's atlas library: a PUT adds a named sheet, the GET lists them.
    Lets a test assert the property that matters — tiles cross the wire once, not once a draw."""
    state = {"sheets": {}, "calls": []}

    class _Resp:
        status_code = 200

        def __init__(self, payload=None):
            self._payload = payload

        def json(self):
            return self._payload if self._payload is not None else {"active": True}

    def _request(method, url, path, *, timeout=None, **kw):
        state["calls"].append((method, path, kw.get("json"), kw.get("content")))
        if method == "GET" and path == "/api/canvas/atlas":
            return _Resp([{"name": n, "tiles": 1, "w": 8, "h": 8, "fmt": 3,
                           "bytes": 192, "resident": True, "persisted": False}
                          for n in state["sheets"]])
        if path.startswith("/api/canvas/atlas/"):
            name = path.rsplit("/", 1)[-1]
            if method == "PUT":
                state["sheets"][name] = kw.get("content")
            elif method == "DELETE":
                state["sheets"].pop(name, None)
        return _Resp()

    import app.gateway as gateway
    monkeypatch.setattr(gateway, "_request", _request)
    canvas.forget_atlas("http://gw")
    yield state
    canvas.forget_atlas("http://gw")


def _named_surface():
    return canvas.CanvasSurface("http://gw", 128, 32, ("rgb888",), (), sprite=True)


def _imgs(n=2, shade=1):
    from PIL import Image
    return [Image.new("RGB", (8, 8), (shade, shade, shade)) for _ in range(n)]


def _uploads(state):
    return [c for c in state["calls"] if c[0] == "PUT" and c[1].startswith("/api/canvas/atlas/")]


def test_a_named_sheet_uploads_once_then_only_binds(wall):
    """The whole point: identical tiles cross the wire on the first draw and never again — later
    draws carry a small bind op instead of ~8 KB of pixels."""
    tiles = _imgs()
    for _ in range(5):
        s = _named_surface()
        assert s.upload_atlas(tiles) is True
        s.sprite(0, 0, 0)
        s.show()
    assert len(_uploads(wall)) == 1                     # one upload for five draws
    ops = [o for c in wall["calls"] if c[0] == "POST" for o in (c[2] or [])]
    binds = [o for o in ops if o.get("op") == "atlas"]
    assert len(binds) == 5                              # every batch binds, so none relies on stickiness
    assert binds[0]["name"] == canvas.atlas_name_for(
        b"".join(im.tobytes() for im in tiles), 8, 8, 2, "rgb888")


def test_the_bind_precedes_the_sprites_in_the_batch(wall):
    s = _named_surface()
    s.upload_atlas(_imgs())
    s.sprite(0, 1, 2)
    s.show()
    ops = [o for c in wall["calls"] if c[0] == "POST" for o in (c[2] or [])]
    kinds = [o["op"] for o in ops]
    assert kinds.index("atlas") < kinds.index("sprite")  # binding after the blit would draw nothing


def test_different_tiles_get_their_own_sheet(wall):
    a, b = _imgs(shade=1), _imgs(shade=9)
    for tiles in (a, b, a, b):
        s = _named_surface()
        s.upload_atlas(tiles)
        s.show()
    assert len(_uploads(wall)) == 2                      # two distinct sheets, each sent once
    assert len(wall["sheets"]) == 2


def test_a_sheet_the_wall_lost_is_re_uploaded(wall):
    """A reboot or an LRU eviction drops the sheet; a `sprite` with nothing bound draws nothing,
    so the belief has to be re-checked against the wall rather than trusted forever."""
    tiles = _imgs()
    s = _named_surface(); s.upload_atlas(tiles); s.show()
    assert len(_uploads(wall)) == 1
    wall["sheets"].clear()                               # the wall rebooted
    entry = canvas._ATLAS_KNOWN["http://gw"]             # age our belief past the verify window
    canvas._ATLAS_KNOWN["http://gw"] = {"at": entry["at"] - canvas._ATLAS_VERIFY_S - 1,
                                        "rows": entry["rows"]}
    s = _named_surface(); s.upload_atlas(tiles); s.show()
    assert len(_uploads(wall)) == 2                      # noticed and restored


def test_sheets_survive_handing_the_panel_back(wall):
    """The wall keeps its library across uses, so a playlist that cycles away from a canvas app
    and back again re-binds by name — it does not re-upload, and does not even re-check."""
    tiles = _imgs()
    s = _named_surface(); s.upload_atlas(tiles); s.show()
    canvas.release("http://gw")                          # slot ends, panel handed back
    before = len([c for c in wall["calls"] if c[0] == "GET"])
    s = _named_surface(); s.upload_atlas(tiles); s.show()   # the app comes round again
    assert len(_uploads(wall)) == 1                       # no re-upload
    assert len([c for c in wall["calls"] if c[0] == "GET"]) == before   # and no re-check


def test_a_persisted_sheet_is_saved_once(wall):
    """A stable icon sheet (persist=True) is POST-saved to flash once — not on every draw — so it
    survives a reboot and LRU eviction; a per-matchup sheet (persist=False) is never saved."""
    tiles = _imgs()
    for _ in range(4):
        _named_surface().upload_atlas(tiles, persist=True)
    saves = [c for c in wall["calls"] if c[0] == "POST" and c[1].endswith("/save")]
    assert len(saves) == 1                              # saved once, then the cache marks it persisted
    before = len([c for c in wall["calls"] if c[0] == "POST"])
    _named_surface().upload_atlas(_imgs(shade=7), persist=False)
    assert len([c for c in wall["calls"] if c[0] == "POST" and c[1].endswith("/save")]) == 1
    assert before == before                             # no extra save for the non-persisted sheet


def test_the_sheet_name_is_wall_legal():
    """Firmware rule: [a-z0-9._-]{1,32}, and it doubles as the content fingerprint."""
    import re
    for tiles, w, h, n in ((b"\x01" * 192, 8, 8, 1), (b"\x02" * 49152, 128, 64, 2)):
        for fmt in ("rgb888", "rgb565"):
            name = canvas.atlas_name_for(tiles, w, h, n, fmt)
            assert re.fullmatch(r"[a-z0-9._-]{1,32}", name), name
    a = canvas.atlas_name_for(b"\x01" * 192, 8, 8, 1)
    assert a != canvas.atlas_name_for(b"\x02" * 192, 8, 8, 1)      # content decides the name
    assert a != canvas.atlas_name_for(b"\x01" * 192, 8, 8, 1, "rgb565")   # so does the format


def test_a_canvas_app_honours_per_entry_overrides(monkeypatch, tmp_path):
    """A canvas app in a playlist must get the entry's setting overrides (a Scoreboard following
    its own teams) — the overlay a flap app already got, which the canvas render path used to drop."""
    from conftest import make_runtime
    import app.gateway as gateway
    monkeypatch.setattr(gateway, "_request",
                        lambda *a, **k: type("R", (), {"status_code": 200, "json": lambda s: {"active": True}})())
    rt = make_runtime(tmp_path=tmp_path, installed=["canvas-scoreboard"],
                      caps=device.from_capabilities(CANVAS_DOC))
    mod = rt._modules["canvas-scoreboard"]
    seen = {}
    monkeypatch.setattr(mod, "_games", lambda follow, filt: seen.update(follow=follow) or [])
    rt.render_canvas("canvas-scoreboard")                          # no override -> the app's own config
    base = seen["follow"]
    rt.render_canvas("canvas-scoreboard", {"plugin_canvas-scoreboard_follow": "nba:BOS|Boston"})
    assert seen["follow"] == "nba:BOS|Boston" and seen["follow"] != base


def test_face_snaps_to_a_bundled_size():
    s = _surface()
    assert s.faces == (8, 9, 10, 13, 18, 20)
    assert s.face(7) == 8                        # below the smallest -> the floor
    assert s.face(12) == 10                       # snaps DOWN to the nearest bundled face
    assert s.face(30) == 20                       # above the largest -> the largest
    assert s.face_width(10) == 6 and s.face_width(20) == 10


def test_fit_picks_the_largest_face_that_fits():
    s = _surface()
    assert s.fit("AB", 100, 40) == 20             # 2 glyphs, roomy -> biggest face
    assert s.fit("AB", 11, 40) == 8               # 2*5=10 <= 11 only at face 8
    assert s.fit("HELLO", 100, 9) == 9            # height caps it at 9 even with width to spare


def test_cp_keeps_cp1252_and_drops_the_rest():
    s = _surface()
    assert s.cp("72°F") == "72°F"       # degree sign survives (CP1252)
    assert s.cp("café") == "café"       # Latin accent survives
    assert s.cp("hi \U0001f600 中") == "hi  "  # emoji + CJK dropped


def test_shadow_text_draws_shadow_then_text(gw_calls):
    s = _surface()
    s.shadow_text(5, 6, "Hi", "white", 10, shadow=(1, 2, 3))
    s.show()
    _, _, body, _ = gw_calls[0]
    texts = [o for o in body if o["op"] == "text"]
    assert len(texts) == 2                         # shadow + foreground
    assert (texts[0]["x"], texts[0]["y"]) == (6, 7) and texts[0]["color"] == [1, 2, 3]
    assert (texts[1]["x"], texts[1]["y"]) == (5, 6) and texts[1]["color"] == [255, 255, 255]


def test_shadow_text_skips_an_empty_string(gw_calls):
    s = _surface()
    s.shadow_text(0, 0, "\U0001f600", "white", 8)  # all-dropped -> nothing drawn
    s.show()
    assert gw_calls == []                          # no ops queued, show() is a no-op


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
        ps.set_installed(["effect_plasma", "time"])
        rt = PluginRuntime(cfg, ps, __import__("pathlib").Path(__file__).resolve().parents[2] / "apps")
        caps = device.from_capabilities(CANVAS_DOC)
        rt.attach_caps(lambda: caps)            # before load: the per-effect apps synthesize from caps
        rt.load()
        ctl.attach_plugins(rt)
        await ctl.start()

        await ctl.run_app("effect_plasma")      # a canvas (effect) app
        await asyncio.sleep(0.25)
        assert ctl._canvas_active
        paths = [p for _, p, _, _ in gw_calls]
        assert "/api/canvas" in paths and "/api/canvas/effect" in paths

        gw_calls.clear()
        forgot = []
        real_forget = ctl.transport.forget
        ctl.transport.forget = lambda: (forgot.append(True), real_forget())[1]
        await ctl.run_app("time")               # a flap app takes over
        await asyncio.sleep(0.3)
        assert not ctl._canvas_active
        # The flap page hands the panel back by auto-stopping canvas mode on the firmware (its
        # first flap command). The companion must NOT eagerly POST /api/canvas {active:false} or
        # effect:none — that repaints the reel's STALE pre-canvas flaps and holds them until the
        # new page lands (the "old flaps flash"). It just drops the bypassed shown-cell cache so
        # the flap page repaints whole (and thus auto-stops the panel).
        assert not any("/api/canvas" in p for _, p, _, _ in gw_calls), \
            "must not eagerly release the panel on a canvas→flap switch"
        assert forgot, "the shown-cell cache was not dropped leaving canvas mode"
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
        ps.set_installed(["effect_plasma", "time"])
        rt = PluginRuntime(cfg, ps, Path(__file__).resolve().parents[2] / "apps")
        rt.attach_caps(lambda: device.from_capabilities(CANVAS_DOC))
        rt.load()
        ctl.attach_plugins(rt)
        await ctl.start()

        await ctl.run_playlist([{"type": "app", "app": "effect_plasma", "duration": 0.25},
                                {"type": "app", "app": "time", "duration": 0.25}], loop=False)
        await asyncio.sleep(0.7)                 # effect slot -> release -> flap slot

        bodies = [(p, b) for _, p, b, _ in gw_calls]
        assert any(p == "/api/canvas/effect" for p, _ in bodies), "effect never started"
        # When its slot ended the companion LET GO of the panel — _canvas_active clears — and the
        # next (flap) entry's page auto-stops the effect on the firmware. It does NOT send an eager
        # effect:none / active:false, which would flash the stale wall before that page lands.
        assert not ctl._canvas_active, "the effect was never let go when its slot ended"
        assert not any(p == "/api/canvas" and (b or {}).get("active") is False for p, b in bodies) \
            and not any(p == "/api/canvas/effect" and (b or {}).get("type") == "none" for p, b in bodies), \
            "must not eagerly release the panel; the next flap page auto-stops the effect"
        await ctl.stop()

    asyncio.run(run())


def test_a_canvas_app_will_not_run_on_a_non_canvas_wall(tmp_path):
    async def run():
        ctl = DisplayController(Config(data_dir=tmp_path), DisplayState(45))
        ps = PluginSettings(tmp_path)
        ps.set_installed(["canvas-art-clock"])
        rt = PluginRuntime(Config(data_dir=tmp_path), ps,
                           __import__("pathlib").Path(__file__).resolve().parents[2] / "apps")
        rt.attach_caps(lambda: device.SPLIT_FLAP)   # a physical wall: no canvas
        rt.load()
        ctl.attach_plugins(rt)
        with pytest.raises(KeyError):
            await ctl.run_app("canvas-art-clock")

    asyncio.run(run())


def test_the_canvas_apps_are_marked_canvas_surface():
    from pathlib import Path
    apps = Path(__file__).resolve().parents[2] / "apps"
    import json
    for app in ("effects", "canvas-art-clock", "canvas-image", "canvas-weather",
                "canvas-date", "canvas-world", "canvas-countdown", "canvas-overview"):
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
    ("canvas-overview", {}),                         # renders clock/date even with no weather
])
@pytest.mark.parametrize("size", [(128, 32), (64, 32), (128, 64)])
def test_new_canvas_apps_push_a_frame(gw_calls, app_id, settings, size):
    W, H = size
    _hold, img, content = _push(gw_calls, _load(app_id), W, H, settings)
    assert gw_calls[-1][1] == "/api/canvas/frame" and len(content) == W * H * 3
    assert img is not None and _bright(img) > 20


def test_canvas_apps_fill_a_big_256x64_panel(gw_calls):
    """The rich apps must use a large Matrix panel, not cluster in a corner — this
    just pins that the big-panel branches render a full, non-black 256x64 frame."""
    def gww(days=3, air=False):
        return {"ok": True, "sky": "clear", "temp_f": 68, "hi_f": 88, "lo_f": 64,
                "city": "Boston", "humidity": 40, "feels_like_f": 71, "wind_mph": 6,
                "forecast": [{"date": "2026-07-16", "hi_f": 90, "lo_f": 66},
                             {"date": "2026-07-17", "hi_f": 84, "lo_f": 61},
                             {"date": "2026-07-18", "hi_f": 79, "lo_f": 58}]}
    for app_id, kw in (("canvas-weather", {"get_weather": gww}),
                       ("canvas-overview", {"get_weather": gww}),
                       ("canvas-date", {})):
        _h, img, content = _push(gw_calls, _load(app_id), 256, 64, {}, **kw)
        assert len(content) == 256 * 64 * 3 and _bright(img) > 30, app_id


def test_overview_weather_column_never_clips_off_the_bottom(gw_calls, monkeypatch):
    """Regression: the weather column stacks up to five lines (temp, condition,
    high/low, feels, humidity/wind). Their natural height can exceed the drawable
    region on a short panel — _fit_stack must shrink the column together so the
    last line never spills past the bottom edge. Spy on _draw_stack and assert
    every stack fits its region, for ordinary AND all-3-digit extreme readings."""
    app = _load("canvas-overview")
    seen = []
    orig = app._draw_stack

    def spy(draw, x, top, region_h, lines, gap):
        total = sum(ln[2] for ln in lines) + gap * max(0, len(lines) - 1)
        seen.append((total, region_h))
        return orig(draw, x, top, region_h, lines, gap)

    monkeypatch.setattr(app, "_draw_stack", spy)

    for reading in (
        {"ok": True, "sky": "cloudy", "temp_f": 84, "hi_f": 87, "lo_f": 64,
         "humidity": 47, "feels_like_f": 87, "wind_mph": 4},
        {"ok": True, "sky": "storm", "temp_f": 104, "hi_f": 108, "lo_f": 99,
         "humidity": 100, "feels_like_f": 112, "wind_mph": 25},
    ):
        for W, H in ((256, 64), (192, 48), (128, 64)):
            app.fetch._state = None                     # bypass the 10-min weather cache
            seen.clear()
            _push(gw_calls, app, W, H, {}, get_weather=lambda days=1, air=False: reading)
            assert seen, f"{W}x{H}: nothing drawn"
            for total, region_h in seen:
                assert total <= region_h, \
                    f"{W}x{H}: a {total}px stack overflows the {region_h}px region"


@pytest.mark.parametrize("app_id", ["canvas-date", "canvas-world", "canvas-countdown"])
def test_new_canvas_apps_are_none_without_a_panel(app_id):
    assert _load(app_id).fetch({}, None, None, None, canvas=None) is None


# --- the 1.18 canvas extras: qoi, ticker, anim, rect, effect params ----------
def _qoi_decode(data):
    """A reference QOI decoder, so the round-trip test proves the encoder is correct."""
    w = int.from_bytes(data[4:8], "big"); h = int.from_bytes(data[8:12], "big")
    out = bytearray(); index = [(0, 0, 0, 0)] * 64
    r, g, b, a = 0, 0, 0, 255; p, count, n = 14, 0, w * h
    while count < n:
        byte = data[p]; p += 1
        if byte == 0xFE:
            r, g, b = data[p], data[p + 1], data[p + 2]; p += 3
        elif byte == 0xFF:
            r, g, b, a = data[p:p + 4]; p += 4
        elif byte >> 6 == 0:
            r, g, b, a = index[byte & 0x3F]
        elif byte >> 6 == 1:
            r = (r + ((byte >> 4) & 3) - 2) & 0xFF
            g = (g + ((byte >> 2) & 3) - 2) & 0xFF
            b = (b + (byte & 3) - 2) & 0xFF
        elif byte >> 6 == 2:
            b2 = data[p]; p += 1; vg = (byte & 0x3F) - 32
            r = (r + vg + ((b2 >> 4) & 0xF) - 8) & 0xFF
            g = (g + vg) & 0xFF
            b = (b + vg + (b2 & 0xF) - 8) & 0xFF
        else:
            run = (byte & 0x3F) + 1
            out += bytes((r, g, b)) * run; count += run
            index[(r * 3 + g * 5 + b * 7 + a * 11) & 63] = (r, g, b, a); continue
        index[(r * 3 + g * 5 + b * 7 + a * 11) & 63] = (r, g, b, a)
        out += bytes((r, g, b)); count += 1
    return bytes(out)


def test_capabilities_parse_the_canvas_extras():
    doc = dict(CANVAS_DOC,
               canvas={"formats": ["rgb888", "rgb565", "qoi"], "width": 256, "height": 64,
                       "rect": True, "anim": True, "ticker": True},
               effects=["plasma", "clock", "life"], effectParams=["hue", "density"])
    caps = device.from_capabilities(doc)
    assert caps.canvas_qoi and caps.canvas_rect and caps.canvas_anim and caps.canvas_ticker
    assert caps.effect_params == ("hue", "density")


def test_qoi_encode_round_trips_to_the_exact_pixels():
    from PIL import Image
    w, h = 64, 32
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (x * 4 % 256, y * 8 % 256, (x + y) % 256)
    raw = img.tobytes()
    q = canvas.qoi_encode(raw, w, h)
    assert q[:4] == b"qoif" and q[-8:] == bytes((0, 0, 0, 0, 0, 0, 0, 1))
    assert _qoi_decode(q) == raw            # the wall draws exactly what we rendered


def test_frame_uses_qoi_when_advertised_else_raw(gw_calls):
    from PIL import Image
    img = Image.new("RGB", (64, 32), (10, 20, 30))
    canvas.CanvasSurface("http://gw", 64, 32, ("rgb888", "rgb565", "qoi"), ()).frame(img)
    m, path, _b, content = gw_calls[-1]
    assert m == "PUT" and path == "/api/canvas/qoi" and content[:4] == b"qoif"
    canvas.CanvasSurface("http://gw", 64, 32, ("rgb888",), ()).frame(img)
    m, path, _b, content = gw_calls[-1]
    assert m == "PUT" and path == "/api/canvas/frame" and len(content) == 64 * 32 * 3


def test_ticker_posts_text_colour_speed(gw_calls):
    cv = canvas.CanvasSurface("http://gw", 128, 32, ("rgb888",), (), ticker=True)
    assert cv.can_ticker
    cv.ticker("HELLO", (0, 255, 0), speed=6)
    m, path, body, _ = gw_calls[-1]
    assert m == "POST" and path == "/api/canvas/ticker"
    assert body == {"text": "HELLO", "color": [0, 255, 0], "speed": 6}


def test_effect_passes_hue_and_density(gw_calls):
    cv = canvas.CanvasSurface("http://gw", 128, 32, ("rgb888",), ("matrix",),
                              effect_params=("hue", "density"))
    cv.effect("matrix", speed=6, hue=120, density=40)
    _m, path, body, _ = gw_calls[-1]
    assert path == "/api/canvas/effect" and body["hue"] == 120 and body["density"] == 40


def test_anim_uploads_an_mpga_loop(gw_calls):
    from PIL import Image
    cv = canvas.CanvasSurface("http://gw", 8, 4, ("rgb888",), (), anim=True)
    cv.anim([Image.new("RGB", (8, 4), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))],
            fps=10, loop=True)
    m, path, _b, content = gw_calls[-1]
    assert m == "PUT" and path == "/api/canvas/anim"
    assert content[:4] == b"MPGA" and content[4] == 1 and content[5] == 3   # ver=1, fmt=rgb888
    assert content[6] == 10 and (content[7] & 1) == 1                       # fps, loop flag
    assert int.from_bytes(content[12:14], "big") == 3                       # 3 frames
    assert len(content) == 14 + 3 * 8 * 4 * 3


def test_ticker_app_scrolls_a_message(gw_calls):
    cv = canvas.CanvasSurface("http://gw", 256, 64, ("rgb888", "qoi"), (), ticker=True)
    hold = _load("canvas-ticker").fetch(
        {"ticker_source": "message", "ticker_text": "HI THERE", "ticker_color": "green",
         "ticker_speed": "6"}, None, lambda: 8, lambda: 42, canvas=cv)
    m, path, body, _ = gw_calls[-1]
    assert m == "POST" and path == "/api/canvas/ticker"
    assert body["text"] == "HI THERE" and body["speed"] == 6 and hold > 0


def test_canvas_image_both_fit_modes_produce_a_frame():
    """Regression: 'Fit' (contain) called img.__class__.new — not a real method, so it
    raised AttributeError and fell back to the demo gradient. Both modes must return a
    panel-sized frame: contain letterboxes (black border), cover fills to the edges."""
    from PIL import Image
    fit = _load("canvas-image")._fit
    img = Image.new("RGB", (200, 50), (255, 0, 0))     # wide image onto a square panel
    contained = fit(img, 64, 64, "contain")
    covered = fit(img, 64, 64, "cover")
    assert contained.size == (64, 64) and covered.size == (64, 64)
    assert contained.getpixel((0, 0)) == (0, 0, 0)     # letterbox corner is black
    assert covered.getpixel((0, 0)) == (255, 0, 0)     # cover fills, no border


def test_countdown_arrived_and_no_target_do_not_crash(gw_calls):
    app = _load("canvas-countdown")
    for settings in ({"countdown_event": "Party", "countdown_target": "2000-01-01T00:00"},  # past
                     {}):                                                                     # unset
        _h, _i, content = _push(gw_calls, app, 128, 32, settings)
        assert len(content) == 128 * 32 * 3


# --- the effect picker is driven by what the wall advertises ----------------

def test_effects_become_one_app_per_advertised_effect(tmp_path):
    """The single 'Effects' app is presented as one app per effect the WALL advertises,
    named from the effect; the generic app itself is not listed."""
    from conftest import make_runtime
    caps = device.from_capabilities(dict(CANVAS_DOC, effects=["plasma", "fire", "matrix", "sparkle"]))
    rt = make_runtime(tmp_path=tmp_path, installed=[], caps=caps)
    apps = {a["id"]: a for a in rt.available_list()}
    assert "effects" not in apps
    assert {"effect_plasma", "effect_fire", "effect_matrix", "effect_sparkle"} <= set(apps)
    assert apps["effect_sparkle"]["name"] == "Sparkle"       # a new firmware effect, auto-named
    assert apps["effect_fire"]["surface"] == "canvas"


def test_no_effect_apps_on_a_wall_without_effects(tmp_path):
    from conftest import make_runtime
    rt = make_runtime(tmp_path=tmp_path, installed=[], caps=device.SPLIT_FLAP)
    ids = {a["id"] for a in rt.available_list()}
    assert "effects" not in ids and not any(i.startswith("effect_") for i in ids)


def test_a_per_effect_app_pins_its_effect(tmp_path):
    """Each per-effect app hands the shared effects module its own effect (no picker)."""
    from conftest import make_runtime
    rt = make_runtime(tmp_path=tmp_path, installed=["effect_fire"], caps=device.from_capabilities(CANVAS_DOC))
    ps = rt._plugin_settings("effect_fire", rt.manifest("effect_fire"))
    assert ps["effect"] == "fire"
    keys = {f.get("key") for f in rt.settings_schema("effect_fire")["fields"]}
    assert "plugin_effect_fire_effect" not in keys           # the effect picker is gone


def test_a_synthetic_effect_app_can_be_installed_and_persists(tmp_path):
    """Installing a per-effect app must be allowed (it has no folder) and survive the
    reload set_installed triggers — set_installed rebuilt the list from disk apps only,
    which silently dropped effect ids."""
    from conftest import make_runtime
    caps = device.from_capabilities(dict(CANVAS_DOC, effects=["plasma", "fire"]))
    rt = make_runtime(tmp_path=tmp_path, installed=[], caps=caps)
    assert "effect_fire" in rt.installable_ids()
    rt.set_installed("effect_fire", True)
    assert "effect_fire" in rt.settings.installed_apps       # not dropped by the reload
    assert rt.manifest("effect_fire")["pinned_effect"] == "fire"
    rt.set_installed("effect_fire", False)
    assert "effect_fire" not in rt.settings.installed_apps


# --- the live-preview / HA-image canvas frame cache -------------------------

def test_canvas_frame_is_cached_then_released_for_the_preview(gw_calls):
    """A canvas app's pushed frame is remembered per gateway URL so the live
    preview and the HA board image can show the panel (both otherwise render the
    flap grid a canvas app bypasses). Releasing the panel drops it."""
    from PIL import Image
    url = "http://preview-gw"
    surf = canvas.CanvasSurface(url, 64, 32, ("rgb888",), ())
    assert not canvas.has_frame(url)
    surf.frame(Image.new("RGB", (64, 32), (30, 60, 90)))
    assert canvas.has_frame(url)
    png = canvas.last_frame_png(url)
    assert png and png[:4] == b"\x89PNG"                # a real PNG
    canvas.release(url)
    assert not canvas.has_frame(url)                    # released -> preview gone


def test_controller_serves_a_canvas_preview_only_while_a_canvas_app_draws(gw_calls, tmp_path):
    from pathlib import Path

    async def run():
        cfg = Config(data_dir=tmp_path)
        cfg.update({"transport": {"gateway_url": "http://gw"}})
        ctl = DisplayController(cfg, DisplayState(45))
        ps = PluginSettings(tmp_path)
        ps.set_installed(["canvas-art-clock", "time"])
        rt = PluginRuntime(cfg, ps, Path(__file__).resolve().parents[2] / "apps")
        rt.load()
        rt.attach_caps(lambda: device.from_capabilities(CANVAS_DOC))
        ctl.attach_plugins(rt)
        await ctl.start()

        await ctl.run_app("canvas-art-clock")           # a frame-push canvas app
        await asyncio.sleep(0.4)
        assert ctl.has_canvas_preview()
        png = ctl.canvas_preview_png()
        assert png and png[:4] == b"\x89PNG"

        await ctl.run_app("time")                       # a flap app: no preview
        await asyncio.sleep(0.1)
        assert not ctl.has_canvas_preview()
        assert ctl.canvas_preview_png() is None
        await ctl.stop()

    asyncio.run(run())


# --- simulation mode + canvas ----------------------------------------------

def test_sim_mode_keeps_the_real_walls_caps(tmp_path):
    """Sim mode's no-op transport carries no caps; the controller keeps the ones the real wall last
    reported, so a Matrix/canvas app isn't suddenly 'uninstalled' when you flip simulation on."""
    from app.transport import SimTransport
    cfg = Config(data_dir=tmp_path)
    cfg.update({"transport": {"gateway_url": "http://gw"}})
    ctl = DisplayController(cfg, DisplayState(45))
    ctl.transport.caps = device.from_capabilities(CANVAS_DOC)     # a Matrix wall reports a framebuffer
    assert ctl.caps.has_canvas                                    # non-sim: read it (and remember it)
    cfg.set_sim_mode(True)
    ctl.transport = SimTransport()                               # sim swaps to a capless transport
    assert ctl.caps.has_canvas                                    # still canvas-capable, from the remembered caps


def test_sim_mode_runs_a_canvas_app_without_driving_the_panel(monkeypatch):
    """A simulated canvas app renders (its frame is cached for the preview) but never POSTs to the
    real panel — sim mode's whole point."""
    calls = []
    monkeypatch.setattr(canvas.gateway, "_request",
                        lambda *a, **k: calls.append(a[:3]) or type("R", (), {"status_code": 200, "json": lambda s: {}})())
    canvas.set_sim("http://sim", True)
    canvas.forget_frame("http://sim")
    s = canvas.CanvasSurface("http://sim", 8, 4, ("rgb888", "qoi"), (), rects=True, sprite=True)
    s.frame(bytes(8 * 4 * 3))                                     # frame-push
    s.clear("black").text(0, 0, "hi", "white", 8).show()         # ops
    assert canvas.set_active("http://sim", True) is True         # takeover
    assert canvas.play_effect("http://sim", "plasma") is True    # effect
    assert calls == []                                           # nothing reached the gateway
    assert canvas.has_frame("http://sim")                        # but the frame-push frame is cached for preview
    assert canvas.get_frame("http://sim") is None                # no real panel to read back
    canvas.set_sim("http://sim", False)
    canvas.forget_frame("http://sim")


def test_a_canvas_app_on_a_flap_wall_is_rejected_clearly(tmp_path):
    """On a wall with no framebuffer, run_app raises with a message the route turns into a clear
    409 — not the misleading 'app not installed'."""
    async def run():
        cfg = Config(data_dir=tmp_path)
        ctl = DisplayController(cfg, DisplayState(45))
        ps = PluginSettings(tmp_path)
        ps.set_installed(["canvas-art-clock"])
        rt = PluginRuntime(cfg, ps, __import__("pathlib").Path(__file__).resolve().parents[2] / "apps")
        rt.attach_caps(lambda: device.SPLIT_FLAP)               # a flap wall
        rt.load()
        ctl.attach_plugins(rt)
        with pytest.raises(KeyError) as e:
            await ctl.run_app("canvas-art-clock")
        assert "needs a canvas" in str(e.value)                 # the string the route maps to 409
    asyncio.run(run())


def test_leaving_a_canvas_app_forgets_the_flap_cache(gw_calls, tmp_path):
    """Regression: a canvas app draws straight to the framebuffer, bypassing the
    flap transport's shown-cell cache. When you switch back to a flap app, that
    cache is stale — so its unchanged cells were skipped and never repainted, and
    the wall looked stuck on the canvas. Leaving canvas mode must forget the cache."""
    from pathlib import Path

    async def run():
        cfg = Config(data_dir=tmp_path)
        cfg.update({"transport": {"gateway_url": "http://gw"}})
        ctl = DisplayController(cfg, DisplayState(45))
        ps = PluginSettings(tmp_path)
        ps.set_installed(["effect_plasma", "time"])
        rt = PluginRuntime(cfg, ps, Path(__file__).resolve().parents[2] / "apps")
        rt.attach_caps(lambda: device.from_capabilities(CANVAS_DOC))
        rt.load()
        ctl.attach_plugins(rt)
        await ctl.start()

        forgot = []
        real = ctl.transport.forget
        ctl.transport.forget = lambda: (forgot.append(True), real())[1]

        await ctl.run_app("effect_plasma")      # a canvas (effect) app takes the panel over
        await asyncio.sleep(0.2)
        assert ctl._canvas_active
        await ctl.run_app("time")               # switch back to a flap app
        assert forgot, "the flap shown-cell cache was not forgotten leaving canvas mode"
        await ctl.stop()

    asyncio.run(run())
