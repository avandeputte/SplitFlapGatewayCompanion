"""Weather gets a forecast: a day per line, its sky as a COLOR FLAP, its high/low in a column.

The sky is a color rather than a picture because a color is the only weather icon that
works on EVERY wall. The flap reel has no cloud and no raindrop — but it has had seven
colors since the beginning, and a split-flap shows them natively. (A pictograph would work
on a Matrix Portal and degrade to an asterisk on a real wall.)

Three of the four providers already called a forecast endpoint and simply asked for one day;
OpenWeather has no daily endpoint on the free plan at all, so its days are built from the
3-hourly list — which is where the interesting bugs live.
"""
import pytest

from app import renderer
from conftest import Resp as _Resp
from conftest import make_runtime


def _pages(rows, cols, provider="openmeteo", **settings):
    rt = make_runtime(installed=["weather"], rows=rows, cols=cols,
                      settings={"weather_provider": provider, "location_lat": "50.85",
                                "location_lon": "4.35", **settings})
    return rt.get_pages("weather")


OPENMETEO = {
    "current": {"temperature_2m": 70.0, "apparent_temperature": 71.0,
                "weather_code": 3, "uv_index": 2.0},
    "daily": {"time": ["2026-07-13", "2026-07-14", "2026-07-15", "2026-07-16"],
              "temperature_2m_max": [91.0, 89.0, 86.0, 79.0],
              "temperature_2m_min": [68.0, 71.0, 70.0, 66.0],
              "weather_code": [3, 0, 61, 95]},          # cloud, clear, rain, storm
}


@pytest.fixture
def openmeteo(stub_http):
    def fake(url, **kw):
        if "air-quality" in url:
            return _Resp({"current": {}})
        if "open-meteo" in url:
            return _Resp(OPENMETEO)
        raise AssertionError(url)
    stub_http(fake)


def _forecast(pages, rows, cols):
    """The forecast page's non-blank rows: the header, then a line per day.

    NOT simply rows 0..n — format_lines CENTERS the block vertically, so a two-day forecast
    on a five-row wall starts on row 1, not row 0.
    """
    p = next(pg for pg in pages if "Forecast" in pg)
    rowsx = [p[r * cols:(r + 1) * cols] for r in range(rows)]
    return [r for r in rowsx if r.strip()]


def _dots(pages, rows, cols):
    """The first cell of each DAY line, as a color name."""
    p = next(pg for pg in pages if "Forecast" in pg)
    p = (renderer.normalize(p, rows * cols))
    out = []
    seen_header = False
    for r in range(rows):
        row = p[r * cols:(r + 1) * cols]
        if not row.strip():
            continue
        if not seen_header:
            seen_header = True          # the header line
            continue
        out.append(_color(row[0]))
    return out


def _color(cell):
    return renderer.PUA_TO_NAME.get(cell, "")


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------
def test_a_day_per_line_with_its_sky_and_its_high_low(openmeteo):
    rows, cols = 5, 15
    lines = _forecast(_pages(rows, cols), rows, cols)

    assert lines[0].strip() == "Forecast"
    body = lines[1:]
    assert len(body) == 3                     # the default is three days

    for l in body:
        assert len(l) == 15, "the line was re-centered, so the highs will not line up"
        assert l[-1] != " ", "the high/low must be flush right, in a column"
    assert body[0].endswith("89/71") and body[1].endswith("86/70")


def test_it_says_WHAT_the_weather_will_be(openmeteo):
    """A color tells you "wet"; it does not tell you drizzle from a downpour. The word is
    the thing you actually wanted, so on a 15-wide wall it gets the room."""
    rows, cols = 5, 15
    body = _forecast(_pages(rows, cols), rows, cols)[1:]
    assert [l.split()[1] for l in body] == ["Sunny", "Rain-", "Storm"]


