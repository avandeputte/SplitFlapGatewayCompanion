"""Upcoming public holidays for your location (keyless: Nager.Date).

Which calendar to show is driven by the configured LOCATION (the language can't tell
France from Canada from Switzerland), down to the province/state: in Quebec you get
Quebec's holidays, not the other provinces'. Names use the source's native localName,
or a localized translation for the common holidays (so a French speaker in Quebec —
where the source only has English — still reads Fête du Travail, Action de grâce…).
An explicit Country setting overrides the location; the Language is the last resort.
"""


def _wrap(text, cols, maxlines):
    """Word-wrap, because a holiday name is the whole point of the app and cutting
    it in half ("MARTIN LUTHER KING J") is worse than using another row."""
    words, lines, cur = str(text or '').split(), [], ''
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= cols:
            cur = f'{cur} {w}'.strip()
        else:
            if cur:
                lines.append(cur)
            cur = w[:cols]
            if len(lines) >= maxlines:
                break
    if cur and len(lines) < maxlines:
        lines.append(cur)
    return lines[:maxlines] or ['']


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_location=None):
    import requests
    from datetime import datetime, date
    rows, cols = get_rows(), get_cols()

    def t(s, ctx="time"):
        return i18n.t(s, ctx) if i18n is not None else s

    loc = (get_location() or {}) if get_location is not None else {}
    country = str(settings.get('country', '') or '').strip().upper()[:2]
    if not country:
        country = str(loc.get('country') or '') or (i18n.country() if i18n is not None else 'US')
    # Province/state (e.g. CA-QC) — only trusted when it belongs to the country we're
    # actually showing (an explicit Country setting can differ from the location).
    subdivision = str(loc.get('subdivision') or '')
    if subdivision and not subdivision.startswith(country):
        subdivision = ''
    try:
        data = requests.get(f'https://date.nager.at/api/v3/NextPublicHolidays/{country}', timeout=8).json()
        if not isinstance(data, list) or not data:
            return [format_lines('HOLIDAYS', 'NONE FOUND', country)]
        # Keep nationwide holidays + the ones for our own province/state; drop other
        # regions' (so Quebec doesn't list British Columbia Day).
        if subdivision:
            data = [h for h in data if h.get('global') or subdivision in (h.get('counties') or [])]
        pages, today = [], date.today()
        for h in data[:4]:
            localized = i18n.holiday(h.get('name')) if i18n is not None else None
            name = str(localized or h.get('localName') or h.get('name') or '').upper()
            cd, when = '', ''
            try:
                dt = datetime.strptime(h.get('date', ''), '%Y-%m-%d').date()
                days = (dt - today).days
                if days == 0:
                    cd = t('TODAY')
                elif days > 0:
                    cd = f'{t("IN")} {days} {t("DAYS")}'
                if i18n is not None:
                    dow = i18n.weekday(dt, short=True)
                    # "MON SEPTEMBER 7" is already 15 — one more letter and the wall
                    # would truncate it. Shorten the month rather than lose the day.
                    when = f'{dow} {i18n.date(dt)}'
                    if len(when) > cols:
                        when = f'{dow} {i18n.date(dt, short=True)}'
                else:
                    when = dt.strftime('%a %b %d').upper()
            except ValueError:
                pass

            head = t('NEXT HOLIDAY', 'holidays')
            if rows == 1:
                pages.append(f'{name} {cd}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(name, cd))
            elif rows == 3:
                # Three rows is the common wall, and it has exactly one to spare. A name
                # that fits keeps the header; one that doesn't takes the header's row
                # rather than being truncated — "NEXT HOLIDAY" says less than the name.
                if len(name) <= cols:
                    pages.append(format_lines(head, name, cd))
                else:
                    pages.append(format_lines(*_wrap(name, cols, 2), cd))
            else:
                # A tall wall gives the name as many rows as it needs, and spends what's
                # left on the date rather than on blank flaps.
                lines = [head] + _wrap(name, cols, rows - 2) + [cd]
                if when and len(lines) < rows:
                    lines.insert(-1, when)
                pages.append(format_lines(*lines))
        return pages or [format_lines('HOLIDAYS', 'NONE', '')]
    except Exception:
        return [format_lines('HOLIDAYS', 'OFFLINE', '')]
