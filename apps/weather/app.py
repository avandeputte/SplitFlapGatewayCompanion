"""Weather — current conditions, forecast, and the air you'll breathe out there.

All PROVIDER knowledge lives in the companion's shared weather helper: this app
opts in with a ``get_weather`` parameter and receives one normalized document —
current conditions, a canonical ``sky`` token per forecast day, and air
quality/UV/pollen already classified into bands. What remains here is entirely
presentation: which of it fits this wall, in what order, in whose language.

On a host with no helper to inject, ``_fallback_fetch`` keeps the app working
via keyless Open-Meteo (current + forecast + air), with the keyed providers
available only under the companion.
"""


# =============================================================================
# SHARED — the weather DATA both surfaces read: one normalized document via
# _conditions() (the companion's injected get_weather helper, else the keyless
# Open-Meteo fallback), temperature conversion, the spelled-out sky phrases —
# and the trigger (surface-independent by contract).
# =============================================================================


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


# The condition spelled OUT for a wide Matrix wall, where there is room for the whole
# phrase instead of the narrow wall's 6-char word + '-'/'!' intensity mark. The eight
# single-word forms are the same keys _SKY uses, so they are already translated; the six
# two-word phrases fall back to English until translated — acceptable because they show
# ONLY on a wide wall (the narrow wall keeps the fully-translated short word).
_SKY_FULL = {
    'clear': 'Sunny', 'pcloudy': 'Partly cloudy', 'cloudy': 'Cloudy', 'fog': 'Fog',
    'rainl': 'Light rain', 'rain': 'Rain', 'rainh': 'Heavy rain', 'shwr': 'Showers',
    'snowl': 'Light snow', 'snow': 'Snow', 'snowh': 'Heavy snow', 'sleet': 'Sleet',
    'storm': 'Storm', 'hail': 'Hail',
}


def _sky_phrase(sky, t):
    """The condition, spelled out and translated where the catalog has it."""
    return t(_SKY_FULL.get(sky or 'cloudy', 'Cloudy'))


