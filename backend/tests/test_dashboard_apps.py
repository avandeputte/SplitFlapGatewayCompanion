"""The rename/threshold logic behind the two Home Assistant dashboard apps —
``entity-board``'s matrix view (colors the value) and its flap rows (split-flap,
picks a status/threshold color flap). The interesting part is the config parsing and the
green/amber/red banding; drive the pure helpers directly.
"""

from conftest import load_app

_load = load_app


# -- config parsing (shared shape across both apps) -------------------------

def test_parse_config_names_thresholds_and_comments():
    m = _load("entity-board")
    cfg, order = m._parse_config(
        "sensor.co2 | CO2 | <1000,2000\nlight.k | Kitchen\n# a comment\n\nsensor.bad | X | 5")
    assert order == ["sensor.co2", "light.k", "sensor.bad"]     # comments/blanks skipped, order kept
    assert cfg["sensor.co2"] == ("CO2", ("low", 1000.0, 2000.0))
    assert cfg["light.k"] == ("Kitchen", None)
    assert cfg["sensor.bad"] == ("X", None)                     # a lone UNPREFIXED value is no band


def test_parse_config_polarity_grammar():
    m = _load("entity-board")
    assert m._parse_band("60,78") == ("band", 60.0, 78.0)       # comfort band
    assert m._parse_band("2000,1000") == ("band", 1000.0, 2000.0)   # min,max regardless of order
    assert m._parse_band("<1000") == ("low", 1000.0, 1000.0)    # single limit, lower is better
    assert m._parse_band("<500,1000") == ("low", 500.0, 1000.0)
    assert m._parse_band(">20") == ("high", 20.0, 20.0)         # single floor, higher is better
    assert m._parse_band(">80,20") == ("high", 20.0, 80.0)      # numbers sort; the mode says which side
    assert m._parse_band("60") is None and m._parse_band("x,y") is None


def test_entities_dedup_and_cap_at_twelve():
    m = _load("entity-board")
    _, order = m._parse_config("\n".join(f"s.{i}" for i in range(15)) + "\ns.0")
    ids = m._entities(order)
    assert ids[:2] == ["s.0", "s.1"]
    assert len(ids) == 12 and len(set(ids)) == 12              # deduped, capped


# -- entity-board: value -> (text, color flap) -----------------------------

def test_entity_board_threshold_polarity():
    m = _load("entity-board")
    low = ("low", 1000, 2000)                                   # CO2: lower is better
    assert m._value("500", low) == ("500", m._GREEN)
    assert m._value("1500", low) == ("1500", m._AMBER)
    assert m._value("2500", low) == ("2500", m._RED)
    high = ("high", 20, 80)                                     # battery: higher is better
    assert m._value("90", high)[1] == m._GREEN
    assert m._value("50", high)[1] == m._AMBER
    assert m._value("10", high)[1] == m._RED
    band = ("band", 60, 78)                                     # comfort band
    assert m._value("72.4", band)[1] == m._GREEN
    assert m._value("50", band)[1] == m._RED
    assert m._value("85", band)[1] == m._RED
    assert m._value("999", ("low", 1000, 1000))[1] == m._GREEN  # single limit: no amber zone
    assert m._value("1001", ("low", 1000, 1000))[1] == m._RED


def test_entity_board_on_off_and_dead():
    m = _load("entity-board")
    assert m._value("on", None)[1] == m._GREEN                  # on -> green flap
    assert m._value("off", None) == ("Off", "")                 # off -> no flap
    assert m._value("unavailable", None) == ("--", "")


def test_entity_board_row_clamps_to_columns():
    m = _load("entity-board")
    row = m._row("A Very Long Entity Name", "1500", m._AMBER, 12)
    assert len(row) == 12                                       # never overflows the wall width


# -- entity-board's matrix value classifier: value -> (text, RGB) ------------

def _cp(s):   # stands in for the injected canvas.cp (CP1252 filter)
    return str(s).encode("cp1252", "ignore").decode("cp1252")


def test_matrix_value_bands_and_unit():
    m = _load("entity-board")
    txt, col = m._mx_value("2500", {"unit_of_measurement": "ppm"}, ("low", 1000, 2000), _cp)
    assert col == m._C_RED and txt == "2500"                    # long unit dropped, above bad -> red
    assert m._mx_value("640", {}, ("low", 1000, 2000), _cp)[1] == m._C_GREEN
    assert m._mx_value("50", {}, ("high", 20, 80), _cp)[1] == m._C_AMBER   # polarity carries over
    assert m._mx_value("70", {}, ("band", 60, 78), _cp)[1] == m._C_GREEN
    assert m._mx_value("on", {}, None, _cp)[1] == m._C_GREEN    # on state -> green
