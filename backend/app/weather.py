"""
weather.py — the shared weather helper: every provider quirk lives HERE.

Several apps want the weather. Rather than each hardcoding providers, keys,
forecast bucketing and air-quality scales, this resolves the *global* weather
settings (provider + API key + location) and returns one normalized document.
The plugin runtime injects it into any app whose ``fetch()`` opts in with a
``get_weather`` parameter (see plugins.py); the injected callable is

    get_weather(settings=None, days=0, air=False)

* ``get_weather()``            — current conditions (cheap, cached)
* ``get_weather(days=3)``      — adds a per-day ``forecast`` and an ``hourly``
                                 temperature series
* ``get_weather(air=True)``    — adds AQI / UV / pollen under ``air``

The document (fields may be None when a provider has no answer):

    ok, provider, city, lat, lon,
    temp_f, temp_c, feels_like_f, hi_f, lo_f, humidity, wind_mph, cloud_cover,
    desc            provider text (native-language on keyed providers),
    code            the raw provider condition code (WMO on Open-Meteo),
    sky             the CANONICAL condition — one of SKY_TOKENS, whichever
                    provider it came from, so apps never read provider codes
    forecast        [{date: 'YYYY-MM-DD', hi_f, lo_f, sky}], today excluded —
                    the conditions above already ARE today. OpenWeather's free
                    plan has no daily endpoint, so its days are bucketed from
                    3-hourly slots in the CITY's timezone, and each day's sky is
                    the WORST daylight sky (a day with one thunderstorm in it is
                    a stormy day, however sunny the rest of it was).
    hourly          {time: [...], temp_f: [...], utc_offset_s} — always from
                    keyless Open-Meteo, whatever the provider, so a temperature
                    ribbon works with any key or none
    air             {aqi, aqi_label, aqi_band, uv, uv_label, uv_band,
                     pollen: {grass, tree, weed, overall}, pollen_label,
                     pollen_band}. Labels are display text on the provider's own
                     scale; BANDS are canonical ('good'/'moderate'/'poor'/'bad',
                     plus 'none'/'unknown'), so an app needs ONE color map.

Providers: Open-Meteo (keyless — the default and the fallback), OpenWeather,
WeatherAPI and QWeather (keyed via the global weather_api_key). Temperatures
are normalized to Fahrenheit; the caller formats/converts (temp_c comes along
for the metric-only). Keyed providers get the global Language, so ``desc``
arrives already localized where the provider can.

Location: the app's own ``location`` override ("lat,lon" or "lat,lon|City")
wins, then the global precise coordinates, then the geocoded ZIP, then Boston —
so weather is never blank.

Caching: one fetch per (provider, location, shape) per window, shared by every
app. The window follows the app's polling_rate setting when present (the
merged settings arrive per-app), else 10 minutes.

Runs blocking httpx in the plugin threadpool, so it uses a synchronous client.
"""

from __future__ import annotations

import copy
import logging
import time
from datetime import datetime, timezone

import httpx

from . import i18n, location

log = logging.getLogger("companion.weather")

_DEFAULT_TTL = 600  # seconds, when the caller's settings carry no polling_rate
_cache: dict = {}   # (provider, key, lat, lon, days, air, lang) -> (fetched_at, doc)

# Catalog globals the helper consumes — so the UI can credit weather-using apps
# under these settings even though they read them via get_weather, not directly.
GLOBAL_KEYS = ("weather_provider", "weather_api_key", "zip_code", "location_precise")

# ---------------------------------------------------------------------------
# The canonical sky. A color tells you "wet"; the token tells you drizzle from
# a downpour. Every provider's private code dialect is translated to these, so
# no app ever reads a provider code again.
# ---------------------------------------------------------------------------
SKY_TOKENS = ('clear', 'pcloudy', 'cloudy', 'fog', 'rainl', 'shwr', 'rain', 'rainh',
              'snowl', 'snow', 'snowh', 'sleet', 'hail', 'storm')

# Worst-last, for describing a whole day by the worst thing in it.
SKY_SEVERITY = SKY_TOKENS


def _worst_sky(skies):
    skies = [s for s in skies if s in SKY_SEVERITY] or ['cloudy']
    return max(skies, key=SKY_SEVERITY.index)


