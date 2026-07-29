"""Firmware 3.5 draw ops: arc/poly/clip/origin/textbox, stroke thickness, text styles,
and sprite transforms — the Surface wrappers emit exactly the documented shapes, gate on
the wall's advertised op list, and shadow_text collapses to one styled op on a 3.5 wall
while keeping the two-op fallback for older firmware.
"""

from conftest import canvas_surface

OPS35 = ("clear", "pixel", "hline", "vline", "line", "rect", "circle", "ellipse",
         "triangle", "roundrect", "gradient", "polyline", "poly", "arc", "clip",
         "origin", "text", "textbox", "image", "sprite", "scroll", "show")
OPS_OLD = ("clear", "pixel", "hline", "vline", "line", "rect", "circle", "ellipse",
           "triangle", "roundrect", "gradient", "polyline", "text", "image", "sprite",
           "scroll", "show")


def _cv(ops=OPS35, w=128, h=32):
    return canvas_surface("http://gw", w, h, ("rgb888",), (), ops=ops)


def test_has_op_reflects_the_advertised_vocabulary():
    assert _cv().has_op("arc") and _cv().has_op("textbox")
    old = _cv(OPS_OLD)
    assert old.has_op("text") and not old.has_op("arc") and not old.can_text_styles


def test_new_op_wrappers_emit_the_documented_shapes():
    cv = _cv()
    cv.arc(64, 16, 12, 0, 270, (255, 0, 0), t=3, fill=False)
    cv.poly([(0, 0), (10, 0), (5, 8)], (0, 255, 0))
    cv.poly([(0, 0), (10, 0), (5, 8)], (0, 255, 0), fill=False, t=2)
    cv.clip(4, 4, 40, 20)
    cv.clip()
    cv.origin(10, 6)
    cv.origin()
    cv.textbox(2, 2, 60, 28, "HELLO WORLD", (255, 255, 255), size=10,
               align="center", valign="middle")
    assert cv._ops == [
        {"op": "arc", "x": 64, "y": 16, "r": 12, "start": 0, "end": 270,
         "color": [255, 0, 0], "t": 3, "fill": False},
        {"op": "poly", "points": [[0, 0], [10, 0], [5, 8]], "color": [0, 255, 0],
         "fill": True},
        {"op": "poly", "points": [[0, 0], [10, 0], [5, 8]], "color": [0, 255, 0],
         "fill": False, "t": 2},
        {"op": "clip", "x": 4, "y": 4, "w": 40, "h": 20},
        {"op": "clip"},
        {"op": "origin", "x": 10, "y": 6},
        {"op": "origin"},
        {"op": "textbox", "x": 2, "y": 2, "w": 60, "h": 28, "s": "HELLO WORLD",
         "color": [255, 255, 255], "size": 10, "align": "center", "valign": "middle"},
    ]


def test_thickness_is_emitted_only_when_asked():
    cv = _cv()
    cv.rect(0, 0, 10, 10)
    cv.rect(0, 0, 10, 10, t=3)
    cv.line(0, 0, 5, 5, t=2)
    cv.circle(5, 5, 4)
    assert "t" not in cv._ops[0] and cv._ops[1]["t"] == 3
    assert cv._ops[2]["t"] == 2 and "t" not in cv._ops[3]


def test_text_styles_and_sprite_transforms():
    cv = _cv()
    cv.text(0, 0, "HI", aa=True, outline=(0, 0, 0))
    cv.sprite(3, 10, 10, flip="h", rot=180, scale=2)
    cv.sprite(3, 10, 10)
    assert cv._ops[0]["aa"] is True and cv._ops[0]["outline"] == [0, 0, 0]
    assert cv._ops[1] == {"op": "sprite", "i": 3, "x": 10, "y": 10,
                          "flip": "h", "rot": 180, "scale": 2}
    assert cv._ops[2] == {"op": "sprite", "i": 3, "x": 10, "y": 10}


def test_shadow_text_is_one_op_on_a_35_wall_and_two_before():
    new = _cv()
    new.shadow_text(10, 5, "SCORE", (255, 255, 255), 10)
    assert len(new._ops) == 1 and new._ops[0]["shadow"] == [0, 0, 0] \
        and new._ops[0]["s"] == "SCORE"
    old = _cv(OPS_OLD)
    old.shadow_text(10, 5, "SCORE", (255, 255, 255), 10)
    assert len(old._ops) == 2 and old._ops[0]["color"] == [0, 0, 0] \
        and "shadow" not in old._ops[1]


