"""Smart cadence: a canvas app returns the seconds until its next VISIBLE change, so the engine
sleeps until then instead of repainting an identical frame on a fast timer (which still advances
the keyframe counter and forces a periodic full frame over the wire). These pin the returns of the
time-based apps — the animated ones (aquarium, weather-sky, the countdown sweep) keep their fast
frame rate and aren't covered here.
"""

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from app import canvas as canvas_mod
from conftest import canvas_surface

ROOT = Path(__file__).resolve().parents[2]


def _load(app_id):
    p = ROOT / "apps" / app_id / "app.py"
    spec = importlib.util.spec_from_file_location(f"_c_{app_id.replace('-', '_')}", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def surface(monkeypatch):
    """A real CanvasSurface whose transport is stubbed — font/blank/vgrad/frame all behave, but
    frame() never touches the network, so an app renders exactly as it would on a wall."""
    import app.gateway as gateway
    monkeypatch.setattr(gateway, "_request",
                        lambda *a, **k: type("R", (), {"status_code": 200, "json": lambda s: {}})())
    return canvas_surface("http://gw", 256, 64, ("rgb888", "qoi"), ())


def test_world_clock_holds_until_the_next_minute(surface):
    """HH:MM per zone changes only on the minute — the hold is the time to the next boundary,
    never the old flat 1s that repainted ~60×/min."""
    m = _load("world_clock")           # dual-view: canvas branch draws the lit rows (was canvas-world)
    hold = m.fetch_matrix({"world_clock_zones": "America/New_York, Europe/London"}, surface)
    now = datetime.now()
    expected = 60.0 - now.second - now.microsecond / 1_000_000.0
    assert 1.0 <= hold <= 60.0
    assert hold == pytest.approx(max(1.0, expected), abs=0.5)


def test_date_card_holds_until_around_midnight(surface):
    """The card only changes when the day rolls, so it sleeps toward midnight (capped at an hour),
    not the old 2s repaint."""
    m = _load("date")           # dual-surface: the Date Card is date's matrix branch
    hold = m.fetch_matrix({}, surface)
    now = datetime.now()
    secs_to_midnight = ((24 - now.hour) * 3600 - now.minute * 60 - now.second)
    assert 1.0 <= hold <= 3600.0
    assert hold == pytest.approx(min(3600.0, secs_to_midnight), abs=2.0)


def test_countdown_canvas_cadence(surface):
    """The dual-view Countdown's canvas bars: seconds off -> a gentle 1s repaint; seconds on ->
    the ~5fps sweep. Empty settings still draw slot 1's default New Year countdown (the flap
    default carries over to the panel), so this is never a static prompt."""
    m = _load("countdown")
    m.fetch_matrix.__dict__.pop("_state", None)
    slow = m.fetch_matrix({}, surface, caps=None)
    assert slow == 1.0
    fast = m.fetch_matrix({"show_seconds": "yes"}, surface, caps=None)
    assert fast == 0.2
