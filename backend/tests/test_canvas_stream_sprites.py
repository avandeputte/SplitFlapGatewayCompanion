"""Sprite + compositing apps on the fast path (the aquarium smoothness fix).

Three pieces make an aquarium-shaped batch stream at game rate instead of one HTTP
POST per frame: rgba colors encode as the fw-3.8 batch-alpha opcode (0x15) instead of
forcing the JSON fallback; atlas binds ride the draw stream's own 0x04 record (the
batch splits around them); and a mid-stream atlas re-upload closes the stream first
(the REST PUT 409s while one is open) so the engine can re-adopt.
"""

from conftest import canvas_surface, load_app
from test_canvas_ops35 import OPS35
from app.canvas import encode_ops_bin
from app import canvas as canvas_mod


def _cv(composite=True, **kw):
    return canvas_surface("http://gw", 128, 64, ("rgb888",), (), ops=OPS35, ops_bin=1,
                          composite=composite, sprite=True, stream=True, **kw)


# --- the codec: rgba -> batch alpha 0x15 ------------------------------------

def test_rgba_encodes_as_batch_alpha():
    glow = {"op": "circle", "x": 3, "y": 3, "r": 2, "color": [90, 150, 210, 70], "fill": True}
    crisp = {"op": "circle", "x": 3, "y": 3, "r": 1, "color": [200, 235, 255], "fill": True}
    b = encode_ops_bin([glow, glow, crisp])
    assert b.count(b"\x15\x46") == 1          # one 0x15 covers the run of alpha-70 ops
    assert b"\x15\xff" in b                   # ...and the opaque op restores alpha first
    # order: alpha set -> glows -> alpha reset -> crisp
    assert b.index(b"\x15\x46") < b.index(b"\x15\xff")


def test_rgba_is_binary_on_every_wall():
    # one firmware generation: per-color alpha always has its 0x15 encoding
    op = {"op": "circle", "x": 0, "y": 0, "r": 2, "color": [1, 2, 3, 4], "fill": True}
    assert encode_ops_bin([op])[:2] == b"\x15\x04"


def test_sprite_scale_whole_is_fast_path_fractional_is_sprite2():
    """§2.5: a whole 1–4 scale stays on the fast integer SPRITE (0x11, scale in the flag byte);
    a fractional (or > 4) scale becomes SPRITE2 (0x23) with the scale as its own u16 8.8 field."""
    from app.canvas import encode_ops_bin
    assert encode_ops_bin([{"op": "sprite", "i": 3, "x": 10, "y": 20, "scale": 2}]) \
        == bytes.fromhex("110003000a001410")               # 0x11, flags 0x10 = (2-1) << 4
    b = encode_ops_bin([{"op": "sprite", "i": 3, "x": 10, "y": 20, "scale": 2.5}])
    assert b[:1] == b"\x23" and b[7] == 0 and b[8:10] == (640).to_bytes(2, "big")   # 2.5 * 256
    # flip-h + rot-90 land in the flag byte (bit0 | bits2-3); the scale stays its own field
    b2 = encode_ops_bin([{"op": "sprite", "i": 1, "x": 0, "y": 0, "scale": 1.5,
                          "flip": "h", "rot": 90}])
    assert b2[:1] == b"\x23" and b2[7] == 0x05 and b2[8:10] == (384).to_bytes(2, "big")


def test_sprite_after_an_rgba_op_draws_opaque():
    # JSON parity: a sprite has no color, so per-color alpha never dims it — the batch
    # alpha must be reset before the blit or the fish would render translucent.
    b = encode_ops_bin([
        {"op": "circle", "x": 0, "y": 0, "r": 2, "color": [9, 9, 9, 70], "fill": True},
        {"op": "sprite", "i": 1, "x": 3, "y": 3},
    ])
    assert b.index(b"\x15\x46") < b.index(b"\x15\xff") < b.index(b"\x11")


def test_mixed_alphas_in_one_op_still_fall_back():
    # 0x15 is per op; two fields with different alphas can't both be honored.
    assert encode_ops_bin([{"op": "text", "x": 0, "y": 0, "s": "HI",
                            "color": [255, 255, 255], "outline": [0, 0, 0, 128]}]) is None


# --- show(): atlas binds ride the stream ------------------------------------

class _FakeStream:
    alive = True

    def __init__(self):
        self.records = []

    def bind(self, name):
        self.records.append(("bind", name))
        return True

    def opsb(self, payload):
        self.records.append(("opsb", bytes(payload)))
        return True

    def ops(self, data):
        self.records.append(("ops", bytes(data)))
        return True


