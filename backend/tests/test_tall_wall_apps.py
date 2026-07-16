"""Apps that paginated because three rows forced them to, on a wall that has five.

A 5x15 wall is 75 modules. Three apps were spending them badly:

  * weather   — up to FIVE near-empty pages (conditions, AQI, UV, pollen, pollen
                detail), several padded out with a "PROV OPENMETEO" line nobody asked
                for. One row per metric fits them all on one or two pages.
  * wiki-today — the three most-read articles as three separate pages, each using one
                row for a title and leaving the rest blank. It is a three-line list.
  * holidays   — TRUNCATED the holiday name ("MARTIN LUTHER KING J") rather than
                wrapping it onto a row it had going spare.

The three-row wall is what everyone already has, so its layouts must not move — each
app is checked at 3 rows too.

The providers are stubbed. These assert LAYOUT, and a layout test that needs the
internet is a layout test that fails on a train.
"""
import pytest

from conftest import APPS_DIR as APPS
from conftest import Resp as _Resp
from conftest import load_app as _mod
from conftest import make_runtime


# ---------------------------------------------------------------------------
# stubbed providers
# ---------------------------------------------------------------------------
FORECAST = {
    "current": {"temperature_2m": 76.0, "apparent_temperature": 79.0,
                "weather_code": 2, "uv_index": 7.0},
    "daily": {"temperature_2m_max": [91.0], "temperature_2m_min": [64.0]},
}
AIR = {"current": {"us_aqi": 42, "grass_pollen": 12.0, "birch_pollen": 3.0,
                   "ragweed_pollen": 0.0, "weed_pollen": 60.0}}
WIKI = {
    "tfa": {"normalizedtitle": "Manufacturers Trust Company Building"},
    "mostread": {"articles": [{"normalizedtitle": f"Article Number {i}"} for i in range(1, 6)]},
}
# A deliberately long name — this is the reported bug.
HOLIDAYS = [
    {"date": "2026-09-07", "name": "Labour Day", "localName": "Labor Day", "global": True},
    {"date": "2027-01-18", "name": "Martin Luther King, Jr. Day",
     "localName": "Martin Luther King Jr. Day", "global": True},
]


@pytest.fixture
def stub_net(monkeypatch, stub_http):
    import requests

    def fake_get(url, **kw):
        if "air-quality" in url:
            return _Resp(AIR)
        if "open-meteo" in url:
            return _Resp(FORECAST)
        if "wikipedia.org" in url:
            return _Resp(WIKI)
        if "nager.at" in url:
            return _Resp(HOLIDAYS)
        if "nominatim" in url:
            return _Resp([{"lat": "42.35", "lon": "-71.07", "display_name": "Boston, MA"}])
        raise AssertionError(f"unstubbed call: {url}")

    monkeypatch.setattr(requests, "get", fake_get)

    # The weather app reads the shared helper now, which speaks httpx.
    stub_http(fake_get)


def _runtime(rows, cols, tmp_path, app_id, **settings):
    return make_runtime(tmp_path, [app_id], rows=rows, cols=cols, settings=settings)


def _lines(page, rows, cols):
    """The non-blank rows of a rendered page."""
    return [page[r * cols:(r + 1) * cols].strip() for r in range(rows)]


def _body(page, rows, cols):
    return [l for l in _lines(page, rows, cols) if l]


# ---------------------------------------------------------------------------
# the helpers, where the layout decisions actually live
# ---------------------------------------------------------------------------
def test_a_metric_is_one_line_not_one_page():
    w = _mod("weather")
    # A tile on BOTH ends when it fits — the lonely trailing tile looked unbalanced.
    assert w._metric_line("AQI", 42, "GOOD", "GREEN", 15) == "🟩 AQI 42 GOOD 🟩"
    assert w._metric_line("AQI", 42, "GOOD", "GREEN", 15, mono=True) == "AQI 42 GOOD"
    # Narrower: degrade to a single trailing tile, then none.
    assert w._metric_line("AQI", 42, "GOOD", "GREEN", 13) == "AQI 42 GOOD 🟩"


@pytest.mark.parametrize("cols", [8, 11, 15, 20])
def test_a_metric_line_degrades_instead_of_overflowing(cols):
    w = _mod("weather")
    line = w._metric_line("AQI", 151, "V.UNHLTHY", "RED", cols)
    assert len(line) <= cols, f"{line!r} overflows a {cols}-col wall"


def test_pagination_is_balanced_not_greedy():
    """6 lines on a 5-row wall, chunked greedily, gives a full page and then ONE lonely
    line on an otherwise blank screen — that reads as a bug. Split it 3/3."""
    w = _mod("weather")
    assert [len(p) for p in w._paginate(list("abcdef"), 5)] == [3, 3]
    assert [len(p) for p in w._paginate(list("abcd"), 5)] == [4]      # fits: one page
    assert [len(p) for p in w._paginate(list("abcdefghi"), 5)] == [5, 4]
    assert w._paginate([], 5) == []                                   # must not raise


# ---------------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------------
def test_weather_never_names_the_provider():
    """It was on three of the five pages. Nobody bought a split-flap wall to read the
    word OPENMETEO on it."""
    src = (APPS / "weather" / "app.py").read_text("utf-8")
    assert "provider_word" not in src
    assert "weather_provider.upper()" not in src


