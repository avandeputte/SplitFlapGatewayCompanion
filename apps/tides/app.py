"""Today's tide predictions for a NOAA station (keyless: NOAA CO-OPS)."""


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    from datetime import datetime
    rows, cols = get_rows(), get_cols()
    station = str(settings.get('tide_station', '8443970') or '8443970').strip()
    try:
        data = requests.get(
            'https://api.tidesandcurrents.noaa.gov/api/prod/datagetter',
            params={'product': 'predictions', 'application': 'SplitFlapCompanion',
                    'datum': 'MLLW', 'station': station, 'time_zone': 'lst_ldt',
                    'units': 'english', 'interval': 'hilo', 'format': 'json', 'date': 'today'},
            timeout=8).json()
        preds = data.get('predictions') or []
        if not preds:
            return [format_lines('TIDES', 'NO DATA', 'CHECK STATION')]
        pages = []
        for p in preds[:6]:
            raw = str(p.get('t', ''))
            hhmm = raw.split(' ')[-1] if ' ' in raw else raw
            try:
                hhmm = datetime.strptime(hhmm, '%H:%M').strftime('%I:%M%p').lstrip('0')
            except ValueError:
                pass
            kind = 'HIGH' if p.get('type') == 'H' else 'LOW'
            v = str(p.get('v', ''))
            if rows == 1:
                pages.append(f'{kind} {hhmm} {v}FT'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(f'{kind} TIDE', f'{hhmm}  {v}FT'))
            else:
                pages.append(format_lines(f'{kind} TIDE', hhmm, f'{v} FT'))
        return pages or [format_lines('TIDES', 'NO DATA', '')]
    except Exception:
        return [format_lines('TIDES', 'OFFLINE', '')]
