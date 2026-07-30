"""The full binary-ops format (capabilities ``canvas.opsBin: true`` on modern firmware;
fw 3.12 briefly numbered it 2) — the format that closes the
v1→JSON gaps: anti-aliased strokes, the transform stack, offscreen layers, batch macros
and bezier all ride binary now. Golden bytes here mirror the firmware decoder's layouts
(web.cpp canvasOpsRunBin); a v1 wall must keep getting the old bytes / JSON fallbacks.
"""

import struct

from conftest import canvas_surface, load_app
from test_canvas_ops35 import OPS35
from app.canvas import encode_ops_bin
from app import canvas as canvas_mod

V2 = dict(composite=True, version=2)


def _cv(ops_bin=2, composite=True):
    return canvas_surface("http://gw", 128, 64, ("rgb888",), (), ops=OPS35,
                          ops_bin=ops_bin, composite=composite, sprite=True, stream=True)


# --- golden bytes vs the decoder layouts -------------------------------------

def test_transform_stack_encodes():
    assert encode_ops_bin([{"op": "save"}], **V2) == b"\x16"
    assert encode_ops_bin([{"op": "restore"}], **V2) == b"\x17"
    assert encode_ops_bin([{"op": "translate", "x": 3, "y": -2}], **V2) == b"\x18\x00\x03\xff\xfe"
    # scale is u16 8.8 fixed; a missing y is 0 on the wire = uniform (the decoder's rule)
    assert encode_ops_bin([{"op": "scale", "x": 1.5}], **V2) == b"\x19\x01\x80\x00\x00"
    assert encode_ops_bin([{"op": "scale", "x": 2, "y": 0.5}], **V2) == b"\x19\x02\x00\x00\x80"
    assert encode_ops_bin([{"op": "rotate", "deg": -90}], **V2) == b"\x1a\xff\xa6"


def test_layer_and_composite_encode():
    assert encode_ops_bin([{"op": "layer"}], **V2) == b"\x1b"
    assert encode_ops_bin([{"op": "composite", "x": 4, "y": 5, "mode": "add",
                            "alpha": 128}], **V2) == b"\x1c\x00\x04\x00\x05\x01\x80"


def test_macros_map_names_to_slot_ids():
    ops = [{"op": "define", "name": "star",
            "ops": [{"op": "pixel", "x": 1, "y": 2, "color": [9, 9, 9]}]},
           {"op": "call", "name": "star", "x": 10, "y": 3}]
    b = encode_ops_bin(ops, **V2)
    blob = b"\x02\x00\x01\x00\x02\x09\x09\x09"                # the embedded pixel op
    assert b == b"\x1d\x00" + struct.pack(">H", len(blob)) + blob + \
        b"\x1e\x00\x00\x0a\x00\x03"
    # a call to a name this batch never defined can't be resolved to a slot
    assert encode_ops_bin([{"op": "call", "name": "ghost"}], **V2) is None


def test_bezier_encodes_and_validates_arity():
    b = encode_ops_bin([{"op": "bezier", "points": [[0, 0], [10, -4], [20, 8]],
                         "t": 1, "aa": True, "color": [1, 2, 3]}], **V2)
    assert b == b"\x1f\x03\x01\x01\x01\x02\x03" + \
        b"\x00\x00\x00\x00" + b"\x00\x0a\xff\xfc" + b"\x00\x14\x00\x08"
    assert encode_ops_bin([{"op": "bezier", "points": [[0, 0], [1, 1]],
                            "color": [1, 2, 3]}], **V2) is None       # 3 or 4 points only


def test_aa_strokes_ride_v2():
    # line + aa becomes the dedicated AALINE (t dropped — the JSON path ignores it too)
    assert encode_ops_bin([{"op": "line", "x": 1, "y": 2, "x1": 3, "y1": 4, "t": 5,
                            "aa": True, "color": [7, 8, 9]}], **V2) == \
        b"\x20\x00\x01\x00\x02\x00\x03\x00\x04\x07\x08\x09"
    # circle aa = flag bit1; poly/polyline aa = flag bit2 — layouts otherwise unchanged
    c = encode_ops_bin([{"op": "circle", "x": 0, "y": 0, "r": 2, "aa": True,
                         "color": [5, 5, 5]}], **V2)
    assert c[7] == 0x02                                       # flags byte: aa, not filled
    p = encode_ops_bin([{"op": "polyline", "points": [(0, 0), (4, 4)], "t": 1,
                         "aa": True, "color": [5, 5, 5]}], **V2)
    assert p[2] == 0x04                                       # flags byte: aa polyline