def sky_of_wmo(code):
    """Open-Meteo's WMO weather code -> canonical sky."""
    if code is None:
        return 'cloudy'
    return {
        0: 'clear', 1: 'clear', 2: 'pcloudy', 3: 'cloudy',
        45: 'fog', 48: 'fog',
        51: 'rainl', 53: 'rainl', 55: 'rain',
        56: 'sleet', 57: 'sleet',
        61: 'rainl', 63: 'rain', 65: 'rainh',
        66: 'sleet', 67: 'sleet',
        71: 'snowl', 73: 'snow', 75: 'snowh', 77: 'snow',
        80: 'shwr', 81: 'shwr', 82: 'rainh',
        85: 'snowl', 86: 'snowh',
        95: 'storm', 96: 'hail', 99: 'hail',
    }.get(int(code), 'cloudy')


def sky_of_openweather(wid):
    """OpenWeather's condition id -> canonical sky."""
    if wid is None:
        return 'cloudy'
    w = int(wid)
    if w == 800:
        return 'clear'
    if w in (801, 802):
        return 'pcloudy'
    if 803 <= w <= 804:
        return 'cloudy'
    if w == 781:
        return 'storm'
    if 700 <= w < 800:
        return 'fog'
    if 200 <= w < 300:
        return 'storm'
    if 300 <= w < 400:
        return 'rainl'
    if w == 511:
        return 'sleet'
    if 520 <= w < 600:
        return 'shwr'
    if w == 500:
        return 'rainl'
    if w == 501:
        return 'rain'
    if 502 <= w < 520:
        return 'rainh'
    if w in (611, 612, 613, 615, 616):
        return 'sleet'
    if w == 600:
        return 'snowl'
    if w == 602:
        return 'snowh'
    if 600 <= w < 700:
        return 'snow'
    return 'cloudy'


def sky_of_weatherapi(code):
    if code is None:
        return 'cloudy'
    c = int(code)
    if c == 1000:
        return 'clear'
    if c == 1003:
        return 'pcloudy'
    if c in (1006, 1009):
        return 'cloudy'
    if c in (1030, 1135, 1147):
        return 'fog'
    if c in (1087, 1273, 1276, 1279, 1282):
        return 'storm'
    if c in (1237, 1261, 1264):
        return 'hail'
    if c in (1069, 1072, 1168, 1171, 1198, 1201, 1204, 1207, 1249, 1252):
        return 'sleet'
    if c in (1066, 1210, 1213):
        return 'snowl'
    if c in (1222, 1225):
        return 'snowh'
    if c in (1216, 1219, 1255, 1258):
        return 'snow'
    if c in (1240, 1243, 1246):
        return 'shwr'
    if c in (1063, 1150, 1153, 1180, 1183):
        return 'rainl'
    if c in (1192, 1195):
        return 'rainh'
    if 1063 <= c <= 1201:
        return 'rain'
    return 'cloudy'


def sky_of_qweather(icon):
    try:
        i = int(icon)
    except (TypeError, ValueError):
        return 'cloudy'
    if i in (100, 150):
        return 'clear'
    if i in (102, 103, 152, 153):
        return 'pcloudy'
    if i in (101, 104, 151, 154):
        return 'cloudy'
    if 500 <= i <= 515:
        return 'fog'
    if i in (302, 303):
        return 'storm'
    if i == 304:
        return 'hail'
    if i in (313, 404, 405, 406, 456, 457):
        return 'sleet'
    if i in (300, 301, 350, 351):
        return 'shwr'
    if i in (305, 309):
        return 'rainl'
    if i in (307, 308, 310, 311, 312, 318):
        return 'rainh'
    if 300 <= i <= 399:
        return 'rain'
    if i == 400:
        return 'snowl'
    if i in (402, 403):
        return 'snowh'
    if 400 <= i <= 499:
        return 'snow'
    return 'cloudy'


# ---------------------------------------------------------------------------
# Air-quality scales. Each provider grades on its own curve; the LABEL is the
# provider-scale display text, the BAND is the canonical 4-step class
# ('good'/'moderate'/'poor'/'bad') every consumer can color with one map.
# ---------------------------------------------------------------------------
def us_aqi_level(val):
    """US EPA AQI (0-500) -> (label, band)."""
    if val is None:
        return 'Unknown', 'unknown'
    if val <= 50:
        return 'Good', 'good'
    if val <= 100:
        return 'Mod', 'moderate'
    if val <= 150:
        return 'USG', 'poor'
    if val <= 200:
        return 'Unhealthy', 'bad'
    if val <= 300:
        return 'V.Unhlthy', 'bad'
    return 'Hazardous', 'bad'


