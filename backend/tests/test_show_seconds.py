"""Seconds on the clock apps: opt-in, drawn walls (caps.indexed) only, and only
when the geometry has room. A physical module takes seconds per flip, so a
ticking seconds field would keep the wall permanently mid-clatter."""
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

APPS = Path(__file__).resolve().parents[2] / "apps"
DRAWN = SimpleNamespace(indexed=True)
REEL = SimpleNamespace(indexed=False)


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"_{name}", APPS / name / "app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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
