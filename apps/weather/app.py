AQI_LABELS = {1: 'GOOD', 2: 'FAIR', 3: 'MODERATE', 4: 'POOR', 5: 'V.POOR'}

OPENMETEO_WEATHER_CODES = {
    0: 'CLEAR',
    1: 'MAINLY CLEAR',
    2: 'PARTLY CLOUDY',
    3: 'OVERCAST',
    45: 'FOG',
    48: 'RIME FOG',
    51: 'LIGHT DRIZZLE',
    53: 'DRIZZLE',
    55: 'HEAVY DRIZZLE',
    61: 'LIGHT RAIN',
    63: 'RAIN',
    65: 'HEAVY RAIN',
    66: 'LIGHT FREEZING RAIN',
    67: 'FREEZING RAIN',
    71: 'LIGHT SNOW',
    73: 'SNOW',
    75: 'HEAVY SNOW',
    77: 'SNOW GRAINS',
    80: 'RAIN SHOWERS',
    81: 'RAIN SHOWERS',
    82: 'HEAVY SHOWERS',
    85: 'SNOW SHOWERS',
    86: 'HEAVY SNOW SHOWERS',
    95: 'THUNDERSTORM',
    96: 'THUNDER HAIL',
    99: 'SEVERE TSTORM',
}


def _pollen_level(val):
    if val is None or val < 1:
        return 'NONE'
    if val < 10:
        return 'LOW'
    if val < 50:
        return 'MOD'
    if val < 200:
        return 'HIGH'
    return 'V.HIGH'


def _uv_level(val):
    if val is None:
        return 'UNKNOWN'
    if val < 3:
        return 'LOW'
    if val < 6:
        return 'MOD'
    if val < 8:
        return 'HIGH'
    if val < 11:
        return 'V.HIGH'
    return 'EXTREME'


def _us_aqi_level(val):
    if val is None:
        return 'UNKNOWN'
    if val <= 50:
        return 'GOOD'
    if val <= 100:
        return 'MOD'
    if val <= 150:
        return 'USG'
    if val <= 200:
        return 'UNHEALTHY'
    if val <= 300:
        return 'V.UNHLTHY'
    return 'HAZARDOUS'


def _weatherapi_aqi_level(val):
    labels = {
        1: 'GOOD',
        2: 'MOD',
        3: 'USG',
        4: 'UNHEALTHY',
        5: 'V.UNHLTHY',
        6: 'HAZARDOUS',
    }
    return labels.get(val, 'UNKNOWN')


def _aqi_color_from_us(value):
    if value is None:
        return 'UNKNOWN'
    if value <= 50:
        return 'GREEN'
    if value <= 100:
        return 'YELLOW'
    if value <= 150:
        return 'ORANGE'
    return 'RED'


def _aqi_color_from_openweather(value):
    if value is None:
        return 'UNKNOWN'
    if value <= 1:
        return 'GREEN'
    if value == 2:
        return 'YELLOW'
    if value == 3:
        return 'ORANGE'
    return 'RED'


def _aqi_color_from_weatherapi(value):
    if value is None:
        return 'UNKNOWN'
    if value <= 1:
        return 'GREEN'
    if value == 2:
        return 'YELLOW'
    if value == 3:
        return 'ORANGE'
    return 'RED'


def _uv_color(val):
    if val is None:
        return 'UNKNOWN'
    if val < 3:
        return 'GREEN'
    if val < 6:
        return 'YELLOW'
    if val < 8:
        return 'ORANGE'
    return 'RED'


def _pollen_color(val):
    if val is None or val < 1:
        return 'NONE'
    if val < 10:
        return 'GREEN'
    if val < 50:
        return 'YELLOW'
    if val < 200:
        return 'ORANGE'
    return 'RED'


def _to_int(value, default=0):
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _convert_temp_from_f(value, temp_unit):
    if value is None:
        return None
    try:
        f_val = float(value)
    except Exception:
        return None
    if temp_unit == 'c':
        return (f_val - 32.0) * (5.0 / 9.0)
    if temp_unit == 'k':
        return (f_val - 32.0) * (5.0 / 9.0) + 273.15
    return f_val


def _short_temp(value, temp_unit):
    """Just the number. The forecast column is a comparison — 24/14 — and repeating the unit
    on every one of them costs four cells and says nothing the conditions page has not."""
    converted = _convert_temp_from_f(value, temp_unit)
    return '--' if converted is None else str(int(round(converted)))


def _format_temp(value, temp_unit):
    converted = _convert_temp_from_f(value, temp_unit)
    if converted is None:
        return '--'
    return f"{int(round(converted))}{temp_unit.upper()}"


