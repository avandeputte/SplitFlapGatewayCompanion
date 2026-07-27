"""Binary ops (fw 3.5 ``opsBin`` format 1): the encoder emits the decoder's exact
fixed layouts (big-endian, signed int16 coordinates), show() routes a representable
batch to POST /api/canvas/opsb on a wall that advertises the format, and anything the
format cannot carry falls back to the JSON batch — pixel-identical either way.
"""

import pytest

from conftest import canvas_surface
from test_canvas_ops35 import OPS35

from app.canvas import encode_ops_bin


def _cv(ops_bin=1):
    return canvas_surface("http://gw", 128, 32, ("rgb888",), (), ops=OPS35,
                          ops_bin=ops_bin, sprite=True)


def test_golden_encodings_match_the_decoder_layouts():
    assert encode_ops_bin([{"op": "clear", "color": [0, 0, 0]}]) == b"\x01\x00\x00\x00"
    assert encode_ops_bin([{"op": "pixel", "x": -1, "y": 2, "color": [9, 8, 7]}]) == \
        b"\x02\xff\xff\x00\x02\x09\x08\x07"
    # arc with a negative start angle — the gauge/chomper case
    assert encode_ops_bin([{"op": "arc", "x": 10, "y": 5, "r": 7, "start": -135,
                            "end": 135, "color": [1, 2, 3], "t": 2, "fill": False}]) == \
        b"\x0c\x00\x0a\x00\x05\x00\x07\x02\xff\x79\x00\x87\x00\x01\x02\x03"
    assert encode_ops_bin([{"op": "rect", "x": 1, "y": 2, "w": 3, "h": 4,
                            "color": [5, 6, 7], "fill": True}]) == \
        b"\x06\x00\x01\x00\x02\x00\x03\x00\x04\x01\x01\x05\x06\x07"
    # a closed poly outline sets flag bit1; a polyline stays open (flags 0)
    assert encode_ops_bin([{"op": "poly", "points": [[0, 0], [4, 0], [2, 3]],
                            "color": [1, 1, 1], "fill": False}])[:6] == \
        b"\x0d\x03\x02\x01\x01\x01"
    assert encode_ops_bin([{"op": "polyline", "points": [[0, 0], [4, 0]],
                            "color": [1, 1, 1]}])[:6] == b"\x0d\x02\x00\x01\x01\x01"
    # text with a shadow: flag bit4 + the shadow rgb before the length byte
    enc = encode_ops_bin([{"op": "text", "x": 2, "y": 0, "s": "HI", "size": 8,
                           "color": [250, 250, 250], "shadow": [0, 0, 0]}])
    assert enc == b"\x10\x00\x02\x00\x00\x08\x10\xfa\xfa\xfa\x00\x00\x00\x02HI"
    assert encode_ops_bin([{"op": "sprite", "i": 3, "x": 1, "y": 2, "flip": "hv",
                            "rot": 180, "scale": 2}]) == \
        b"\x11\x00\x03\x00\x01\x00\x02" + bytes([0b0001_1011])   # flip hv + rot 180 + scale 2
    assert encode_ops_bin([{"op": "show"}]) == b"\x13"


@pytest.mark.parametrize("op", [
    {"op": "textbox", "x": 0, "y": 0, "w": 10, "h": 10, "s": "X", "color": [1, 1, 1], "size": 10},
    {"op": "atlas", "name": "icons"},
    {"op": "image", "x": 0, "y": 0, "w": 1, "h": 1, "fmt": "rgb888", "data": "AA=="},
    {"op": "text", "x": 0, "y": 0, "s": "X", "size": 10, "color": [1, 1, 1], "font": "custom"},
    {"op": "text", "x": 0, "y": 0, "s": "Y" * 128, "size": 10, "color": [1, 1, 1]},
    {"op": "poly", "points": [[i, i] for i in range(17)], "color": [1, 1, 1]},
])
def test_unrepresentable_ops_return_none(op):
    assert encode_ops_bin([op]) is None


def test_show_routes_binary_on_an_opsbin_wall(gw_calls, monkeypatch):
    import app.gateway as gateway
    calls = []

    class _R:
        status_code = 200

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(gateway, "_request",
                        lambda m, u, p, *, timeout, **kw:
                        (calls.append((p, kw.get("json"), kw.get("content"))) or _R()))
    cv = _cv()
    cv.rect(0, 0, 10, 10, (255, 0, 0), fill=True)
    assert cv.show()
    path, js, content = calls[-1]
    assert path == "/api/canvas/opsb" and js is None and content.endswith(b"\x13")
    # the same batch on a JSON-only wall goes to /api/canvas/ops
    calls.clear()
    old = _cv(ops_bin=0)
    old.rect(0, 0, 10, 10, (255, 0, 0), fill=True)
    assert old.show()
    assert calls[-1][0] == "/api/canvas/ops" and calls[-1][1] is not None


def test_a_batch_with_an_atlas_bind_falls_back_to_json(gw_calls, monkeypatch):
    import app.gateway as gateway
    calls = []

    class _R:
        status_code = 200

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(gateway, "_request",
                        lambda m, u, p, *, timeout, **kw:
                        (calls.append(p) or _R()))
    cv = _cv()
    cv._ops.append({"op": "atlas", "name": "icons"})
    cv.sprite(1, 0, 0)
    assert cv.show()
    assert calls[-1] == "/api/canvas/ops"          # sticky bind: JSON carries it


def test_a_chomper_frame_is_fully_representable(gw_calls):
    from conftest import load_app
    app = load_app("canvas-chomper")
    cv = _cv()
    app.fetch_matrix({"speed": "5", "ghosts": "4"}, cv)
    paths = [c[1] for c in gw_calls]
    assert "/api/canvas/opsb" in paths and "/api/canvas/ops" not in paths
