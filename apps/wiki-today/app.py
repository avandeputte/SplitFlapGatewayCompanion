"""Today on Wikipedia — featured article & most-read (keyless: Wikimedia REST)."""


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


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    from datetime import datetime
    import pytz
    rows, cols = get_rows(), get_cols()
    try:
        try:
            tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone('US/Eastern')
        now = datetime.now(tz)
        d = requests.get(f'https://en.wikipedia.org/api/rest_v1/feed/featured/{now:%Y/%m/%d}',
                         headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'}, timeout=10).json()
        pages = []
        tfa = d.get('tfa') or {}
        title = str(tfa.get('normalizedtitle', '') or '').upper()
        if title:
            if rows == 1:
                pages.append(f'WIKI {title}'[:cols].center(cols))
            else:
                pages.append(format_lines('WIKI FEATURED', *_wrap(title, cols, rows - 1)))
        for a in ((d.get('mostread') or {}).get('articles', []) or [])[:3]:
            t = str(a.get('normalizedtitle', '') or '').upper()
            if not t:
                continue
            if rows == 1:
                pages.append(f'WIKI {t}'[:cols].center(cols))
            else:
                pages.append(format_lines('WIKI MOST READ', *_wrap(t, cols, rows - 1)))
        return pages or [format_lines('WIKIPEDIA', 'NO DATA', '')]
    except Exception:
        return [format_lines('WIKIPEDIA', 'OFFLINE', '')]