def _compact_color(color, mono=False):
    if mono:
        return ''
    return {
        'GREEN': '🟩',
        'YELLOW': '🟨',
        'ORANGE': '🟧',
        'RED': '🟥',
        'NONE': '⬛',
        'UNKNOWN': '⬜',
    }.get(color, color)


def _decorate_status(label, color, cols, mono=False):
    text = str(label or '').strip()
    if mono:                       # colors disabled: show the label only
        return text[:cols]
    swatch = _compact_color(color)
    if not text:
        return swatch

    # Ideal form: multiple swatches on both sides, scaled by available columns.
    # Example: 🟩🟩 GOOD 🟩🟩
    max_side = max(0, (cols - len(text) - 2) // 2)
    if max_side >= 1:
        side = swatch * min(4, max_side)
        return f'{side} {text} {side}'

    # Degrade gracefully as width shrinks.
    if cols >= len(text) + 2:
        return f'{swatch} {text}'
    if cols >= len(text):
        return text[:cols]
    return swatch


def _row(left, right, cols):
    """One full-width line: `left` flush left, `right` flush right. format_lines centres each
    line, so a line already `cols` wide passes through untouched — which is what makes the
    forecast's highs and lows line up in a column you can read down."""
    left, right = str(left), str(right)
    if len(right) >= cols:
        return right[:cols]
    left = left[:cols - len(right) - 1]
    return left + ' ' * (cols - len(left) - len(right)) + right


# The sky: a WORD, an intensity, and a colour.
#
# The word is what you actually want to know — a colour tells you "wet" but not whether that
# is drizzle or a downpour. The intensity is a `-` or `+` suffix rather than a separate word
# (LRain / HRain) because a suffix survives translation: every language gets to keep its own
# noun and the sign means the same thing everywhere. And it is short, which is the whole
# problem: a 15-column line has room for a day, a condition and 24/14, and nothing else.
#
# The colour still comes along when the wall is wide enough for it.
_SKY = {
    #  token       word       suffix  colour tile
    'clear':     ('Sunny',    '',     '\U0001f7e8'),   # yellow
    'pcloudy':   ('PSunny',   '',     '\u2b1c'),       # white
    'cloudy':    ('Cloudy',   '',     '\u2b1c'),
    'fog':       ('Fog',      '',     '\u2b1c'),
    'rainl':     ('Rain',     '-',    '\U0001f7e6'),   # blue
    'rain':      ('Rain',     '',     '\U0001f7e6'),
    'rainh':     ('Rain',     '+',    '\U0001f7e6'),
    'shwr':      ('Shwrs',    '',     '\U0001f7e6'),
    'snowl':     ('Snow',     '-',    '\U0001f7ea'),   # purple
    'snow':      ('Snow',     '',     '\U0001f7ea'),
    'snowh':     ('Snow',     '+',    '\U0001f7ea'),
    'sleet':     ('Sleet',    '',     '\U0001f7ea'),
    'storm':     ('Storm',    '',     '\U0001f7e5'),   # red
    'hail':      ('Hail',     '',     '\U0001f7e5'),
}
_SKY_MAX = 6        # the longest word a forecast column can hold on a 15-wide wall

# Worst-first, for a provider that gives a forecast in slots rather than days (OpenWeather):
# the day is described by the worst thing that happens in it, because that is the thing you
# would have wanted to know before you went out.
_SEVERITY = ('clear', 'pcloudy', 'cloudy', 'fog', 'rainl', 'shwr', 'rain', 'rainh',
             'snowl', 'snow', 'snowh', 'sleet', 'hail', 'storm')


def _sky_word(sky, t):
    """The condition, translated and short enough for the wall.

    The `-`/`+` is dropped rather than the noun when a language's word is already as long as
    the column: knowing it is snow matters more than knowing it is light snow, and a
    truncated noun ("Schne+") tells you neither.
    """
    word, suffix, _ = _SKY.get(sky or 'cloudy', _SKY['cloudy'])
    word = t(word)
    return word + suffix if len(word) + len(suffix) <= _SKY_MAX else word[:_SKY_MAX]


def _sky_tile(sky, mono):
    if mono:
        return ''
    return _SKY.get(sky or 'cloudy', _SKY['cloudy'])[2]


def _sky_of_code(code):
    """Open-Meteo's WMO weather code."""
    if code is None:
        return 'cloudy'
    c = int(code)
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
    }.get(c, 'cloudy')


def _sky_of_openweather(wid):
    """OpenWeather's condition id."""
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


