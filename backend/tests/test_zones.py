"""Zones — several apps compositing into one panel frame: the ZoneCanvas surface
(shared with the screenshot harness), the validate/spec rules, the engine loop's
composited frame pushes, and the playlist entry type."""

import asyncio

import pytest

from app import device
from app.engine import DisplayController, NeedsCanvasError
from app.config import Config
from app.state import DisplayState
from app.zonecanvas import ZoneCanvas


def _matrix_caps(w=256, h=64):
    return device.Capabilities(lowercase=True, pictographs=True, named_colors=True,
                               indexed=True, canvas_w=w, canvas_h=h,
                               canvas_formats=("rgb888",))


class _Plugins:
    """Two tiny inline matrix apps: LEFT paints red, RIGHT paints blue. "game" is
    interactive; "fx" stands in for a gateway-resident effect."""

    def __init__(self):
        self.settings = {}
        self.calls = []
        self.overrides_seen = []

    def manifest(self, app_id):
        return {"id": app_id, "interactive": app_id == "game"} \
            if app_id in ("left", "right", "game", "fx") else None

    def renders_offscreen(self, app_id):
        return app_id in ("left", "right")

    def render_matrix_on(self, app_id, surface, overrides=None):
        self.calls.append(app_id)
        self.overrides_seen.append(overrides)
        color = (255, 0, 0) if app_id == "left" else (0, 0, 255)
        surface.clear(color)
        surface.show()
        return 5.0


def _controller(tmp_path):
    cfg = Config(data_dir=tmp_path)
    ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
    ctrl.plugins = _Plugins()
    ctrl._caps = lambda: _matrix_caps()
    return ctrl


# --- the surface -------------------------------------------------------------

def test_zonecanvas_renders_and_takes():
    z = ZoneCanvas(80, 64)
    z.clear((10, 20, 30))
    z.rect(0, 0, 10, 10, (255, 255, 255), fill=True)
    z.show()
    img = z.take()
    assert img.size == (80, 64) and img.getpixel((2, 2)) == (255, 255, 255)
    assert not z.frames                                   # take() drains the backlog
    assert callable(z.fit_font) and z.num({"a": "3"}, "a", 1) == 3


def test_harness_cap_is_the_same_surface():
    # One ops->PIL shim everywhere: the gallery's Cap must BE ZoneCanvas (plus stubs).
    import importlib.util
    import sys
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "tools" / "screenshot-harness.py"
    src = path.read_text("utf-8")
    assert "class Cap(ZoneCanvas):" in src
    assert "def rect(" not in src.split("class Cap(ZoneCanvas):")[1].split("\n\n\n")[0]


# --- validation --------------------------------------------------------------

def test_validate_zones_rules(tmp_path):
    ctrl = _controller(tmp_path)
    spec = ctrl.validate_zones([{"app": "left"}, {"app": "right", "width": 2}])
    assert [z["app"] for z in spec] == ["left", "right"]
    assert spec[1]["width"] == 2.0
    with pytest.raises(ValueError):
        ctrl.validate_zones([{"app": "left"}])            # needs 2-3
    with pytest.raises(ValueError):
        ctrl.validate_zones([{"app": "left"}, {"app": "nope"}])
    with pytest.raises(ValueError):
        ctrl.validate_zones([{"app": "left"}, {"app": "game"}])   # interactive excluded
    with pytest.raises(ValueError):
        ctrl.validate_zones([{"app": "left"}, {"app": "fx"}])     # gateway-resident excluded
    spec = ctrl.validate_zones([{"app": "left", "overrides": {"units": "c"}}, {"app": "right"}])
    assert spec[0]["overrides"] == {"units": "c"}                 # per-zone settings survive
    ctrl._caps = lambda: device.SPLIT_FLAP
    with pytest.raises(NeedsCanvasError):
        ctrl.validate_zones([{"app": "left"}, {"app": "right"}])


# --- the loop ----------------------------------------------------------------

def test_zones_composites_and_pushes_one_frame(tmp_path, monkeypatch):
    from app import canvas as canvas_mod

    pushed = []

    class _Panel:
        def __init__(self, url, caps):
            pass

        def frame(self, img):
            pushed.append(img.copy())
            return True

    async def run():
        ctrl = _controller(tmp_path)
        monkeypatch.setattr(canvas_mod, "CanvasSurface", _Panel)

        async def fake_take_panel():
            return "http://gw"

        ctrl._take_panel = fake_take_panel
        ctrl._release_canvas = _noop
        await ctrl.run_zones([{"app": "left"}, {"app": "right"}], name="pair")
        for _ in range(100):
            if pushed:
                break
            await asyncio.sleep(0.02)
        assert pushed, "no composite frame reached the panel"
        img = pushed[0]
        assert img.size == (256, 64)
        assert img.getpixel((10, 10)) == (255, 0, 0)      # the left zone
        assert img.getpixel((250, 10)) == (0, 0, 255)     # the right zone
        assert img.getpixel((127, 10)) == (0, 0, 0)       # the divider column
        assert ctrl.active_app == "zones:pair"
        await ctrl.stop_app()

    async def _noop(*a, **k):
        return None

    asyncio.run(run())