def test_bind_plus_binary_batch_marks_opsb_and_streams_as_04_06(gw_calls):
    cv = _cv()
    cv._ops.append({"op": "atlas", "name": "fish8"})
    cv.rect(0, 0, 4, 4, (40, 40, 40), fill=True)
    cv.sprite(0, 1, 1)
    assert cv.show()
    wall = canvas_mod._wall("http://gw")
    assert wall.last_kind == "opsb"           # the engine's adoption gate opens
    # pre-adoption it went over HTTP as the JSON batch (the bind rides inline)
    assert "/api/canvas/ops" in [c[1] for c in gw_calls]

    st = _FakeStream()
    wall.stream = st
    cv._ops.append({"op": "atlas", "name": "fish8"})
    cv.rect(0, 0, 4, 4, (40, 40, 40), fill=True)
    assert cv.show()
    kinds = [k for k, _ in st.records]
    assert kinds == ["bind", "opsb"]          # 0x04 then 0x06 — no HTTP at all
    assert st.records[0][1] == "fish8"
    wall.stream = None


def test_pure_binary_batch_still_posts_opsb(gw_calls):
    cv = _cv()
    cv.rect(0, 0, 4, 4, (40, 40, 40), fill=True)
    assert cv.show()
    assert "/api/canvas/opsb" in [c[1] for c in gw_calls]     # the pre-split fast path


def test_atlas_upload_closes_an_open_stream(monkeypatch, gw_calls):
    closed = []
    wall = canvas_mod._wall("http://gw")
    wall.stream = _FakeStream()
    monkeypatch.setattr(canvas_mod, "stream_end", lambda url: closed.append(url) or wall.__setattr__("stream", None))
    assert canvas_mod.put_atlas_named("http://gw", "sheet1", b"\x00" * 192, 8, 8, 1)
    assert closed == ["http://gw"]            # the REST PUT would 409 against the stream
    wall.stream = None


# --- the aquarium end to end -------------------------------------------------

def test_aquarium_frame_is_opsb_capable_on_a_compositing_wall(monkeypatch):
    import app.gateway as gateway

    class _R:
        status_code = 200

        def json(self):
            return []

    monkeypatch.setattr(gateway, "_request", lambda m, u, p, **kw: _R())
    app = load_app("canvas-aquarium")
    cv = _cv(composite=True)
    for _ in range(3):
        app.fetch_canvas({"fish": "4"}, cv)
    # rgba godrays/glow + the per-frame atlas bind no longer force the JSON kind:
    assert canvas_mod._wall("http://gw").last_kind == "opsb"


def test_aquarium_streams_bind_then_binary_frames(monkeypatch):
    import app.gateway as gateway

    class _R:
        status_code = 200

        def json(self):
            return []

    monkeypatch.setattr(gateway, "_request", lambda m, u, p, **kw: _R())
    app = load_app("canvas-aquarium")
    cv = _cv(composite=True)
    app.fetch_canvas({"fish": "4"}, cv)                       # warm-up: upload + first frame
    st = _FakeStream()
    canvas_mod._wall("http://gw").stream = st
    app.fetch_canvas({"fish": "4"}, cv)
    kinds = [k for k, _ in st.records]
    assert kinds == ["bind", "opsb"]                          # the whole frame rode the socket
    payload = st.records[1][1]
    assert b"\x14\x01" in payload                             # additive blend for the godrays
    assert b"\x15" in payload                                 # batch alpha carries the rgba look
    canvas_mod._wall("http://gw").stream = None


# --- §1.2 the engine adopts the stream UP FRONT -----------------------------

def _stream_doc(*, ops_bin=True):
    """A big stream-capable wall (like the LCD), optionally without binary ops."""
    canvas = {"formats": ["rgb888", "rgb565"], "width": 1280, "height": 800,
              "stream": True, "ops": ["clear", "gtext", "sprite", "rect"]}
    if ops_bin:
        canvas["opsBin"] = True
    return {"features": ["cells", "canvas"], "charset": {"uniform": True, "common": "A"},
            "canvas": canvas}


