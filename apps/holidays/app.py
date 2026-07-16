"""Upcoming holidays for your location — from a bundled ten-year dataset.

The data ships WITH the app (data/<locale>.json: 2026-2035 from
python-holidays, one file per language-region locale, Windows-1252-safe;
rebuilt by scripts/extract_holidays.py) — no API, no network, no key. Each
holiday is one record with its ten dates (an estimated lunar date is prefixed
``~``). Which calendar to show is driven by the configured LOCATION (the language
can't tell France from Canada from Switzerland), down to the province/state:
in Quebec you get Quebec's holidays, not the other provinces'. The names come
already localized when a locale exists for the wall's Language in that country
(fr-CA in Quebec); otherwise any locale for the country, with the catalog's
holiday-name translations as a last resort. An explicit Country setting
overrides the location; the Language's country is the last resort.

PUBLIC holidays always show — they are days off whatever their origin, so
Christmas Day never disappears from a calendar it is statutory in. The
"religious observances" toggle ADDS the non-public religious layer (Christmas
Eve, Yom Kippur, Diwali, Eid al-Fitr...), filtered to the traditions picked in
settings. Dates the source computes rather than observes (some lunar-calendar
dates are announced by sighting) are marked estimated and shown with a ~.
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


TRADITIONS = ('christian', 'islamic', 'jewish', 'hindu', 'buddhist', 'sikh')


def _dataset(locale):
    """One locale file out of the bundled data/ dir, parsed once per process."""
    import json
    import os
    cache = getattr(_dataset, '_cache', None)
    if cache is None:
        cache = _dataset._cache = {}
    if locale in cache:
        return cache[locale]
    path = os.path.join(os.path.dirname(__file__), 'data', f'{locale}.json')
    with open(path, encoding='utf-8') as f:
        doc = json.load(f)
    cache[locale] = doc
    return doc


def _pick_locale(lang, country):
    """The dataset locale for (language, country): the wall's own language in
    that country when it exists, else English there, else any locale for the
    country. None when the dataset has nothing for the country at all. The
    directory listing IS the index."""
    import os
    ddir = os.path.join(os.path.dirname(__file__), 'data')
    have = {f[:-5] for f in os.listdir(ddir)
            if f.endswith('.json') and not f.startswith('_')}
    cc = country.lower()
    for want in (f'{lang}-{cc}', f'en-{cc}'):
        if want in have:
            return want
    for loc in sorted(have):
        if loc.endswith(f'-{cc}'):
            return loc
    return None


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_location=None):
    from datetime import datetime, date
    rows, cols = get_rows(), get_cols()

    def t(s, ctx="time"):
        return i18n.t(s, ctx) if i18n is not None else s

    def on(key, default=''):
        return str(settings.get(key, default) or default).strip().lower() in \
            {'1', 'true', 'yes', 'on'}

    loc = (get_location() or {}) if get_location is not None else {}
    country = str(settings.get('country', '') or '').strip().upper()[:2]
    if not country:
        country = str(loc.get('country') or '') or (i18n.country() if i18n is not None else 'US')
    # Province/state (e.g. CA-QC) — only trusted when it belongs to the country we're
    # actually showing (an explicit Country setting can differ from the location).
    subdivision = str(loc.get('subdivision') or '')
    if subdivision and not subdivision.startswith(country):
        subdivision = ''

    lang = i18n.lang_base if i18n is not None else 'en'
    want_religious = on('religious_holidays')
    traditions = {tr for tr in TRADITIONS if on(f'tradition_{tr}', 'on')}

    try:
        locale = _pick_locale(lang, country)
        if locale is None:
            return [format_lines('Holidays', t('No data', 'holidays'), country)]
        entries = _dataset(locale).get('holidays', [])

        today = date.today()
        upcoming = []
        for h in entries:
            # Nationwide holidays + our own province/state's; drop other regions'
            # (so Quebec doesn't list British Columbia Day).
            subs = h.get('subdivisions')
            if subs and (not subdivision or subdivision not in subs):
                continue
            # Days off always show; the religious toggle adds the OBSERVANCE
            # layer on top, filtered to the chosen traditions.
            if not h.get('public'):
                if not (want_religious and h.get('religious')
                        and h.get('tradition') in traditions):
                    continue
            for ds in h.get('dates', []):
                estimated = ds.startswith('~')
                try:
                    dt = datetime.strptime(ds.lstrip('~'), '%Y-%m-%d').date()
                except ValueError:
                    continue
                if dt >= today:
                    upcoming.append((dt, h, estimated))
                    break            # dates are sorted: the first future one is next
        upcoming.sort(key=lambda e: e[0])

        pages = []
        for dt, h, estimated in upcoming[:4]:
            localized = i18n.holiday(h.get('name')) if i18n is not None else None
            name = str(localized or h.get('name') or '')
            days = (dt - today).days
            cd = t('Today') if days == 0 else f'{t("In")} {days} {t("days")}'
            if i18n is not None:
                dow = i18n.weekday(dt, short=True)
                # "MON SEPTEMBER 7" is already 15 — one more letter and the wall
                # would truncate it. Shorten the month rather than lose the day.
                when = f'{dow} {i18n.date(dt)}'
                if len(when) > cols:
                    when = f'{dow} {i18n.date(dt, short=True)}'
            else:
                when = dt.strftime('%a %b %d')
            if estimated:
                when = f'~{when}'[:cols]     # a computed lunar date, not an announced one

            head = t('Next holiday', 'holidays')
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
        return pages or [format_lines('Holidays', t('None found', 'holidays'), country)]
    except Exception:
        # The dataset is local — a failure here is a broken install, not weather.
        return [format_lines('Holidays', t('No data', 'holidays'), '')]
