"""Currency exchange rates via Frankfurter (European Central Bank data, keyless)."""


# =============================================================================
# SHARED — the rate DATA both surfaces read: base/target selection and one
# Frankfurter (ECB) call, plus the shared rate formatting.
# =============================================================================


def _pick_base(settings, i18n, get_location):
    """Base currency: an explicit setting wins; otherwise the configured LOCATION
    decides (a French speaker in Canada wants CAD, in Switzerland CHF — the
    language can't tell), falling back to the Language only if no location is set."""
    base = str(settings.get('base', '') or '').strip().upper()[:3]
    if not base and get_location is not None:
        base = str((get_location() or {}).get('currency') or '')
    if not base:
        base = i18n.base_currency() if i18n is not None else 'USD'
    return base


def _pick_targets(settings):
    """The configured target currencies, capped at eight."""
    targets = [t.strip().upper()[:3]
               for t in str(settings.get('targets', 'EUR,GBP,JPY')).split(',') if t.strip()]
    return targets[:8] or ['EUR', 'GBP', 'JPY']


def _rates(base, targets):
    """Frankfurter (ECB, keyless) rates for 1 ``base`` in each target currency."""
    import requests
    data = requests.get('https://api.frankfurter.app/latest',
                        params={'from': base, 'to': ','.join(targets)}, timeout=8).json()
    return data.get('rates', {})


