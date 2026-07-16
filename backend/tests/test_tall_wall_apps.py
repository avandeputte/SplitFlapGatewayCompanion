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
import importlib.util
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2] / "apps"


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"_{name}", APPS / name / "app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


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


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def stub_net(monkeypatch):
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
    from app import weather

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            return fake_get(url, **kw)

    weather._cache.clear()
    monkeypatch.setattr(weather.httpx, "Client", lambda **kw: _FakeClient())


def _runtime(rows, cols, tmp_path, app_id, **settings):
    from app.config import Config
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime

    cfg = Config(tmp_path)
    cfg.update({"grid": {"rows": rows, "cols": cols}})
    st = PluginSettings(cfg.data_dir)
    st.set("installed_apps", [app_id])
    for k, v in settings.items():
        st.set(k, v)
    rt = PluginRuntime(cfg, st, APPS, cfg.data_dir / "apps")
    rt.load()
    return rt


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
    assert w._metric_line("AQI", 42, "GOOD", "GREEN", 15) == "AQI 42 GOOD 🟩"
    assert w._metric_line("AQI", 42, "GOOD", "GREEN", 15, mono=True) == "AQI 42 GOOD"


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
# holidays — the reported bug is truncation
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


def test_the_holiday_name_survives_a_tall_wall_intact(stub_net, tmp_path):
    """MARTIN LUTHER KING JR. DAY is 26 characters on a 15-wide wall. It used to be
    cut; now it wraps, and every word of it is on the page."""
    rt = _runtime(5, 15, tmp_path, "holidays", country="US")
    pages = rt.get_pages("holidays")
    mlk = [p for p in pages if "Martin" in p]
    assert mlk, "the long-named holiday is missing entirely"

    body = _body(mlk[0], 5, 15)
    text = " ".join(body)
    for word in ("Martin", "Luther", "King"):
        assert word in text, f"{word} was truncated away"
    assert all(len(l) <= 15 for l in body)


def test_a_tall_wall_spends_its_spare_row_on_the_date(stub_net, tmp_path):
    """A short name (LABOR DAY) leaves a row over: say WHEN, don't show blank flaps."""
    rt = _runtime(5, 15, tmp_path, "holidays", country="US")
    labor = [p for p in rt.get_pages("holidays") if "Labor" in p][0]
    body = _body(labor, 5, 15)
    assert any("Sep" in l for l in body), f"no date line in {body}"
    assert any(l.startswith("In ") for l in body), "the countdown is the point"


def test_three_row_wall_gives_up_the_header_before_it_truncates(stub_net, tmp_path):
    """On the common wall, a name that fits keeps 'Next holiday'; one that doesn't
    takes that row rather than losing half of itself — the header says less than the
    name it was cutting in half."""
    rt = _runtime(3, 15, tmp_path, "holidays", country="US")
    pages = rt.get_pages("holidays")

    short = [p for p in pages if "Labor" in p][0]
    assert "Next holiday" in short

    long = [p for p in pages if "Martin" in p][0]
    assert "Next holiday" not in long, "the header must yield to the name"
    body = _body(long, 3, 15)
    assert "Martin" in " ".join(body) and "King" in " ".join(body)
