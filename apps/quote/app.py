"""An inspirational quote (keyless: DummyJSON quotes)."""


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
        max_len = int(float(settings.get('max_length', '150') or 150))
    except (TypeError, ValueError):
        max_len = 150
    max_len = max(40, min(300, max_len))

    def one():
        d = requests.get('https://dummyjson.com/quotes/random', timeout=8).json()
        return str(d.get('quote', '') or '').strip(), str(d.get('author', '') or '').strip()

    try:
        # The API can't filter by length, so try a few times for a short quote,
        # keeping the shortest seen if none come in under the limit.
        best = None
        for _ in range(3):
            q, a = one()
            if not q:
                continue
            if len(q) <= max_len:
                best = (q, a)
                break
            if best is None or len(q) < len(best[0]):
                best = (q, a)
        if not best:
            return [format_lines('QUOTE', 'NO DATA', '')]
        q, a = best
        text = f'{q.upper()}  - {a.upper()}' if a else q.upper()
        return _pages(format_lines, 'QUOTE', text, rows, cols)
    except Exception:
        return [format_lines('QUOTE', 'OFFLINE', '')]