def _fmt_rate(v, i18n):
    """A rate at the precision its magnitude deserves, in the locale's number
    style (150,25 vs 150.25; 1.350 vs 1,350)."""
    def n(x, d):
        return i18n.number(x, d) if i18n is not None else f'{x:,.{d}f}'
    return n(v, 3) if v < 10 else (n(v, 2) if v < 1000 else n(v, 0))


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_location=None):
    rows, cols = get_rows(), get_cols()

    base = _pick_base(settings, i18n, get_location)
    targets = _pick_targets(settings)

    def fmt(v):
        return _fmt_rate(v, i18n)

    try:
        rates = _rates(base, targets)
        if not rates:
            return [format_lines('FX rates', 'No data', 'Check codes')]

        pairs = [(t, rates[t]) for t in targets if t in rates]
        if rows == 1:
            return [f'1{base}={fmt(v)}{t}'.center(cols)[:cols] for t, v in pairs]

        # Line the decimal points up into a column. format_lines centers EACH line
        # on its own, so alignment only survives if every rate line is the same
        # length — then they all shift by the same amount. Split each value on the
        # locale's decimal separator (found by probing, since it's ',' in fr/de),
        # right-justify the integer part and left-justify the fraction so the
        # separators stack; a whole-number rate (JPY 149) leaves that column blank.
        sep = next((c for c in (i18n.number(1.5, 1) if i18n is not None else '1.5')
                    if not c.isdigit()), '.')
        parts = []
        for _t, v in pairs:
            s = fmt(v)
            ip, _, fp = s.partition(sep)
            parts.append((ip, sep + fp if fp else ''))
        wi = max((len(ip) for ip, _ in parts), default=0)
        wf = max((len(fr) for _, fr in parts), default=0)
        wc = max((len(t) for t, _ in pairs), default=0)
        rate_lines = [f'{t.ljust(wc)} {ip.rjust(wi)}{fr.ljust(wf)}'
                      for (t, _), (ip, fr) in zip(pairs, parts)]

        # Wide wall: several rates per row, in aligned columns across the width, so all
        # of them show at once instead of a narrow single column paginated down a wide
        # wall. Every cell is the same width, so the columns line up down the page too.
        gap = 3
        cw = len(rate_lines[0]) if rate_lines else 0
        per = max(1, (cols + gap) // (cw + gap)) if cw else 1
        rows_of_cells = ([(' ' * gap).join(rate_lines[i:i + per])
                          for i in range(0, len(rate_lines), per)] if per >= 2
                         else rate_lines)
        lines = [f'1 {base} ='] + rows_of_cells
        return [format_lines(*lines[i:i + rows]) for i in range(0, len(lines), rows)]
    except Exception:
        return [format_lines('FX rates', 'Offline', '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The rate table as rows under a '1 BASE =' strip: target code in teal, the
# rate right-aligned in white. More targets than rows rotate page by page.
# Solid black background; adaptive down to 64x32.
# =============================================================================


_CV_TEXT = (238, 238, 244)                 # primary text
_CV_DIM = (150, 150, 158)                  # secondary text
_CV_CODE = (92, 205, 170)                  # currency-code teal


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 5px)."""
    size = max(5, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 5 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_ink(font, text):
    """Ink height of ``text`` in ``font``."""
    b = font.getbbox(text or '0')
    return b[3] - b[1]


def _cv_text(draw, x, y, text, font, fill):
    """Draw with the ink's TOP at ``y`` (bbox-corrected), left edge at ``x``."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill, anchor='la')


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message on black (offline / bad codes) — never a crash,
    never a blank panel."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    h1 = _cv_ink(f1, line1)
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = _cv_ink(f2, line2) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    _cv_text(draw, (W - f1.getlength(line1)) / 2.0, y, line1, f1, _CV_TEXT)
    if line2:
        _cv_text(draw, (W - f2.getlength(line2)) / 2.0, y + h1 + gap, line2, f2, _CV_DIM)
    return img


def fetch_matrix(settings, canvas, i18n=None, get_location=None):
    """The same rate table as the flap pages — '1 BASE =' as a quiet strip, then a
    row per target: code in teal, rate right-aligned in white, decimals in a
    column. More targets than rows rotate page by page. ECB rates move hourly at
    best, so they are cached for 15 minutes."""
    import time
    from PIL import ImageDraw

    base = _pick_base(settings, i18n, get_location)
    targets = _pick_targets(settings)

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'rates': None, 'ts': 0.0, 'sig': None, 'page': 0}
        setattr(fetch_matrix, '_state', st)
    sig = (base, tuple(targets))
    now = time.time()
    if st['rates'] is None or st['sig'] != sig or (now - st['ts']) >= 900.0:
        try:
            st['rates'], st['sig'] = _rates(base, targets), sig
        except Exception:
            if st['sig'] != sig:
                st['rates'] = None         # another base's rates are not these
        st['ts'] = now                     # even after a failure: no hammering
    rates = st['rates'] or {}
    pairs = [(c, rates[c]) for c in targets if c in rates]
    if not pairs:
        canvas.frame(_cv_message(canvas, ImageDraw, 'FX RATES', 'OFFLINE'))
        return 300.0

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    # '1 EUR =' strip: the base in teal so the two currency roles read apart.
    # One shared baseline for the three parts ('=' has no ascender of its own).
    head = f'1 {base} ='
    hf = _cv_fit(canvas, head, W - 6, max(7, min(9, int(H * 0.22))))
    hh = _cv_ink(hf, head)
    ym = 1                                               # the strip hugs the top edge
    x, y0 = 3, ym - hf.getbbox(head)[1]
    for part, col in (('1 ', _CV_DIM), (base, _CV_CODE), (' =', _CV_DIM)):
        draw.text((x, y0), part, font=hf, fill=col, anchor='la')
        x += hf.getlength(part)
    top = ym + hh + ym

    area = H - top
    per = max(1, min(len(pairs), area // (13 if H >= 48 else 10)))
    pages = [pairs[i:i + per] for i in range(0, len(pairs), per)]
    idx = st['page'] % len(pages)
    st['page'] = (st['page'] + 1) % len(pages)
    page = pages[idx]

    edges = [top + round(i * area / len(page)) for i in range(len(page) + 1)]
    rh = min(edges[i + 1] - edges[i] for i in range(len(page)))
    fh = max(7, min(rh - 2, int(rh * 0.80)))
    cf = min((_cv_fit(canvas, c, int(W * 0.34), fh) for c, _v in page),
             key=lambda f: f.size)
    code_w = max(cf.getlength(c) for c, _v in page)
    texts = [_fmt_rate(v, i18n) for _c, v in page]
    pf = min((_cv_fit(canvas, s, W - 6 - code_w - 5, fh) for s in texts),
             key=lambda f: f.size)

    def vy(y0, y1, hgt):
        """Full-height bands: the last row sits its ink on H-1 (the strip already
        owns the top edge), rows above it center in their band."""
        if y1 >= H - 1:
            return y1 - hgt
        return y0 + (y1 - y0 - hgt) / 2.0

    for i, ((code, _v), rate_s) in enumerate(zip(page, texts)):
        y0, y1 = edges[i], edges[i + 1]
        _cv_text(draw, 3, vy(y0, y1, _cv_ink(cf, code)), code, cf, _CV_CODE)
        _cv_text(draw, W - 3 - pf.getlength(rate_s),
                 vy(y0, y1, _cv_ink(pf, rate_s)), rate_s, pf, _CV_TEXT)

    canvas.frame(img)
    if len(pages) > 1:
        try:
            dwell = float(settings.get('loop_delay', 5) or 5)
        except (TypeError, ValueError):
            dwell = 5.0
        return max(3.0, min(30.0, dwell))
    return 300.0
