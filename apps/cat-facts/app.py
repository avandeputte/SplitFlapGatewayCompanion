"""A random cat fact (keyless: catfact.ninja)."""


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
        max_len = int(float(settings.get('max_length', '120') or 120))
    except (TypeError, ValueError):
        max_len = 120
    max_len = max(40, min(250, max_len))
    try:
        # catfact.ninja honors max_length, so we can ask for a display-friendly fact.
        d = requests.get('https://catfact.ninja/fact',
                         params={'max_length': max_len}, timeout=8).json()
        text = str(d.get('fact', '') or '').strip().upper()
        if not text:
            return [format_lines('CAT FACT', 'NO DATA', '')]
        return _pages(format_lines, 'CAT FACT', text, rows, cols)
    except Exception:
        return [format_lines('CAT FACT', 'OFFLINE', '')]
