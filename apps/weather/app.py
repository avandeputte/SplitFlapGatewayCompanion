"""Weather — current conditions, forecast, and the air you'll breathe out there.

All PROVIDER knowledge lives in the companion's shared weather helper: this app
opts in with a ``get_weather`` parameter and receives one normalized document —
current conditions, a canonical ``sky`` token per forecast day, and air
quality/UV/pollen already classified into bands. What remains here is entirely
presentation: which of it fits this wall, in what order, in whose language.

On a stock splitflap-os there is no helper to inject; ``_fallback_fetch`` keeps
the app a working drop-in there via keyless Open-Meteo (current + forecast +
air), with the keyed providers available only under the companion.
"""


# ---------------------------------------------------------------------------
# Bands -> colours. The helper classifies every scale (US AQI, OpenWeather's
# 1-5, WeatherAPI's 1-6, UV, pollen) into canonical bands, so ONE map colours
# them all — there used to be three provider-specific ones.
# ---------------------------------------------------------------------------
_BAND_COLOR = {
    'good': 'GREEN', 'moderate': 'YELLOW', 'poor': 'ORANGE', 'bad': 'RED',
    'none': 'NONE', 'unknown': 'UNKNOWN',
}


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


def _balance(text, swatch, cols):
    """Centre `text` between equal runs of `swatch`, scaled to the width — the
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
    """One full-width line: `left` flush left, `right` flush right. format_lines centres each
    line, so a line already `cols` wide passes through untouched — which is what makes the
    forecast's highs and lows line up in a column you can read down."""
    left, right = str(left), str(right)
    if len(right) >= cols:
        return right[:cols]
    left = left[:cols - len(right) - 1]
    return left + ' ' * (cols - len(left) - len(right)) + right


# The sky: a WORD, an intensity, and a colour — keyed by the helper's canonical
# sky token, so no provider code is ever read here.
#
# The word is what you actually want to know — a colour tells you "wet" but not whether that
# is drizzle or a downpour. The intensity is a suffix rather than a separate word (LRain /
# HRain) because a suffix survives translation: every language gets to keep its own noun and
# the mark means the same thing everywhere. And it is short, which is the whole problem: a
# 15-column line has room for a day, a condition and 24/14, and nothing else.
#
# The marks are `-` and `!`, and the choice is forced. HEAVY used to be `+`, which is on the
# English, German and Scandinavian reels — and on NO OTHER. A module asked for a flap it does
# not carry simply homes, so on a French, Spanish, Italian, Portuguese or Dutch wall "Rain+"
# came out as "Rain", making a downpour indistinguishable from ordinary rain. The suffix IS
# the payload here, and it was being silently dropped in exactly the languages that have a
# reel of their own. `-` and `!` are on every published set (see the wiki's Flaps & Character
# Sets), so they carry the same meaning to every wall.
#
# The colour still comes along when the wall is wide enough for it.
_SKY = {
    #  token       word       suffix  colour tile
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


def _sky_word(sky, t):
    """The condition, translated and short enough for the wall.

    The `-`/`!` is dropped rather than the noun when a language's word is already as long as
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


def _fallback_fetch(settings, days, air):
    """Stock splitflap-os has no helper to inject; keyless Open-Meteo keeps the
    app a working drop-in there. Same document shape the helper returns, minus
    the keyed providers."""
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
        if get_weather is not None:
            w = get_weather(days=forecast_days, air=want_air)
        else:
            w = _fallback_fetch(settings, forecast_days, want_air)
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
        # The current sky gets its colour too, balanced — so the conditions line
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
        # while its neighbour does not is a list you have to read twice.
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
                days.append((dt, d, _sky_word(d.get('sky'), t)))

            if days:
                word_w = max(len(word) for _, _, word in days)
                temp_w = max(len(f"{_short_temp(d['hi_f'], temp_unit)}/"
                                 f"{_short_temp(d['lo_f'], temp_unit)}") for _, d, _ in days)

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
                    right = (f"{_short_temp(d['hi_f'], temp_unit)}/"
                             f"{_short_temp(d['lo_f'], temp_unit)}")
                    fc_lines.append(_row(left, right, cols))

        # --- the pages ----------------------------------------------------------
        # Only one location is supported, so we don't repeat it on every page.
        if rows >= 4:
            # A tall wall can say all of this at once, one row per metric, over as
            # few pages as they fit on.
            # A tall wall has the room, so humidity gets a labelled line of its own.
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
