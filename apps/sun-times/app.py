"""Sunrise / sunset / day length for the configured location (keyless: Open-Meteo).

Times track the location: Open-Meteo returns them in the place's own local time
(timezone=auto), just like the weather app — no separate timezone setting needed."""


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


def _columns(pairs, cols, gap=3):
    """Two aligned columns — label flush left, value flush right — kept together as
    one CENTRED block rather than pinned to the wall's edges.

    format_lines centres each line, so the block is only as wide as its content plus
    a small gap: on a wide wall the label and its time sit together in the middle
    instead of stranded at opposite ends with a lake of empty space between them. The
    value column still lines up down the page (every line the same width). A narrow
    wall falls back to the full width, trimming the label, never the time.
    """
    pairs = [(str(left), str(right)) for left, right in pairs]
    rw = max((len(r) for _, r in pairs), default=0)
    lw = max((len(l) for l, _ in pairs), default=0)
    inner = min(cols, lw + gap + rw)
    lspace = max(1, inner - rw)                       # label column width, incl. the gap
    out = []
    for left, right in pairs:
        if len(left) > lspace - 1:
            left = left[:max(0, lspace - 1)]
        out.append((left.ljust(lspace) + right.rjust(rw))[:cols])
    return out


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_location=None):
    import requests
    from datetime import datetime
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "sun") if i18n is not None else s

    def u(k):                               # localized H/M duration suffix (Dutch U for uur, etc.)
        return i18n.unit(k) if i18n is not None else k

    def fmt_time(iso):                       # ISO is already the location's local time
        if not iso:
            return '--:--'
        dt = datetime.fromisoformat(str(iso))
        # AM/PM is English-only — everyone else gets 24h.
        if i18n is not None:
            return i18n.time(dt, ampm_space=False)
        return dt.strftime('%I:%M%p').lstrip('0')

    try:
        # The platform's cached geocode first (one Nominatim query shared with
        # weather and every other location app); our own ladder only off-host.
        loc = get_location() if get_location is not None else None
        if isinstance(loc, dict) and loc.get('lat') is not None:
            lat, lon = float(loc['lat']), float(loc['lon'])
        else:
            lat, lon = _latlon(settings, requests)
        data = requests.get('https://api.open-meteo.com/v1/forecast',
                            params={'latitude': lat, 'longitude': lon,
                                    'daily': 'sunrise,sunset,daylight_duration',
                                    'timezone': 'auto', 'forecast_days': 1},
                            timeout=8).json()
        daily = data.get('daily', {})
        rise = fmt_time((daily.get('sunrise') or [None])[0])
        sett = fmt_time((daily.get('sunset') or [None])[0])
        secs = int((daily.get('daylight_duration') or [0])[0] or 0)
        length = f'{secs // 3600}{u("H")}{(secs % 3600) // 60:02d}{u("M")}'
        if rows == 1:
            return [format_lines(f'{t("Up")} {rise} {t("Dn")} {sett}')]
        pairs = [(t('Sunrise'), rise), (t('Sunset'), sett)]
        if rows >= 3:
            pairs.append((t('Daylight'), length))
        return [format_lines(*_columns(pairs, cols))]
    except Exception:
        return [format_lines('Sun times', t('Offline'), '')]
