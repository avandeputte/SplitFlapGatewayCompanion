"""Precious metal spot prices, USD per troy ounce (keyless: gold-api.com)."""


# =============================================================================
# SHARED — the spot DATA both surfaces read: gold-api.com prices and the
# local-currency conversion.
# =============================================================================


def _local_currency(i18n, get_location):
    """gold-api.com quotes in USD/oz; show the local currency (Location -> Language
    -> USD) by converting through a keyless ECB rate. Falls back to USD if no
    rate. Returns (CCY code, USD->CCY rate)."""
    loc = get_location() if get_location is not None else None
    ccy = loc.get('currency') if isinstance(loc, dict) and loc.get('ok') else None
    if not ccy and i18n is not None:
        ccy = i18n.base_currency()
    ccy = (ccy or 'USD').upper()
    rate = 1.0
    if ccy != 'USD':
        try:
            import requests
            rate = float(requests.get('https://api.frankfurter.app/latest',
                                      params={'from': 'USD', 'to': ccy}, timeout=8).json()['rates'][ccy])
        except Exception:
            ccy, rate = 'USD', 1.0
    return ccy, rate


def _spot_price(sym):
    """One spot price in USD/oz from keyless gold-api.com ('XAU'/'XAG'), or None."""
    import requests
    try:
        return requests.get(f'https://api.gold-api.com/price/{sym}', timeout=8).json().get('price')
    except Exception:
        return None


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_location=None):
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "metals") if i18n is not None else s

    def n(v, d):        # locale decimal/grouping (2,345 vs 2.345 vs 2 345)
        return i18n.number(v, d) if i18n is not None else f'{v:,.{d}f}'

    ccy, rate = _local_currency(i18n, get_location)
    # With i18n the display can render € £ ¥ (Windows-1252); without it the modules
    # are the basic charset, so use '$' for USD and the ASCII ISO code otherwise.
    cur_sym = i18n.currency_symbol(ccy) if i18n is not None else ('$' if ccy == 'USD' else ccy)
    sep = '' if cur_sym != ccy else ' '

    def fmt(p):
        if not isinstance(p, (int, float)):
            return '--'
        p = p * rate
        body = n(p, 0) if p >= 100 else n(p, 2)
        return f'{cur_sym}{sep}{body}'

    try:
        gold, silver = _spot_price('XAU'), _spot_price('XAG')
        if gold is None and silver is None:
            return [format_lines('Metals', t('Offline'), '')]
        # Localized names vary in length — pad both to the same width so they align.
        g, s = t('Gold'), t('Silver')
        w = max(len(g), len(s))
        if rows == 1:
            pages = []
            if gold is not None:
                pages.append(f'{g} {fmt(gold)}/OZ'[:cols].center(cols))
            if silver is not None:
                pages.append(f'{s} {fmt(silver)}/OZ'[:cols].center(cols))
            return pages
        # Wide wall: both metals on ONE line, so the width carries them instead of two
        # short lines stranded in the middle of it.
        if gold is not None and silver is not None:
            one = f'{g} {fmt(gold)}/OZ   {s} {fmt(silver)}/OZ'
            if len(one) <= cols:
                return ([format_lines(f'{t("Spot price")} /OZ', one)] if rows >= 3
                        else [format_lines(one)])
        if rows == 2:
            return [format_lines(f'{g:<{w}} {fmt(gold)}', f'{s:<{w}} {fmt(silver)}')]
        return [format_lines(f'{t("Spot price")} /OZ', f'{g:<{w}} {fmt(gold)}', f'{s:<{w}} {fmt(silver)}')]
    except Exception:
        return [format_lines('Metals', t('Offline'), '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# Two spot-price rows — GOLD in gold, SILVER in silver, prices right-aligned in
# white — under a quiet 'SPOT /OZ' strip where the height allows. Solid black
# background; adaptive down to 64x32.
# =============================================================================


_CV_TEXT = (238, 238, 244)                 # primary text
_CV_DIM = (150, 150, 158)                  # secondary text
_CV_GOLD = (238, 196, 64)                  # the metal itself names the row's color
_CV_SILVER = (200, 206, 218)


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 8px —
    smaller sizes render wrong-reading glyphs on the panel)."""
    size = max(8, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 8 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
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
    """A quiet two-line message on black (offline) — never a crash, never blank."""
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
    """Gold and silver as two spot-price rows, each metal named in its own color
    (a 'SPOT /OZ' strip on panels tall enough to afford it). Same keyless source
    and the same local-currency conversion the flap pages use; prices are cached
    for five minutes and the last good ones survive an outage."""
    import time
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "metals") if i18n is not None else s

    def n(v, d, grouping=True):
        if i18n is not None:
            return i18n.number(v, d, grouping)
        return f'{v:,.{d}f}' if grouping else f'{v:.{d}f}'

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'data': None, 'ts': 0.0}
        setattr(fetch_matrix, '_state', st)
    now = time.time()
    if st['data'] is None or (now - st['ts']) >= 300.0:
        ccy, rate = _local_currency(i18n, get_location)
        gold, silver = _spot_price('XAU'), _spot_price('XAG')
        if gold is not None or silver is not None:
            st['data'] = (ccy, rate, gold, silver)
        st['ts'] = now                     # even after a failure: no hammering
    if st['data'] is None:
        canvas.frame(_cv_message(canvas, ImageDraw, 'METALS', t('Offline').upper()))
        return 120.0
    ccy, rate, gold, silver = st['data']

    cur_sym = i18n.currency_symbol(ccy) if i18n is not None else ('$' if ccy == 'USD' else ccy)
    sep = '' if cur_sym != ccy else ' '
    compact = canvas.width < 96            # 64-wide: whole units, no grouping — "$3358"
                                           # beats cents or a comma below the 8px floor

    def fmt(p):
        if not isinstance(p, (int, float)):
            return '--'
        p = p * rate
        if compact:
            return f'{cur_sym}{sep}{n(p, 0, grouping=False)}'
        body = n(p, 0) if p >= 100 else n(p, 2)
        return f'{cur_sym}{sep}{body}'

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    top = 0
    if H >= 48:
        head = f'{t("Spot price")} /OZ'.upper()
        hf = _cv_fit(canvas, head, W - 8, 8)
        _cv_text(draw, (W - hf.getlength(head)) / 2.0, 1, head, hf, _CV_DIM)
        top = 1 + _cv_ink(hf, head) + 2

    rows = [(t('Gold').upper(), fmt(gold), _CV_GOLD),
            (t('Silver').upper(), fmt(silver), _CV_SILVER)]
    n_rows = len(rows)
    area = H - top
    rh = area // n_rows
    fh = max(7, min(rh - 3, int(rh * 0.80)))
    # one font per column, sized by the longest entry, so the two rows align
    name_f = min((_cv_fit(canvas, nm, int(W * 0.45), fh) for nm, _p, _c in rows),
                 key=lambda f: f.size)
    name_w = max(name_f.getlength(nm) for nm, _p, _c in rows)
    price_f = min((_cv_fit(canvas, p, max(12, W - 6 - name_w - 5), fh) for _n, p, _c in rows),
                  key=lambda f: f.size)
    # Uniform rows (the stocks pattern): every row gets the SAME ink-box height
    # and the SAME gap between boxes — even spacing beats touching the bottom
    # edge (a spare row under the table reads better than one lopsided gap).
    row_ink = max(max(_cv_ink(name_f, nm), _cv_ink(price_f, p)) for nm, p, _c in rows)
    gap = max(1, (area - 2 - n_rows * row_ink) // (n_rows - 1)) if n_rows > 1 else 0
    for i, (name, prc, col) in enumerate(rows):
        ry = top + 1 + i * (row_ink + gap)
        _cv_text(draw, 3, ry + (row_ink - _cv_ink(name_f, name)) // 2, name, name_f, col)
        _cv_text(draw, W - 3 - price_f.getlength(prc),
                 ry + (row_ink - _cv_ink(price_f, prc)) // 2, prc, price_f, _CV_TEXT)
    canvas.frame(img)
    return 300.0