def _sky_of_weatherapi(code):
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


def _sky_of_qweather(icon):
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


def _metric_line(word, value, label, color, cols, mono=False):
    """One metric on one line: `AQI 42 GOOD 🟩`, degraded to whatever fits.

    A tall wall can afford a row per metric; it cannot afford a whole PAGE per
    metric, which is what the three-row layout had to do.
    """
    swatch = '' if mono else _compact_color(color)
    head = f'{word} {value}' if value is not None else str(word)
    for candidate in (f'{head} {label} {swatch}'.rstrip(), f'{head} {label}', head):
        if len(candidate) <= cols:
            return candidate
    return head[:cols]


def _paginate(lines, rows):
    """Split lines over as few pages as possible, balanced.

    Balanced matters: 6 lines on a 5-row wall chunked greedily gives a full page
    and then one lonely line on an otherwise blank screen, which reads as a bug.
    Split it 3/3 instead.
    """
    if not lines:
        return []
    pages = max(1, -(-len(lines) // rows))          # ceil: fewest pages that fit
    per = max(1, -(-len(lines) // pages))           # ceil: spread evenly over them
    return [lines[i:i + per] for i in range(0, len(lines), per)]


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import time
    from datetime import datetime

    import requests

    # Global Language (when the companion injects i18n). Open-Meteo maps codes to
    # English text we translate ourselves; the keyed providers can return native
    # condition text if we ask, so we pass the language through to them.
    # Base language for the providers' `lang` param — they want 'en'/'fr', not a
    # regional 'en-gb' (the English variants all return English weather text anyway).
    lang = (i18n.lang.split('-')[0] if i18n is not None else 'en')

    def t(s):
        return i18n.t(s, "weather") if i18n is not None else s

    # --- Polling state (persists across calls on this plugin instance) ---
    state = getattr(fetch, '_state', None)
    if state is None:
        state = {
            'last_sig': None,
            'last_polled_at': 0.0,
            'last_pages': None,
        }
        setattr(fetch, '_state', state)

    api_key = settings.get('weather_api_key', '')
    zip_code = settings.get('zip_code', '02118')
    weather_provider = settings.get('weather_provider', 'openweather')
    temp_unit = str(settings.get('temperature_unit', 'f')).lower()
    if temp_unit not in ('f', 'c', 'k'):
        temp_unit = 'f'
    no_color = settings.get('disable_colors', 'no') == 'yes'
    show_aqi = settings.get('show_aqi', 'yes') == 'yes'
    show_uv = settings.get('show_uv', 'yes') == 'yes' and weather_provider in ('openmeteo', 'weatherapi')
    show_pollen = settings.get('show_pollen', 'yes') == 'yes' and weather_provider in ('openmeteo', 'weatherapi')
    try:
        forecast_days = max(0, min(5, int(settings.get('forecast_days', 3) or 0)))
    except (TypeError, ValueError):
        forecast_days = 3
    polling_seconds = max(30.0, min(86400.0, float(settings.get('polling_rate', 300) or 300)))
    openmeteo_air = None

    # Resolve location: per-app override > global lat/lon > geocode zip_code.
    # Any bad input / geocode failure falls back to Boston instead of crashing.
    app_location = settings.get('location', '').strip()
    loc_lat = settings.get('location_lat', '')
    loc_lon = settings.get('location_lon', '')
    loc_name = settings.get('location_name', '')
    _lat, _lon, _city = 42.3496, -71.0783, 'BOSTON'
    try:
        if app_location and ',' in app_location:
            if '|' in app_location:
                coords, _city = app_location.split('|', 1)
                _city = _city.strip()
            else:
                coords = app_location
                _city = 'LOCATION'
            parts = coords.split(',', 1)
            _lat, _lon = float(parts[0]), float(parts[1])
        elif loc_lat and loc_lon:
            _lat, _lon = float(loc_lat), float(loc_lon)
            _city = loc_name.split(',')[0].strip() if loc_name else 'Location'
        else:
            import re
            _geo_params = {'q': zip_code, 'format': 'json', 'limit': 1}
            if re.fullmatch(r'\d{5}', str(zip_code).strip()):   # US ZIP — 02118 also exists abroad
                _geo_params['countrycodes'] = 'us'
            geo = requests.get(
                'https://nominatim.openstreetmap.org/search', params=_geo_params,
                timeout=5, headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'}
            ).json()
            if geo:
                _lat, _lon = float(geo[0]['lat']), float(geo[0]['lon'])
                _city = geo[0].get('display_name', zip_code).split(',')[0].strip()
    except (ValueError, KeyError, IndexError, TypeError, requests.RequestException):
        _lat, _lon, _city = 42.3496, -71.0783, 'BOSTON'

    settings_sig = (
        api_key, _lat, _lon, weather_provider, temp_unit,
        show_aqi, show_uv, show_pollen, polling_seconds, lang,
    )
    now_ts = time.time()
    sig_changed = settings_sig != state['last_sig']
    due_for_poll = (now_ts - state['last_polled_at']) >= polling_seconds
    if not sig_changed and not due_for_poll and state['last_pages'] is not None:
        return state['last_pages']

    def _normalize_weatherapi_pollen(payload):
        if not isinstance(payload, dict):
            return {}
        pollen = {str(k).lower(): v for k, v in payload.items()}
        tree_vals = [pollen.get(name) for name in ('hazel', 'alder', 'birch', 'oak') if pollen.get(name) is not None]
        weed_vals = [pollen.get(name) for name in ('mugwort', 'ragweed') if pollen.get(name) is not None]
        grass = pollen.get('grass')
        tree = max(tree_vals) if tree_vals else None
        weed = max(weed_vals) if weed_vals else None
        vals = [v for v in (grass, tree, weed) if v is not None]
        return {
            'grass': grass,
            'tree': tree,
            'weed': weed,
            'overall': max(vals) if vals else None,
        }

    def _openweather_days():
        """OpenWeather's free plan has no daily endpoint — only /forecast, which is a list of
        3-hourly slots. So the days are built here: bucket the slots by local date and take
        each day's own min and max. Today's bucket is dropped; the conditions page IS today.

        The slot's `dt` is UTC and the city's timezone offset comes back with it, so the
        bucketing is done in the CITY's day, not this machine's — otherwise a wall in Boston
        would split a European day in half at 7pm.
        """
        if not forecast_days:
            return []
        try:
            r = requests.get(
                'https://api.openweathermap.org/data/2.5/forecast',
                params={'lat': _lat, 'lon': _lon, 'appid': api_key,
                        'units': 'imperial', 'lang': lang},
                timeout=10).json()
            shift = int((r.get('city') or {}).get('timezone') or 0)
            buckets = {}
            for slot in (r.get('list') or []):
                when = datetime.utcfromtimestamp(int(slot['dt']) + shift)
                key = when.strftime('%Y-%m-%d')
                temp = (slot.get('main') or {}).get('temp')
                if temp is None:
                    continue
                b = buckets.setdefault(key, {'date': key, 'hi': temp, 'lo': temp, 'skies': []})
                b['hi'] = max(b['hi'], temp)
                b['lo'] = min(b['lo'], temp)
                # The sky of the DAY, not of 3am: only the daylight slots get a vote.
                if 9 <= when.hour <= 18:
                    b['skies'].append(_sky_of_openweather((slot.get('weather') or [{}])[0].get('id')))
            today = datetime.utcfromtimestamp(int(time.time()) + shift).strftime('%Y-%m-%d')
            out = []
            for key in sorted(buckets):
                if key <= today:
                    continue
                b = buckets[key]
                skies = b['skies'] or ['cloudy']
                # The worst sky of the day is the one worth knowing about: a day with one
                # thunderstorm in it is a stormy day, however sunny the rest of it was.
                b['sky'] = max(skies, key=lambda k: _SEVERITY.index(k)
                               if k in _SEVERITY else 0)
                out.append(b)
            return out[:forecast_days]
        except Exception:
            return []

    def _fetch_openweather_weather():
        payload = requests.get(
            'https://api.openweathermap.org/data/2.5/weather',
            params={'lat': _lat, 'lon': _lon, 'appid': api_key, 'units': 'imperial', 'lang': lang},
            timeout=10
        ).json()
        return {
            'days': _openweather_days(),
            'city': payload.get('name', _city),
            'temp': int(payload['main']['temp']),
            'feels_like': int(payload['main']['feels_like']),
            'hi': int(payload['main']['temp_max']),
            'lo': int(payload['main']['temp_min']),
            'desc': payload['weather'][0]['description'],
            'lat': _lat,
            'lon': _lon,
        }

    def _fetch_openmeteo_weather():
        weather = requests.get(
            'https://api.open-meteo.com/v1/forecast',
            params={
                'latitude': _lat,
                'longitude': _lon,
                'current': 'temperature_2m,apparent_temperature,weather_code,uv_index',
                'daily': 'temperature_2m_max,temperature_2m_min,weather_code',
                'temperature_unit': 'fahrenheit',
                'timezone': 'auto',
                'forecast_days': forecast_days + 1,
            },
            timeout=10
        ).json()
        current = weather.get('current', {})
        daily = weather.get('daily', {})
        hi_values = daily.get('temperature_2m_max') or [current.get('temperature_2m')]
        lo_values = daily.get('temperature_2m_min') or [current.get('temperature_2m')]
        codes = daily.get('weather_code') or []
        days = [{'date': d, 'hi': hi, 'lo': lo,
                 'sky': _sky_of_code(codes[i] if i < len(codes) else None)}
                for i, (d, hi, lo) in enumerate(zip(daily.get('time') or [],
                                                    hi_values, lo_values))]
        return {
            'days': days[1:],           # [0] is today, which the conditions page already is
            'city': _city,
            'temp': int(round(current.get('temperature_2m', 0))),
            'feels_like': int(round(current.get('apparent_temperature', current.get('temperature_2m', 0)))),
            'hi': int(round(hi_values[0] if hi_values else 0)),
            'lo': int(round(lo_values[0] if lo_values else 0)),
            'desc': t(OPENMETEO_WEATHER_CODES.get(current.get('weather_code'), 'CURRENT CONDITIONS')),
            'lat': _lat,
            'lon': _lon,
            'uv': current.get('uv_index'),
        }

    def _fetch_weatherapi_weather():
        payload = requests.get(
            'https://api.weatherapi.com/v1/forecast.json',
            params={
                'key': api_key,
                'q': f'{_lat},{_lon}',
                'days': forecast_days + 1,
                'aqi': 'yes',
                'pollen': 'yes',
                'lang': lang,
            },
            timeout=10
        ).json()
        location = payload.get('location', {})
        current = payload.get('current', {})
        forecastdays = (payload.get('forecast') or {}).get('forecastday') or [{}]
        forecast = forecastdays[0]
        day = forecast.get('day', {})
        pollen = forecast.get('pollen') or current.get('pollen') or day.get('pollen') or {}
        return {
            'city': str(location.get('name', _city)),
            'temp': int(round(current.get('temp_f', 0))),
            'feels_like': int(round(current.get('feelslike_f', current.get('temp_f', 0)))),
            'hi': int(round(day.get('maxtemp_f', current.get('temp_f', 0)))),
            'lo': int(round(day.get('mintemp_f', current.get('temp_f', 0)))),
            'desc': str((current.get('condition') or {}).get('text', t('CURRENT CONDITIONS'))),
            'lat': location.get('lat'),
            'lon': location.get('lon'),
            'uv': current.get('uv'),
            'weatherapi_aqi': ((current.get('air_quality') or {}).get('us-epa-index')),
            'pollen': _normalize_weatherapi_pollen(pollen),
            'days': [{'date': f.get('date'),
                      'hi': (f.get('day') or {}).get('maxtemp_f'),
                      'lo': (f.get('day') or {}).get('mintemp_f'),
                      'sky': _sky_of_weatherapi(((f.get('day') or {}).get('condition') or {}).get('code'))}
                     for f in forecastdays[1:]],
        }

    def _fetch_qweather_weather():
        location = f'{_lon:.2f},{_lat:.2f}'
        headers = {'Authorization': f'Bearer {api_key}'}

        now_r = requests.get(
            'https://devapi.qweather.com/v7/weather/now',
            params={'location': location, 'lang': lang, 'unit': 'i'},
            headers=headers,
            timeout=10
        ).json()
        now = now_r.get('now', {})

        daily_r = requests.get(
            'https://devapi.qweather.com/v7/weather/7d' if forecast_days > 2
            else 'https://devapi.qweather.com/v7/weather/3d',
            params={'location': location, 'lang': lang, 'unit': 'i'},
            headers=headers,
            timeout=10
        ).json()
        all_days = daily_r.get('daily') or [{}]
        first_day = all_days[0]

        return {
            'days': [{'date': d.get('fxDate'),
                      'hi': _to_int(d.get('tempMax')), 'lo': _to_int(d.get('tempMin')),
                      'sky': _sky_of_qweather(d.get('iconDay'))}
                     for d in all_days[1:]],
            'city': _city,
            'temp': _to_int(now.get('temp')),
            'feels_like': _to_int(now.get('feelsLike'), _to_int(now.get('temp'))),
            'hi': _to_int(first_day.get('tempMax'), _to_int(now.get('temp'))),
            'lo': _to_int(first_day.get('tempMin'), _to_int(now.get('temp'))),
            'desc': str(now.get('text', t('CURRENT CONDITIONS'))),
            'lat': _lat,
            'lon': _lon,
        }

    def _fetch_openweather_aqi(lat, lon):
        aq = requests.get(
            'https://api.openweathermap.org/data/2.5/air_pollution',
            params={'lat': lat, 'lon': lon, 'appid': api_key},
            timeout=10
        ).json()
        return aq['list'][0]['main']['aqi']

    def _fetch_openmeteo_air(lat, lon):
        return requests.get(
            'https://air-quality-api.open-meteo.com/v1/air-quality',
            params={
                'latitude': lat,
                'longitude': lon,
                'current': 'us_aqi,uv_index,grass_pollen,birch_pollen,ragweed_pollen,weed_pollen',
            },
            timeout=10
        ).json().get('current', {})

    def _fetch_qweather_aqi(lat, lon):
        headers = {'Authorization': f'Bearer {api_key}'}
        location = f'{lon:.2f},{lat:.2f}'
        aq = requests.get(
            'https://devapi.qweather.com/v7/air/now',
            params={'location': location, 'lang': 'en'},
            headers=headers,
            timeout=10
        ).json()
        return _to_int((aq.get('now') or {}).get('aqi'), None)

    def _get_openmeteo_air(lat, lon):
        nonlocal openmeteo_air
        if openmeteo_air is None:
            openmeteo_air = _fetch_openmeteo_air(lat, lon)
        return openmeteo_air

    try:
        if weather_provider == 'openmeteo':
            weather = _fetch_openmeteo_weather()
        elif weather_provider == 'weatherapi':
            weather = _fetch_weatherapi_weather()
        elif weather_provider == 'qweather':
            weather = _fetch_qweather_weather()
        else:
            weather = _fetch_openweather_weather()

        cols = get_cols()
        rows = get_rows()
        narrow = cols <= 12
        feels_word = t('FLS') if narrow else t('FEELS')
        pollen_word = t('POL') if narrow else t('POLLEN')
        sun_exposure_text = t('SUN UV') if narrow else t('SUN EXPOSURE')
        grass_word = t('GRS') if narrow else t('GRASS')
        tree_word = t('TRE') if narrow else t('TREE')
        weed_word = t('WED') if narrow else t('WEED')

        temp = _format_temp(weather.get('temp'), temp_unit)
        feels = f"{feels_word} {_format_temp(weather.get('feels_like'), temp_unit)}"
        desc = weather['desc']
        hi = f"H {_format_temp(weather.get('hi'), temp_unit)}"
        lo = f"L {_format_temp(weather.get('lo'), temp_unit)}"
        lat = weather['lat']
        lon = weather['lon']

        # --- gather the optional metrics BEFORE laying anything out -------------
        # How many of AQI / UV / pollen the provider actually gave us decides how
        # many pages we need, so none of them can be known too late.
        aqi_num = aqi_label = aqi_color = None
        if show_aqi:
            try:
                if weather_provider == 'openmeteo':
                    raw_aqi = _get_openmeteo_air(lat, lon).get('us_aqi')
                    aqi_num = None if raw_aqi is None else int(round(float(raw_aqi)))
                    aqi_label = _us_aqi_level(aqi_num)
                    aqi_color = _aqi_color_from_us(aqi_num)
                elif weather_provider == 'weatherapi':
                    raw_aqi = weather.get('weatherapi_aqi')
                    aqi_num = int(raw_aqi) if raw_aqi is not None else None
                    aqi_label = _weatherapi_aqi_level(aqi_num)
                    aqi_color = _aqi_color_from_weatherapi(aqi_num)
                elif weather_provider == 'qweather':
                    aqi_num = _fetch_qweather_aqi(lat, lon)
                    aqi_label = _us_aqi_level(aqi_num)
                    aqi_color = _aqi_color_from_us(aqi_num)
                else:
                    aqi_num = _fetch_openweather_aqi(lat, lon)
                    aqi_label = AQI_LABELS.get(aqi_num, 'UNKNOWN')
                    aqi_color = _aqi_color_from_openweather(aqi_num)
                if aqi_num is None or aqi_num <= 0:      # provider had nothing usable
                    aqi_num = None
            except Exception:
                aqi_num = None

        uv_num = uv_label = uv_color = None
        if show_uv:
            try:
                uv_value = weather.get('uv') if weather_provider in ('openmeteo', 'weatherapi') else None
                if uv_value is not None:
                    uv_num = int(round(float(uv_value)))
                    uv_label = _uv_level(float(uv_value))
                    uv_color = _uv_color(float(uv_value))
            except Exception:
                uv_num = None

        pollen_overall = None
        pollen_parts = []            # 'GRASS LOW 🟩' — one per component we got
        if show_pollen:
            try:
                if weather_provider == 'weatherapi':
                    pollen = weather.get('pollen') or {}
                    grass, birch = pollen.get('grass'), pollen.get('tree')
                    ragweed = weed = pollen.get('weed')
                elif weather_provider == 'openmeteo':
                    curr = _get_openmeteo_air(lat, lon)
                    grass = curr.get('grass_pollen')
                    birch = curr.get('birch_pollen')
                    ragweed = curr.get('ragweed_pollen')
                    weed = curr.get('weed_pollen')
                else:
                    grass = birch = ragweed = weed = None

                vals = [v for v in (grass, birch, ragweed, weed) if v is not None]
                if vals:
                    pollen_overall = max(vals)
                    for word, val in ((grass_word, grass), (tree_word, birch),
                                      (weed_word, weed or ragweed)):
                        if val is not None:
                            pollen_parts.append(_metric_line(
                                word, None, t(_pollen_level(val)), _pollen_color(val), cols, no_color))
            except Exception:
                pollen_overall, pollen_parts = None, []

        # --- the forecast --------------------------------------------------------
        # A day per line: what the sky will do, and the high/low in a column you can read
        # down. The FORMAT is chosen once for the whole page, from the longest condition on
        # it, so the columns line up — a line that shrinks its day to make room for "PSunny"
        # while its neighbour does not is a list you have to read twice.
        fc_lines = []
        if forecast_days and rows >= 3:
            days = []
            for d in (weather.get('days') or [])[:forecast_days]:
                if d.get('hi') is None or d.get('lo') is None:
                    continue
                try:
                    dt = datetime.strptime(str(d.get('date'))[:10], '%Y-%m-%d')
                except (TypeError, ValueError):
                    continue
                days.append((dt, d, _sky_word(d.get('sky'), t)))

            if days:
                word_w = max(len(w) for _, _, w in days)
                temp_w = max(len(f"{_short_temp(d['hi'], temp_unit)}/"
                                 f"{_short_temp(d['lo'], temp_unit)}") for _, d, _ in days)

                # The day shrinks before the condition does: "We" is still Wednesday, but a
                # truncated condition is not a condition. One letter is a last resort — Tue
                # and Thu, Sat and Sun share one.
                day_w = next((n for n in (3, 2, 1)
                              if n + 1 + word_w + 1 + temp_w <= cols), 1)
                # …and the colour flap only if it costs nobody a letter.
                tile = not no_color and (2 + day_w + 1 + word_w + 1 + temp_w) <= cols

                for dt, d, word in days:
                    day = (i18n.weekday(dt, short=True) if i18n is not None
                           else dt.strftime('%a'))
                    day = day.replace('.', '')[:day_w]
                    left = f'{day} {word}'
                    if tile:
                        left = f'{_sky_tile(d.get("sky"), no_color)} {left}'
                    right = (f"{_short_temp(d['hi'], temp_unit)}/"
                             f"{_short_temp(d['lo'], temp_unit)}")
                    fc_lines.append(_row(left, right, cols))

        # --- the pages ----------------------------------------------------------
        # Only one location is supported, so we don't repeat it on every page.
        if rows >= 4:
            # A tall wall can say all of this at once. It used to page through up to
            # five near-empty screens instead, each padded out with a "PROV OPENMETEO"
            # line nobody asked for — burning exactly the room the wall was bought
            # for. One row per metric, over as few pages as they fit on.
            lines = [f'{temp} {feels}', f'{hi} {lo}', desc]
            if aqi_num is not None:
                lines.append(_metric_line('AQI', aqi_num, t(aqi_label), aqi_color, cols, no_color))
            if uv_num is not None:
                lines.append(_metric_line('UV', uv_num, t(uv_label), uv_color, cols, no_color))
            if pollen_overall is not None:
                lines.append(_metric_line(pollen_word, None, t(_pollen_level(pollen_overall)),
                                          _pollen_color(pollen_overall), cols, no_color))
                if len(lines) + len(pollen_parts) <= rows:     # room for the breakdown too
                    lines.extend(pollen_parts)
            pages = [format_lines(*chunk) for chunk in _paginate(lines, rows)]
        else:
            if rows == 1:
                pages = [format_lines(f'{temp} {desc}')]
            elif rows == 2:
                pages = [
                    format_lines(f'{temp} {feels}', desc),
                    format_lines(f'{hi} {lo}', desc),
                ]
            else:
                pages = [format_lines(f'{temp} {feels}', f'{hi} {lo}', desc)]

            if aqi_num is not None:
                aqi_display = _decorate_status(t(aqi_label), aqi_color, cols, no_color)
                if rows == 1:
                    pages.append(format_lines(f'AQI {aqi_display}'))
                elif rows == 2:
                    pages.append(format_lines(f'AQI {aqi_num}', aqi_display))
                else:
                    pages.append(format_lines(t('AIR QUALITY'), f'AQI {aqi_num}', aqi_display))

            if uv_num is not None:
                uv_display = _decorate_status(t(uv_label), uv_color, cols, no_color)
                if rows == 1:
                    pages.append(format_lines(f'UV {uv_display}'))
                elif rows == 2:
                    pages.append(format_lines(f'UV {uv_num}', uv_display))
                else:
                    pages.append(format_lines(sun_exposure_text, f'UV {uv_num}', uv_display))

            if pollen_overall is not None:
                overall_display = _decorate_status(t(_pollen_level(pollen_overall)),
                                                   _pollen_color(pollen_overall), cols, no_color)
                if rows == 1:
                    pages.append(format_lines(f'{pollen_word} {overall_display}'))
                else:
                    pages.append(format_lines(pollen_word, overall_display))
                    if pollen_parts:
                        pages.append(format_lines(*pollen_parts))

        if fc_lines:
            # A header plus as many days as the wall has room for; the rest turn the page.
            for chunk in _paginate(fc_lines, rows - 1):
                pages.append(format_lines(t('FORECAST'), *chunk))

        state['last_pages'] = pages
        state['last_polled_at'] = now_ts
        state['last_sig'] = settings_sig
        return pages
    except Exception:
        # On transient error, reuse last good pages if available
        if state['last_pages'] is not None:
            return state['last_pages']
        return [format_lines('WEATHER', 'ERROR', 'CHECK API KEY')]


def trigger(settings, conditions):
    """Fire on severe weather, temperature threshold, rain starting, rapid temp change, UV, or wind."""
    import requests

    condition = conditions.get('condition', 'severe')
    threshold_f = float(conditions.get('temp_threshold', 90))
    uv_threshold = float(conditions.get('uv_threshold', 7))
    wind_threshold = float(conditions.get('wind_threshold', 25))

    SEVERE_CODES = {65, 67, 75, 77, 82, 86, 95, 96, 99}
    RAIN_CODES = {51, 53, 55, 61, 63, 65, 66, 67, 80, 81, 82}
    DRY_CODES = {0, 1, 2, 3, 45, 48}

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'last_code': None, 'last_temp': None}
        setattr(trigger, '_state', state)

    try:
        loc_lat = settings.get('location_lat', '')
        loc_lon = settings.get('location_lon', '')
        if loc_lat and loc_lon:
            lat, lon = float(loc_lat), float(loc_lon)
        else:
            zip_code = settings.get('zip_code', '02118')
            geo = requests.get(
                f'https://nominatim.openstreetmap.org/search?q={zip_code}&format=json&limit=1',
                timeout=5, headers={'User-Agent': 'SplitFlapOS/1.0'}
            ).json()
            if not geo:
                return False
            lat, lon = float(geo[0]['lat']), float(geo[0]['lon'])

        data = requests.get(
            f'https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}'
            '&current=temperature_2m,weather_code,uv_index,wind_speed_10m'
            '&temperature_unit=fahrenheit&wind_speed_unit=mph',
            timeout=8
        ).json()
        current = data.get('current', {})
        temp_f = current.get('temperature_2m')
        code = int(current.get('weather_code') or 0)
        uv = current.get('uv_index')
        wind = current.get('wind_speed_10m')

        if condition == 'severe':
            return code in SEVERE_CODES

        if condition == 'temp_above' and temp_f is not None:
            return float(temp_f) >= threshold_f

        if condition == 'temp_below' and temp_f is not None:
            return float(temp_f) <= threshold_f

        if condition == 'rain_starting':
            prev_code = state['last_code']
            state['last_code'] = code
            was_dry = prev_code is not None and prev_code in DRY_CODES
            now_rain = code in RAIN_CODES
            return was_dry and now_rain

        if condition == 'rapid_temp_change' and temp_f is not None:
            prev_temp = state['last_temp']
            state['last_temp'] = float(temp_f)
            if prev_temp is not None:
                return abs(float(temp_f) - prev_temp) >= threshold_f
            return False

        if condition == 'uv_high' and uv is not None:
            return float(uv) >= uv_threshold

        if condition == 'wind_high' and wind is not None:
            return float(wind) >= wind_threshold

    except Exception:
        raise
    return False