def _fallback_fetch(settings, days, air):
    """With no injected helper (a bare host), keyless Open-Meteo keeps the app
    working. Same document shape the helper returns, minus the keyed
    providers."""
    import requests

    lat_s = str(settings.get('location_lat', '') or '').strip()
    lon_s = str(settings.get('location_lon', '') or '').strip()
    lat, lon, city = 42.3496, -71.0783, 'Boston'
    try:
        if lat_s and lon_s:
            lat, lon = float(lat_s), float(lon_s)
            city = (str(settings.get('location_name', '') or '').split(',')[0].strip()
                    or 'Location')
        else:
            import re
            q = str(settings.get('zip_code', '02118') or '02118').strip()
            params = {'q': q, 'format': 'json', 'limit': 1}
            if re.fullmatch(r'\d{5}', q):
                params['countrycodes'] = 'us'
            geo = requests.get('https://nominatim.openstreetmap.org/search', params=params,
                               timeout=5, headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'}).json()
            if geo:
                lat, lon = float(geo[0]['lat']), float(geo[0]['lon'])
                city = geo[0].get('display_name', q).split(',')[0].strip()
    except Exception:
        pass

    _SKY_OF_WMO = {0: 'clear', 1: 'clear', 2: 'pcloudy', 3: 'cloudy', 45: 'fog', 48: 'fog',
                   51: 'rainl', 53: 'rainl', 55: 'rain', 56: 'sleet', 57: 'sleet',
                   61: 'rainl', 63: 'rain', 65: 'rainh', 66: 'sleet', 67: 'sleet',
                   71: 'snowl', 73: 'snow', 75: 'snowh', 77: 'snow', 80: 'shwr',
                   81: 'shwr', 82: 'rainh', 85: 'snowl', 86: 'snowh',
                   95: 'storm', 96: 'hail', 99: 'hail'}
    _DESC = {0: 'Clear', 1: 'Mainly clear', 2: 'Partly cloudy', 3: 'Overcast', 45: 'Fog',
             48: 'Rime fog', 51: 'Light drizzle', 53: 'Drizzle', 55: 'Heavy drizzle',
             61: 'Light rain', 63: 'Rain', 65: 'Heavy rain', 71: 'Light snow', 73: 'Snow',
             75: 'Heavy snow', 80: 'Rain showers', 81: 'Rain showers', 82: 'Heavy showers',
             95: 'Thunderstorm', 96: 'Thunder hail', 99: 'Severe tstorm'}

    d = requests.get('https://api.open-meteo.com/v1/forecast', params={
        'latitude': lat, 'longitude': lon,
        'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,uv_index',
        'daily': 'temperature_2m_max,temperature_2m_min,weather_code',
        'temperature_unit': 'fahrenheit', 'timezone': 'auto',
        'forecast_days': max(1, days + 1)}, timeout=10).json()
    cur, daily = d.get('current', {}), d.get('daily', {})
    his = daily.get('temperature_2m_max') or [cur.get('temperature_2m')]
    los = daily.get('temperature_2m_min') or [cur.get('temperature_2m')]
    codes = daily.get('weather_code') or []
    code = cur.get('weather_code')
    doc = {
        'ok': True, 'provider': 'openmeteo', 'city': city, 'lat': lat, 'lon': lon,
        'temp_f': _to_int(cur.get('temperature_2m')),
        'feels_like_f': _to_int(cur.get('apparent_temperature'), _to_int(cur.get('temperature_2m'))),
        'humidity': cur.get('relative_humidity_2m'),
        'hi_f': _to_int(his[0] if his else None), 'lo_f': _to_int(los[0] if los else None),
        'desc': _DESC.get(code, 'Current conditions'), 'code': code,
        'sky': _SKY_OF_WMO.get(code, 'cloudy'), 'uv': cur.get('uv_index'),
        'forecast': [{'date': t2, 'hi_f': _to_int(hi), 'lo_f': _to_int(lo),
                      'sky': _SKY_OF_WMO.get(codes[i] if i < len(codes) else None, 'cloudy')}
                     for i, (t2, hi, lo) in enumerate(zip(daily.get('time') or [], his, los))][1:],
    }
    if air:
        def _band(v, steps):
            for limit, label, band in steps:
                if v <= limit:
                    return label, band
            return steps[-1][1], steps[-1][2]
        try:
            a = requests.get('https://air-quality-api.open-meteo.com/v1/air-quality', params={
                'latitude': lat, 'longitude': lon,
                'current': 'us_aqi,uv_index,grass_pollen,birch_pollen,ragweed_pollen,weed_pollen',
            }, timeout=10).json().get('current', {})
        except Exception:
            a = {}
        aqi = a.get('us_aqi')
        aqi = None if aqi is None else _to_int(aqi)
        aqi_label, aqi_band = (('Unknown', 'unknown') if aqi is None else _band(
            aqi, [(50, 'Good', 'good'), (100, 'Mod', 'moderate'), (150, 'USG', 'poor'),
                  (10 ** 6, 'Unhealthy', 'bad')]))
        uv = a.get('uv_index', doc.get('uv'))
        uv_label, uv_band = (('Unknown', 'unknown') if uv is None else _band(
            float(uv), [(2.9, 'Low', 'good'), (5.9, 'Mod', 'moderate'),
                        (7.9, 'High', 'poor'), (10 ** 6, 'V.High', 'bad')]))
        tree = a.get('birch_pollen')
        weed = a.get('weed_pollen') if a.get('weed_pollen') is not None else a.get('ragweed_pollen')
        vals = [v for v in (a.get('grass_pollen'), tree, weed) if v is not None]
        pollen = ({'grass': a.get('grass_pollen'), 'tree': tree, 'weed': weed,
                   'overall': max(vals)} if vals else {})
        overall = pollen.get('overall')
        p_label, p_band = (('None', 'none') if not overall or overall < 1 else _band(
            overall, [(9.9, 'Low', 'good'), (49.9, 'Mod', 'moderate'),
                      (199.9, 'High', 'poor'), (10 ** 6, 'V.High', 'bad')]))
        doc['air'] = {'aqi': aqi, 'aqi_label': aqi_label, 'aqi_band': aqi_band,
                      'uv': None if uv is None else _to_int(uv), 'uv_label': uv_label,
                      'uv_band': uv_band, 'pollen': pollen,
                      'pollen_label': p_label, 'pollen_band': p_band}
    return doc


def _conditions(settings, get_weather, days, air):
    """The ONE weather document every surface renders: the companion's injected
    ``get_weather`` helper when the host provides it, else the keyless Open-Meteo
    fallback — so the flap pages and the matrix card always describe the same
    weather."""
    if get_weather is not None:
        return get_weather(days=days, air=air)
    return _fallback_fetch(settings, days, air)


def trigger(settings, conditions):
    """Fire on severe weather, temperature threshold, rain starting, rapid temp change, UV, or wind.

    Self-contained keyless Open-Meteo on purpose: triggers get no injected
    helpers (their contract is ``trigger(settings, conditions)`` on both
    runtimes), and a trigger poll must stay one cheap call."""
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
                timeout=5, headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'}
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


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================


# ---------------------------------------------------------------------------
# Bands -> colors. The helper classifies every scale (US AQI, OpenWeather's
# 1-5, WeatherAPI's 1-6, UV, pollen) into canonical bands, so ONE map colors
# them all.
# ---------------------------------------------------------------------------
_BAND_COLOR = {
    'good': 'GREEN', 'moderate': 'YELLOW', 'poor': 'ORANGE', 'bad': 'RED',
    'none': 'NONE', 'unknown': 'UNKNOWN',
}


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


def _balance(text, swatch, cols):
    """Center `text` between equal runs of `swatch`, scaled to the width — the
    balanced form a lone tile floating at one end never had. Example, wide:
    🟩🟩 GOOD 🟩🟩; narrower: 🟩 GOOD 🟩; then GOOD; then just the tile."""
    text = str(text or '').strip()
    if not swatch:
        return text[:cols]
    if not text:
        return swatch
    max_side = max(0, (cols - len(text) - 2) // 2)
    if max_side >= 1:
        side = swatch * min(4, max_side)
        return f'{side} {text} {side}'
    if cols >= len(text) + 2:
        return f'{swatch} {text}'
    if cols >= len(text):
        return text[:cols]
    return swatch


def _decorate_status(label, color, cols, mono=False):
    return _balance(label, '' if mono else _compact_color(color), cols)


def _row(left, right, cols):
    """One full-width line: `left` flush left, `right` flush right. format_lines centers each
    line, so a line already `cols` wide passes through untouched — which is what makes the
    forecast's highs and lows line up in a column you can read down."""
    left, right = str(left), str(right)
    if len(right) >= cols:
        return right[:cols]
    left = left[:cols - len(right) - 1]
    return left + ' ' * (cols - len(left) - len(right)) + right


# The sky: a WORD, an intensity, and a color — keyed by the helper's canonical
# sky token, so no provider code is ever read here.
#
# The word is what you actually want to know — a color tells you "wet" but not whether that
# is drizzle or a downpour. The intensity is a suffix rather than a separate word (LRain /
# HRain) because a suffix survives translation: every language gets to keep its own noun and
# the mark means the same thing everywhere. And it is short, which is the whole problem: a
# 15-column line has room for a day, a condition and 24/14, and nothing else.
#
# The marks are `-` and `!`, and the choice is forced. `+` is ruled out: it is on the
# English, German and Scandinavian reels — and on NO OTHER. A module asked for a flap it does
# not carry simply homes, so on a French, Spanish, Italian, Portuguese or Dutch wall "Rain+"
# would come out as "Rain", making a downpour indistinguishable from ordinary rain — the
# suffix IS the payload, silently dropped in exactly the languages that have a reel of
# their own. `-` and `!` are on every published set (see the wiki's Flaps & Character
# Sets), so they carry the same meaning to every wall.
#
# The color still comes along when the wall is wide enough for it.
_SKY = {
    #  token       word       suffix  color tile
    'clear':     ('Sunny',    '',     '\U0001f7e8'),   # yellow
    'pcloudy':   ('PSunny',   '',     '⬜'),       # white
    'cloudy':    ('Cloudy',   '',     '⬜'),
    'fog':       ('Fog',      '',     '⬜'),
    'rainl':     ('Rain',     '-',    '\U0001f7e6'),   # blue
    'rain':      ('Rain',     '',     '\U0001f7e6'),
    'rainh':     ('Rain',     '!',    '\U0001f7e6'),
    'shwr':      ('Shwrs',    '',     '\U0001f7e6'),
    'snowl':     ('Snow',     '-',    '\U0001f7ea'),   # purple
    'snow':      ('Snow',     '',     '\U0001f7ea'),
    'snowh':     ('Snow',     '!',    '\U0001f7ea'),
    'sleet':     ('Sleet',    '',     '\U0001f7ea'),
    'storm':     ('Storm',    '',     '\U0001f7e5'),   # red
    'hail':      ('Hail',     '',     '\U0001f7e5'),
}


_SKY_MAX = 6        # the longest word a forecast column can hold on a 15-wide wall


def _sky_word(sky, t, cap=True):
    """The condition, translated — and, for a narrow wall, short enough for it.

    The `-`/`!` is dropped rather than the noun when a language's word is already as long as
    the column: knowing it is snow matters more than knowing it is light snow, and a
    truncated noun ("Schne+") tells you neither. ``cap=False`` lifts the width limit — a wide
    Matrix wall has room for the whole (translated) word and its intensity mark.
    """
    word, suffix, _ = _SKY.get(sky or 'cloudy', _SKY['cloudy'])
    word = t(word)
    if not cap:
        return word + suffix
    return word + suffix if len(word) + len(suffix) <= _SKY_MAX else word[:_SKY_MAX]


def _sky_tile(sky, mono):
    if mono:
        return ''
    return _SKY.get(sky or 'cloudy', _SKY['cloudy'])[2]


def _metric_line(word, value, label, color, cols, mono=False):
    """One metric on one line: `AQI 42 Good 🟩`, degraded to whatever fits.

    A tall wall can afford a row per metric; it cannot afford a whole PAGE per
    metric, which is what the three-row layout had to do.
    """
    swatch = '' if mono else _compact_color(color)
    head = f'{word} {value}' if value is not None else str(word)
    # Prefer a tile on BOTH ends — one lonely trailing tile is the unbalanced
    # look; degrade to trailing-only, then no tile, as the width shrinks.
    for candidate in (f'{swatch} {head} {label} {swatch}', f'{head} {label} {swatch}',
                      f'{head} {label}', head):
        c = candidate.strip()
        if len(c) <= cols:
            return c
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


def _forecast_lines(days, temp_unit, t, i18n, cols, no_color):
    """The forecast: a day per line.

    On a WIDE wall (a big Matrix panel) the day is spelled OUT — the condition in
    full ('Partly cloudy', not 'PSunny'; 'Light rain', not 'Rain-'), the high/low
    with degree signs, and the weekday in full where there is room — laid out as an
    aligned, CENTERED block: the days, the conditions and the temperatures each line
    up in a column you can read down, sitting together in the middle rather than
    flung to the wall's edges. That is what turns the wide wall's room into more
    weather instead of more empty space.

    The fullest form that fits `cols` is used, degrading in this order:
      1. full weekday + full condition + degrees   (Wednesday  Partly cloudy  84°/61°)
      2. short weekday + full condition + degrees   (Wed  Partly cloudy  84°/61°)
      3. the compact narrow form                    (Wed PSunny 84/61, edges pinned)
    so the spelled-out condition survives down to a fairly narrow wall, and only a
    truly 15-wide wall falls back to the abbreviations it has always used.

    `days` is a list of (datetime, forecast-day-dict).
    """
    deg = '\N{DEGREE SIGN}'
    gap = 2
    pre = 0 if no_color else 2                        # 'X ' color-tile prefix width

    def temps(d):
        return (f"{_short_temp(d['hi_f'], temp_unit)}{deg}/"
                f"{_short_temp(d['lo_f'], temp_unit)}{deg}")

    # --- the aligned, spelled-out block: full weekday if it fits, else short ------
    for day_full in (True, False):
        rich = []
        for dt, d in days:
            day = (i18n.weekday(dt, short=not day_full) if i18n is not None
                   else dt.strftime('%A' if day_full else '%a')).replace('.', '')
            rich.append((_sky_tile(d.get('sky'), no_color), day,
                         _sky_phrase(d.get('sky'), t), temps(d)))
        w_day = pre + max(len(day) for _, day, _, _ in rich)
        w_word = max(len(word) for _, _, word, _ in rich)
        w_temp = max(len(tmp) for _, _, _, tmp in rich)
        if w_day + gap + w_word + gap + w_temp <= cols:
            return [((f'{tile} {day}' if pre else day).ljust(w_day) + ' ' * gap
                     + word.ljust(w_word) + ' ' * gap + tmp.rjust(w_temp))
                    for tile, day, word, tmp in rich]

    # --- the compact, narrow-wall form (the day shrinks before the condition) ----
    words = [_sky_word(d.get('sky'), t) for _, d in days]
    word_w = max(len(word) for word in words)
    temp_w = max(len(f"{_short_temp(d['hi_f'], temp_unit)}/"
                     f"{_short_temp(d['lo_f'], temp_unit)}") for _, d in days)
    day_w = next((n for n in (3, 2, 1) if n + 1 + word_w + 1 + temp_w <= cols), 1)
    # …and the color flap only if it costs nobody a letter.
    tile = not no_color and (2 + day_w + 1 + word_w + 1 + temp_w) <= cols
    out = []
    for (dt, d), word in zip(days, words):
        day = (i18n.weekday(dt, short=True) if i18n is not None else dt.strftime('%a'))
        day = day.replace('.', '')[:day_w]
        left = f'{day} {word}'
        if tile:
            left = f'{_sky_tile(d.get("sky"), no_color)} {left}'
        right = (f"{_short_temp(d['hi_f'], temp_unit)}/"
                 f"{_short_temp(d['lo_f'], temp_unit)}")
        out.append(_row(left, right, cols))
    return out


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_weather=None):
    from datetime import datetime

    def t(s):
        return i18n.t(s, "weather") if i18n is not None else s

    temp_unit = str(settings.get('temperature_unit', 'f')).lower()
    if temp_unit not in ('f', 'c', 'k'):
        temp_unit = 'f'
    no_color = settings.get('disable_colors', 'no') == 'yes'
    show_aqi = settings.get('show_aqi', 'yes') == 'yes'
    show_uv = settings.get('show_uv', 'yes') == 'yes'
    show_pollen = settings.get('show_pollen', 'yes') == 'yes'
    try:
        forecast_days = max(0, min(5, int(settings.get('forecast_days', 3) or 0)))
    except (TypeError, ValueError):
        forecast_days = 3

    # Last good pages survive a transient outage — the wall shows yesterday's
    # weather over an error page any day.
    state = getattr(fetch, '_state', None)
    if state is None:
        state = {'last_pages': None}
        setattr(fetch, '_state', state)

    try:
        want_air = show_aqi or show_uv or show_pollen
        w = _conditions(settings, get_weather, forecast_days, want_air)
        if not w or not w.get('ok'):
            raise RuntimeError(str((w or {}).get('error') or 'no data'))

        cols = get_cols()
        rows = get_rows()
        narrow = cols <= 12
        feels_word = t('Fls') if narrow else t('Feels')
        pollen_word = t('Pol') if narrow else t('Pollen')
        sun_exposure_text = t('Sun UV') if narrow else t('Sun exposure')
        grass_word = t('Grs') if narrow else t('Grass')
        tree_word = t('Tre') if narrow else t('Tree')
        weed_word = t('Wed') if narrow else t('Weed')

        temp = _format_temp(w.get('temp_f'), temp_unit)
        feels = f"{feels_word} {_format_temp(w.get('feels_like_f'), temp_unit)}"
        humidity = w.get('humidity')
        hum_word = t('Hum') if narrow else t('Humidity')
        # Open-Meteo's condition text is ours to translate; a keyed provider
        # already answered in the global Language.
        desc = t(w['desc']) if w.get('provider') == 'openmeteo' else str(w.get('desc') or '')
        # The current sky gets its color too, balanced — so the conditions line
        # carries a tile like every forecast row does, instead of standing bare.
        desc_tiled = _balance(desc, _sky_tile(w.get('sky'), no_color), cols)
        hi = f"H {_format_temp(w.get('hi_f'), temp_unit)}"
        lo = f"L {_format_temp(w.get('lo_f'), temp_unit)}"

        # --- the optional metrics, already classified by the helper ------------
        a = w.get('air') or {}
        aqi_num = a.get('aqi') if show_aqi else None
        uv_num = a.get('uv') if show_uv else None
        if uv_num is not None and a.get('uv_band') == 'unknown':
            uv_num = None
        pollen = a.get('pollen') or {}
        pollen_overall = pollen.get('overall') if show_pollen else None
        pollen_parts = []
        if pollen_overall is not None:
            for word, key in ((grass_word, 'grass'), (tree_word, 'tree'), (weed_word, 'weed')):
                val = pollen.get(key)
                if val is not None:
                    # per-component levels reuse the helper's thresholds via band lookup
                    label, band = _pollen_label(val)
                    pollen_parts.append(_metric_line(word, None, t(label),
                                                     _BAND_COLOR[band], cols, no_color))

        # --- the forecast --------------------------------------------------------
        # A day per line: what the sky will do, and the high/low in a column you can read
        # down. The FORMAT is chosen once for the whole page, from the longest condition on
        # it, so the columns line up — a line that shrinks its day to make room for "PSunny"
        # while its neighbor does not is a list you have to read twice. A wide Matrix wall
        # spells the whole thing out (see _forecast_lines); a 15-wide wall gets the compact
        # 'Wed Rain- 78/61'.
        fc_lines = []
        if forecast_days and rows >= 3:
            days = []
            for d in (w.get('forecast') or [])[:forecast_days]:
                if d.get('hi_f') is None or d.get('lo_f') is None:
                    continue
                try:
                    dt = datetime.strptime(str(d.get('date'))[:10], '%Y-%m-%d')
                except (TypeError, ValueError):
                    continue
                days.append((dt, d))
            if days:
                fc_lines = _forecast_lines(days, temp_unit, t, i18n, cols, no_color)

        # --- the pages ----------------------------------------------------------
        # Only one location is supported, so we don't repeat it on every page.
        if rows >= 4:
            # A tall wall can say all of this at once, one row per metric, over as
            # few pages as they fit on.
            # A tall wall has the room, so humidity gets a labeled line of its own.
            lines = [f'{temp} {feels}', f'{hi} {lo}']
            if humidity is not None:
                lines.append(f'{hum_word} {humidity}%')
            lines.append(desc_tiled)
            if aqi_num is not None:
                lines.append(_metric_line('AQI', aqi_num, t(a.get('aqi_label', '')),
                                          _BAND_COLOR.get(a.get('aqi_band'), 'UNKNOWN'), cols, no_color))
            if uv_num is not None:
                lines.append(_metric_line('UV', uv_num, t(a.get('uv_label', '')),
                                          _BAND_COLOR.get(a.get('uv_band'), 'UNKNOWN'), cols, no_color))
            if pollen_overall is not None:
                lines.append(_metric_line(pollen_word, None, t(a.get('pollen_label', '')),
                                          _BAND_COLOR.get(a.get('pollen_band'), 'UNKNOWN'), cols, no_color))
                if len(lines) + len(pollen_parts) <= rows:     # room for the breakdown too
                    lines.extend(pollen_parts)
            pages = [format_lines(*chunk) for chunk in _paginate(lines, rows)]
        else:
            if rows == 1:
                pages = [format_lines(f'{temp} {desc}')]
            elif rows == 2:
                pages = [
                    format_lines(f'{temp} {feels}', desc_tiled),
                    format_lines(f'{hi} {lo}', desc_tiled),
                ]
            else:
                pages = [format_lines(f'{temp} {feels}', f'{hi} {lo}', desc_tiled)]

            if aqi_num is not None:
                aqi_display = _decorate_status(t(a.get('aqi_label', '')),
                                               _BAND_COLOR.get(a.get('aqi_band'), 'UNKNOWN'), cols, no_color)
                if rows == 1:
                    pages.append(format_lines(f'AQI {aqi_display}'))
                elif rows == 2:
                    pages.append(format_lines(f'AQI {aqi_num}', aqi_display))
                else:
                    pages.append(format_lines(t('Air quality'), f'AQI {aqi_num}', aqi_display))

            if uv_num is not None:
                uv_display = _decorate_status(t(a.get('uv_label', '')),
                                              _BAND_COLOR.get(a.get('uv_band'), 'UNKNOWN'), cols, no_color)
                if rows == 1:
                    pages.append(format_lines(f'UV {uv_display}'))
                elif rows == 2:
                    pages.append(format_lines(f'UV {uv_num}', uv_display))
                else:
                    pages.append(format_lines(sun_exposure_text, f'UV {uv_num}', uv_display))

            if pollen_overall is not None:
                overall_display = _decorate_status(t(a.get('pollen_label', '')),
                                                   _BAND_COLOR.get(a.get('pollen_band'), 'UNKNOWN'),
                                                   cols, no_color)
                if rows == 1:
                    pages.append(format_lines(f'{pollen_word} {overall_display}'))
                else:
                    pages.append(format_lines(pollen_word, overall_display))
                    if pollen_parts:
                        pages.append(format_lines(*pollen_parts))

        if fc_lines:
            # Each forecast row already starts with its weekday, so it labels
            # itself — the "Forecast" header only earns a row when there is a
            # spare one. On a 5-row wall that means five days fill the page
            # instead of a title plus four.
            for i, chunk in enumerate(_paginate(fc_lines, rows)):
                if i == 0 and len(chunk) < rows:
                    pages.append(format_lines(t('Forecast'), *chunk))
                else:
                    pages.append(format_lines(*chunk))

        state['last_pages'] = pages
        return pages
    except Exception:
        # On transient error, reuse last good pages if available
        if state['last_pages'] is not None:
            return state['last_pages']
        return [format_lines('Weather', 'Error', 'Check API key')]


def _pollen_label(val):
    """Per-component pollen (label, band) — same thresholds the helper uses for
    the overall level."""
    if val is None or val < 1:
        return 'None', 'none'
    if val < 10:
        return 'Low', 'good'
    if val < 50:
        return 'Mod', 'moderate'
    if val < 200:
        return 'High', 'poor'
    return 'V.High', 'bad'


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A current-conditions card: the temperature big and white, the condition in a
# sky-matched accent color, the day's high/low color-coded warm/cool — the
# same document the flap pages render (via _conditions), deliberately simpler
# than a forecast board. Solid black background; adaptive down to 64x32.
# =============================================================================


_CV_TEXT = (238, 238, 244)                 # primary text
_CV_DIM = (150, 150, 158)                  # secondary text
_CV_HI = (255, 150, 70)                    # the day's high — warm
_CV_LO = (95, 165, 255)                    # the day's low — cool
_CV_SKY_ACCENT = {                         # condition accent, keyed by the sky token
    'clear': (255, 200, 50), 'pcloudy': (215, 220, 230), 'cloudy': (170, 175, 185),
    'fog': (150, 160, 172), 'rainl': (85, 155, 250), 'rain': (85, 155, 250),
    'rainh': (65, 125, 240), 'shwr': (85, 155, 250), 'snowl': (190, 212, 255),
    'snow': (190, 212, 255), 'snowh': (190, 212, 255), 'sleet': (165, 195, 240),
    'storm': (255, 95, 70), 'hail': (255, 95, 70),
}


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 8px)."""
    size = max(8, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 8 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_ink(font, text):
    """Ink height of ``text`` in ``font``."""
    b = font.getbbox(text or '0')
    return b[3] - b[1]


def _cv_text(draw, x, y, text, font, fill):
    """Draw with the ink's TOP at ``y`` (bbox-corrected), left edge at ``x``."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill, anchor='la')


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message on black (offline / no data) — never a crash, never blank."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    h1 = _cv_ink(f1, line1)
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = _cv_ink(f2, line2) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    _cv_text(draw, (W - f1.getlength(line1)) / 2.0, y, line1, f1, _CV_TEXT)
    if line2:
        _cv_text(draw, (W - f2.getlength(line2)) / 2.0, y + h1 + gap, line2, f2, _CV_DIM)
    return img