def openweather_aqi_level(val):
    """OpenWeather's 1-5 scale -> (label, band)."""
    labels = {1: ('Good', 'good'), 2: ('Fair', 'moderate'), 3: ('Moderate', 'moderate'),
              4: ('Poor', 'poor'), 5: ('V.Poor', 'bad')}
    return labels.get(val, ('Unknown', 'unknown'))


def epa6_aqi_level(val):
    """WeatherAPI's us-epa-index, 1-6 -> (label, band)."""
    labels = {1: ('Good', 'good'), 2: ('Mod', 'moderate'), 3: ('USG', 'poor'),
              4: ('Unhealthy', 'bad'), 5: ('V.Unhlthy', 'bad'), 6: ('Hazardous', 'bad')}
    return labels.get(val, ('Unknown', 'unknown'))


def uv_level(val):
    """UV index -> (label, band)."""
    if val is None:
        return 'Unknown', 'unknown'
    if val < 3:
        return 'Low', 'good'
    if val < 6:
        return 'Mod', 'moderate'
    if val < 8:
        return 'High', 'poor'
    if val < 11:
        return 'V.High', 'bad'
    return 'Extreme', 'bad'


def pollen_level(val):
    """Grains/m³ (Open-Meteo & WeatherAPI report comparable magnitudes) -> (label, band)."""
    if val is None or val < 1:
        return 'None', 'none'
    if val < 10:
        return 'Low', 'good'
    if val < 50:
        return 'Mod', 'moderate'
    if val < 200:
        return 'High', 'poor'
    return 'V.High', 'bad'


# Open-Meteo WMO code -> condition text (Title case, matching the app-i18n
# catalog keys, so i18n.t(desc, "weather") localizes it).
_OPENMETEO_CODES = {
    0: 'Clear', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast',
    45: 'Fog', 48: 'Rime fog', 51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
    56: 'Freezing drizzle', 57: 'Freezing drizzle', 61: 'Light rain', 63: 'Rain',
    65: 'Heavy rain', 66: 'Freezing rain', 67: 'Freezing rain', 71: 'Light snow',
    73: 'Snow', 75: 'Heavy snow', 77: 'Snow grains', 80: 'Rain showers',
    81: 'Rain showers', 82: 'Heavy showers', 85: 'Snow showers', 86: 'Heavy snow showers',
    95: 'Thunderstorm', 96: 'Thunder hail', 99: 'Severe tstorm',
}


def _i(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _f2c(f):
    return None if f is None else round((float(f) - 32.0) * 5.0 / 9.0, 1)


# Boston, so weather always has something to show if no location is configured.
_FALLBACK_LOCATION = (42.3496, -71.0783, "BOSTON")


def _resolve_location(settings):
    """(lat, lon, city). The app's own ``location`` override wins ("lat,lon" or
    "lat,lon|City" — what the per-app Location field stores), then the global
    precise coordinates / geocoded ZIP (shared, cached), then Boston."""
    override = str(settings.get("location", "") or "").strip()
    if override and "," in override:
        coords, _, city = override.partition("|")
        try:
            lat_s, _, lon_s = coords.partition(",")
            return float(lat_s), float(lon_s), (city.strip() or "Location")
        except ValueError:
            pass
    return location.coordinates(settings) or _FALLBACK_LOCATION


# ---------------------------------------------------------------------------
# Providers. Each returns the normalized document core; days > 0 adds
# `forecast` (today excluded). Air is fetched separately below — except
# WeatherAPI, whose weather payload already carries it (stashed under `_air`).
# ---------------------------------------------------------------------------
def _forecast_entry(date, hi, lo, sky):
    """One forecast day, in the document's shape — every provider builds its
    days through here so the shape can never drift per provider."""
    return {"date": date, "hi_f": _i(hi), "lo_f": _i(lo), "sky": sky}


def _openmeteo(client, lat, lon, city, _key, days, _lang):
    d = client.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,"
                   "relative_humidity_2m,wind_speed_10m,cloud_cover,uv_index",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "timezone": "auto", "forecast_days": max(1, days + 1),
    }).json()
    cur = d.get("current", {})
    daily = d.get("daily", {})
    his = daily.get("temperature_2m_max") or [cur.get("temperature_2m")]
    los = daily.get("temperature_2m_min") or [cur.get("temperature_2m")]
    codes = daily.get("weather_code") or []
    code = cur.get("weather_code")
    forecast = [_forecast_entry(t, hi, lo, sky_of_wmo(codes[i] if i < len(codes) else None))
                for i, (t, hi, lo) in enumerate(zip(daily.get("time") or [], his, los))]
    return {
        "city": city, "temp_f": _i(cur.get("temperature_2m")),
        "feels_like_f": _i(cur.get("apparent_temperature")),
        "hi_f": _i(his[0] if his else None), "lo_f": _i(los[0] if los else None),
        "desc": _OPENMETEO_CODES.get(code, "Current conditions"),
        "code": code, "sky": sky_of_wmo(code),
        "humidity": _i(cur.get("relative_humidity_2m")),
        "wind_mph": cur.get("wind_speed_10m"), "cloud_cover": _i(cur.get("cloud_cover")),
        "uv": cur.get("uv_index"),
        "forecast": forecast[1:],           # [0] is today: the conditions ARE today
    }