def test_entity_board_draws_arc_gauges_for_banded_entities(gw_calls):
    """A thresholded numeric entity renders as a dial (two arc ops: track + sweep) in
    its icon slot on a 3.5 wall; an unbanded entity keeps its sprite icon."""
    from conftest import load_app
    app = load_app("entity-board")
    ha = [{"entity_id": "sensor.co2", "state": "612",
           "attributes": {"friendly_name": "CO2", "unit_of_measurement": "ppm"}},
          {"entity_id": "sensor.hum", "state": "47",
           "attributes": {"friendly_name": "Hum", "unit_of_measurement": "%"}}]
    cfg = "sensor.co2 | CO2 | 500,1000\nsensor.hum | Hum"
    cv = _cv(w=128, h=64)
    cv.can_sprite = True
    app.fetch_matrix({"config": cfg}, cv, get_ha_states=lambda: ha)
    batch = next(b for p, b in [(c[1], c[2]) for c in gw_calls]
                 if p == "/api/canvas/ops" and isinstance(b, list))
    arcs = [o for o in batch if o["op"] == "arc"]
    sprites = [o for o in batch if o["op"] == "sprite"]
    assert len(arcs) == 2 and len(sprites) == 1        # dial for CO2, icon for Hum
    # the older wall keeps icons for both
    gw_calls.clear()
    cv_old = _cv(OPS_OLD, w=128, h=64)
    cv_old.can_sprite = True
    load_app("entity-board").fetch_matrix({"config": cfg}, cv_old, get_ha_states=lambda: ha)
    batch = next(b for p, b in [(c[1], c[2]) for c in gw_calls]
                 if p == "/api/canvas/ops" and isinstance(b, list))
    assert not [o for o in batch if o["op"] == "arc"]
    assert len([o for o in batch if o["op"] == "sprite"]) == 2


def test_chomper_plays_itself_through_ops(gw_calls):
    """The arcade app emits one ops batch per frame — a filled-arc chomper and poly
    ghost skirts on a 3.5 wall, the circle+triangle fallback before — and the
    simulation advances between frames."""
    from conftest import load_app
    app = load_app("canvas-chomper")
    cv = _cv(w=256, h=64)
    hold = app.fetch_matrix({"speed": "5", "ghosts": "2"}, cv)
    batch = [b for p, b in [(c[1], c[2]) for c in gw_calls]
             if p == "/api/canvas/ops" and isinstance(b, list)][-1]
    assert 0.05 <= hold <= 0.35
    assert any(o["op"] == "arc" and o.get("fill") for o in batch)      # the chomper
    assert any(o["op"] == "rect" for o in batch)                       # maze walls
    score_before = app._state._st["step"]
    app.fetch_matrix({"speed": "5", "ghosts": "2"}, cv)
    assert app._state._st["step"] == score_before + 1                  # the sim ticks
    # pre-3.5 wall: same game, no 3.5 ops
    gw_calls.clear()
    old = load_app("canvas-chomper")
    old.fetch_matrix({"speed": "5", "ghosts": "2"}, _cv(OPS_OLD, w=256, h=64))
    batch = [b for p, b in [(c[1], c[2]) for c in gw_calls]
             if p == "/api/canvas/ops" and isinstance(b, list)][-1]
    assert not [o for o in batch if o["op"] in ("arc", "poly")]
    assert any(o["op"] == "triangle" for o in batch)                   # the mouth bite


def test_canvas_num_reads_raw_string_settings():
    """canvas.num — the shared clamped settings read for matrix apps: raw strings and
    junk degrade to the default, bounds clamp, and int-ness follows the default."""
    from app.canvas import CanvasSurface
    num = CanvasSurface.num
    assert num({"speed": "7"}, "speed", 5, 1, 10) == 7
    assert num({"speed": ""}, "speed", 5, 1, 10) == 5          # blank -> default
    assert num({"speed": "abc"}, "speed", 5, 1, 10) == 5       # junk -> default
    assert num({}, "speed", 5, 1, 10) == 5                     # missing -> default
    assert num({"speed": "99"}, "speed", 5, 1, 10) == 10       # clamped high
    assert num({"speed": "-3"}, "speed", 5, 1, 10) == 1        # clamped low
    assert num({"speed": "7.9"}, "speed", 5, 1, 10) == 7       # int default truncates
    assert num({"d": "4.5"}, "d", 5.0, 3.0, 30.0) == 4.5       # float default stays float
    assert num(None, "speed", 5, 1, 10) == 5                   # no settings at all
