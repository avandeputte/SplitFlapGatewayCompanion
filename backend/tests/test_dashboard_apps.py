"""The rename/threshold logic behind the two Home Assistant dashboard apps —
``canvas-dashboard`` (Matrix panel, colors the value) and ``entity-board`` (split-flap,
picks a status/threshold color flap). The interesting part is the config parsing and the
green/amber/red banding; drive the pure helpers directly.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(app_id):
    p = ROOT / "apps" / app_id / "app.py"
    spec = importlib.util.spec_from_file_location(f"_app_{app_id.replace('-', '_')}", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# -- config parsing (shared shape across both apps) -------------------------

def test_parse_config_names_thresholds_and_comments():
    m = _load("entity-board")
    cfg, order = m._parse_config(
        "sensor.co2 | CO2 | 1000,2000\nlight.k | Kitchen\n# a comment\n\nsensor.bad | X | 5")
    assert order == ["sensor.co2", "light.k", "sensor.bad"]     # comments/blanks skipped, order kept
    assert cfg["sensor.co2"] == ("CO2", (1000.0, 2000.0))
    assert cfg["light.k"] == ("Kitchen", None)
    assert cfg["sensor.bad"] == ("X", None)                     # a lone threshold value is ignored


def test_parse_config_normalizes_reversed_thresholds():
    m = _load("entity-board")
    cfg, _ = m._parse_config("s.x | X | 2000,1000")
    assert cfg["s.x"][1] == (1000.0, 2000.0)                    # min,max regardless of order


def test_entities_dedup_and_cap_at_twelve():
    m = _load("entity-board")
    _, order = m._parse_config("\n".join(f"s.{i}" for i in range(15)) + "\ns.0")
    ids = m._entities(order)
    assert ids[:2] == ["s.0", "s.1"]
    assert len(ids) == 12 and len(set(ids)) == 12              # deduped, capped


# -- entity-board: value -> (text, color flap) -----------------------------

def test_entity_board_threshold_bands():
    m = _load("entity-board")
    assert m._value("500", (1000, 2000)) == ("500", m._GREEN)   # below low
    assert m._value("1500", (1000, 2000)) == ("1500", m._AMBER)  # between
    assert m._value("2500", (1000, 2000)) == ("2500", m._RED)   # above high


def test_entity_board_on_off_and_dead():
    m = _load("entity-board")
    assert m._value("on", None)[1] == m._GREEN                  # on -> green flap
    assert m._value("off", None) == ("Off", "")                 # off -> no flap
    assert m._value("unavailable", None) == ("--", "")


def test_entity_board_row_clamps_to_columns():
    m = _load("entity-board")
    row = m._row("A Very Long Entity Name", "1500", m._AMBER, 12)
    assert len(row) == 12                                       # never overflows the wall width


# -- canvas-dashboard: value -> (text, RGB) ---------------------------------

def _cp(s):   # stands in for the injected canvas.cp (CP1252 filter)
    return str(s).encode("cp1252", "ignore").decode("cp1252")


def test_canvas_dashboard_bands_and_unit():
    m = _load("canvas-dashboard")
    txt, col = m._value("2500", {"unit_of_measurement": "ppm"}, (1000, 2000), _cp)
    assert col == m._RED and txt == "2500"                      # long unit dropped, above high -> red
    assert m._value("640", {}, (1000, 2000), _cp)[1] == m._GREEN   # below low -> green
    assert m._value("on", {}, None, _cp)[1] == m._GREEN         # on state -> green
