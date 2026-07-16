"""A random piece of advice (keyless: Advice Slip)."""


def fetch(settings, format_lines, get_rows, get_cols, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])
    import requests
    try:
        d = requests.get('https://api.adviceslip.com/advice', timeout=8).json()
        text = str((d.get('slip') or {}).get('advice', '') or '').strip()
        if not text:
            return [format_lines('Advice', 'No data', '')]
        return paginate(f'Advice: {text}')
    except Exception:
        return [format_lines('Advice', 'Offline', '')]
