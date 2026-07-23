"""Aurora / geomagnetic activity — planetary K-index (NOAA SWPC, keyless)."""


# =============================================================================
# SHARED — the Kp DATA: the NOAA feed, the latest reading and the recent series,
# and the severity ladder both surfaces grade it with.
# =============================================================================

_KP_URL = 'https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json'


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


def _kp_data(requests):
    """(latest_kp, recent_series) from the SWPC feed — a list of {time_tag, Kp}
    records (newest last, a header row first). None when the feed is empty; a
    malformed latest record raises, like it always did."""
    data = requests.get(_KP_URL, timeout=8).json()
    if not isinstance(data, list) or not data:
        return None
    latest = data[-1]
    kp = float(latest.get('Kp') if isinstance(latest, dict) else latest[1])
    series = []
    for rec in data:
        try:
            series.append(float(rec.get('Kp') if isinstance(rec, dict) else rec[1]))
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            continue                                   # the header row, gaps
    return kp, series


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

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
        got = _kp_data(requests)
        if got is None:
            return [format_lines(t('Aurora'), t('No data'), '')]
        kp, _series = got
        kps = num(kp)
        # Severity at a glance: a color square renders everywhere — colored
        # pixels on a matrix wall, the matching color FLAP on a physical one.
        tile = '🟩' if kp < 5 else '🟨' if kp < 6 else '🟧' if kp < 7 else '🟥'
        label = f'{tile} {t(_label(kp))}'
        if rows == 1:
            return [format_lines(f'{t("Aurora")} KP {kps}')]
        if rows == 2:
            return [format_lines(f'{t("Aurora")} KP {kps}', label)]
        # A wide wall gets a full-width gauge: the bar fills to Kp (0-9) across the whole
        # wall, in the severity color — a short green bar when it is quiet, a long red
        # one in a storm — so aurora chances read at a glance. Color tiles render
        # everywhere (matrix pixels / the matching color FLAP on a reel), like
        # moon-phase. A narrow wall keeps the concise three-line text.
        if cols >= 24:
            filled = max(0, min(cols, round(kp / 9 * cols)))
            bar = tile * filled + '⬛' * (cols - filled)
            return [format_lines(t('Aurora'), bar, f'KP {kps}  {t(_label(kp))}')]
        return [format_lines(t('Aurora'), f'KP index {kps}', label)]
    except Exception:
        return [format_lines(t('Aurora'), t('Offline'), '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The Kp index as a color-graded gauge: nine segments, green through red, lit
# up to the current reading, with a big KP number in the severity color and the
# condition label beside it. A panel with width to spare adds the last 24h of
# 3-hour readings as small bars — the same feed the number came from. Black
# background.
# =============================================================================

_TXT_COL = (245, 245, 248)
_SUB_COL = (132, 136, 148)
_SEG_OFF = (30, 32, 40)


def _kp_color(v):
    """The severity color the flap tiles use: green / yellow / orange / red."""
    if v < 5:
        return (66, 214, 108)
    if v < 6:
        return (250, 205, 58)
    if v < 7:
        return (255, 148, 42)
    return (240, 70, 58)


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 8px — smaller renders wrong-reading glyphs)."""
    size = max(8, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 8 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (offline / no data)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    b1 = f1.getbbox(line1)
    h1 = b1[3] - b1[1]
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = (f2.getbbox(line2)[3] - f2.getbbox(line2)[1]) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    draw.text(((W - f1.getlength(line1)) / 2.0, y - b1[1]), line1, font=f1, fill=_TXT_COL)
    if line2:
        y += h1 + gap
        b2 = f2.getbbox(line2)
        draw.text(((W - f2.getlength(line2)) / 2.0, y - b2[1]), line2, font=f2, fill=_SUB_COL)
    return img


def fetch_matrix(settings, canvas, i18n=None):
    import requests
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "aurora") if i18n is not None else s

    def num(kp):
        whole = (kp == int(kp))
        if i18n is not None:
            return i18n.number(kp, decimals=0 if whole else 1, grouping=False)
        return f'{kp:.0f}' if whole else f'{kp:.1f}'

    try:
        got = _kp_data(requests)
    except Exception:
        got = 'offline'
    if got == 'offline':
        canvas.frame(_cv_message(canvas, ImageDraw, t('Aurora').upper(), t('Offline').upper()))
        return 120.0
    if got is None:
        canvas.frame(_cv_message(canvas, ImageDraw, t('Aurora').upper(), t('No data').upper()))
        return 120.0
    kp, series = got

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    col = _kp_color(kp)

    # Layout bands: the 9-segment gauge flush on the bottom edge, the headline
    # grown to fill everything above it, pinned to the top row. (Ink starts 1px
    # in — the reported bbox can under-report a pixel.)
    gauge_h = max(5, H // 6)
    gy1 = H - 1
    gy0 = gy1 - gauge_h

    kps = f'KP {num(kp)}'
    label = t(_label(kp)).upper()

    def ink(font, s):
        b = font.getbbox(s)
        return b[3] - b[1]

    # With width to spare, the last 24h of 3-hour readings own the right column
    # as bars rising from the gauge — same feed, so the story matches the number.
    hist = series[-8:] if (W >= 192 and len(series) >= 2) else []
    bw, bgap = 5, 2
    hw = len(hist) * (bw + bgap) - bgap if hist else 0
    avail = W - (hw + 10) if hist else W

    # Headline: "KP 5.7" big in the severity color, the condition label right
    # under it — on a panel where the label would be mush it yields: the color
    # and the gauge already say how bad it is.
    head_h = gy0 - 3                    # ink rows 1..gy0-3, a breath above the gauge
    lf = _cv_fit(canvas, label, avail - 4, max(6, int(head_h * 0.30)))
    show_label = ink(lf, label) >= 5
    lh = ink(lf, label) if show_label else 0
    kf = _cv_fit(canvas, kps, avail - 4, head_h - ((lh + 2) if show_label else 0))
    kb = kf.getbbox(kps)
    kh = ink(kf, kps)
    draw.text(((avail - kf.getlength(kps)) / 2.0, 1 - kb[1]), kps, font=kf, fill=col)
    if show_label:
        lb = lf.getbbox(label)
        draw.text(((avail - lf.getlength(label)) / 2.0, 1 + kh + 2 - lb[1]), label,
                  font=lf, fill=_TXT_COL)

    # The gauge: nine segments 1..9, each in its own severity color when lit,
    # asleep in dark gray beyond the current Kp. A fractional reading part-lights
    # its segment's leading edge.
    gap = 2 if W >= 96 else 1
    seg_w = (W - 2 - 8 * gap) / 9.0
    x = 1.0
    for i in range(9):
        lit = kp >= i + 1
        part = (not lit) and (kp > i)
        c = _kp_color(i + 1) if (lit or part) else _SEG_OFF
        w = seg_w if not part else max(1.0, seg_w * (kp - i))
        draw.rectangle([round(x), gy0, round(x + w) - 1, gy1], fill=c)
        if part:
            draw.rectangle([round(x + w), gy0, round(x + seg_w) - 1, gy1], fill=_SEG_OFF)
        x += seg_w + gap

    if hist:
        hx = W - hw - 4
        hy1 = gy0 - 3                   # bars sit just above the gauge...
        hh = hy1 - 1                    # ...and a Kp-9 bar would reach the top row
        for i, v in enumerate(hist):
            bh = max(1, int(round(min(9.0, v) / 9.0 * hh)))
            c = _kp_color(v) if i == len(hist) - 1 else tuple(int(cc * 0.55) for cc in _kp_color(v))
            draw.rectangle([hx + i * (bw + bgap), hy1 - bh + 1,
                            hx + i * (bw + bgap) + bw - 1, hy1], fill=c)

    canvas.frame(img)
    return 300.0                    # the index updates a few times an hour at most
