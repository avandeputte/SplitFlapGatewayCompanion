"""Forecast Ribbon — the shape of the day, painted in flap colours (keyless: Open-Meteo).

Each COLUMN is an hour. The BAR HEIGHT is how warm it gets relative to the rest of the
window, and the COLOUR is the actual temperature — violet through blue, green, yellow and
orange to red. So the day has a silhouette you read at a glance: a cold morning is a low
blue foothill, the warm afternoon is a tall orange ridge, and a cold front arriving at four
o'clock is a cliff.

It is a sibling of art-clock: a picture, not a page of numbers. Like art-clock it declares
`"animation": true`, which is what keeps its lowercase r/o/y/g/b/p/w meaning the COLOUR
FLAPS rather than the letters — the companion sends an animation's page RAW.

The colour is absolute and the height is relative, which is the whole trick: the height
tells you the shape of *this* day, and the colour tells you whether that shape is a warm one.
A flat green wall is a mild, boring day; a flat red one is a heatwave.
"""

# Temperature (°C) -> flap colour. Absolute, so red always means hot — not merely "hotter
# than the rest of today", which is what a normalized colour ramp would say on a cold day.
_BANDS = (
    (-5.0, 'p'),     # violet   — deep freeze
    (2.0, 'b'),      # blue     — freezing / near it
    (10.0, 'g'),     # green    — cool
    (18.0, 'y'),     # yellow   — mild
    (26.0, 'o'),     # orange   — warm
)
_HOTTEST = 'r'       # red      — hot


def _band(c):
    for limit, colour in _BANDS:
        if c < limit:
            return colour
    return _HOTTEST


def _latlon(settings, requests):
    """The global location: precise lat/lon if set, else the ZIP geocoded, else Boston."""
    lat = str(settings.get('location_lat', '') or '').strip()
    lon = str(settings.get('location_lon', '') or '').strip()
    if lat and lon:
        try:
            return float(lat), float(lon)
        except ValueError:
            pass
    zip_code = str(settings.get('zip_code', '02118') or '02118').strip()
    try:
        import re
        params = {'q': zip_code, 'format': 'json', 'limit': 1}
        if re.fullmatch(r'\d{5}', zip_code):        # a US ZIP — 02118 also exists abroad
            params['countrycodes'] = 'us'
        geo = requests.get('https://nominatim.openstreetmap.org/search', params=params,
                           headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'},
                           timeout=6).json()
        if geo:
            return float(geo[0]['lat']), float(geo[0]['lon'])
    except Exception:
        pass
    return 42.3601, -71.0589


def fetch(settings, format_lines, get_rows, get_cols, get_weather=None):
    import requests
    from datetime import datetime, timedelta, timezone

    rows, cols = get_rows(), get_cols()
    mono = settings.get('disable_colors', 'no') == 'yes'

    try:
        # The shared helper carries an hourly series (keyless Open-Meteo, whatever
        # the provider) — one fetch, cached, shared with the weather app. Without
        # a helper (stock splitflap-os) we ask Open-Meteo ourselves, as before.
        if get_weather is not None:
            w = get_weather(days=1)
            if not w or not w.get('ok'):
                raise RuntimeError(str((w or {}).get('error') or 'offline'))
            hourly = w.get('hourly') or {}
            times = hourly.get('time') or []
            temps = hourly.get('temp_c') or []
            offset = int(hourly.get('utc_offset_s') or 0)
        else:
            lat, lon = _latlon(settings, requests)
            data = requests.get(
                'https://api.open-meteo.com/v1/forecast',
                params={'latitude': lat, 'longitude': lon,
                        'hourly': 'temperature_2m',
                        'timezone': 'auto', 'forecast_days': 2},
                timeout=8).json()
            hourly = data.get('hourly') or {}
            times = hourly.get('time') or []
            temps = hourly.get('temperature_2m') or []
            offset = int(data.get('utc_offset_seconds') or 0)
        if not times or not temps:
            return [format_lines('FORECAST', 'NO DATA', '')]

        # Start at the current hour WHERE THE WEATHER IS. The API returns its hours in the
        # location's own local time (timezone=auto), so comparing them against this
        # machine's clock lands you hours away: a companion in Boston asking about Brussels
        # would draw tomorrow morning and call it tonight. utc_offset_seconds is the
        # location's, so it is what "now" has to mean here.
        there = datetime.now(timezone.utc) + timedelta(seconds=offset)
        now = there.strftime('%Y-%m-%dT%H:00')
        start = next((i for i, t in enumerate(times) if t >= now), 0)
        window = [t for t in temps[start:start + cols] if t is not None][:cols]
        if not window:
            return [format_lines('FORECAST', 'NO DATA', '')]

        lo, hi = min(window), max(window)
        span = (hi - lo) or 1.0        # a dead-flat day would divide by zero

        # Column c is an hour; the bar rises from the bottom. Height is RELATIVE (the shape
        # of this day), colour is ABSOLUTE (whether that shape is a warm one).
        page = [' '] * (rows * cols)
        for c, t in enumerate(window):
            height = 1 + int(round((t - lo) / span * (rows - 1)))
            ink = '#' if mono else _band(t)
            for r in range(rows - height, rows):
                page[r * cols + c] = ink
        return [''.join(page)]
    except Exception:
        return [format_lines('FORECAST', 'OFFLINE', '')]
