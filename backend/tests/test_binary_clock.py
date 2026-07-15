"""The binary clock: BCD bit math, layout widths, the label row, and the rule
that seconds only tick where the wall's motion allows (caps.instant)."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

APPS = Path(__file__).resolve().parents[2] / "apps"
DRAWN = SimpleNamespace(instant=True)      # a Matrix Portal-ish wall


def _mod():
    spec = importlib.util.spec_from_file_location(
        "_binary_clock", APPS / "binary-clock" / "app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fetch(rows, cols, caps=DRAWN, **settings):
    m = _mod()
    pages = m.fetch(settings, lambda *lines, **kw: list(lines),
                    lambda: rows, lambda: cols, caps=caps)
    assert len(pages) == 1
    return m, pages[0]


def test_bcd_bit_math():
    m = _mod()
    # digits 1 and 9, read down the weight rows 8-4-2-1: 1 = 0001, 9 = 1001
    assert m._bits_rows([1, 9], '1', '0', ' ') == ['01', '00', '00', '11']


def test_four_rows_of_tiles_with_seconds():
    m, lines = _fetch(4, 15)
    assert len(lines) == 4
    # six digit columns in three groups, wide gaps on a 15-wide wall: 10 flaps
    assert all(len(l) == 10 for l in lines)
    assert set(''.join(lines)) <= set(m.TILES.values()) | {' '}


def test_seconds_drop_when_turned_off():
    m, lines = _fetch(4, 15, show_seconds='false')
    assert all(len(l) == 6 for l in lines)      # HH MM only


def test_seconds_drop_on_a_physical_wall():
    # caps=None is the pessimistic default: a real reel that takes seconds per
    # flip — a ticking seconds column would keep it permanently mid-clatter.
    m, lines = _fetch(4, 15, caps=None)
    assert all(len(l) == 6 for l in lines)


def test_tight_wall_uses_single_gaps():
    m, lines = _fetch(4, 8)
    assert all(len(l) == 8 for l in lines)      # 6 tiles + 2 single gaps


def test_units_row_appears_on_tall_walls_and_stays_aligned():
    m, lines = _fetch(5, 15)
    assert len(lines) == 5
    # same width as the bit rows — per-line centring keeps H/M/S under the columns
    assert len(lines[4]) == len(lines[0])
    assert lines[4].replace(' ', '') == 'HMS'


def test_zero_can_be_a_blank_flap():
    m, lines = _fetch(4, 15, zero_color='off')
    # the tens-of-hours column is never >= 8, so a zero bit always exists
    assert m.TILES['off'] in ''.join(lines)
