"""Upcoming public holidays for a country (keyless: Nager.Date)."""


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    from datetime import datetime, date
    rows, cols = get_rows(), get_cols()
    country = str(settings.get('country', 'US') or 'US').strip().upper()[:2] or 'US'
    try:
        data = requests.get(f'https://date.nager.at/api/v3/NextPublicHolidays/{country}', timeout=8).json()
        if not isinstance(data, list) or not data:
            return [format_lines('HOLIDAYS', 'NONE FOUND', country)]
        pages, today = [], date.today()
        for h in data[:4]:
            name = str(h.get('localName') or h.get('name') or '').upper()
            cd = ''
            try:
                days = (datetime.strptime(h.get('date', ''), '%Y-%m-%d').date() - today).days
                cd = 'TODAY' if days == 0 else (f'IN {days} DAYS' if days > 0 else '')
            except ValueError:
                pass
            if rows == 1:
                pages.append(f'{name} {cd}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(name, cd))
            else:
                pages.append(format_lines('NEXT HOLIDAY', name, cd))
        return pages or [format_lines('HOLIDAYS', 'NONE', '')]
    except Exception:
        return [format_lines('HOLIDAYS', 'OFFLINE', '')]
