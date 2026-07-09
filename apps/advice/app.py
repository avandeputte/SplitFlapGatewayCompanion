"""A random piece of advice (keyless: Advice Slip)."""


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
        d = requests.get('https://api.adviceslip.com/advice', timeout=8).json()
        text = str((d.get('slip') or {}).get('advice', '') or '').strip().upper()
        if not text:
            return [format_lines('ADVICE', 'NO DATA', '')]
        return _pages(format_lines, 'ADVICE', text, rows, cols)
    except Exception:
        return [format_lines('ADVICE', 'OFFLINE', '')]
