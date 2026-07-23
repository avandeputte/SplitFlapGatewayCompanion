"""One rule: an app passes the lines it HAS; format_lines owns vertical placement.

Break that rule in either direction and the page is off-center:

  * pad to `rows` yourself and format_lines has nothing left to center, so three world
    clocks sit pinned to the top of a five-row wall (the reported bug);
  * center it yourself and format_lines centers it AGAIN, so the block drifts BELOW the
    middle. Three apps did this — they were centering by hand before format_lines learned
    to, and the beta.5 change silently pushed them down a row.

Both are invisible in a unit test that only checks the text, which is why the guard here
is about WHERE the text lands.
"""
import re
from pathlib import Path

import pytest

from conftest import APPS_DIR as APPS
from conftest import make_runtime


def _runtime(rows, cols, app_id, **settings):
    return make_runtime(installed=[app_id], rows=rows, cols=cols, settings=settings)


def _rows(page, rows, cols):
    return [page[r * cols:(r + 1) * cols] for r in range(rows)]


def _blanks(lines):
    """(blank rows above the content, blank rows below it)."""
    filled = [i for i, l in enumerate(lines) if l.strip()]
    assert filled, "the page is entirely blank"
    return filled[0], len(lines) - 1 - filled[-1]


def _assert_centered(lines):
    """Centered, biasing UP when the spare rows are odd — the conventional choice, and
    what format_lines does (top = pad // 2)."""
    above, below = _blanks(lines)
    assert abs(above - below) <= 1, f"not centered: {above} blank above, {below} below"
    assert above <= below, f"content sits BELOW the middle ({above} above, {below} below)"


# ---------------------------------------------------------------------------
# format_lines itself — the thing every app now delegates to
# ---------------------------------------------------------------------------
# NOTE (audit E4): these format_lines-centers-the-block assertions overlap with
# test_grid_and_shadowing.py's vertical-centering checks. Deliberately left in both
# places for now — that file guards the send pipeline end to end, this one guards
# the app-facing format_lines contract; merge them when one file owns centering.
@pytest.mark.parametrize("rows,n", [(3, 1), (3, 2), (5, 1), (5, 2), (5, 3), (5, 4), (6, 2)])
def test_format_lines_centers_any_block_on_any_wall(rows, n):
    rt = _runtime(rows, 15, "time")
    page = rt.format_lines(*[f"L{i}" for i in range(n)])
    _assert_centered(_rows(page, rows, 15))


def test_one_line_on_a_three_row_wall_is_in_the_middle():
    """The simplest case, and the one asked for by name."""
    rt = _runtime(3, 15, "time")
    lines = _rows(rt.format_lines("HELLO"), 3, 15)
    assert not lines[0].strip() and lines[1].strip() == "HELLO" and not lines[2].strip()


def test_a_full_page_is_untouched():
    """The three-row wall everyone has, with a three-line app: byte-for-byte as before."""
    rt = _runtime(3, 15, "time")
    assert rt.format_lines("A", "B", "C") == "A".center(15) + "B".center(15) + "C".center(15)


# ---------------------------------------------------------------------------
# the reported bug, end to end (world_clock is offline, so this is deterministic)
# ---------------------------------------------------------------------------
def test_three_zones_are_centered_on_a_five_row_wall():
    rt = _runtime(5, 15, "world_clock",
                  plugin_world_clock_world_clock_zones="US/Eastern,Europe/Paris,Asia/Tokyo")
    lines = _rows(rt.get_pages("world_clock")[0], 5, 15)
    assert _blanks(lines) == (1, 1)
    _assert_centered(lines)


def test_one_zone_is_centered_on_a_three_row_wall():
    rt = _runtime(3, 15, "world_clock",
                  plugin_world_clock_world_clock_zones="US/Eastern")
    lines = _rows(rt.get_pages("world_clock")[0], 3, 15)
    assert _blanks(lines) == (1, 1)


def test_a_full_wall_of_zones_still_fills_it():
    rt = _runtime(3, 15, "world_clock",
                  plugin_world_clock_world_clock_zones="US/Eastern,Europe/Paris,Asia/Tokyo")
    lines = _rows(rt.get_pages("world_clock")[0], 3, 15)
    assert all(l.strip() for l in lines), "centering must not eat a row we needed"


