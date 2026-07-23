"""Recent significant earthquakes worldwide (USGS FDSN, keyless)."""


def _wrap(text, cols, maxlines):
    words, lines, cur = text.split(), [], ''
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= cols:
            cur = f'{cur} {w}'.strip()
        else:
            lines.append(cur)
            cur = w[:cols]
            if len(lines) >= maxlines:
                break
    if cur and len(lines) < maxlines:
        lines.append(cur)
    return lines[:maxlines] or ['']


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    from datetime import datetime, timezone
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "quake") if i18n is not None else s

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
            place = str(p.get('place', '') or t('Unknown'))
            if isinstance(mag, (int, float)):
                # Severity at a glance: a colour square renders everywhere —
                # coloured pixels on a matrix wall, the colour FLAP on a real one.
                tile = '🟥' if mag >= 7 else '🟧' if mag >= 6 else '🟨' if mag >= 5 else '🟩'
                mags = f'{tile} M{mag:.1f}'
            else:
                mags = 'M?'
            ago = ''
            ms = p.get('time')
            if isinstance(ms, (int, float)):
                mins = int((now - ms / 1000) / 60)
                ago = f'{mins}m {t("ago")}' if mins < 120 else f'{mins // 60}h {t("ago")}'
            # USGS place is like "134 km E of Bitung, Indonesia": show the distance
            # on the header line and give the location name the remaining rows so
            # it isn't cut off. Match on the folded text and slice the original —
            # USGS writes "of" in lowercase, and the place keeps its original casing.
            folded = place.upper()
            if ' OF ' in folded:
                cut = folded.index(' OF ')
                dist, loc = place[:cut], place[cut + 4:]
                head = f'{mags} {dist.strip()}'
            else:
                loc, head = place, f'{mags}  {ago}'.strip()
            if rows == 1:
                pages.append(f'{mags} {loc}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(head, *_wrap(loc, cols, 1)))
            else:
                pages.append(format_lines(head, *_wrap(loc, cols, rows - 1)))
        return pages or [format_lines('Earthquakes', t('None recent'), '')]
    except Exception:
        return [format_lines('Earthquakes', t('Offline'), '')]