def test_v1_walls_keep_the_old_behavior():
    # every v2-only op falls back to JSON on a v1 wall…
    for op in ({"op": "save"}, {"op": "translate", "x": 1, "y": 1}, {"op": "layer"},
               {"op": "bezier", "points": [[0, 0], [1, 1], [2, 2]], "color": [1, 1, 1]},
               {"op": "line", "x": 0, "y": 0, "x1": 3, "y1": 3, "aa": True, "color": [1, 1, 1]}):
        assert encode_ops_bin([op], composite=True, version=1) is None, op
    # …and a plain v1 batch is byte-identical whichever version encodes it
    plain = [{"op": "rect", "x": 0, "y": 0, "w": 4, "h": 4, "color": [9, 9, 9], "fill": True},
             {"op": "show"}]
    assert encode_ops_bin(plain, version=1) == encode_ops_bin(plain, **V2)


# --- the surface: wrappers, aa_ok, end-to-end --------------------------------

def test_wrappers_emit_the_shared_json_ops():
    cv = _cv()
    cv.save().translate(4, 6).scale(2).rotate(45).restore()
    cv.layer()
    cv.rect(0, 0, 3, 3, (9, 9, 9), fill=True)
    cv.composite(mode="add", alpha=90)
    cv.bezier([(0, 0), (5, 5), (9, 0)], (1, 2, 3), aa=True)
    with cv.define("dot"):
        cv.pixel(1, 1, (9, 9, 9))
    cv.call("dot", 8, 8)
    kinds = [o["op"] for o in cv._ops]
    assert kinds == ["save", "translate", "scale", "rotate", "restore", "layer", "rect",
                     "composite", "bezier", "define", "call"]
    assert cv._ops[9]["ops"] == [{"op": "pixel", "x": 1, "y": 1, "color": [9, 9, 9]}]
    assert encode_ops_bin(cv._ops, **V2) is not None          # the whole thing is v2-binary


def test_aa_ok_reflects_the_wall_generation():
    assert _cv(ops_bin=2, composite=True).aa_ok               # v2: aa stays binary
    assert not _cv(ops_bin=1, composite=True).aa_ok           # v1: aa would force JSON
    assert canvas_surface("http://gw", 128, 64, ("rgb888",), (), ops=OPS35,
                          composite=True).aa_ok               # no binary at all: JSON anyway
    assert not _cv(ops_bin=2, composite=False).aa_ok          # no compositing: no aa either


def test_v2_batch_with_aa_still_posts_binary(gw_calls):
    cv = _cv(ops_bin=2)
    cv.line(0, 0, 9, 9, (40, 40, 40), aa=True)
    cv.circle(5, 5, 3, (50, 50, 50), aa=True)
    assert cv.show()
    assert "/api/canvas/opsb" in [c[1] for c in gw_calls]
    assert canvas_mod._wall("http://gw").last_kind == "opsb"


def test_aquarium_keeps_streaming_with_aa_on_a_v2_wall(monkeypatch):
    import app.gateway as gateway

    class _R:
        status_code = 200

        def json(self):
            return []

    monkeypatch.setattr(gateway, "_request", lambda m, u, p, **kw: _R())
    app = load_app("canvas-aquarium")
    cv = _cv(ops_bin=2, composite=True)
    assert cv.aa_ok
    for _ in range(6):                                        # bubbles spawn over a few frames
        app.fetch_matrix({"fish": "4"}, cv)
    assert canvas_mod._wall("http://gw").last_kind == "opsb"  # aa'd frame, still binary


def test_opsbin_capability_parses_bool_and_legacy_ints():
    """canvas.opsBin is a plain boolean now — True = the FULL format. bool is an int
    subclass in Python, so the parse must check it before int(): int(True) == 1 would
    silently downgrade a modern wall to the legacy subset (aa/transform batches would
    fall back to JSON-over-HTTP). Legacy integers survive for old walls."""
    from app import device
    base = {"features": ["canvas"], "charset": {"common": "A"}}

    def parse(raw):
        return device.from_capabilities(
            dict(base, canvas={"width": 64, "height": 32, "opsBin": raw})).canvas_ops_bin

    assert parse(True) == 2                    # the modern boolean: full format
    assert parse(False) == 0
    assert parse(1) == 1                       # fw 3.5-3.11
    assert parse(2) == 2                       # fw 3.12's transitional number
    assert parse("junk") == 0
    # and the surface follows: a boolean-advertising compositing wall keeps aa on binary
    doc = dict(base, canvas={"width": 64, "height": 32, "opsBin": True, "compositing": True,
                             "ops": ["line", "circle", "show"]})
    from app.canvas import CanvasSurface
    cv = CanvasSurface("http://gw", device.from_capabilities(doc))
    assert cv.ops_bin_v == 2 and cv.can_ops_bin and cv.aa_ok