# ---------------------------------------------------------------------------
# the static guard — this is the class, not the instance
# ---------------------------------------------------------------------------
def _sources():
    for d in sorted(APPS.iterdir()):
        f = d / "app.py"
        if f.is_file():
            yield d.name, f.read_text("utf-8")


# Padding a page to `rows` leaves format_lines nothing to center.
_PADS = re.compile(r"""\[\s*['"]{2}\s*\]\s*\*\s*\(?\s*(?:max\(\s*0\s*,\s*)?(?:rows|get_rows\(\))""")
# Centering by hand means format_lines centers it a second time.
_CENTERS = re.compile(r"""\[\s*['"]{2}\s*\]\s*\*\s*top\b""")


def test_no_app_pads_the_page_to_the_row_count():
    offenders = [name for name, src in _sources() if _PADS.search(src)]
    assert not offenders, (
        "these fill the page themselves, so format_lines cannot center them and their "
        f"content pins to the top of a tall wall: {offenders}")


def test_no_app_centers_itself():
    offenders = [name for name, src in _sources() if _CENTERS.search(src)]
    assert not offenders, (
        "format_lines already centers; centering here too lands the block BELOW the "
        f"middle: {offenders}")


# ---------------------------------------------------------------------------
# the opt-out: an app may declare where its block sits
# ---------------------------------------------------------------------------
# Centering is right for almost every app, but it is a POLICY, and an app that builds its
# own layout (a fixed header, hand-placed rows) needs to be able to say so — otherwise its
# placement gets centered a second time and drifts. "vertical_align": "top" places lines
# byte-for-byte where the app put them, so it is the opt-out for apps that pad themselves.
def test_vertical_align_defaults_to_center_when_the_manifest_says_nothing():
    """Additive by construction: an app whose manifest says nothing gets the default,
    center — the key is pure opt-in."""
    rt = _runtime(5, 15, "time")
    assert rt.vertical_align("time") == "center"
    assert rt.vertical_align(None) == "center"
    assert rt.vertical_align("no-such-app") == "center"


@pytest.mark.parametrize("align,expected", [
    ("center", (1, 1)),      # blank above, blank below
    ("top", (0, 2)),         # block at row 0, spare rows fall to the bottom
    ("bottom", (2, 0)),
])
def test_vertical_align_places_the_block(align, expected):
    rt = _runtime(5, 15, "time")
    page = rt.format_lines("A", "B", "C", align=align)
    assert _blanks(_rows(page, 5, 15)) == expected


def test_top_is_byte_for_byte_splitflap_os():
    """The escape hatch has to be the ORIGINAL behavior, or it is not an escape hatch."""
    rt = _runtime(5, 15, "time")
    page = rt.format_lines("A", "B", "C", align="top")
    assert page == "A".center(15) + "B".center(15) + "C".center(15) + " " * 30


def test_an_app_that_declares_top_can_place_its_own_rows(tmp_path):
    """The point of the opt-out: emit blanks where you want them and they are respected,
    rather than being re-centered into somewhere else."""
    rt = _runtime(5, 15, "time")
    page = rt.format_lines("", "HEADER", "", "BODY", align="top")
    lines = [l.strip() for l in _rows(page, 5, 15)]
    assert lines == ["", "HEADER", "", "BODY", ""]


def test_a_typo_in_the_manifest_does_not_take_the_wall_down(tmp_path, caplog):
    rt = _runtime(5, 15, "time")
    rt._registry["time"] = dict(rt._registry["time"], vertical_align="middle")
    assert rt.vertical_align("time") == "center"      # falls back rather than raising


def test_the_app_gets_a_format_lines_bound_to_its_alignment():
    """Apps call format_lines(*lines) — a fixed public signature. The alignment has to
    reach it WITHOUT changing that signature, or drop-in compatibility
    (a hard requirement, see COMPATIBILITY.md) is gone."""
    src = (Path(__file__).resolve().parents[1] / "app" / "plugins.py").read_text("utf-8")
    assert "functools.partial(self.format_lines," in src
    assert "align=self.vertical_align(app_id)" in src
