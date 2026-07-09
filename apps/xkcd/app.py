"""The latest xkcd comic — number & title (keyless: xkcd.com)."""


def _pages(format_lines, title, text, rows, cols):
    lines, cur = [], ''
    for w in text.split():
        w = w if len(w) <= cols else w[:cols]
        if len(cur) + len(w) + (1 if cur else 0) <= cols:
            cur = f'{cur} {w}'.strip()
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if not lines:
        lines = ['']
    if rows == 1:
        return [ln.center(cols)[:cols] for ln in lines]
    pages = [format_lines(title, *lines[:rows - 1])]
    i = rows - 1
    while i < len(lines):
        pages.append(format_lines(*lines[i:i + rows]))
        i += rows
    return pages


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    rows, cols = get_rows(), get_cols()
    try:
        d = requests.get('https://xkcd.com/info.0.json', timeout=8).json()
        num = d.get('num', '')
        title = str(d.get('safe_title', '') or d.get('title', '') or '').strip().upper()
        if not title:
            return [format_lines('XKCD', 'NO DATA', '')]
        return _pages(format_lines, f'XKCD #{num}', title, rows, cols)
    except Exception:
        return [format_lines('XKCD', 'OFFLINE', '')]
