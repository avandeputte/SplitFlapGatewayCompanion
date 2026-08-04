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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# The weather as a living sky scene (the merged Weather Sky app): a black panel
# with a crisp sun by day or a moon and colored stars by night, cloud-shaped
# clouds, falling rain or snow, lightning in a storm — and over it the numbers:
# a big temperature, the condition, high/low; a 256x64 panel opens into a full
# info column (feels-like, humidity, wind, a 3-day forecast strip). The scene
# animates (~6 fps hold); conditions come from the same _conditions document the
# flap pages render, cached ten minutes while the sky moves.
# =============================================================================


_CV_SKY_WORD = {'clear': 'Clear', 'pcloudy': 'Partly', 'cloudy': 'Cloudy', 'fog': 'Fog',
                'rainl': 'Light rain', 'rain': 'Rain', 'rainh': 'Heavy rain', 'shwr': 'Showers',
                'snowl': 'Light snow', 'snow': 'Snow', 'snowh': 'Heavy snow', 'sleet': 'Sleet',
                'storm': 'Storm', 'hail': 'Hail'}
_CV_RAIN = {'rainl': 8, 'rain': 14, 'rainh': 22, 'shwr': 13, 'sleet': 10}
_CV_SNOW = {'snowl': 10, 'snow': 16, 'snowh': 24}
_CV_CLOUDY = ('pcloudy', 'cloudy', 'fog', 'rainl', 'rain', 'rainh', 'shwr', 'sleet',
              'snowl', 'snow', 'snowh', 'storm', 'hail')


def _cv_disc(draw, cx, cy, r, col):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)


def _cv_num(v, unit):
    """The document's Fahrenheit reading in the configured unit, as an int."""
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    if unit == 'c':
        return int(round((f - 32) * 5 / 9))
    if unit == 'k':
        return int(round((f - 32) * 5 / 9 + 273.15))
    return int(round(f))


