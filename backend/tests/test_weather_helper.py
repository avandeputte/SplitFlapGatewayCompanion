"""The shared weather helper: sky normalization, bands, the document shape, and
the fallback ladder. Provider knowledge lives in ONE place now — these tests
hold it to that."""
import pytest

from app import weather


class _Resp:
    def __init__(self, p):
        self._p = p

    def json(self):
        return self._p


class _FakeClient:
    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        return self._handler(url, **kw)


def _mock(monkeypatch, handler):
    weather._cache.clear()
    monkeypatch.setattr(weather.httpx, "Client", lambda **kw: _FakeClient(handler))


OPENMETEO = {
    "current": {"temperature_2m": 70.0, "apparent_temperature": 71.0, "weather_code": 61,
                "relative_humidity_2m": 40, "wind_speed_10m": 5.0, "cloud_cover": 20,
                "uv_index": 4.0},
    "daily": {"time": ["2026-07-15", "2026-07-16", "2026-07-17"],
              "temperature_2m_max": [91.0, 89.0, 86.0],
              "temperature_2m_min": [68.0, 71.0, 70.0],
              "weather_code": [61, 0, 95]},
    "hourly": {"time": ["2026-07-15T00:00", "2026-07-15T01:00"],
               "temperature_2m": [20.0, 21.5]},
    "utc_offset_seconds": 7200,
}

SETTINGS = {"location_lat": "50.85", "location_lon": "4.35", "location_name": "Brussels"}


# ---------------------------------------------------------------------------
# every provider dialect lands on the same canonical sky
# ---------------------------------------------------------------------------
def test_sky_normalization_speaks_every_dialect():
    assert weather.sky_of_wmo(0) == "clear" and weather.sky_of_wmo(95) == "storm"
    assert weather.sky_of_openweather(800) == "clear" and weather.sky_of_openweather(210) == "storm"
    assert weather.sky_of_weatherapi(1000) == "clear" and weather.sky_of_weatherapi(1087) == "storm"
    assert weather.sky_of_qweather(100) == "clear" and weather.sky_of_qweather(302) == "storm"
    for fn in (weather.sky_of_wmo, weather.sky_of_openweather,
               weather.sky_of_weatherapi, weather.sky_of_qweather):
        assert fn(None) == "cloudy"          # unknown is a shrug, not a crash


def test_the_worst_sky_describes_the_day():
    assert weather._worst_sky(["clear", "storm", "clear"]) == "storm"
    assert weather._worst_sky([]) == "cloudy"


# ---------------------------------------------------------------------------
# scales -> (label, band): the label speaks the provider's scale, the band is
# canonical so one colour map fits all
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fn,val,label,band", [
    (weather.us_aqi_level, 42, "Good", "good"),
    (weather.us_aqi_level, 160, "Unhealthy", "bad"),
    (weather.openweather_aqi_level, 2, "Fair", "moderate"),
    (weather.epa6_aqi_level, 3, "USG", "poor"),
    (weather.uv_level, 9, "V.High", "bad"),
    (weather.pollen_level, 0, "None", "none"),
    (weather.pollen_level, 75, "High", "poor"),
])
def test_levels_and_bands(fn, val, label, band):
    assert fn(val) == (label, band)


def test_unknown_is_a_band_too():
    assert weather.us_aqi_level(None) == ("Unknown", "unknown")
    assert weather.uv_level(None) == ("Unknown", "unknown")


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------
def test_the_document_carries_sky_forecast_hourly_and_air(monkeypatch):
    def fake(url, **kw):
        if "air-quality" in url:
            return _Resp({"current": {"us_aqi": 42, "uv_index": 5.0, "grass_pollen": 12.0,
                                      "birch_pollen": None, "ragweed_pollen": 3.0,
                                      "weed_pollen": None}})
        return _Resp(OPENMETEO)
    _mock(monkeypatch, fake)

    w = weather.fetch_weather(dict(SETTINGS), days=2, air=True)
    assert w["ok"] and w["provider"] == "openmeteo"
    assert w["sky"] == "rainl" and w["code"] == 61
    assert w["temp_f"] == 70 and w["temp_c"] == 21.1
    # forecast excludes today; each day carries a canonical sky
    assert [d["sky"] for d in w["forecast"]] == ["clear", "storm"]
    assert w["forecast"][0]["hi_f"] == 89
    # hourly is always present with days>0, in both units
    assert w["hourly"]["temp_c"] == [20.0, 21.5]
    assert w["hourly"]["temp_f"] == [68.0, 70.7]
    assert w["hourly"]["utc_offset_s"] == 7200
    # air is classified, not raw
    a = w["air"]
    assert (a["aqi"], a["aqi_label"], a["aqi_band"]) == (42, "Good", "good")
    # UV prefers the weather payload's own reading (4.0); the air API's 5.0
    # is the fallback for providers that don't carry UV
    assert (a["uv"], a["uv_band"]) == (4, "moderate")
    assert a["pollen"]["overall"] == 12.0 and a["pollen_band"] == "moderate"


def test_current_only_stays_light(monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append(url)
        return _Resp(OPENMETEO)
    _mock(monkeypatch, fake)

    w = weather.fetch_weather(dict(SETTINGS))
    assert w["ok"] and w["forecast"] == [] and "air" not in w and "hourly" not in w
    assert len(calls) == 1, "current-only must be a single request"


def test_a_failing_keyed_provider_falls_back_to_openmeteo(monkeypatch):
    def fake(url, **kw):
        if "openweathermap" in url:
            return _Resp({"cod": 401})            # bad key: no temperature in the body
        return _Resp(OPENMETEO)
    _mock(monkeypatch, fake)

    w = weather.fetch_weather(dict(SETTINGS, weather_provider="openweather",
                                   weather_api_key="bad"))
    assert w["ok"] and w["provider"] == "openmeteo"


def test_the_per_app_location_override_wins(monkeypatch):
    seen = {}

    def fake(url, **kw):
        seen.update(kw.get("params") or {})
        return _Resp(OPENMETEO)
    _mock(monkeypatch, fake)

    w = weather.fetch_weather(dict(SETTINGS, location="48.85,2.35|Paris"))
    assert w["ok"] and w["city"] == "Paris"
    assert round(seen["latitude"], 2) == 48.85 and round(seen["longitude"], 2) == 2.35
