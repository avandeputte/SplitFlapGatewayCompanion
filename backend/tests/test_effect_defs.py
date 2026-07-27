"""Self-describing effects (firmware 3.4 ``effectDefs``): the wall names each effect and
exactly the params it consumes (key/type/range/default/label), and the companion builds
the per-effect apps from that description — settings fields, display names, and the wire
body all follow the def, so a future effect or option needs no companion change. Older
walls keep the fixed speed/hue/density template path.
"""

import tempfile

from app import device
from conftest import make_runtime
from conftest import CANVAS_DOC

DEFS_DOC = dict(CANVAS_DOC) | {
    "features": CANVAS_DOC["features"] + ["audio", "effectDefs"],
    "effects": ["plasma", "fire", "life"],
    "effectParams": ["hue", "density", "audio"],
    "effectDefs": [
        {"id": "plasma", "name": "Plasma", "params": [
            {"key": "speed", "type": "int", "min": 1, "max": 10, "default": 5, "label": "Speed"},
            {"key": "hue", "type": "int", "min": 0, "max": 255, "label": "Hue"},
            {"key": "audio", "type": "bool", "default": False, "label": "Audio reactive"},
        ]},
        {"id": "fire", "name": "Fire", "params": [
            {"key": "audio", "type": "bool", "default": False, "label": "Audio reactive"},
        ]},
        {"id": "life", "name": "Game of Life", "params": [
            {"key": "speed", "type": "int", "min": 1, "max": 10, "default": 5, "label": "Speed"},
            {"key": "density", "type": "int", "min": 1, "max": 100, "label": "Density"},
            {"key": "shade", "type": "gradient", "label": "Shade"},   # a future, unknown type
        ]},
    ],
}


def _rt(doc):
    return make_runtime(tmp_path=tempfile.mkdtemp(),
                        installed=["effect_plasma", "effect_fire", "effect_life"],
                        caps=device.from_capabilities(doc))


class _Cap:
    """The two attributes the effects app reads, plus a capture of the effect() call."""

    def __init__(self, defs):
        self.effects = tuple(d["id"] for d in defs) or ("plasma",)
        self.effect_defs = tuple(defs)
        self.effect_params = ("hue", "density", "audio")
        self.calls = []

    def effect(self, name, speed=5, hue=None, density=None, params=None):
        self.calls.append({"name": name, "speed": speed, "hue": hue,
                           "density": density, "params": params})
        return True


def test_effect_defs_are_parsed_and_malformed_entries_dropped():
    caps = device.from_capabilities(dict(DEFS_DOC) | {
        "effectDefs": DEFS_DOC["effectDefs"] + ["junk", {"id": 7, "params": []},
                                                {"id": "x"}]})
    assert [d["id"] for d in caps.effect_defs] == ["plasma", "fire", "life"]
    assert device.from_capabilities(CANVAS_DOC).effect_defs == ()


def test_per_effect_settings_come_from_the_wall_description():
    rt = _rt(DEFS_DOC)
    # Fire consumes ONLY the audio knob — no speed/hue/density fields appear.
    fire = rt.manifest("effect_fire")
    assert fire["name"] == "Fire"
    assert [(f["key"], f["type"]) for f in fire["settings"]] == [("audio", "toggle")]
    assert fire["settings"][0]["label"] == "Audio reactive"
    # Life gets the declared ints with their ranges; the unknown "gradient" type
    # degrades to no field rather than an error.
    life = rt.manifest("effect_life")
    fields = {f["key"]: f for f in life["settings"]}
    assert set(fields) == {"speed", "density"}
    assert fields["speed"]["type"] == "number" and fields["speed"]["min"] == "1" \
        and fields["speed"]["max"] == "10" and fields["speed"]["default"] == "5"
    assert fields["density"]["min"] == "1" and fields["density"]["max"] == "100" \
        and fields["density"]["default"] == ""     # no default: blank keeps the on-device look
    assert life["name"] == "Game of Life"          # display name from the def


def test_an_older_wall_keeps_the_template_knobs():
    rt = _rt(CANVAS_DOC)                           # no effectDefs advertised
    keys = [f["key"] for f in rt.manifest("effect_plasma")["settings"]]
    assert keys == ["speed", "hue", "density"]


def test_the_app_serializes_exactly_the_declared_params():
    from conftest import load_app
    app = load_app("effects")
    cap = _Cap(DEFS_DOC["effectDefs"])
    # Plasma: bool set -> explicit True; int clamped into its declared range; the
    # blank speed is omitted (the effect keeps its current pace).
    app.fetch_matrix({"effect": "plasma", "audio": "yes", "hue": "999", "speed": ""}, cap)
    assert cap.calls[-1]["params"] == {"audio": True, "hue": 255}
    # Fire consumes only audio — a stray speed setting stays off the wire; "no" is
    # sent explicitly so a def with default:true could still be turned off.
    app.fetch_matrix({"effect": "fire", "speed": "9", "audio": "no"}, cap)
    assert cap.calls[-1]["params"] == {"audio": False}
    # The unknown "gradient" param never reaches the wire.
    app.fetch_matrix({"effect": "life", "density": "50", "shade": "3"}, cap)
    assert cap.calls[-1]["params"] == {"density": 50}


def test_without_defs_the_legacy_call_shape_is_unchanged():
    from conftest import load_app
    app = load_app("effects")
    cap = _Cap([])
    cap.effects = ("plasma",)
    app.fetch_matrix({"effect": "plasma", "speed": "7", "hue": "40"}, cap)
    c = cap.calls[-1]
    assert c["params"] is None and c["speed"] == 7 and c["hue"] == 40


def test_def_driven_wire_body_has_no_implicit_speed(monkeypatch):
    from conftest import canvas_surface
    import app.gateway as gateway
    calls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(gateway, "_request",
                        lambda method, url, path, *, timeout, **kw:
                        (calls.append((method, path, kw.get("json"))) or _Resp()))
    cv = canvas_surface("http://gw", 128, 32, ("rgb888",), ("fire",))
    assert cv.effect("fire", params={"audio": True})
    method, path, body = calls[-1]
    assert path == "/api/canvas/effect" and body == {"type": "fire", "audio": True}
