"""An inspirational quote (keyless: DummyJSON quotes)."""


def fetch(settings, format_lines, get_rows, get_cols, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])
    import requests
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
            return [format_lines('Quote', 'No data', '')]
        q, a = best
        text = f'{q}  - {a}' if a else q
        return paginate(f'Quote: {text}')
    except Exception:
        return [format_lines('Quote', 'Offline', '')]