def _openweather(client, lat, lon, city, key, days, lang):
    d = client.get("https://api.openweathermap.org/data/2.5/weather", params={
        "lat": lat, "lon": lon, "appid": key, "units": "imperial", "lang": lang,
    }).json()
    main = d.get("main", {})
    wid = (d.get("weather") or [{}])[0].get("id")
    doc = {
        "city": str(d.get("name") or city), "temp_f": _i(main.get("temp")),
        "feels_like_f": _i(main.get("feels_like")),
        "hi_f": _i(main.get("temp_max")), "lo_f": _i(main.get("temp_min")),
        "desc": str((d.get("weather") or [{}])[0].get("description", "Current conditions")),
        "code": wid, "sky": sky_of_openweather(wid),
        "humidity": _i(main.get("humidity")),
        "wind_mph": (d.get("wind") or {}).get("speed"),
        "cloud_cover": _i((d.get("clouds") or {}).get("all")),
        "uv": None, "forecast": [],
    }
    if days:
        doc["forecast"] = _openweather_days(client, lat, lon, key, lang, days)
    return doc


def _openweather_days(client, lat, lon, key, lang, days):
    """OpenWeather's free plan has no daily endpoint — only /forecast, a list of
    3-hourly slots. So the days are built here: bucket the slots by the CITY's
    local date (the slot `dt` is UTC; the city's offset comes back with it —
    otherwise a wall in Boston splits a European day in half at 7pm) and take
    each day's own min and max. Only daylight slots (9-18) vote on the sky, and
    the WORST of them wins. Today's bucket is dropped."""
    r = client.get("https://api.openweathermap.org/data/2.5/forecast", params={
        "lat": lat, "lon": lon, "appid": key, "units": "imperial", "lang": lang,
    }).json()
    shift = int((r.get("city") or {}).get("timezone") or 0)
    buckets: dict = {}
    for slot in (r.get("list") or []):
        when = datetime.fromtimestamp(int(slot["dt"]) + shift, tz=timezone.utc)
        temp = (slot.get("main") or {}).get("temp")
        if temp is None:
            continue
        b = buckets.setdefault(when.strftime("%Y-%m-%d"),
                               {"hi": temp, "lo": temp, "skies": []})
        b["hi"] = max(b["hi"], temp)
        b["lo"] = min(b["lo"], temp)
        if 9 <= when.hour <= 18:      # the sky of the DAY, not of 3am
            b["skies"].append(sky_of_openweather((slot.get("weather") or [{}])[0].get("id")))
    today = datetime.fromtimestamp(int(time.time()) + shift, tz=timezone.utc).strftime("%Y-%m-%d")
    out = [_forecast_entry(k, b["hi"], b["lo"], _worst_sky(b["skies"]))
           for k, b in sorted(buckets.items()) if k > today]
    return out[:days]


