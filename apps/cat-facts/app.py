"""A random cat fact (keyless: catfact.ninja)."""


def fetch(settings, format_lines, get_rows, get_cols, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])
    import requests
    try:
        max_len = int(float(settings.get('max_length', '120') or 120))
    except (TypeError, ValueError):
        max_len = 120
    max_len = max(40, min(250, max_len))
    try:
        # catfact.ninja honors max_length, so we can ask for a display-friendly fact.
        d = requests.get('https://catfact.ninja/fact',
                         params={'max_length': max_len}, timeout=8).json()
        text = str(d.get('fact', '') or '').strip()
        if not text:
            return [format_lines('Cat fact', 'No data', '')]
        return paginate(text)   # no title — just the fact
    except Exception:
        return [format_lines('Cat fact', 'Offline', '')]
