"""The LCD surface: kind detection from the capabilities' surface object, the
logical-panel arithmetic, the LcdSurface render-and-upscale pipeline (what fixes
LED-era sprites/fonts coming out comically small on a 1280x800 panel), zones on
an LCD, and the big-height text fitters."""

import pytest

from app import device
from app.zonecanvas import LcdSurface, ZoneCanvas


def _lcd_caps(w=1280, h=800):
    return device.from_capabilities({
        "product": "LCD Gateway", "features": ["canvas", "sound"],
        "surface": {"kind": "lcd", "w": w, "h": h, "colorBits": 16, "refreshHz": 60},
        "charset": {"common": "A"},
        "canvas": {"width": w, "height": h, "formats": ["rgb888", "qoi"], "opsBin": True}})


class _Rec:
    def __init__(self):
        self.frames = []

    def frame(self, img):
        self.frames.append(img.copy())
        return True


# --- detection + arithmetic --------------------------------------------------

def test_surface_kind_parses_from_the_capability_object():
    assert _lcd_caps().surface_kind == "lcd" and _lcd_caps().is_lcd
    led = device.from_capabilities({
        "product": "Matrix Gateway", "features": ["canvas"],
        "surface": {"kind": "led-matrix", "w": 256, "h": 64},
        "charset": {"common": "A"}, "canvas": {"width": 256, "height": 64}})
    assert led.surface_kind == "led-matrix" and not led.is_lcd
    old = device.from_capabilities({"product": "Matrix Gateway", "features": ["canvas"],
                                    "charset": {"common": "A"},
                                    "canvas": {"width": 256, "height": 64}})
    assert old.surface_kind == "" and not old.is_lcd      # pre-field firmware = LED


def test_lcd_logical_lands_in_the_led_design_range():
    assert device.lcd_logical(_lcd_caps(1280, 800)) == (256, 160, 5)
    assert device.lcd_logical(_lcd_caps(800, 480)) == (267, 160, 3)
    lw, lh, k = device.lcd_logical(_lcd_caps(1920, 1080))
    assert 96 <= lh <= 200 and k >= 5                     # any panel: app heuristics in range


# --- the surface -------------------------------------------------------------

def test_lcd_surface_reports_logical_and_pushes_native():
    caps = _lcd_caps()
    s = LcdSurface("http://lcd", caps)
    s._panel = _Rec()
    assert (s.width, s.height) == (256, 160)              # what the APP sees
    s.clear((0, 0, 0))
    s.rect(10, 10, 1, 1, (255, 0, 0), fill=True)          # one logical pixel
    assert s.show()
    img = s._panel.frames[-1]
    assert img.size == (1280, 800)                        # what the WALL gets
    # NEAREST upscale: the logical pixel is a solid 5x5 block, edges sharp
    for dx in range(5):
        for dy in range(5):
            assert img.getpixel((50 + dx, 50 + dy)) == (255, 0, 0)
    assert img.getpixel((49, 50)) == (0, 0, 0) and img.getpixel((55, 50)) == (0, 0, 0)


def test_lcd_surface_native_variant_for_proportional_apps():
    caps = _lcd_caps()
    s = LcdSurface("http://lcd", caps, native=True)
    s._panel = _Rec()
    assert (s.width, s.height) == (1280, 800)
    img = s.blank((0, 0, 0))
    assert s.frame(img) and s._panel.frames[-1].size == (1280, 800)


def test_build_canvas_surface_picks_the_lcd_path(tmp_path):
    from conftest import make_runtime
    rt = make_runtime(tmp_path=tmp_path, installed=["canvas-stock-graph"], caps=_lcd_caps())
    s = rt.build_canvas_surface()
    assert isinstance(s, LcdSurface) and (s.width, s.height) == (256, 160)
    n = rt.build_canvas_surface(native=True)
    assert (n.width, n.height) == (1280, 800)
    # the proportional card apps carry the opt-in the render path reads
    assert rt._registry["canvas-stock-graph"].get("lcd_native") is True


# --- zones on an LCD ---------------------------------------------------------

def test_zones_on_lcd_lay_out_logical_and_push_native(tmp_path, monkeypatch):
    import asyncio

    from app import canvas as canvas_mod
    from test_zones import _Plugins

    from app.config import Config
    from app.engine import DisplayController
    from app.state import DisplayState

    pushed = []

    class _Panel:
        def __init__(self, url, caps):
            pass

        def frame(self, img):
            pushed.append(img.copy())
            return True

    async def run():
        cfg = Config(data_dir=tmp_path)
        ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
        ctrl.plugins = _Plugins()
        ctrl._caps = lambda: _lcd_caps()
        monkeypatch.setattr(canvas_mod, "CanvasSurface", _Panel)

        async def fake_take_panel():
            return "http://lcd"

        async def _noop(*a, **k):
            return None

        ctrl._take_panel = fake_take_panel
        ctrl._release_canvas = _noop
        await ctrl.run_zones([{"app": "left"}, {"app": "right"}])
        for _ in range(100):
            if pushed:
                break
            await asyncio.sleep(0.02)
        assert pushed and pushed[0].size == (1280, 800)   # native out …
        assert pushed[0].getpixel((40, 40)) == (255, 0, 0)
        assert pushed[0].getpixel((1240, 40)) == (0, 0, 255)
        await ctrl.stop_app()

    asyncio.run(run())


# --- text at LCD heights ------------------------------------------------------

def test_fitters_reach_lcd_sizes():
    z = ZoneCanvas(1280, 800)
    assert z.fit_font("72°", 1200, 400).size > 150        # the old -1x96 loop maxed ~8
    font, lines = z.wrap_fit("Dinner is ready — come and get it", 1000, 700, max_lines=3)
    assert font.size > 100 and 1 <= len(lines) <= 3


def test_toast_proportions_scale_to_the_panel():
    from app import toast
    card = toast.render(1280, 800, "Hi", icon="bell")[-1]
    assert card.getpixel((5, 400)) == toast.ACCENTS["bell"]   # a ~14px accent bar
    assert card.getpixel((20, 400)) != toast.ACCENTS["bell"]  # …not a 3px LED sliver
    led = toast.render(128, 32, "Hi", icon="bell")[-1]
    assert led.getpixel((1, 16)) == toast.ACCENTS["bell"]     # the LED card unchanged
