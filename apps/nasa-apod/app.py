"""NASA Astronomy Picture of the Day — the day's title (api.nasa.gov)."""


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
    rows, cols = get_rows(), get_cols()
    key = str(settings.get('nasa_api_key', 'DEMO_KEY') or 'DEMO_KEY').strip()
    try:
        d = requests.get('https://api.nasa.gov/planetary/apod',
                         params={'api_key': key}, timeout=10).json()
        title = str(d.get('title', '') or '').upper()
        if not title:
            return [format_lines('NASA APOD', 'NO DATA', str(d.get('msg', ''))[:cols])]
        if rows == 1:
            return [title[:cols].center(cols)]
        return [format_lines('NASA APOD', *_wrap(title, cols, rows - 1))]
    except Exception:
        return [format_lines('NASA APOD', 'OFFLINE', '')]