def _weatherapi(client, lat, lon, city, key, days, lang):
    d = client.get("https://api.weatherapi.com/v1/forecast.json", params={
        "key": key, "q": f"{lat},{lon}", "days": max(1, days + 1),
        "aqi": "yes", "pollen": "yes", "lang": lang,
    }).json()
    cur = d.get("current", {})
    fdays = (d.get("forecast") or {}).get("forecastday") or [{}]
    day0 = fdays[0].get("day", {})
    code = (cur.get("condition") or {}).get("code")
    pollen_raw = fdays[0].get("pollen") or cur.get("pollen") or day0.get("pollen") or {}
    return {
        "city": str((d.get("location") or {}).get("name") or city),
        "temp_f": _i(cur.get("temp_f")), "feels_like_f": _i(cur.get("feelslike_f")),
        "hi_f": _i(day0.get("maxtemp_f", cur.get("temp_f"))),
        "lo_f": _i(day0.get("mintemp_f", cur.get("temp_f"))),
        "desc": str((cur.get("condition") or {}).get("text", "Current conditions")),
        "code": code, "sky": sky_of_weatherapi(code),
        "humidity": _i(cur.get("humidity")), "wind_mph": cur.get("wind_mph"),
        "cloud_cover": _i(cur.get("cloud")), "uv": cur.get("uv"),
        "forecast": [_forecast_entry(f.get("date"),
                                     (f.get("day") or {}).get("maxtemp_f"),
                                     (f.get("day") or {}).get("mintemp_f"),
                                     sky_of_weatherapi(((f.get("day") or {}).get("condition") or {}).get("code")))
                     for f in fdays[1:]],
        # WeatherAPI delivers air in the same payload; stash it for _fetch_air.
        "_air": {"epa6": (cur.get("air_quality") or {}).get("us-epa-index"),
                 "pollen": _weatherapi_pollen(pollen_raw)},
    }


def _weatherapi_pollen(payload):
    if not isinstance(payload, dict):
        return {}
    p = {str(k).lower(): v for k, v in payload.items()}
    tree = [p.get(n) for n in ("hazel", "alder", "birch", "oak") if p.get(n) is not None]
    weed = [p.get(n) for n in ("mugwort", "ragweed") if p.get(n) is not None]
    grass, tree_v, weed_v = p.get("grass"), (max(tree) if tree else None), (max(weed) if weed else None)
    vals = [v for v in (grass, tree_v, weed_v) if v is not None]
    return {"grass": grass, "tree": tree_v, "weed": weed_v,
            "overall": max(vals) if vals else None}


def _qweather(client, lat, lon, city, key, days, lang):
    loc = f"{lon:.2f},{lat:.2f}"
    headers = {"Authorization": f"Bearer {key}"}
    now = client.get("https://devapi.qweather.com/v7/weather/now",
                     params={"location": loc, "lang": lang, "unit": "i"},
                     headers=headers).json().get("now", {})
    horizon = "7d" if days > 2 else "3d"
    daily = client.get(f"https://devapi.qweather.com/v7/weather/{horizon}",
                       params={"location": loc, "lang": lang, "unit": "i"},
                       headers=headers).json().get("daily") or [{}]
    day0 = daily[0]
    icon = now.get("icon")
    return {
        "city": city, "temp_f": _i(now.get("temp")),
        "feels_like_f": _i(now.get("feelsLike")),
        "hi_f": _i(day0.get("tempMax", now.get("temp"))),
        "lo_f": _i(day0.get("tempMin", now.get("temp"))),
        "desc": str(now.get("text", "Current conditions")),
        "code": icon, "sky": sky_of_qweather(icon),
        "humidity": _i(now.get("humidity")), "wind_mph": now.get("windSpeed"),
        "cloud_cover": _i(now.get("cloud")), "uv": None,
        "forecast": [_forecast_entry(d.get("fxDate"), d.get("tempMax"), d.get("tempMin"),
                                     sky_of_qweather(d.get("iconDay")))
                     for d in daily[1:]][:days],
    }


_PROVIDERS = {
    "openmeteo": _openmeteo, "openweather": _openweather,
    "weatherapi": _weatherapi, "qweather": _qweather,
}


# ---------------------------------------------------------------------------
# Air. One normalized block whatever the provider — labels on the provider's
# own scale, bands canonical.
# ---------------------------------------------------------------------------
def _openmeteo_air(client, lat, lon):
    return client.get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
        "latitude": lat, "longitude": lon,
        "current": "us_aqi,uv_index,grass_pollen,birch_pollen,ragweed_pollen,weed_pollen",
    }).json().get("current", {})


