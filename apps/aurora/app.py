"""Aurora / geomagnetic activity — planetary K-index (NOAA SWPC, keyless)."""


def _label(kp):
    if kp < 3:
        return 'Quiet'
    if kp < 5:
        return 'Unsettled'
    if kp < 6:
        return 'Minor storm'
    if kp < 7:
        return 'Moderate'
    if kp < 8:
        return 'Strong storm'
    if kp < 9:
        return 'Severe storm'
    return 'Extreme'


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "aurora") if i18n is not None else s

    def num(kp):                              # integer Kp shows no decimal
        whole = (kp == int(kp))
        if i18n is not None:
            return i18n.number(kp, decimals=0 if whole else 1, grouping=False)
        return f'{kp:.0f}' if whole else f'{kp:.1f}'

    try:
        data = requests.get('https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json',
                            timeout=8).json()
        if not isinstance(data, list) or not data:
            return [format_lines(t('Aurora'), t('No data'), '')]
        latest = data[-1]
        # The feed is a list of {time_tag, Kp, ...} records (newest last).
        kp = float(latest.get('Kp') if isinstance(latest, dict) else latest[1])
        kps = num(kp)
        # Severity at a glance: a colour square renders everywhere — coloured
        # pixels on a matrix wall, the matching colour FLAP on a physical one.
        tile = '🟩' if kp < 5 else '🟨' if kp < 6 else '🟧' if kp < 7 else '🟥'
        label = f'{tile} {t(_label(kp))}'
        if rows == 1:
            return [format_lines(f'{t("Aurora")} KP {kps}')]
        if rows == 2:
            return [format_lines(f'{t("Aurora")} KP {kps}', label)]
        # A wide wall gets a full-width gauge: the bar fills to Kp (0-9) across the whole
        # wall, in the severity colour — a short green bar when it is quiet, a long red
        # one in a storm — so aurora chances read at a glance. Colour tiles render
        # everywhere (matrix pixels / the matching colour FLAP on a reel), like
        # moon-phase. A narrow wall keeps the concise three-line text.
        if cols >= 24:
            filled = max(0, min(cols, round(kp / 9 * cols)))
            bar = tile * filled + '⬛' * (cols - filled)
            return [format_lines(t('Aurora'), bar, f'KP {kps}  {t(_label(kp))}')]
        return [format_lines(t('Aurora'), f'KP index {kps}', label)]
    except Exception:
        return [format_lines(t('Aurora'), t('Offline'), '')]