def _cv_temp(value, temp_unit):
    """'72°' — the degree sign the flap reels never had. Kelvin keeps its K."""
    body = _short_temp(value, temp_unit)
    return f'{body}K' if temp_unit == 'k' else f'{body}\N{DEGREE SIGN}'


def _cv_card(canvas, ImageDraw, w, temp_unit, t):
    """The conditions card: temperature big and white, the condition in its sky's
    accent, the high warm / low cool. Side-by-side on a roomy panel, stacked on a
    small one; the city rides along only where there is height to spare."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    accent = _CV_SKY_ACCENT.get(w.get('sky'), _CV_SKY_ACCENT['cloudy'])
    temp = _cv_temp(w.get('temp_f'), temp_unit)
    # The same condition text the flap pages show (translated when it is ours to
    # translate), just set in type instead of flaps.
    desc = (t(w['desc']) if w.get('provider') == 'openmeteo'
            else str(w.get('desc') or '')).upper()
    hi, lo = _cv_temp(w.get('hi_f'), temp_unit), _cv_temp(w.get('lo_f'), temp_unit)

    def hilo_row(x, y, font):
        """H 75°  L 61° with each number in its own color, one shared baseline."""
        y0 = y - font.getbbox('H0')[1]
        for part, col in (('H ', _CV_DIM), (hi, _CV_HI), ('  L ', _CV_DIM), (lo, _CV_LO)):
            draw.text((x, y0), part, font=font, fill=col, anchor='la')
            x += font.getlength(part)

    if W >= 96:
        # Side by side: the temperature owns the left, the words own the right.
        # The city is a quiet strip across the top (only where height allows) so
        # the right column stays a two-item hierarchy: condition, then high/low.
        city = str(w.get('city') or '').upper()
        top = 0
        if H >= 52 and city:
            cf = _cv_fit(canvas, city, W - 8, 8)
            _cv_text(draw, 4, 1, city, cf, _CV_DIM)
            top = 1 + _cv_ink(cf, city) + 2
        ah = H - top                                  # the card area under the strip
        tf = _cv_fit(canvas, temp, int(W * 0.46), ah)
        th = _cv_ink(tf, temp)
        _cv_text(draw, 4, top + (ah - th) / 2.0, temp, tf, _CV_TEXT)
        rx = 4 + tf.getlength(temp) + 7
        rw = W - 4 - rx
        # A long condition ("PARTLY CLOUDY") wraps onto two lines rather than
        # shrinking to the 7px floor on one; the wrap point is the space nearest
        # the middle, and both lines share the larger font that results.
        dlines = [desc]
        df = _cv_fit(canvas, desc, rw, max(8, int(ah * 0.40)))
        words = desc.split()
        if len(words) > 1 and df.size < 9:
            mid = min(range(1, len(words)),
                      key=lambda i: abs(len(' '.join(words[:i])) - len(desc) / 2))
            dlines = [' '.join(words[:mid]), ' '.join(words[mid:])]
            longest = max(dlines, key=lambda s: len(s))
            df = _cv_fit(canvas, longest, rw, max(8, int(ah * 0.30)))
        dh = _cv_ink(df, 'AG')
        hl = f'H {hi}  L {lo}'
        hf = _cv_fit(canvas, hl, rw, max(7, int(ah * 0.30)))
        hh = _cv_ink(hf, hl)
        dgap = max(1, dh // 5)
        # The condition hangs from the top edge (or the city strip) and the
        # high/low sits its ink on the bottom row — the card spends the whole
        # height instead of banking slack above and below a centered block.
        y = top if top else 1
        for ln in dlines:
            _cv_text(draw, rx, y, ln, df, accent)
            y += dh + dgap
        hilo_row(rx, H - hh, hf)
    else:
        # Stacked: temperature + high/low hung from the top edge, the condition
        # strip sitting on the bottom row — every row of the panel works.
        df = _cv_fit(canvas, desc, W - 4, max(7, int(H * 0.30)))
        if df.getlength(desc) > W - 4 and ' ' in desc:
            # Too long for a legible line even at the 8px floor: keep the noun
            # ("CLOUDY", "RAIN"), not a smaller alphabet.
            desc = desc.split()[-1]
            df = _cv_fit(canvas, desc, W - 4, max(7, int(H * 0.30)))
        dh = _cv_ink(df, desc)
        top_h = H - dh - 2                            # everything above the strip
        # The hi/lo column is measured FIRST (it can no longer shrink below the
        # 8px floor); the temperature gets exactly the width that's left, so the
        # degree sign can never collide with the column.
        hi_s, lo_s = f'H {hi}', f'L {lo}'
        sf = _cv_fit(canvas, max(hi_s, lo_s, key=len), W // 2, max(7, (top_h - 3) // 2))
        sh = _cv_ink(sf, hi_s)
        rx = W - 3 - max(sf.getlength(hi_s), sf.getlength(lo_s))
        tf = _cv_fit(canvas, temp, max(10, rx - 6), top_h - 1)
        _cv_text(draw, 3, 1, temp, tf, _CV_TEXT)
        _cv_text(draw, rx, 1, hi_s, sf, _CV_HI)
        _cv_text(draw, rx, top_h - 1 - sh, lo_s, sf, _CV_LO)
        _cv_text(draw, (W - df.getlength(desc)) / 2.0, H - dh, desc, df, accent)
    return img


def fetch_matrix(settings, canvas, i18n=None, get_weather=None):
    """Draw the current-conditions card from the same document the flap pages use
    (_conditions); the last good card survives a transient outage. Holds for the
    configured polling rate — weather does not change by the second."""
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "weather") if i18n is not None else s

    temp_unit = str(settings.get('temperature_unit', 'f')).lower()
    if temp_unit not in ('f', 'c', 'k'):
        temp_unit = 'f'
    try:
        hold = float(settings.get('polling_rate', 300) or 300)
    except (TypeError, ValueError):
        hold = 300.0
    hold = max(120.0, min(900.0, hold))

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'last': None}
        setattr(fetch_matrix, '_state', st)
    try:
        w = _conditions(settings, get_weather, 0, False)
        if not w or not w.get('ok'):
            raise RuntimeError(str((w or {}).get('error') or 'no data'))
        st['last'] = w
    except Exception:
        w = st['last']                     # yesterday's weather beats an error card
    if not w:
        canvas.frame(_cv_message(canvas, ImageDraw, 'WEATHER', 'OFFLINE'))
        return 120.0
    canvas.frame(_cv_card(canvas, ImageDraw, w, temp_unit, t))
    return hold