def test_zones_playlist_entry_runs_and_skips(tmp_path, monkeypatch):
    from app import canvas as canvas_mod
    from test_engine_interrupts import FakeGateway, _until

    class _Panel:
        def __init__(self, url, caps):
            pass

        def frame(self, img):
            return True

    async def run():
        ctrl = _controller(tmp_path)
        ctrl.transport = FakeGateway()
        monkeypatch.setattr(canvas_mod, "CanvasSurface", _Panel)

        async def fake_take_panel():
            return "http://gw"

        async def _noop(*a, **k):
            return None

        ctrl._take_panel = fake_take_panel
        ctrl._release_canvas = _noop
        entries = [{"type": "zones", "zones": [{"app": "left"}, {"app": "right"}],
                    "duration": 3600},
                   {"type": "zones", "zones": [{"app": "right"}, {"app": "left"}],
                    "duration": 3600}]
        await ctrl.run_playlist(entries, loop=True, name="Z")
        await _until(lambda: ctrl.state.playlist_index == 0, "zones entry never started")
        ctrl.skip_playlist_entry()
        await _until(lambda: ctrl.state.playlist_index == 1, "zones slot outlived its skip")
        await ctrl.stop_app()

    asyncio.run(run())


def test_zones_playlist_entry_resolves_a_saved_layout(tmp_path, monkeypatch):
    from app import canvas as canvas_mod
    from app.engine import _entry_label
    from test_engine_interrupts import FakeGateway, _until

    assert _entry_label({"type": "zones", "layout": "Morning"}) == "Morning"
    assert _entry_label({"type": "zones"}) == "(multiview)"

    class _Panel:
        def __init__(self, url, caps):
            pass

        def frame(self, img):
            return True

    async def run():
        ctrl = _controller(tmp_path)
        ctrl.transport = FakeGateway()
        ctrl.plugins.settings = {"saved_zone_layouts":
                                 {"pair": {"zones": [{"app": "left"}, {"app": "right"}]}}}
        monkeypatch.setattr(canvas_mod, "CanvasSurface", _Panel)

        async def fake_take_panel():
            return "http://gw"

        async def _noop(*a, **k):
            return None

        ctrl._take_panel = fake_take_panel
        ctrl._release_canvas = _noop
        entries = [{"type": "zones", "layout": "pair", "duration": 3600},
                   {"type": "compose", "text": "NEXT", "duration": 3600}]
        await ctrl.run_playlist(entries, loop=True, name="Z")
        await _until(lambda: ctrl.plugins.calls != [], "the layout's apps never rendered")
        assert set(ctrl.plugins.calls) == {"left", "right"}
        ctrl.skip_playlist_entry()
        await _until(lambda: ctrl.state.playlist_index == 1, "zones slot outlived its skip")
        await ctrl.stop_app()

    asyncio.run(run())


def test_renders_offscreen_against_the_real_registry(tmp_path):
    from conftest import make_runtime
    rt = make_runtime(tmp_path=tmp_path,
                      installed=["canvas-sensor-graph", "canvas-snake", "canvas-anim", "dad-jokes"])
    assert rt.renders_offscreen("canvas-sensor-graph") is True    # offscreen-rendering functional
    assert rt.renders_offscreen("canvas-snake") is False          # interactive
    assert rt.renders_offscreen("canvas-anim") is False           # drives the device renderer
    assert rt.renders_offscreen("dad-jokes") is False             # a channel renders via channel_art


def test_zone_overrides_reach_the_app(tmp_path, monkeypatch):
    from app import canvas as canvas_mod

    class _Panel:
        def __init__(self, url, caps):
            pass

        def frame(self, img):
            return True

    async def run():
        ctrl = _controller(tmp_path)
        monkeypatch.setattr(canvas_mod, "CanvasSurface", _Panel)

        async def fake_take_panel():
            return "http://gw"

        async def _noop(*a, **k):
            return None

        ctrl._take_panel = fake_take_panel
        ctrl._release_canvas = _noop
        await ctrl.run_zones([{"app": "left", "overrides": {"units": "c"}},
                              {"app": "right"}])
        for _ in range(100):
            if len(ctrl.plugins.overrides_seen) >= 2:
                break
            await asyncio.sleep(0.02)
        assert {"units": "c"} in ctrl.plugins.overrides_seen
        await ctrl.stop_app()

    asyncio.run(run())


def test_saved_layouts_survive_the_settings_roundtrip(tmp_path):
    """The restart bug: saved_zone_layouts was a stray bare key, so _to_nested
    dropped it — layouts lived in memory and vanished on every restart/update.
    As a meta key it must ride the local file AND the gateway snapshot/restore."""
    from app.plugin_settings import PluginSettings

    st = PluginSettings(tmp_path / "settings.json")
    st.set("saved_zone_layouts", {"Morning": {"zones": [{"app": "time", "width": 1.0}]}})

    doc = st.snapshot()
    assert doc["saved_zone_layouts"]["Morning"]["zones"][0]["app"] == "time"

    fresh = PluginSettings(tmp_path / "settings2.json")
    fresh.restore_from_doc(doc)                            # the gateway-restore path
    assert fresh.get("saved_zone_layouts")["Morning"]["zones"][0]["app"] == "time"

    reread = PluginSettings(tmp_path / "settings.json")    # the local-file path
    assert reread.get("saved_zone_layouts")["Morning"]["zones"][0]["app"] == "time"
