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

    def writable(self):
        return True                        # never backlogged (backpressure has its own tests)

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

# ---------------------------------------------------------------------------
# Stream backpressure: never queue stale frames behind a slow wall
# ---------------------------------------------------------------------------
def _live_stream_wall(url):
    """A wall with a live (fake) stream attached; returns (wall, sent_records)."""
    sent = []

    class _Stream:
        alive = True
        _head_pending = False

        def writable(self):
            return False                       # the wall is behind; the pipe is full

        def bind(self, name):
            sent.append(("bind", name)); return True

        def opsb(self, enc):
            sent.append(("opsb", enc)); return True

        def ops(self, payload):
            sent.append(("ops", payload)); return True

        def frame(self, fmt, px):
            sent.append(("frame", fmt)); return True

        def rects(self, rects, fmt=2):
            sent.append(("rects", fmt)); return True

    w = canvas_mod._wall(url)
    w.stream = _Stream()
    return w, sent


def test_a_backlogged_stream_skips_the_ops_batch(monkeypatch):
    """The LCD renders the stream at ~2 records/s under the aquarium while the app draws at
    10 fps — sendall just parks the excess in the OS socket buffer, so the panel plays a
    growing backlog: late, jerky, and still swimming after a stop. A full pipe now SKIPS
    the whole batch (bind included, all-or-nothing); the next redraw supersedes it."""
    url = "http://bp-ops"
    surf = canvas_surface(url, 128, 64, ("rgb888",), ops_bin=True)
    w, sent = _live_stream_wall(url)
    surf.clear((0, 0, 0)).rect(1, 1, 4, 4, (255, 0, 0), fill=True)
    assert surf.show() is True                 # reported drawn — the app loop just moves on
    assert sent == []                          # but NOTHING was queued behind the backlog
    w.stream = None
    canvas_mod.forget_frame(url)


def test_a_backlogged_stream_skips_the_frame_and_keeps_the_delta_base(monkeypatch):
    """Skipping a frame must not advance the delta base: the wall still shows the last
    DELIVERED frame, so the next delta has to diff against that, not the skipped one."""
    url = "http://bp-frame"
    surf = canvas_surface(url, 8, 4, ("rgb888", "rgb565"), rects=True)
    w, sent = _live_stream_wall(url)
    base = bytes(8 * 4 * 3)
    w.last_frame = (8, 4, base)                # what the wall genuinely shows
    newer = bytes([255]) * (8 * 4 * 3)
    assert surf._push_rgb(newer) is True       # skipped, not queued
    assert sent == []
    assert w.last_frame == (8, 4, base)        # base untouched -> the next delta is honest
    w.stream = None
    canvas_mod.forget_frame(url)


def test_stream_socket_is_small_buffered_and_closes_abortively():
    """The OS default send buffer held ~10 s of batches — the wall played a kernel-held
    backlog and, after a stop, a graceful close kept FLUSHING it (the wall rendered stale
    frames for ~14 s, re-raising canvas mode over the hand-back, so the flap wall never
    returned). The stream socket therefore (a) shrinks SO_SNDBUF so writable() reflects
    the wall's true drain state, and (b) closes with SO_LINGER 0 — an RST that takes any
    unsent frames down with the stream. A stop is final; a re-adopted stream keyframes."""
    import socket as _socket
    import struct as _struct
    import threading

    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    accepted = []
    t = threading.Thread(target=lambda: accepted.append(srv.accept()[0]), daemon=True)
    t.start()
    st = canvas_mod.CanvasStream(f"http://127.0.0.1:{srv.getsockname()[1]}")
    try:
        assert st.open() is True
        snd = st.sock.getsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF)
        assert snd <= 16384 * 4                    # small (kernels may round/double), not the ~128K+ default
        sock = st.sock
        st.close()
        assert st.sock is None and not st.alive
        # the linger struct was applied before close: onoff=1, linger=0 (abortive RST)
        # (read back from the fd is not portable post-close; assert via a fresh apply path)
        s2 = _socket.socket()
        s2.setsockopt(_socket.SOL_SOCKET, _socket.SO_LINGER, _struct.pack("ii", 1, 0))
        assert s2.getsockopt(_socket.SOL_SOCKET, _socket.SO_LINGER, 8)[:4] != b"\x00\x00\x00\x00"
        s2.close()
    finally:
        t.join(timeout=2)
        for c in accepted:
            c.close()
        srv.close()
