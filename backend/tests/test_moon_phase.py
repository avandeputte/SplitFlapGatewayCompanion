"""Moon phase spells the day unit out where there's room ('5 Days'), abbreviating to
'5D' only on a wall too narrow to fit it — a wide Matrix wall shouldn't abbreviate."""
from conftest import load_app as _mod


def _day_lines(cols):
    app = _mod("moon-phase")
    pages = app.fetch({}, lambda *l: "|".join(l), lambda: 5, lambda: cols)
    return [l for l in pages[0].split("|") if " in " in l]


def test_wide_wall_spells_the_day_unit_out():
    lines = _day_lines(40)
    assert len(lines) == 2 and all("Days" in l for l in lines)


def test_narrow_wall_abbreviates_the_day_unit():
    lines = _day_lines(10)
    assert len(lines) == 2
    assert all("Days" not in l and l.rstrip().endswith("D") for l in lines)