def test_weather_puts_everything_on_one_tall_page(stub_net, tmp_path):
    """The reported complaint. Conditions + AQI + UV + pollen were five pages; on a
    five-row wall they are one, one metric per row."""
    rt = _runtime(5, 15, tmp_path, "weather", weather_provider="openmeteo",
                  location_lat="42.35", location_lon="-71.07",
                  plugin_weather_show_aqi="yes", plugin_weather_show_uv="yes",
                  plugin_weather_show_pollen="yes")
    pages = rt.get_pages("weather")
    assert len(pages) <= 2, f"still {len(pages)} pages on a 5-row wall"

    everything = " ".join(l for p in pages for l in _body(p, 5, 15))
    assert "Feels" in everything                       # conditions
    assert "AQI 42" in everything                      # air quality, as a LINE
    assert "UV 7" in everything                        # uv, as a LINE
    assert "Pollen" in everything
    assert "PROV" not in everything


def test_weather_pages_are_full_not_padded(stub_net, tmp_path):
    """The old tall layout put two useful lines on a page and filled the rest. Every
    page we emit now must be carrying real content on most of its rows."""
    rt = _runtime(5, 15, tmp_path, "weather", weather_provider="openmeteo",
                  location_lat="42.35", location_lon="-71.07",
                  plugin_weather_show_aqi="yes", plugin_weather_show_uv="yes",
                  plugin_weather_show_pollen="yes")
    for page in rt.get_pages("weather"):
        assert len(_body(page, 5, 15)) >= 3


def test_weather_keeps_the_three_row_layout(stub_net, tmp_path):
    """The wall everyone already has: conditions page is temp+feels / hi+lo / desc,
    and AQI/UV/pollen still get their own pages. Unchanged."""
    rt = _runtime(3, 15, tmp_path, "weather", weather_provider="openmeteo",
                  location_lat="42.35", location_lon="-71.07",
                  plugin_weather_show_aqi="yes", plugin_weather_show_uv="yes",
                  plugin_weather_show_pollen="yes")
    pages = rt.get_pages("weather")
    first = _lines(pages[0], 3, 15)
    assert "Feels" in first[0]
    assert first[1].startswith("H ") and " L " in first[1]
    assert len(pages) > 1, "the metrics still page on a short wall"
    assert any("Air quality" in " ".join(_lines(p, 3, 15)) for p in pages)


# ---------------------------------------------------------------------------
# wiki-today
# ---------------------------------------------------------------------------
def test_wiki_most_read_is_one_page_listing_the_articles(stub_net, tmp_path):
    """Three articles on three pages, each using one row of five, becomes one page
    that is simply the list."""
    rt = _runtime(5, 15, tmp_path, "wiki-today")
    pages = [p for p in rt.get_pages("wiki-today") if "Most read" in p]
    assert len(pages) == 1

    body = _body(pages[0], 5, 15)
    assert body[0].startswith("Wiki")
    assert len(body[1:]) == 4, "a 5-row wall lists four articles under the header"
    assert all(len(l) <= 15 for l in body)


def test_wiki_keeps_a_page_per_article_on_a_three_row_wall(stub_net, tmp_path):
    rt = _runtime(3, 15, tmp_path, "wiki-today")
    assert len([p for p in rt.get_pages("wiki-today") if "Most read" in p]) == 3


# ---------------------------------------------------------------------------
# holidays — the reported bug is truncation. The full-page layout tests
# moved to test_holidays_dataset.py when the app went offline (fixture
# data instead of the retired Nager stub).
# ---------------------------------------------------------------------------
def test_a_long_holiday_name_wraps_rather_than_being_cut():
    h = _mod("holidays")
    assert h._wrap("MARTIN LUTHER KING JR DAY", 15, 3) == ["MARTIN LUTHER", "KING JR DAY"]
    # a single word wider than the wall is the one case truncation is unavoidable
    assert h._wrap("SUPERCALIFRAGILISTIC", 15, 2) == ["SUPERCALIFRAGIL"]


def test_wrap_never_emits_a_blank_first_line():
    """The wiki-today version appends '' when the first word overflows. Ours must not:
    a blank top row on a holiday page looks like the app crashed."""
    h = _mod("holidays")
    assert h._wrap("SUPERCALIFRAGILISTICEXPI ALIDOCIOUS", 10, 2)[0] != ""



# --- weather improvements (July 2026): balanced tiles, humidity, +1 forecast day
_WX = {
    "ok": True, "provider": "openweather", "city": "Boston", "temp_f": 72,
    "feels_like_f": 75, "humidity": 66, "hi_f": 91, "lo_f": 64,
    "desc": "Sunny", "sky": "clear", "uv": 7,
    "forecast": [{"date": f"2026-08-0{i}", "hi_f": 80 + i, "lo_f": 60 + i, "sky": "clear"}
                 for i in range(1, 6)],
}


def _weather_pages(rows, cols=15, **settings):
    w = _mod("weather")
    settings.setdefault("forecast_days", "5")
    return w.fetch(settings, lambda *l, **k: list(l), lambda: rows, lambda: cols,
                   get_weather=lambda days=0, air=False: _WX)


def test_humidity_shows_on_a_tall_wall():
    body = " ".join(l for p in _weather_pages(5) for l in p)
    assert "Humidity 66%" in body


def test_condition_carries_balanced_sky_tiles():
    tile = "\U0001f7e8"      # yellow, clear sky
    cond = next(l for p in _weather_pages(5) for l in p if "Sunny" in l)
    assert cond.startswith(tile) and cond.rstrip().endswith(tile), cond


def test_five_day_forecast_fills_a_five_row_page():
    pages = _weather_pages(5)
    fc = [p for p in pages if any("/" in l and l.split()[0][:2].isalpha() for l in p)]
    # the forecast page holds five day-rows, no "Forecast" header eating one
    full = [p for p in fc if len(p) == 5]
    assert full, [len(p) for p in fc]
    assert not any("Forecast" in l for l in full[0])