# Each provider's air fetcher returns (aqi, aqi_label, aqi_band, pollen,
# uv_fallback) on its own scale; _fetch_air composes the canonical block.
def _weatherapi_air(client, key, lat, lon, doc):
    """WeatherAPI ships air inside the weather payload — see `_air` above."""
    stash = doc.pop("_air", {}) or {}
    aqi = _i(stash.get("epa6"))
    label, band = epa6_aqi_level(aqi)
    return aqi, label, band, stash.get("pollen") or {}, None


def _openweather_air(client, key, lat, lon, doc):
    try:
        got = client.get("https://api.openweathermap.org/data/2.5/air_pollution",
                         params={"lat": lat, "lon": lon, "appid": key}).json()
        aqi = _i(got["list"][0]["main"]["aqi"])
    except Exception:  # noqa: BLE001 — air is optional garnish, never fatal
        aqi = None
    label, band = openweather_aqi_level(aqi)
    return aqi, label, band, {}, None


def _qweather_air(client, key, lat, lon, doc):
    try:
        got = client.get("https://devapi.qweather.com/v7/air/now",
                         params={"location": f"{lon:.2f},{lat:.2f}", "lang": "en"},
                         headers={"Authorization": f"Bearer {key}"}).json()
        aqi = _i((got.get("now") or {}).get("aqi"))
    except Exception:  # noqa: BLE001
        aqi = None
    label, band = us_aqi_level(aqi)
    return aqi, label, band, {}, None


def _openmeteo_air_block(client, key, lat, lon, doc):
    """Open-Meteo's keyless air API also carries UV and pollen; the UV rides
    along as the fallback for providers whose weather payload has none."""
    try:
        cur = _openmeteo_air(client, lat, lon)
    except Exception:  # noqa: BLE001
        cur = {}
    aqi = _i(cur.get("us_aqi"))
    label, band = us_aqi_level(aqi)
    tree = cur.get("birch_pollen")
    weed = cur.get("weed_pollen") if cur.get("weed_pollen") is not None else cur.get("ragweed_pollen")
    vals = [v for v in (cur.get("grass_pollen"), tree, weed) if v is not None]
    pollen = {"grass": cur.get("grass_pollen"), "tree": tree, "weed": weed,
              "overall": max(vals) if vals else None} if vals else {}
    return aqi, label, band, pollen, cur.get("uv_index")


_AIR_PROVIDERS = {
    "openmeteo": _openmeteo_air_block, "openweather": _openweather_air,
    "weatherapi": _weatherapi_air, "qweather": _qweather_air,
}


def _fetch_air(client, provider, key, lat, lon, doc):
    aqi, aqi_label, aqi_band, pollen, uv_fallback = \
        _AIR_PROVIDERS.get(provider, _openmeteo_air_block)(client, key, lat, lon, doc)
    uv = doc.get("uv")
    if uv is None:
        uv = uv_fallback

    if aqi is not None and aqi <= 0:      # a provider with nothing usable says 0
        aqi, aqi_label, aqi_band = None, 'Unknown', 'unknown'
    uv_num = _i(uv)
    uv_label, uv_band = uv_level(float(uv)) if uv is not None else ('Unknown', 'unknown')
    overall = pollen.get("overall") if pollen else None
    p_label, p_band = pollen_level(overall)
    return {"aqi": aqi, "aqi_label": aqi_label, "aqi_band": aqi_band,
            "uv": uv_num, "uv_label": uv_label, "uv_band": uv_band,
            "pollen": pollen, "pollen_label": p_label, "pollen_band": p_band}


def _fetch_hourly(client, lat, lon):
    """The next two days of hourly temperatures — always keyless Open-Meteo, so a
    temperature ribbon works whatever the chosen provider. Times are the
    LOCATION's local time; utc_offset_s says what "now" means there."""
    d = client.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "hourly": "temperature_2m",
        "timezone": "auto", "forecast_days": 2,
    }).json()
    hourly = d.get("hourly") or {}
    return {"time": hourly.get("time") or [], "temp_f": None,
            "temp_c": hourly.get("temperature_2m") or [],
            "utc_offset_s": int(d.get("utc_offset_seconds") or 0)}