def _run_first_tick(monkeypatch, tmp_path, doc):
    """Start canvas-art-clock on a controller with ``doc``'s caps and return the ordered log of
    ("stream" | "render") events up to and including its first draw."""
    import asyncio
    from pathlib import Path
    from app.config import Config
    from app.engine import DisplayController
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime
    from app.state import DisplayState
    from app import device
    import app.gateway as gateway
    from conftest import until

    class _R:
        status_code = 200
        headers = {}
        content = b""

        def json(self):
            return {}

    monkeypatch.setattr(gateway, "_request", lambda m, u, p, **kw: _R())
    events = []
    monkeypatch.setattr(canvas_mod, "stream_begin", lambda url: events.append("stream") or True)

    async def run():
        cfg = Config(data_dir=tmp_path)
        cfg.update({"transport": {"gateway_url": "http://gw"}})
        ctl = DisplayController(cfg, DisplayState(45))
        ps = PluginSettings(tmp_path)
        ps.set_installed(["canvas-art-clock"])
        rt = PluginRuntime(cfg, ps, Path(__file__).resolve().parents[2] / "apps")
        rt.attach_caps(lambda: device.from_capabilities(doc))
        rt.load()
        real = rt.render_matrix
        monkeypatch.setattr(rt, "render_matrix",
                            lambda *a, **k: (events.append("render"), real(*a, **k))[1])
        ctl.attach_plugins(rt)
        await ctl.start()
        await ctl.run_app("canvas-art-clock")
        await until(lambda: "render" in events, "the app never drew")
        await ctl.stop()

    asyncio.run(run())
    return events


def test_frame_push_app_opens_the_stream_before_its_first_draw(monkeypatch, tmp_path):
    """§1.2: on a stream + binary-ops wall, an offscreen-render app adopts the draw stream UP
    FRONT — so its first frame rides the stream (second core) instead of a one-shot ~2 MB PUT
    that pins the single control worker and reads the wall 'offline'."""
    events = _run_first_tick(monkeypatch, tmp_path, _stream_doc(ops_bin=True))
    assert events and events[0] == "stream", \
        f"the stream must open before the first draw, got {events[:3]}"


def test_no_eager_stream_without_binary_ops(monkeypatch, tmp_path):
    """Gated on binary ops: a wall that only takes JSON ops would 409 them against an open
    stream, so the stream is NOT opened up front there — the first event is the draw, not a
    stream open. (Lazy adoption may still happen later for a frame-push app; that is fine.)"""
    events = _run_first_tick(monkeypatch, tmp_path, _stream_doc(ops_bin=False))
    assert events and events[0] == "render", \
        f"a JSON-ops wall must not pre-open the stream, got {events[:3]}"


def test_aquarium_opts_out_of_the_lcd_draw_stream(tmp_path):
    """Regression: streaming the aquarium crashed the 0.1.0 LCD firmware. It re-asserts a sprite
    atlas every run, and the atlas REST PUT must close any open draw stream — the open->atlas->close
    sequence panics the wall (and streaming gains it nothing there anyway). It carries manifest
    ``lcd_no_stream``, so the engine keeps it on HTTP ops on the LCD (is_lcd) while STILL streaming
    it on the Matrix Gateway, where it works."""
    import json
    from pathlib import Path
    from app.config import Config
    from app.engine import DisplayController
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime
    from app.state import DisplayState
    from app import device
    APPS = Path(__file__).resolve().parents[2] / "apps"
    assert json.loads((APPS / "canvas-aquarium" / "manifest.json").read_text()).get("lcd_no_stream") is True
    cfg = Config(data_dir=tmp_path)
    ctl = DisplayController(cfg, DisplayState(45))
    ps = PluginSettings(tmp_path); ps.set_installed(["canvas-aquarium"])
    rt = PluginRuntime(cfg, ps, APPS); rt.load(); ctl.attach_plugins(rt)
    lcd = device.from_capabilities({"product": "LCD Gateway", "features": ["canvas"],
        "surface": {"kind": "lcd", "w": 1280, "h": 800}, "charset": {"common": "A"},
        "canvas": {"width": 1280, "height": 800, "stream": True, "opsBin": True}})
    led = device.from_capabilities({"product": "Matrix Gateway", "features": ["canvas"],
        "surface": {"kind": "led-matrix", "w": 256, "h": 64}, "charset": {"common": "A"},
        "canvas": {"width": 256, "height": 64, "stream": True, "opsBin": True}})
    assert ctl._lcd_stream_opt_out("canvas-aquarium", lcd) is True    # HTTP ops on the LCD
    assert ctl._lcd_stream_opt_out("canvas-aquarium", led) is False   # streams on the Matrix Gateway
    assert ctl._lcd_stream_opt_out("time", lcd) is False              # a normal app still streams
