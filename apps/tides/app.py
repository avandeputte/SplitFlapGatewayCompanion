"""Today's tide predictions for a NOAA station (keyless: NOAA CO-OPS)."""


def _columns(pairs, cols, gap=3):
    """Two aligned columns — time flush left, height flush right — kept together as
    one CENTRED block rather than pinned to the wall's edges.

    format_lines centres each line, so the block is only as wide as its content plus a
    small gap: on a wide wall the time and its height sit together in the middle, not
    stranded at opposite ends. The heights still line up in a column you can read down
    (every line the same width). A narrow wall falls back to the full width."""
    pairs = [(str(left), str(right)) for left, right in pairs]
    rw = max((len(r) for _, r in pairs), default=0)
    lw = max((len(l) for l, _ in pairs), default=0)
    inner = min(cols, lw + gap + rw)
    lspace = max(1, inner - rw)                       # time column width, incl. the gap
    out = []
    for left, right in pairs:
        if len(left) > lspace - 1:
            left = left[:max(0, lspace - 1)]
        out.append((left.ljust(lspace) + right.rjust(rw))[:cols])
    return out


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
    import requests
    from datetime import datetime
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "tides") if i18n is not None else s

    def fmt_time(hhmm):                       # NOAA gives 24h local time ("15:48")
        try:
            dt = datetime.strptime(hhmm, '%H:%M')
        except ValueError:
            return hhmm
        # AM/PM is English-only — everyone else gets 24h.
        if i18n is not None:
            return i18n.time(dt, ampm_space=False)
        return dt.strftime('%I:%M%p').lstrip('0')

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
            return [format_lines(t('Tides'), t('No data'), t('Check station'))]
        # Four rows or more: today's tides are a LIST, and a list belongs on one page.
        # One tide per page meant waiting through four page turns to answer "when is high
        # tide?" — a question the whole app exists to answer at a glance.
        if rows >= 4:
            # An arrow says high-or-low in ONE cell, which on a 15-wide wall is the
            # difference between "HIGH 9:28AM" not fitting and "↑ 9:28AM  11.2FT" fitting
            # with room to spare. Only where the wall HAS arrows: on a real reel a ↑ falls
            # back to "^", which is not what you want a tide table to say — so there it
            # keeps the word, and shortens it to an initial only if it must.
            arrows = bool(caps and caps.pictographs)
            pairs = []
            for p in preds[:rows - 1]:
                raw = str(p.get('t', ''))
                hhmm = fmt_time(raw.split(' ')[-1] if ' ' in raw else raw)
                is_high = p.get('type') == 'H'
                height = f"{p.get('v', '')}FT"
                if arrows:
                    kind = '\u2191' if is_high else '\u2193'
                else:
                    kind = t('High') if is_high else t('Low')
                left = f'{kind} {hhmm}'
                if len(left) + len(height) + 1 > cols:   # narrow wall: initial will do
                    left = f'{kind[:1]} {hhmm}'
                pairs.append((left, height))
            return [format_lines(t('Tides'), *_columns(pairs, cols))]

        pages = []
        for p in preds[:6]:
            raw = str(p.get('t', ''))
            hhmm = fmt_time(raw.split(' ')[-1] if ' ' in raw else raw)
            is_high = p.get('type') == 'H'
            v = str(p.get('v', ''))
            if rows == 1:
                # Compact single row: the short generic high/low word keeps the
                # time + height on one line (the full "X TIDE" would crowd them out).
                kind = t('High') if is_high else t('Low')
                pages.append(f'{kind} {hhmm} {v}FT'[:cols].center(cols))
            elif rows == 2:
                kind = t('High tide') if is_high else t('Low tide')
                pages.append(format_lines(kind, f'{hhmm}  {v}FT'))
            else:
                kind = t('High tide') if is_high else t('Low tide')
                pages.append(format_lines(kind, hhmm, f'{v} FT'))
        return pages or [format_lines(t('Tides'), t('No data'), '')]
    except Exception:
        return [format_lines(t('Tides'), t('Offline'), '')]
