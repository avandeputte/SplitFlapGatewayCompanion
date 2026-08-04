"""Firmware 3.8 compositing (canvas.compositing): per-color alpha, blend modes, AA. The
companion exposes ``blend()``, an ``aa`` flag on the smooth-stroke ops, and 4-component
rgba colors — gated on ``can_composite`` so older walls keep the plain look. Batch blend
rides the binary stream (opcode 0x14); per-color alpha is JSON-only, so a batch that uses
it falls back to JSON rather than silently dropping the alpha.
"""

from conftest import canvas_surface, load_app
from test_canvas_ops35 import OPS35
from app.canvas import encode_ops_bin


def _cv(composite=True, w=128, h=64):
    return canvas_surface("http://gw", w, h, ("rgb888",), (), ops=OPS35, ops_bin=1,
                          composite=composite, sprite=True)


def test_capability_and_wrappers():
    cv = _cv()
    assert cv.can_composite
    cv.blend("add")
    cv.circle(1, 1, 2, (9, 9, 9), fill=True, aa=True)
    cv.line(0, 0, 5, 5, (1, 1, 1), aa=True)
    cv.poly([(0, 0), (4, 0), (2, 3)], (5, 6, 7, 80))       # rgba: per-color alpha
    assert cv._ops[0] == {"op": "blend", "mode": "add"}
    assert cv._ops[1].get("aa") is True and cv._ops[2].get("aa") is True
    assert cv._ops[3]["color"] == [5, 6, 7, 80]            # alpha preserved through _rgb
    assert not canvas_surface("http://gw", 128, 64, ("rgb888",), (), ops=OPS35).can_composite


def test_blend_and_rgba_encode_binary():
    assert encode_ops_bin([{"op": "blend", "mode": "over"}]) == b"\x14\x00"
    assert encode_ops_bin([{"op": "blend", "mode": "add"}]) == b"\x14\x01"
    assert encode_ops_bin([{"op": "blend", "mode": "screen"}]) == b"\x14\x03"
    # a 4-component color rides as batch alpha 0x15 + the plain rgb op
    assert encode_ops_bin([{"op": "circle", "x": 0, "y": 0, "r": 2,
                            "color": [1, 2, 3, 4], "fill": True}])[:2] == b"\x15\x04"


def test_aquarium_adds_glow_only_on_a_compositing_wall(monkeypatch):
    import app.gateway as gateway

    def batch(cv):
        seen = []

        class _R:
            status_code = 200

        monkeypatch.setattr(gateway, "_request",
                            lambda m, u, p, *, timeout, **kw: (seen.append((p, kw.get("json"))) or _R()))
        app = load_app("canvas-aquarium")
        for _ in range(6):
            app.fetch_canvas({}, cv)
        return next(b for p, b in reversed(seen) if p == "/api/canvas/ops" and isinstance(b, list))

    new = batch(_cv(composite=True))
    assert any(o.get("op") == "blend" for o in new)         # godrays / bubble glow
    assert any(isinstance(o.get("color"), list) and len(o["color"]) == 4 for o in new)
    old = batch(_cv(composite=False))
    assert not any(o.get("op") == "blend" for o in old)     # plain look on an older wall
    assert not any(isinstance(o.get("color"), list) and len(o["color"]) == 4 for o in old)


def test_chomper_power_glow_still_streams_binary(gw_calls):
    app = load_app("canvas-chomper")
    cv = _cv(composite=True)
    for _ in range(20):                                     # attract: power pellets present
        app.fetch_canvas({"speed": "6"}, cv)
    paths = [c[1] for c in gw_calls]
    assert "/api/canvas/opsb" in paths and "/api/canvas/ops" not in paths
