"""An inspirational quote (keyless: ZenQuotes)."""


def _pages(format_lines, title, text, rows, cols):
    """Word-wrap text to cols and lay it out across as many pages as needed
    (title heads the first page)."""
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
        d = requests.get('https://zenquotes.io/api/random', timeout=8).json()
        item = d[0] if isinstance(d, list) and d else {}
        q = str(item.get('q', '') or '').strip().upper()
        a = str(item.get('a', '') or '').strip().upper()
        if not q:
            return [format_lines('QUOTE', 'NO DATA', '')]
        text = f'{q}  - {a}' if a else q
        return _pages(format_lines, 'QUOTE', text, rows, cols)
    except Exception:
        return [format_lines('QUOTE', 'OFFLINE', '')]
