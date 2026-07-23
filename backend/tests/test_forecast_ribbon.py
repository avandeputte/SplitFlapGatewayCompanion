"""Forecast Ribbon — the day's temperature as a color bar chart.

A sibling of art-clock: a picture rather than a page of numbers. Two things make it work,
and both are easy to get wrong:

  * It must declare `"animation": true`. That is what makes the companion send its page RAW,
    which is what keeps its lowercase r/o/y/g/b/p/w meaning the COLOR FLAPS instead of the
    letters r, o, y, g, b, p and w.
  * HEIGHT is relative (the shape of this day) but COLOR is absolute (whether that shape is
    a warm one). A color ramp normalized to the day would paint a freezing morning red
    merely because it was the warmest hour of a freezing day.
"""
import json

import pytest

from conftest import APPS_DIR as APPS
from conftest import make_runtime

APP = "forecast-ribbon"


def _hourly(temps):
    return {"utc_offset_seconds": 0,
            "hourly": {"time": [f"2000-01-01T{h:02d}:00" for h in range(len(temps))],
                       "temperature_2m": list(temps)}}


@pytest.fixture
def stub(monkeypatch, stub_http):
    """The ribbon reads the shared weather HELPER (httpx — conftest.stub_http);
    the requests mock stays for the no-helper fallback path."""
    import requests

    def go(temps):
        def fake_get(url, **kw):
            class R:
                def json(self):
                    return _hourly(temps)
            return R()
        monkeypatch.setattr(requests, "get", fake_get)
        stub_http(fake_get)
    return go


def _page(rows, cols, **settings):
    rt = make_runtime(installed=[APP], rows=rows, cols=cols,
                      settings={"location_lat": "0",      # skip the geocode
                                "location_lon": "0", **settings})
    return rt.get_pages(APP)[0]


def _cols(page, rows, cols):
    return ["".join(page[r * cols + c] for r in range(rows)) for c in range(cols)]


def test_it_is_an_animation_or_its_colors_become_letters():
    """The whole app is drawn in lowercase color codes. Without this flag the companion
    folds the page and every color flap turns into the LETTER of the same name."""
    m = json.loads((APPS / APP / "manifest.json").read_text("utf-8"))
    assert m["animation"] is True


def test_the_bars_rise_from_the_bottom(stub):
    stub([0] * 24)
    rows, cols = 5, 15
    page = _page(rows, cols)
    for col in _cols(page, rows, cols):
        assert col.rstrip() == "" or col.lstrip() == col.lstrip(" "), col
        assert col[-1] != " ", "a bar must be anchored to the bottom row"


def test_height_is_relative_to_the_window(stub):
    """A cold day still has a tall bar at its warmest hour — that is what makes the SHAPE
    of the day readable rather than a flat line."""
    stub([-10, -5, 0] + [-10] * 21)
    rows, cols = 5, 3
    page = _page(rows, cols)
    heights = [rows - c.count(" ") for c in _cols(page, rows, cols)]
    assert heights[0] < heights[1] < heights[2]


def test_color_is_absolute_not_relative(stub):
    """…but the COLOR of that warmest hour is still freezing-blue. A ramp normalized to the
    day would paint it red for being the least-cold hour of an arctic afternoon."""
    stub([-10, -5, 0] + [-10] * 21)
    page = _page(5, 3)
    assert set(page) <= {" ", "b", "p", "g"}, page
    assert "r" not in page and "o" not in page


def test_a_hot_day_is_red(stub):
    stub([30] * 24)
    page = _page(3, 5)
    assert set(page) - {" "} == {"r"}


def test_the_bands_run_cold_to_hot(stub):
    stub([-10, 0, 5, 14, 22, 30] + [0] * 18)
    page = _page(1, 6)                     # one row: just the color ribbon
    assert page == "pbgyor"


def test_mono_draws_blocks_instead_of_colors(stub):
    stub([20] * 24)
    page = _page(3, 4, disable_colors="yes")   # a GLOBAL, as weather reads it
    assert set(page) - {" "} == {"#"}


def test_a_flat_day_does_not_divide_by_zero(stub):
    stub([15] * 24)
    page = _page(5, 10)
    assert page.count("y") == 10, "a dead-flat day is one row of bars, not a crash"


def test_offline_says_so_rather_than_drawing_nothing(monkeypatch):
    import requests

    from app import weather

    def boom(*a, **k):
        raise RuntimeError("no network")
    monkeypatch.setattr(requests, "get", boom)
    weather._cache.clear()
    monkeypatch.setattr(weather.httpx, "Client", lambda **kw: _FakeClient(boom))
    assert "OFFLINE" in _page(3, 15)
