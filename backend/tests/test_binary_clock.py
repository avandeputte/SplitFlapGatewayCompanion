"""The binary clock: BCD bit math, layout widths, the label row, and the rule
that seconds only tick where the wall's motion allows (caps.instant)."""
from types import SimpleNamespace

from conftest import load_app

DRAWN = SimpleNamespace(instant=True)      # a Matrix Portal-ish wall


def _mod():
    return load_app("binary-clock")


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


def test_decimal_digits_align_under_the_binary_columns():
    """The plain-language time is the answer key under the bits — and each digit
    sits directly under its binary column. That means the decimal row has the
    SAME geometry as a bit row (two-digit H/M/S groups, colon in the gap), so it
    is exactly as wide and per-line centering drops the digits onto the columns."""
    import re
    m, lines = _fetch(5, 15)
    assert len(lines) == 5
    dec = lines[4]
    assert len(dec) == len(lines[0]), (dec, lines[0])   # same width -> aligned
    assert re.fullmatch(r"\d\d: \d\d: \d\d", dec), dec   # colon in the 2-wide gap
    # a narrow wall uses single gaps; the colon fills it and it still lines up
    m, lines = _fetch(5, 8)
    assert re.fullmatch(r"\d\d:\d\d:\d\d", lines[4]), lines[4]
    assert len(lines[4]) == len(lines[0])
    # no seconds -> two groups, still aligned
    m, lines = _fetch(5, 15, show_seconds="false")
    assert re.fullmatch(r"\d\d: \d\d", lines[4]), lines[4]
    assert len(lines[4]) == len(lines[0])


def test_units_row_sits_above_the_decimal_on_a_six_row_wall():
    import re
    m, lines = _fetch(6, 15)
    assert len(lines) == 6
    # same width as the bit rows — per-line centering keeps H/M/S under the columns
    assert len(lines[4]) == len(lines[0])
    assert lines[4].replace(' ', '') == 'HMS'
    assert re.fullmatch(r"\d\d: \d\d: \d\d", lines[5]), lines[5]


def test_zero_can_be_a_blank_flap():
    m, lines = _fetch(4, 15, zero_color='off')
    # the tens-of-hours column is never >= 8, so a zero bit always exists
    assert m.TILES['off'] in ''.join(lines)
