"""A random useless fact (keyless: uselessfacts)."""


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
        max_len = int(float(settings.get('max_length', '140') or 140))
    except (TypeError, ValueError):
        max_len = 140
    max_len = max(40, min(280, max_len))

    def one():
        d = requests.get('https://uselessfacts.jsph.pl/api/v2/facts/random',
                         params={'language': 'en'}, timeout=8).json()
        return str(d.get('text', '') or '').strip()

    try:
        # This API can't filter by length, so try a few times for a short one and
        # keep the shortest we've seen if none come in under the limit.
        best = None
        for _ in range(3):
            t = one()
            if t and len(t) <= max_len:
                best = t
                break
            if t and (best is None or len(t) < len(best)):
                best = t
        if not best:
            return [format_lines('RANDOM FACT', 'NO DATA', '')]
        return _pages(format_lines, 'DID YOU KNOW', best.upper(), rows, cols)
    except Exception:
        return [format_lines('RANDOM FACT', 'OFFLINE', '')]
