"""Upcoming public holidays for a country (keyless: Nager.Date)."""


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    from datetime import datetime, date
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s) if i18n is not None else s

    # Country: an explicit code wins; otherwise follow the Language's country
    # (US/GB/AU/FR/DE/…). Nager returns each holiday's localName in the country's own
    # language, so the calendar AND the holiday names match the chosen language.
    country = str(settings.get('country', '') or '').strip().upper()[:2]
    if not country:
        country = i18n.country() if i18n is not None else 'US'
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
                if days == 0:
                    cd = t('TODAY')
                elif days > 0:
                    cd = f'{t("IN")} {days} {t("DAYS")}'
            except ValueError:
                pass
            if rows == 1:
                pages.append(f'{name} {cd}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(name, cd))
            else:
                pages.append(format_lines(t('NEXT HOLIDAY'), name, cd))
        return pages or [format_lines('HOLIDAYS', 'NONE', '')]
    except Exception:
        return [format_lines('HOLIDAYS', 'OFFLINE', '')]
