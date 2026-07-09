"""Sunrise / sunset / day length for the configured location (keyless: sunrise-sunset.org)."""


def _latlon(settings, requests):
    """Global precise location, else geocode of the ZIP, else a Boston fallback."""
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
        if re.fullmatch(r'\d{5}', zip_code):     # a US ZIP — disambiguate (02118 also exists abroad)
            params['countrycodes'] = 'us'
        geo = requests.get('https://nominatim.openstreetmap.org/search', params=params,
                           headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'},
                           timeout=6).json()
        if geo:
            return float(geo[0]['lat']), float(geo[0]['lon'])
    except Exception:
        pass
    return 42.3601, -71.0589


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    from datetime import datetime
    import pytz
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s) if i18n is not None else s

    def line(label, value):                 # label trimmed to fit — never the time
        return f'{label[:max(1, cols - len(value) - 1)]} {value}'
    try:
        lat, lon = _latlon(settings, requests)
        data = requests.get('https://api.sunrise-sunset.org/json',
                            params={'lat': lat, 'lng': lon, 'formatted': 0},
                            timeout=8).json()
        r = data.get('results', {})
        try:
            tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone('US/Eastern')

        def local(key):
            iso = r.get(key)
            if not iso:
                return '--:--'
            dt = datetime.fromisoformat(str(iso).replace('Z', '+00:00')).astimezone(tz)
            return dt.strftime('%I:%M%p').lstrip('0')

        rise, sett = local('sunrise'), local('sunset')
        secs = int(r.get('day_length', 0) or 0)
        length = f'{secs // 3600}H{(secs % 3600) // 60:02d}M'
        if rows == 1:
            return [format_lines(f'{t("UP")} {rise} {t("DN")} {sett}')]
        if rows == 2:
            return [format_lines(line(t('SUNRISE'), rise), line(t('SUNSET'), sett))]
        return [format_lines(line(t('SUNRISE'), rise), line(t('SUNSET'), sett),
                             line(t('DAYLIGHT'), length))]
    except Exception:
        return [format_lines('SUN TIMES', 'OFFLINE', '')]