def _cv_weather_gtext(canvas, wx, unit, show_city, night, sky, frame):
    """The weather sky scene as on-device ops + gtext instead of a pushed pixel frame: a dark
    sky gradient, a warm sun (day) or crescent moon + colored stars (night), cloud-shaped puffs
    and rain/snow, a blurred info-column scrim, then the same tall info column (city, big temp,
    condition, H/L, feels, humidity/wind) over a bottom forecast strip. The ops twin of the
    H>=96 PIL layout below — every position/size/color mirrors it, so it fills the 1280x800 LCD
    wall at native resolution and previews at 256x160 the same way the pixel path does."""
    import math
    from datetime import datetime as _dt

    W, H = canvas.width, canvas.height
    deg = '\N{DEGREE SIGN}'

    # --- the sky: a dark vertical gradient (a whisper of dusk-blue by day, deep navy by night)
    # under the celestial scene — kept dark enough that the sun/moon/stars and the outlined info
    # text read exactly as they do over the LED path's black panel.
    if night:
        top_sky, bot_sky = (8, 10, 22), (1, 2, 6)
    else:
        top_sky, bot_sky = (16, 26, 46), (3, 6, 12)
    canvas.gradient(0, 0, W, H, top_sky, bot_sky, 'v')

    def _sky_at(y):                                     # the gradient's own color at a row
        r = max(0.0, min(1.0, y / max(1, H - 1)))
        return tuple(int(top_sky[k] + (bot_sky[k] - top_sky[k]) * r) for k in range(3))

    # --- celestial: a crisp sun (day) or moon + colored stars (night); same geometry as PIL ----
    icx, icy, ir = W - int(H * 0.42) - 1, int(H * 0.40), max(4, int(H * 0.26))
    if night:
        stars = [(0.06, 0.18, (200, 210, 255)), (0.16, 0.42, (255, 240, 200)),
                 (0.30, 0.12, (180, 220, 255)), (0.40, 0.55, (255, 220, 220)),
                 (0.52, 0.24, (210, 235, 255)), (0.63, 0.10, (255, 245, 210))]
        for i, (fx, fy, col) in enumerate(stars):
            if (frame // 7 + i) % 4:
                canvas.pixel(int(W * fx), int(H * fy), col)
        if sky in ('clear', 'pcloudy'):
            canvas.circle(icx, icy, ir, color=(232, 236, 250), fill=True)
            # the crescent: an offset disc carves the shadow — the local sky tone (black on the
            # LED's flat panel; the gradient's own color here) so the cut reads seamless
            canvas.circle(icx + int(ir * 0.55), icy - int(ir * 0.2), ir,
                          color=_sky_at(icy), fill=True)
    else:
        if sky in ('clear', 'pcloudy'):
            canvas.circle(icx, icy, ir, color=(255, 210, 70), fill=True)
            canvas.circle(icx, icy, max(1, ir - 2), color=(255, 226, 120), fill=True)

    # --- clouds: one resting by the sun/moon, one drifting across — three overlapping discs ----
    if sky in _CV_CLOUDY:
        dark = sky in ('storm', 'hail')
        cc = (70, 74, 88) if dark else (150, 156, 172)

        def _puff(px, py, s):
            for dx, dy, rr in ((0, 0, s), (int(s * 0.9), 4, int(s * 0.78)),
                               (-int(s * 0.9), 3, int(s * 0.72))):
                canvas.circle(int(px + dx), int(py + dy), max(2, rr), color=cc, fill=True)

        _puff(icx - int(W * 0.1), icy, ir)                       # the cloud beside the sun/moon
        cx = (frame * 0.4) % (W + ir * 6) - ir * 3               # a smaller cloud drifting across
        _puff(cx, H * 0.28, max(3, int(ir * 0.62)))

    # --- precipitation ----------------------------------------------------------
    if sky in _CV_RAIN:
        for i in range(_CV_RAIN[sky]):
            x = (i * 53 + 7) % W
            y = int(H * 0.30) + (frame * 3 + i * 11) % max(1, int(H * 0.7))
            canvas.line(x, y, x - 1, min(H - 1, y + 3), color=(120, 170, 255))
    elif sky in _CV_SNOW:
        for i in range(_CV_SNOW[sky]):
            x = (i * 41 + 5 + int(2 * math.sin(frame * 0.15 + i))) % W
            y = int(H * 0.28) + (frame + i * 9) % max(1, int(H * 0.72))
            canvas.pixel(x, y, (238, 244, 255))
    if sky in ('storm', 'hail') and frame % 22 < 2:
        bx = int(W * 0.3)
        canvas.polyline([(bx, int(H * 0.3)), (bx - 3, int(H * 0.55)), (bx + 2, int(H * 0.55)),
                         (bx - 2, int(H * 0.8))], color=(255, 255, 170))

    # --- the info column + forecast strip, over a soft scrim (the on-device version of the PIL
    # GaussianBlur darken). The scene must never crowd the text, so dim the info column + forecast
    # strip toward the dark sky — the app "blacks out the info column", and an intruding cloud
    # vanishes behind it — then a box blur feathers the dimmed panels into the bright scene.
    pad = 4
    left_w = int(W * 0.60)
    strip_h = max(16, int(H * 0.13))
    fy = H - strip_h                                    # the forecast strip's top
    blur_r = max(2, int(H * 0.05))
    dim_top = tuple(int(c * 0.45) for c in top_sky)
    dim_bot = tuple(int(c * 0.45) for c in bot_sky)
    canvas.gradient(0, 0, left_w, H, dim_top, dim_bot, 'v')             # dim the left info column
    canvas.rect(0, fy - 2, W, H - (fy - 2), color=dim_bot, fill=True)   # dim the forecast strip
    canvas.blur(0, 0, min(W, left_w + 2 * blur_r), fy - 2, blur_r)      # feather the column edge
    sb = max(0, fy - 2 - 2 * blur_r)
    canvas.blur(0, sb, W, H - sb, blur_r)                               # feather the strip edge
    colw = left_w - 2 * pad

    name_size = max(9, int(H * 0.095))
    temp_size = max(16, int(H * 0.26))
    cond_size = max(9, int(H * 0.11))
    info_size = max(8, int(H * 0.085))

    temp = _cv_num(wx.get('temp_f'), unit)
    hi, lo = _cv_num(wx.get('hi_f'), unit), _cv_num(wx.get('lo_f'), unit)
    word = _CV_SKY_WORD.get(sky, 'Weather')

    y = pad
    if show_city and wx.get('city'):
        cs = str(wx['city'])
        while cs and canvas.text_width(cs, name_size) > colw:
            cs = cs[:-1]
        if cs:
            canvas.gtext(pad, y, cs, color=(216, 226, 244), size=name_size, outline=(0, 0, 0))
    y += name_size + max(2, int(H * 0.015))

    ts = f'{temp}{deg}' if temp is not None else '--'
    canvas.gtext(pad, y, ts, color=(255, 255, 255) if temp is not None else (200, 200, 200),
                 size=temp_size, outline=(0, 0, 0))
    y += temp_size + max(1, int(H * 0.005))

    canvas.gtext(pad, y, word[:16], color=(214, 226, 246), size=cond_size, outline=(0, 0, 0))
    y += cond_size + max(3, int(H * 0.03))

    step = info_size + max(3, int(H * 0.028))
    if hi is not None or lo is not None:
        x = pad
        if hi is not None:
            canvas.gtext(x, y, f'H {hi}{deg}', color=(255, 150, 55), size=info_size,
                         outline=(0, 0, 0))
            x += canvas.text_width(f'H {hi}{deg}', info_size) + max(8, int(W * 0.05))
        if lo is not None:
            canvas.gtext(x, y, f'L {lo}{deg}', color=(55, 150, 255), size=info_size,
                         outline=(0, 0, 0))
        y += step
    feels = _cv_num(wx.get('feels_like_f'), unit)
    if feels is not None:
        canvas.gtext(pad, y, f'Feels {feels}{deg}', color=(198, 208, 228), size=info_size,
                     outline=(0, 0, 0))
        y += step
    extra = []
    if wx.get('humidity') is not None:
        extra.append(f'Hum {int(wx["humidity"])}%')
    if wx.get('wind_mph') is not None:
        extra.append(f'Wind {int(wx["wind_mph"])}')
    if extra:
        canvas.gtext(pad, y, '   '.join(extra), color=(198, 208, 228), size=info_size,
                     outline=(0, 0, 0))

    # --- the forecast strip: one fitted cell per day, spread across the full width -------------
    fc = wx.get('forecast') or []
    if fc:
        n = min(3, len(fc))
        cw = W // n
        strip_size = max(8, int(strip_h * 0.62))
        for i, day in enumerate(fc[:n]):
            dhi, dlo = _cv_num(day.get('hi_f'), unit), _cv_num(day.get('lo_f'), unit)
            try:
                lbl = _dt.strptime(str(day.get('date'))[:10], '%Y-%m-%d').strftime('%a')
            except Exception:
                lbl = str(day.get('day') or '')[:3].title()
            fs = (f'{lbl} {dhi}{deg}/{dlo}{deg}' if (dhi is not None and dlo is not None)
                  else (lbl or ''))
            fsize = canvas.fit_gtext(fs, cw - 6, strip_size) if fs else strip_size
            canvas.gtext(i * cw + pad, fy + max(1, (strip_h - fsize) // 2), fs,
                         color=(206, 216, 234), size=fsize, outline=(0, 0, 0))

    canvas.show()


def fetch_canvas(settings, canvas, get_weather=None):
    import math
    from datetime import datetime
    from PIL import Image, ImageDraw, ImageFilter

    st = getattr(fetch_canvas, '_state', None)
    if st is None:
        st = {'frame': 0, 'wx': None, 'at': None}
        setattr(fetch_canvas, '_state', st)
    st['frame'] += 1
    frame = st['frame']

    tzname = str(settings.get('timezone') or '').strip()
    try:
        now = datetime.now(__import__('pytz').timezone(tzname)) if tzname else datetime.now()
    except Exception:
        now = datetime.now()
    hour = now.hour
    night = hour < 6 or hour >= 20

    # One reading per ten minutes while the scene animates; a transient failure
    # keeps showing the last good reading rather than a blank sky.
    nowt = datetime.now()
    stale = st['at'] is None or (nowt - st['at']).total_seconds() > 600
    if st['wx'] is None or stale:
        try:
            wx = _conditions(settings, get_weather, 3, False)
            if wx and wx.get('ok'):
                st['wx'], st['at'] = wx, nowt
            elif st['wx'] is None:
                st['wx'], st['at'] = {'ok': False}, nowt
        except Exception:
            if st['wx'] is None:
                st['wx'], st['at'] = {'ok': False}, nowt
    wx = st['wx'] or {}

    unit = str(settings.get('temperature_unit', 'f') or 'f').lower()
    if unit not in ('f', 'c', 'k'):
        unit = 'f'
    show_city = str(settings.get('show_city', 'yes') or 'yes') != 'no'
    sky = wx.get('sky') or 'cloudy'

    W, H = canvas.width, canvas.height

    if getattr(canvas, "can_gtext", False) and H >= 96:
        # A wall with scalable on-device text + box blur (the LCD): draw the whole sky scene as
        # ops and the info column / forecast as gtext, instead of pushing a pixel frame — crisp at
        # native resolution, a draw stream instead of megabytes a frame. Same animated scene and
        # tall layout as the PIL H>=96 path below; the LED path (H<=64) never reaches this.
        _cv_weather_gtext(canvas, wx, unit, show_city, night, sky, frame)
        return 0.16

    large = W >= 192 and H >= 48                # a large panel gets the richer layout
    img = canvas.blank((0, 0, 0))               # black — bright weather elements read best on unlit pixels
    draw = ImageDraw.Draw(img)

    # --- celestial: a crisp sun (day) or moon + colored stars (night) ----------
    # No soft glow — a blurred halo reads as an ugly gradient on the unlit black panel, so the
    # sun and moon are drawn as clean discs.
    icx, icy, ir = W - int(H * 0.42) - 1, int(H * 0.40), max(4, int(H * 0.26))
    if night:
        stars = [(0.06, 0.18, (200, 210, 255)), (0.16, 0.42, (255, 240, 200)),
                 (0.30, 0.12, (180, 220, 255)), (0.40, 0.55, (255, 220, 220)),
                 (0.52, 0.24, (210, 235, 255)), (0.63, 0.10, (255, 245, 210))]
        for i, (fx, fy, col) in enumerate(stars):
            if (frame // 7 + i) % 4:
                draw.point((int(W * fx), int(H * fy)), fill=col)
        if sky in ('clear', 'pcloudy'):
            _cv_disc(draw, icx, icy, ir, (232, 236, 250))
            _cv_disc(draw, icx + int(ir * 0.55), icy - int(ir * 0.2), ir, (0, 0, 0))   # crescent cut
    else:
        if sky in ('clear', 'pcloudy'):
            _cv_disc(draw, icx, icy, ir, (255, 210, 70))
            _cv_disc(draw, icx, icy, max(1, ir - 2), (255, 226, 120))

    # --- clouds: one resting by the sun/moon, one drifting across — both clearly cloud-SHAPED
    # (three overlapping gray discs) so a cloud never reads as a lone white ball on the black sky.
    if sky in _CV_CLOUDY:
        dark = sky in ('storm', 'hail')
        cc = (70, 74, 88) if dark else (150, 156, 172)

        def _puff(px, py, s):
            for dx, dy, rr in ((0, 0, s), (int(s * 0.9), 4, int(s * 0.78)), (-int(s * 0.9), 3, int(s * 0.72))):
                _cv_disc(draw, int(px + dx), int(py + dy), max(2, rr), cc)

        _puff(icx - int(W * 0.1), icy, ir)                       # the cloud beside the sun/moon
        cx = (frame * 0.4) % (W + ir * 6) - ir * 3               # a smaller cloud drifting across
        _puff(cx, H * 0.28, max(3, int(ir * 0.62)))

    # --- precipitation ----------------------------------------------------------
    if sky in _CV_RAIN:
        for i in range(_CV_RAIN[sky]):
            x = (i * 53 + 7) % W
            y = int(H * 0.30) + (frame * 3 + i * 11) % max(1, int(H * 0.7))
            draw.line([(x, y), (x - 1, min(H - 1, y + 3))], fill=(120, 170, 255))
    elif sky in _CV_SNOW:
        for i in range(_CV_SNOW[sky]):
            x = (i * 41 + 5 + int(2 * math.sin(frame * 0.15 + i))) % W
            y = int(H * 0.28) + (frame + i * 9) % max(1, int(H * 0.72))
            draw.point((x, y), fill=(238, 244, 255))
    if sky in ('storm', 'hail') and frame % 22 < 2:
        bx = int(W * 0.3)
        draw.line([(bx, int(H * 0.3)), (bx - 3, int(H * 0.55)), (bx + 2, int(H * 0.55)),
                   (bx - 2, int(H * 0.8))], fill=(255, 255, 170), width=1)

    # --- the text -----------------------------------------------------------
    # A soft scrim blacks out the info column so the scene never crowds the text; the celestial
    # scene keeps the right. A small panel gets a compact left column; a big panel (256x64) gets
    # a full info dashboard, so the space isn't wasted.
    temp = _cv_num(wx.get('temp_f'), unit)
    hi, lo = _cv_num(wx.get('hi_f'), unit), _cv_num(wx.get('lo_f'), unit)
    deg = '\N{DEGREE SIGN}'
    word = _CV_SKY_WORD.get(sky, 'Weather')

    def _outline(draw, x, y, s, font, col):
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):   # a dark outline for contrast
            draw.text((x + dx, y + dy), s, font=font, fill=(0, 0, 0), anchor='la')
        draw.text((x, y), s, font=font, fill=col, anchor='la')

    if H >= 96:
        # A TALL panel (the LCD's 1.6:1): the large branch below balloons the font with
        # H but keeps the LED horizontal layout, so its temp shoves H/L off the scrim and
        # its forecast cells overlap. This lays weather out for the height instead — a
        # left info column stacked vertically (city, big temp, condition, then H/L, feels,
        # humidity/wind each on their own line) over a scrim, the sky scene on the right,
        # and a full-width forecast strip of fitted cells along the bottom.
        pad = 4
        left_w = int(W * 0.60)
        strip_h = max(16, int(H * 0.13))
        fy = H - strip_h                                    # the forecast strip's top
        scrim = Image.new('L', (W, H), 0)
        _sd = ImageDraw.Draw(scrim)
        _sd.rectangle([0, 0, left_w, fy - 2], fill=200)
        _sd.rectangle([0, fy - 2, W - 1, H - 1], fill=200)
        img = Image.composite(Image.new('RGB', (W, H), (0, 0, 0)), img,
                              scrim.filter(ImageFilter.GaussianBlur(7)))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        colw = left_w - 2 * pad

        name_f = canvas.font(max(9, int(H * 0.095)))
        temp_f = canvas.font(max(16, int(H * 0.26)))
        cond_f = canvas.font(max(9, int(H * 0.11)))
        info_f = canvas.font(max(8, int(H * 0.085)))

        y = pad
        if show_city and wx.get('city'):
            cs = str(wx['city'])
            while cs and name_f.getlength(cs) > colw:
                cs = cs[:-1]
            _outline(draw, pad, y, cs, name_f, (216, 226, 244))
        y += name_f.size + max(2, int(H * 0.015))

        ts = f'{temp}{deg}' if temp is not None else '--'
        _outline(draw, pad, y, ts, temp_f, (255, 255, 255) if temp is not None else (200, 200, 200))
        y += temp_f.size + max(1, int(H * 0.005))

        _outline(draw, pad, y, word[:16], cond_f, (214, 226, 246))
        y += cond_f.size + max(3, int(H * 0.03))

        step = info_f.size + max(3, int(H * 0.028))
        if hi is not None or lo is not None:
            x = pad
            if hi is not None:
                _outline(draw, x, y, f'H {hi}{deg}', info_f, (255, 150, 55))
                x += info_f.getlength(f'H {hi}{deg}') + max(8, int(W * 0.05))
            if lo is not None:
                _outline(draw, x, y, f'L {lo}{deg}', info_f, (55, 150, 255))
            y += step
        feels = _cv_num(wx.get('feels_like_f'), unit)
        if feels is not None:
            _outline(draw, pad, y, f'Feels {feels}{deg}', info_f, (198, 208, 228))
            y += step
        extra = []
        if wx.get('humidity') is not None:
            extra.append(f'Hum {int(wx["humidity"])}%')
        if wx.get('wind_mph') is not None:
            extra.append(f'Wind {int(wx["wind_mph"])}')
        if extra:
            _outline(draw, pad, y, '   '.join(extra), info_f, (198, 208, 228))

        fc = wx.get('forecast') or []
        if fc:
            from datetime import datetime as _dt
            n = min(3, len(fc))
            cw = W // n                                     # full-width, one cell per day
            fcf = canvas.font(max(8, int(strip_h * 0.62)))
            for i, day in enumerate(fc[:n]):
                dhi, dlo = _cv_num(day.get('hi_f'), unit), _cv_num(day.get('lo_f'), unit)
                try:
                    lbl = _dt.strptime(str(day.get('date'))[:10], '%Y-%m-%d').strftime('%a')
                except Exception:
                    lbl = str(day.get('day') or '')[:3].title()
                fs = f'{lbl} {dhi}{deg}/{dlo}{deg}' if (dhi is not None and dlo is not None) else (lbl or '')
                f2 = fcf
                while fs and f2.getlength(fs) > cw - 6:     # never let a cell spill into the next
                    if f2.size > 8:
                        f2 = canvas.font(f2.size - 1)
                    else:
                        fs = fs[:-1]
                _outline(draw, i * cw + pad, fy + max(1, (strip_h - f2.size) // 2), fs, f2,
                         (206, 216, 234))
    elif large:
        # A dark info column on the left holds the place, a big temperature, the
        # condition, high/low, feels-like, humidity, wind and a 3-day forecast; the
        # sky scene fills the right.
        pad, left_w = 3, int(W * 0.58)
        name_f = canvas.font(max(8, int(H * 0.15)))
        temp_f = canvas.font(max(12, int(H * 0.38)))
        info_f = canvas.font(max(8, int(H * 0.15)))
        step = info_f.size + 2
        fy = H - info_f.size - 2                            # the forecast strip's top
        scrim = Image.new('L', (W, H), 0)
        _sd = ImageDraw.Draw(scrim)
        _sd.rectangle([0, 0, left_w, fy], fill=196)          # left info panel
        _sd.rectangle([0, fy - 1, W - 1, H - 1], fill=196)   # full-width forecast strip
        img = Image.composite(Image.new('RGB', (W, H), (0, 0, 0)), img,
                              scrim.filter(ImageFilter.GaussianBlur(6)))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"

        if show_city and wx.get('city'):
            cs = str(wx['city'])
            while cs and name_f.getlength(cs) > left_w - pad:
                cs = cs[:-1]
            _outline(draw, pad, 1, cs, name_f, (216, 226, 244))

        ty = int(H * 0.20)
        ts = f'{temp}{deg}' if temp is not None else '--'
        _outline(draw, pad, ty, ts, temp_f, (255, 255, 255) if temp is not None else (200, 200, 200))
        _outline(draw, pad, ty + temp_f.size, word[:14], info_f, (214, 226, 246))

        dx, dyy = int(pad + temp_f.getlength(ts) + 8), ty
        if hi is not None:
            _outline(draw, dx, dyy, f'H {hi}{deg}', info_f, (255, 150, 55))
            if lo is not None:
                _outline(draw, dx + info_f.getlength(f'H {hi}{deg}') + 7, dyy,
                         f'L {lo}{deg}', info_f, (55, 150, 255))
            dyy += step
        feels = _cv_num(wx.get('feels_like_f'), unit)
        if feels is not None:
            _outline(draw, dx, dyy, f'Feels {feels}{deg}', info_f, (198, 208, 228))
            dyy += step
        extra = []
        if wx.get('humidity') is not None:
            extra.append(f'Hum {int(wx["humidity"])}%')
        if wx.get('wind_mph') is not None:
            extra.append(f'Wind {int(wx["wind_mph"])}')
        if extra:
            _outline(draw, dx, dyy, '  '.join(extra), info_f, (198, 208, 228))

        fc = wx.get('forecast') or []
        if fc:
            from datetime import datetime as _dt
            n = min(3, len(fc))
            cw = W // n                                     # spread across the FULL width
            for i, day in enumerate(fc[:n]):
                dhi, dlo = _cv_num(day.get('hi_f'), unit), _cv_num(day.get('lo_f'), unit)
                try:
                    lbl = _dt.strptime(str(day.get('date'))[:10], '%Y-%m-%d').strftime('%a')
                except Exception:
                    lbl = str(day.get('day') or '')[:3].title()
                fs = f'{lbl}  {dhi}{deg}/{dlo}{deg}' if (dhi is not None and dlo is not None) else (lbl or '')
                _outline(draw, i * cw + pad + 2, fy, fs, info_f, (206, 216, 234))
    else:
        # Compact: place, big temperature, then condition + high/low, all in a left
        # column over the scrim.
        pad = 2
        text_w = int(W * 0.66)
        scrim = Image.new('L', (W, H), 0)
        ImageDraw.Draw(scrim).rectangle([0, 0, text_w, H], fill=180)
        img = Image.composite(Image.new('RGB', (W, H), (0, 0, 0)), img,
                              scrim.filter(ImageFilter.GaussianBlur(6)))
        draw = ImageDraw.Draw(img)
        draw.fontmode = "1"
        tiny = canvas.font(max(8, int(H * 0.17)))          # 8px floor: smaller renders wrong-reading glyphs
        small = canvas.font(max(8, int(H * 0.24)))

        top = 0
        if show_city and wx.get('city'):
            cs = str(wx['city'])
            while cs and tiny.getlength(cs) > text_w - pad:
                cs = cs[:-1]
            if cs:
                _outline(draw, pad, 0, cs, tiny, (216, 226, 244))
                top = tiny.size + 1

        info_y = H - (small.size + 1)
        band = info_y - top
        big = canvas.font(max(10, int(band * 0.96)))
        s = f'{temp}{deg}' if temp is not None else '--'
        ty = top + max(0, (band - big.size) // 2)
        _outline(draw, pad, ty, s, big, (255, 255, 255) if temp is not None else (200, 200, 200))

        hi_s = f'{hi}{deg}' if hi is not None else ''
        lo_s = f'{lo}{deg}' if lo is not None else ''
        x = pad
        if word and small.getlength(word + '  ' + hi_s + '/' + lo_s) <= text_w - pad:
            _outline(draw, x, info_y, word, small, (214, 226, 246))
            x += small.getlength(word + '  ')
        if hi_s:
            _outline(draw, x, info_y, hi_s, small, (255, 150, 55))
            x += small.getlength(hi_s)
        if hi_s and lo_s:
            _outline(draw, x, info_y, '/', small, (186, 196, 216))
            x += small.getlength('/')
        if lo_s:
            _outline(draw, x, info_y, lo_s, small, (55, 150, 255))

    canvas.frame(img)
    return 0.16