def test_five_letter_conditions_leave_the_day_its_three(openmeteo):
    rows, cols = 5, 15
    body = _forecast(_pages(rows, cols), rows, cols)[1:]
    assert body[0].startswith("Tue Sunny")     # Sunny / Rain- / Storm all fit in five
    for l in body:
        assert len(l) == 15 and l[-1] != " "   # the highs still line up in a column


def test_the_day_shrinks_before_the_condition_does(stub_http):
    """A six-letter condition ("Cloudy") takes the day's third letter. "We" is still
    Wednesday; a truncated condition is not a condition."""
    six = dict(OPENMETEO)
    six["daily"] = dict(OPENMETEO["daily"], weather_code=[3, 3, 3, 3])   # all Cloudy

    def fake(url, **kw):
        if "air-quality" in url:
            return _Resp({"current": {}})
        return _Resp(six)
    stub_http(fake)

    rows, cols = 5, 15
    body = _forecast(_pages(rows, cols), rows, cols)[1:]
    assert body[0].startswith("Tu Cloudy")   # two letters, so "Cloudy" keeps all six
    # …and the WHOLE page uses ONE format, or the columns do not line up: the condition
    # starts at the same cell on every row, and so does the high.
    starts = {l.index("Cloudy") for l in body}
    assert len(starts) == 1, f"the rows do not agree where the condition starts: {body}"
    assert all(len(l) == 15 and l[-1] != " " for l in body)


def test_the_color_comes_back_when_the_wall_is_wide_enough(openmeteo):
    """The flap costs two cells. It is spent only when it costs nobody a letter."""
    rows, cols = 5, 22
    assert _dots(_pages(rows, cols), rows, cols) == ["yellow", "blue", "red"]
    body = _forecast(_pages(rows, cols), rows, cols)[1:]
    assert "Sunny" in body[0] and body[0].startswith(" ") is False


def test_a_wide_matrix_spells_the_forecast_out(openmeteo):
    """On a wide Matrix wall the forecast stops abbreviating: the condition in full
    ('Light rain', not 'Rain-'), full weekdays, the high/low with degree signs, laid
    out as an aligned block CENTERED on the wall instead of the day and the temps
    pinned to opposite edges with a lake of space between them."""
    rows, cols = 5, 40
    body = _forecast(_pages(rows, cols), rows, cols)[1:]
    text = " ".join(body)
    assert "Light rain" in text, "the condition is spelled out, not 'Rain-'"
    assert "Sunny" in text and "Storm" in text
    assert "Tuesday" in text and "Wednesday" in text        # full weekday, not 'Tue'
    assert "\N{DEGREE SIGN}" in text                        # degree signs on the temps
    assert len({len(l) for l in body}) == 1, "every day the same width => columns line up"
    assert all(l.startswith(" ") for l in body), "the block is centered, not edge-pinned"


def test_today_is_not_in_the_forecast(openmeteo):
    """The conditions page IS today. Repeating it costs a line and says nothing new."""
    rows, cols = 5, 15
    body = _forecast(_pages(rows, cols), rows, cols)[1:]
    assert not any("91/68" in l for l in body), "today was listed again"


def test_short_wall_packs_days_instead_of_spending_a_row_on_the_header(openmeteo):
    """Each forecast row starts with its own weekday, so it labels itself. On a
    full page the "Forecast" header no longer steals a row: the three days pack
    onto ONE 3-row page rather than a header page plus a lonely third day."""
    rows, cols = 3, 15
    fc = [p for p in _pages(rows, cols) if p.count("/") >= 1]   # rows carry hi/lo
    assert len(fc) == 1, "the three days pack onto one page now"
    assert "Forecast" not in fc[0], "a full page spends every row on a day"


def test_off_means_off(openmeteo):
    assert not any("Forecast" in p for p in _pages(5, 15, plugin_weather_forecast_days="0"))


def test_five_days_when_asked(openmeteo):
    pages = [p for p in _pages(5, 15, plugin_weather_forecast_days="5") if "Forecast" in p]
    assert pages, "the forecast disappeared"


