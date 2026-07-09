"""Aurora / geomagnetic activity — planetary K-index (NOAA SWPC, keyless)."""


def _label(kp):
    if kp < 3:
        return 'QUIET'
    if kp < 5:
        return 'UNSETTLED'
    if kp < 6:
        return 'MINOR STORM'
    if kp < 7:
        return 'MODERATE'
    if kp < 8:
        return 'STRONG STORM'
    if kp < 9:
        return 'SEVERE STORM'
    return 'EXTREME'


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    rows = get_rows()
    try:
        data = requests.get('https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json',
                            timeout=8).json()
        if not isinstance(data, list) or not data:
            return [format_lines('AURORA', 'NO DATA', '')]
        latest = data[-1]
        # The feed is a list of {time_tag, Kp, ...} records (newest last).
        kp = float(latest.get('Kp') if isinstance(latest, dict) else latest[1])
        kps = f'{kp:.0f}' if kp == int(kp) else f'{kp:.1f}'
        label = _label(kp)
        if rows == 1:
            return [format_lines(f'AURORA KP {kps}')]
        if rows == 2:
            return [format_lines(f'AURORA KP {kps}', label)]
        return [format_lines('AURORA', f'KP INDEX {kps}', label)]
    except Exception:
        return [format_lines('AURORA', 'OFFLINE', '')]