# ---------------------------------------------------------------------------
# The entry point, in three pieces: resolve the request from settings,
# consult the cache, dispatch to the provider and add the garnish.
# ---------------------------------------------------------------------------
def _resolve_provider(settings):
    """(provider, key, request language) from the merged settings. An unknown
    provider — or a keyed one missing its key — degrades to keyless Open-Meteo,
    so weather works with no key at all."""
    provider = str(settings.get("weather_provider", "openmeteo") or "openmeteo").lower()
    key = str(settings.get("weather_api_key", "") or "").strip()
    if provider not in _PROVIDERS or (provider != "openmeteo" and not key):
        provider = "openmeteo"
    lang = i18n.base_lang(settings.get("language") or "en") or "en"
    return provider, key, lang


def _cache_ttl(settings) -> float:
    """The cache window: the app's polling_rate when present (the merged
    settings arrive per-app), clamped to [1 min, 1 day], else 10 minutes."""
    try:
        return max(60.0, min(86400.0, float(settings.get("polling_rate") or _DEFAULT_TTL)))
    except (TypeError, ValueError):
        return float(_DEFAULT_TTL)


def _fetch_document(provider, key, lat, lon, city, days, air, lang):
    """One provider round-trip (with the Open-Meteo fallback ladder), then the
    garnish: the air block when asked for, the keyless hourly ribbon when
    days > 0. Returns (doc, the provider actually used); raises only when
    Open-Meteo itself fails."""
    with httpx.Client(timeout=10.0) as client:
        doc = None
        if provider != "openmeteo":
            try:
                got = _PROVIDERS[provider](client, lat, lon, city, key, days, lang)
                # A keyed provider that 401s/429s still returns 200-ish JSON
                # with no temperature: treat a missing temp as a failure.
                if got.get("temp_f") is not None:
                    doc = got
                else:
                    log.warning("weather provider %s returned no data; using open-meteo", provider)
            except Exception as e:  # noqa: BLE001
                log.warning("weather provider %s failed (%s); using open-meteo", provider, e)
            if doc is None:
                provider = "openmeteo"
        if doc is None:
            doc = _openmeteo(client, lat, lon, city, key, days, lang)
        if not days:
            doc["forecast"] = []
        if air:
            doc["air"] = _fetch_air(client, provider, key, lat, lon, doc)
        else:
            doc.pop("_air", None)
        if days:
            try:
                h = _fetch_hourly(client, lat, lon)
                # Open-Meteo's hourly endpoint speaks Celsius by default; keep
                # both so no consumer converts.
                h["temp_f"] = [None if c is None else round(c * 9.0 / 5.0 + 32.0, 1)
                               for c in h["temp_c"]]
                doc["hourly"] = h
            except Exception:  # noqa: BLE001 — the ribbon is garnish for most apps
                doc["hourly"] = {"time": [], "temp_f": [], "temp_c": [], "utc_offset_s": 0}
    return doc, provider


def fetch_weather(settings, days: int = 0, air: bool = False) -> dict:
    """The normalized weather document (see the module docstring). Falls back to
    keyless Open-Meteo when a keyed provider is missing its key OR fails /
    returns an error body (bad key, rate-limited, outage). Returns
    ``{ok: False, error, provider}`` only if Open-Meteo itself fails; never
    raises."""
    days = max(0, min(7, int(days or 0)))
    provider, key, lang = _resolve_provider(settings)
    lat, lon, city = _resolve_location(settings)
    ttl = _cache_ttl(settings)

    cache_key = (provider, key, round(lat, 3), round(lon, 3), days, air, lang)
    hit = _cache.get(cache_key)
    if hit and (time.time() - hit[0]) < ttl:
        # Every caller gets its OWN copy. The cache is shared by every app across
        # the plugin threadpool; handing out the stored dict itself would let one
        # mutating app poison weather for all of them until the TTL.
        return copy.deepcopy(hit[1])

    try:
        doc, provider = _fetch_document(provider, key, lat, lon, city, days, air, lang)
        doc.update(ok=True, provider=provider, lat=lat, lon=lon,
                   temp_c=_f2c(doc.get("temp_f")))
        # Cache only successful fetches — and a private copy, for the same
        # isolation reason as above.
        _cache[cache_key] = (time.time(), copy.deepcopy(doc))
        return doc
    except Exception as e:  # noqa: BLE001
        log.warning("weather fetch failed: %s", e)
        return {"ok": False, "error": str(e), "provider": provider}