def test_it_does_not_rewrite_todays_high_and_low(openmeteo):
    """The forecast loop once reused `hi` and `lo` — the names the CONDITIONS page had already
    built — so today's "H 91F L 68F" silently came out as the last forecast day's "79 66"."""
    rows, cols = 5, 15
    conditions = _pages(rows, cols)[0]
    assert "H 91F" in conditions and "L 68F" in conditions


def test_colors_off_drops_the_dot_not_the_day(openmeteo):
    rows, cols = 5, 15
    body = _forecast(_pages(rows, cols, disable_colors="yes"), rows, cols)[1:]
    assert body and body[0].endswith("89/71")
    assert not any(renderer.is_color(c) for l in body for c in l)


# ---------------------------------------------------------------------------
# OpenWeather has no daily endpoint — the days are built from 3-hourly slots
# ---------------------------------------------------------------------------
def test_openweather_buckets_the_slots_into_the_citys_days(stub_http):
    # Slots on the next three days, for a city 2h ahead. Built relative to the real clock,
    # because the app drops TODAY (the conditions page already is today) — a fixed date would
    # be in the past by tomorrow and the test would evaporate.
    import calendar
    import datetime as _dt

    base = _dt.datetime.now(_dt.timezone.utc).date()
    slots = []
    for n, (a, b) in enumerate([(70, 80), (60, 75), (50, 65)], start=1):
        d = base + _dt.timedelta(days=n)
        for hour, temp, wid in ((6, a, 800), (12, b, 500), (21, a, 800)):
            slots.append({"dt": calendar.timegm((d.year, d.month, d.day, hour, 0, 0, 0, 0, 0)),
                          "main": {"temp": temp},
                          "weather": [{"id": wid}]})

    def fake(url, **kw):
        if "open-meteo" in url:      # the helper's supplemental hourly series
            return _Resp({})
        if "/forecast" in url:
            return _Resp({"city": {"timezone": 7200}, "list": slots})
        if "air_pollution" in url:
            return _Resp({"list": [{"main": {"aqi": 1}}]})
        return _Resp({"name": "Brussels", "main": {"temp": 70, "feels_like": 71,
                                                   "temp_max": 80, "temp_min": 60},
                      "weather": [{"description": "clear"}]})
    stub_http(fake)

    rows, cols = 5, 15
    body = _forecast(_pages(rows, cols, provider="openweather",
                        weather_api_key="test-key"), rows, cols)[1:]
    assert body, "openweather produced no forecast"
    # each day's own min and max, from its own slots
    assert body[0].endswith("75/60") or body[0].endswith("80/70"), body[0]


def test_openweather_reports_the_worst_sky_of_the_day(stub_http):
    """A day with one thunderstorm in it is a stormy day, however sunny the rest of it was."""
    import calendar
    import datetime as _dt

    d = _dt.datetime.now(_dt.timezone.utc).date() + _dt.timedelta(days=1)   # tomorrow
    slots = [{"dt": calendar.timegm((d.year, d.month, d.day, h, 0, 0, 0, 0, 0)),
              "main": {"temp": 70},
              "weather": [{"id": 800 if h != 15 else 210}]}          # 15:00 -> storm
             for h in (9, 12, 15, 18)]

    def fake(url, **kw):
        if "open-meteo" in url:      # the helper's supplemental hourly series
            return _Resp({})
        if "/forecast" in url:
            return _Resp({"city": {"timezone": 0}, "list": slots})
        if "air_pollution" in url:
            return _Resp({"list": [{"main": {"aqi": 1}}]})
        return _Resp({"name": "X", "main": {"temp": 70, "feels_like": 70,
                                            "temp_max": 70, "temp_min": 70},
                      "weather": [{"description": "clear"}]})
    stub_http(fake)

    rows, cols = 5, 22          # wide enough for the color flap as well as the word
    pages = _pages(rows, cols, provider="openweather", weather_api_key="test-key")
    assert _dots(pages, rows, cols) == ["red"], "a storm in the afternoon is a stormy day"
    assert "Storm" in _forecast(pages, rows, cols)[1]
