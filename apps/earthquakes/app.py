"""Recent significant earthquakes worldwide (USGS FDSN, keyless)."""


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    from datetime import datetime, timezone
    rows, cols = get_rows(), get_cols()
    try:
        minmag = str(settings.get('min_magnitude', '4.5') or '4.5')
        data = requests.get('https://earthquake.usgs.gov/fdsnws/event/1/query',
                            params={'format': 'geojson', 'orderby': 'time', 'limit': 5,
                                    'minmagnitude': minmag}, timeout=8).json()
        feats = data.get('features', []) or []
        now = datetime.now(timezone.utc).timestamp()
        pages = []
        for ft in feats[:5]:
            p = ft.get('properties', {}) or {}
            mag = p.get('mag')
            place = str(p.get('place', '') or 'UNKNOWN').upper()
            mags = f'M{mag:.1f}' if isinstance(mag, (int, float)) else 'M?'
            ago = ''
            ms = p.get('time')
            if isinstance(ms, (int, float)):
                mins = int((now - ms / 1000) / 60)
                ago = f'{mins}M AGO' if mins < 120 else f'{mins // 60}H AGO'
            if rows == 1:
                pages.append(f'{mags} {place}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(f'{mags} QUAKE', place))
            else:
                pages.append(format_lines(f'{mags} EARTHQUAKE', place, ago))
        return pages or [format_lines('EARTHQUAKES', 'NONE RECENT', '')]
    except Exception:
        return [format_lines('EARTHQUAKES', 'OFFLINE', '')]
