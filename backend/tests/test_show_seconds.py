"""Seconds on the clock apps: opt-in, walls that say sub-second updates are honest (caps.instant), and only
when the geometry has room. A physical module takes seconds per flip, so a
ticking seconds field would keep the wall permanently mid-clatter."""
import re
from types import SimpleNamespace

from conftest import APPS_DIR as APPS
from conftest import load_app as _mod

DRAWN = SimpleNamespace(instant=True)
REEL = SimpleNamespace(instant=False)


def _first_page(name, rows, cols, caps, **settings):
    m = _mod(name)
    pages = m.fetch(settings, lambda *lines, **kw: list(lines),
                    lambda: rows, lambda: cols, caps=caps)
    return pages[0]


# ---- time --------------------------------------------------------------
def test_time_shows_seconds_on_a_drawn_wall():
    lines = _first_page("time", 1, 15, DRAWN, show_seconds="true", time_format="24hr")
    assert re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", lines[0])


def test_time_ignores_the_toggle_on_a_reel():
    for caps in (REEL, None):
        lines = _first_page("time", 1, 15, caps, show_seconds="true", time_format="24hr")
        assert re.fullmatch(r"\d{1,2}:\d{2}", lines[0])


def test_time_drops_seconds_when_they_do_not_fit():
    # 12h + seconds + AM/PM is 11 chars; a 8-wide wall can't take it
    lines = _first_page("time", 1, 8, DRAWN, show_seconds="true", time_format="12hr")
    assert len(lines[0]) <= 8 and lines[0].count(":") == 1


def test_time_defaults_to_no_seconds():
    lines = _first_page("time", 1, 15, DRAWN, time_format="24hr")
    assert re.fullmatch(r"\d{1,2}:\d{2}", lines[0])


# ---- countdown ----------------------------------------------------------
def _countdown(caps, cols=15, **settings):
    from datetime import datetime, timedelta
    target = (datetime.now() + timedelta(days=2, hours=3)).replace(microsecond=0)
    settings.setdefault("countdown_event", "TEST")
    settings.setdefault("countdown_target", target.strftime("%Y-%m-%dT%H:%M:%S"))
    return _first_page("countdown", 2, cols, caps, **settings)


def test_countdown_seconds_are_opt_in_on_a_drawn_wall():
    lines = _countdown(DRAWN, show_seconds="true")
    assert re.search(r"\d+S\b", lines[1]), lines


def test_countdown_has_no_seconds_by_default():
    lines = _countdown(DRAWN)
    assert not re.search(r"\d+S\b", lines[1]), lines


def test_countdown_ignores_the_toggle_on_a_reel():
    lines = _countdown(REEL, show_seconds="true")
    assert not re.search(r"\d+S\b", lines[1]), lines


def test_countdown_seconds_are_space_padded_for_a_stable_width():
    # 10S -> 9S used to shorten the line and shift everything left of it by a
    # flap; a two-wide seconds field keeps the width constant between ticks.
    # Only seconds get this — they tick every second; H and M stay natural.
    lines = _countdown(DRAWN, show_seconds="true")
    assert re.fullmatch(r"\d+D \d+H \d+M [ \d]\dS", lines[1]), lines


def _countdown_tall(caps, rows=5, cols=15, **settings):
    from datetime import datetime, timedelta
    target = (datetime.now() + timedelta(days=2, hours=3)).replace(microsecond=0)
    settings.setdefault("countdown_event", "TEST")
    settings.setdefault("countdown_target", target.strftime("%Y-%m-%dT%H:%M:%S"))
    return _first_page("countdown", rows, cols, caps, **settings)


def test_countdown_five_rows_get_a_bar_per_unit():
    lines = _countdown_tall(DRAWN, show_seconds="true")
    assert len(lines) == 5 and lines[0] == "TEST"
    bar_tiles = set("\U0001f7e6\U0001f7e9\U0001f7e8\U0001f7e5⬛")
    for line in lines[1:]:
        assert len(line) == 15, line                      # constant width
        assert re.match(r"[ \d]{2}\d[DHMS] ", line), line  # right-aligned value
        assert set(line[5:]) <= bar_tiles, line            # then only bar cells


def test_countdown_five_rows_drop_the_seconds_row_on_a_reel():
    lines = _countdown_tall(REEL, show_seconds="true")
    assert len(lines) == 4                                # event + D/H/M
    assert not any(re.match(r"[ \d]{2}\d+S ", l) for l in lines)


# ---- the toggle itself ---------------------------------------------------
def test_every_manifest_toggle_declares_its_options():
    """The settings dialog renders a toggle as a segmented control built FROM
    its options — a toggle without options is an empty, unclickable control.
    Regression: show_seconds shipped as a bare boolean and nobody could turn
    it on (which read as 'capability detection is broken')."""
    import json
    for mf in sorted(APPS.glob("*/manifest.json")):
        m = json.loads(mf.read_text("utf-8"))
        for s in m.get("settings") or []:
            if isinstance(s, dict) and s.get("type") == "toggle":
                assert s.get("options"), f"{mf.parent.name}: toggle '{s.get('key')}' has no options"
