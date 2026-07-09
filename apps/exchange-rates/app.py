"""Currency exchange rates via Frankfurter (European Central Bank data, keyless)."""


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    rows, cols = get_rows(), get_cols()
    base = (str(settings.get('base', 'USD') or 'USD').strip().upper() or 'USD')[:3]
    targets = [t.strip().upper()[:3] for t in str(settings.get('targets', 'EUR,GBP,JPY')).split(',') if t.strip()]
    targets = targets[:8] or ['EUR', 'GBP', 'JPY']
    try:
        data = requests.get('https://api.frankfurter.app/latest',
                            params={'from': base, 'to': ','.join(targets)}, timeout=8).json()
        rates = data.get('rates', {})
        if not rates:
            return [format_lines('FX RATES', 'NO DATA', 'CHECK CODES')]

        def fmt(v):
            return f'{v:.3f}' if v < 10 else (f'{v:.2f}' if v < 1000 else f'{v:.0f}')

        pairs = [(t, rates[t]) for t in targets if t in rates]
        if rows == 1:
            return [f'1{base}={fmt(v)}{t}'.center(cols)[:cols] for t, v in pairs]
        lines = [f'1 {base} ='] + [f'{t} {fmt(v)}' for t, v in pairs]
        return [format_lines(*lines[i:i + rows]) for i in range(0, len(lines), rows)]
    except Exception:
        return [format_lines('FX RATES', 'OFFLINE', '')]
