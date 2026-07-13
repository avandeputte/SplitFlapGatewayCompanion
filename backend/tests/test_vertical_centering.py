"""One rule: an app passes the lines it HAS; format_lines owns vertical placement.

Break that rule in either direction and the page is off-centre:

  * pad to `rows` yourself and format_lines has nothing left to centre, so three world
    clocks sit pinned to the top of a five-row wall (the reported bug);
  * centre it yourself and format_lines centres it AGAIN, so the block drifts BELOW the
    middle. Three apps did this — they were centring by hand before format_lines learned
    to, and the beta.5 change silently pushed them down a row.

Both are invisible in a unit test that only checks the text, which is why the guard here
is about WHERE the text lands.
"""
import re
import tempfile
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2] / "apps"


def _runtime(rows, cols, app_id, **settings):
    from app.config import Config
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime

    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp)
    cfg.update({"grid": {"rows": rows, "cols": cols}})
    st = PluginSettings(cfg.data_dir)
    st.set("installed_apps", [app_id])
    for k, v in settings.items():
        st.set(k, v)
    rt = PluginRuntime(cfg, st, APPS, cfg.data_dir / "apps")
    rt.load()
    return rt


def _rows(page, rows, cols):
    return [page[r * cols:(r + 1) * cols] for r in range(rows)]


def _blanks(lines):
    """(blank rows above the content, blank rows below it)."""
    filled = [i for i, l in enumerate(lines) if l.strip()]
    assert filled, "the page is entirely blank"
    return filled[0], len(lines) - 1 - filled[-1]


def _assert_centred(lines):
    """Centred, biasing UP when the spare rows are odd — the conventional choice, and
    what format_lines does (top = pad // 2)."""
    above, below = _blanks(lines)
    assert abs(above - below) <= 1, f"not centred: {above} blank above, {below} below"
    assert above <= below, f"content sits BELOW the middle ({above} above, {below} below)"


# ---------------------------------------------------------------------------
# format_lines itself — the thing every app now delegates to
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("rows,n", [(3, 1), (3, 2), (5, 1), (5, 2), (5, 3), (5, 4), (6, 2)])
def test_format_lines_centres_any_block_on_any_wall(rows, n):
    rt = _runtime(rows, 15, "time")
    page = rt.format_lines(*[f"L{i}" for i in range(n)])
    _assert_centred(_rows(page, rows, 15))


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
def test_three_zones_are_centred_on_a_five_row_wall():
    rt = _runtime(5, 15, "world_clock",
                  plugin_world_clock_world_clock_zones="US/Eastern,Europe/Paris,Asia/Tokyo")
    lines = _rows(rt.get_pages("world_clock")[0], 5, 15)
    assert _blanks(lines) == (1, 1)
    _assert_centred(lines)


def test_one_zone_is_centred_on_a_three_row_wall():
    rt = _runtime(3, 15, "world_clock",
                  plugin_world_clock_world_clock_zones="US/Eastern")
    lines = _rows(rt.get_pages("world_clock")[0], 3, 15)
    assert _blanks(lines) == (1, 1)


def test_a_full_wall_of_zones_still_fills_it():
    rt = _runtime(3, 15, "world_clock",
                  plugin_world_clock_world_clock_zones="US/Eastern,Europe/Paris,Asia/Tokyo")
    lines = _rows(rt.get_pages("world_clock")[0], 3, 15)
    assert all(l.strip() for l in lines), "centring must not eat a row we needed"


# ---------------------------------------------------------------------------
# the static guard — this is the class, not the instance
# ---------------------------------------------------------------------------
def _sources():
    for d in sorted(APPS.iterdir()):
        f = d / "app.py"
        if f.is_file():
            yield d.name, f.read_text("utf-8")


# Padding a page to `rows` leaves format_lines nothing to centre.
_PADS = re.compile(r"""\[\s*['"]{2}\s*\]\s*\*\s*\(?\s*(?:max\(\s*0\s*,\s*)?(?:rows|get_rows\(\))""")
# Centring by hand means format_lines centres it a second time.
_CENTRES = re.compile(r"""\[\s*['"]{2}\s*\]\s*\*\s*top\b""")


def test_no_app_pads_the_page_to_the_row_count():
    offenders = [name for name, src in _sources() if _PADS.search(src)]
    assert not offenders, (
        "these fill the page themselves, so format_lines cannot centre them and their "
        f"content pins to the top of a tall wall: {offenders}")


def test_no_app_centres_itself():
    offenders = [name for name, src in _sources() if _CENTRES.search(src)]
    assert not offenders, (
        "format_lines already centres; centring here too lands the block BELOW the "
        f"middle: {offenders}")
