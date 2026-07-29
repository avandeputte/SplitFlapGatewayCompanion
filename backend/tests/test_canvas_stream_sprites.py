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

def test_rgba_encodes_as_batch_alpha_on_a_compositing_wall():
    glow = {"op": "circle", "x": 3, "y": 3, "r": 2, "color": [90, 150, 210, 70], "fill": True}
    crisp = {"op": "circle", "x": 3, "y": 3, "r": 1, "color": [200, 235, 255], "fill": True}
    b = encode_ops_bin([glow, glow, crisp], composite=True)
    assert b.count(b"\x15\x46") == 1          # one 0x15 covers the run of alpha-70 ops
    assert b"\x15\xff" in b                   # ...and the opaque op restores alpha first
    # order: alpha set -> glows -> alpha reset -> crisp
    assert b.index(b"\x15\x46") < b.index(b"\x15\xff")


def test_rgba_still_falls_back_to_json_without_compositing():
    op = {"op": "circle", "x": 0, "y": 0, "r": 2, "color": [1, 2, 3, 4], "fill": True}
    assert encode_ops_bin([op]) is None                       # pre-3.8 wall: no 0x15
    assert encode_ops_bin([op], composite=False) is None


def test_sprite_after_an_rgba_op_draws_opaque():
    # JSON parity: a sprite has no color, so per-color alpha never dims it — the batch
    # alpha must be reset before the blit or the fish would render translucent.
    b = encode_ops_bin([
        {"op": "circle", "x": 0, "y": 0, "r": 2, "color": [9, 9, 9, 70], "fill": True},
        {"op": "sprite", "i": 1, "x": 3, "y": 3},
    ], composite=True)
    assert b.index(b"\x15\x46") < b.index(b"\x15\xff") < b.index(b"\x11")


def test_mixed_alphas_in_one_op_still_fall_back():
    # 0x15 is per op; two fields with different alphas can't both be honored.
    assert encode_ops_bin([{"op": "text", "x": 0, "y": 0, "s": "HI",
                            "color": [255, 255, 255], "outline": [0, 0, 0, 128]}],
                          composite=True) is None


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
        app.fetch_matrix({"fish": "4"}, cv)
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
    app.fetch_matrix({"fish": "4"}, cv)                       # warm-up: upload + first frame
    st = _FakeStream()
    canvas_mod._wall("http://gw").stream = st
    app.fetch_matrix({"fish": "4"}, cv)
    kinds = [k for k, _ in st.records]
    assert kinds == ["bind", "opsb"]                          # the whole frame rode the socket
    payload = st.records[1][1]
    assert b"\x14\x01" in payload                             # additive blend for the godrays
    assert b"\x15" in payload                                 # batch alpha carries the rgba look
    canvas_mod._wall("http://gw").stream = None
