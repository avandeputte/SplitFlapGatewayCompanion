# =============================================================================
# SHARED — the coin DATA both surfaces read: one CoinGecko call (_prices), the
# local display currency, and the slug->ticker names — plus the trigger
# (surface-independent by contract).
# =============================================================================


# CoinGecko is queried by id slug ('bitcoin'), but a slug is not a display
# name: uppercasing and cutting it made "BITCOI". Show the ticker people know
# and fall back to the slug as written (the wall folds case if it has to).
_TICKERS = {'bitcoin': 'BTC', 'ethereum': 'ETH', 'tether': 'USDT',
            'binancecoin': 'BNB', 'solana': 'SOL', 'ripple': 'XRP',
            'cardano': 'ADA', 'dogecoin': 'DOGE', 'tron': 'TRX',
            'polkadot': 'DOT', 'litecoin': 'LTC', 'monero': 'XMR',
            'chainlink': 'LINK', 'shiba-inu': 'SHIB', 'avalanche-2': 'AVAX'}


def _display_currency(i18n, get_location):
    """Price in the local currency: where you are (Location) -> your Language -> USD.
    CoinGecko quotes natively in the target currency, so no FX conversion is needed.
    Returns (CCY code, its display symbol, the symbol/value separator)."""
    loc = get_location() if get_location is not None else None
    ccy = loc.get('currency') if isinstance(loc, dict) and loc.get('ok') else None
    if not ccy and i18n is not None:
        ccy = i18n.base_currency()
    ccy = (ccy or 'USD').upper()
    # With i18n the display can render € £ ¥ (Windows-1252); without it the modules
    # are the basic charset, so use '$' for USD and the ASCII ISO code otherwise.
    cur_sym = i18n.currency_symbol(ccy) if i18n is not None else ('$' if ccy == 'USD' else ccy)
    sep = '' if cur_sym != ccy else ' '    # 3-letter-code fallback reads better spaced
    return ccy, cur_sym, sep


def _prices(coins, vs):
    """CoinGecko spot prices + 24h change for the id slugs, quoted in ``vs``."""
    import requests
    return requests.get(
        'https://api.coingecko.com/api/v3/simple/price',
        params={'ids': ','.join(coins), 'vs_currencies': vs, 'include_24hr_change': 'true'},
        timeout=10
    ).json()


def trigger(settings, conditions):
    """Fire when any followed coin moves beyond threshold or hits a price target."""
    import requests

    condition_type = conditions.get('condition_type', 'pct_change')
    coins = [s.strip() for s in settings.get('crypto_list', '').split(',') if s.strip()]
    if not coins:
        return False

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'fired_targets': set()}
        setattr(trigger, '_state', state)

    try:
        r = requests.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={'ids': ','.join(coins), 'vs_currencies': 'usd', 'include_24hr_change': 'true'},
            timeout=10
        ).json()

        for c in coins:
            d = r.get(c, {})
            price = d.get('usd')
            chg = d.get('usd_24h_change')

            if condition_type == 'pct_change':
                threshold = float(conditions.get('threshold', 5))
                # The direction select is shared between condition types; map the
                # price-target vocabulary so no combination is silently dead.
                direction = {'above': 'up', 'below': 'down'}.get(
                    conditions.get('direction', 'either'), conditions.get('direction', 'either'))
                if chg is None:
                    continue
                if direction == 'up' and chg >= threshold:
                    return True
                if direction == 'down' and chg <= -threshold:
                    return True
                if direction == 'either' and abs(chg) >= threshold:
                    return True

            elif condition_type == 'price_target' and price is not None:
                target = float(conditions.get('price_target', 0))
                direction = {'up': 'above', 'down': 'below', 'either': 'above'}.get(
                    conditions.get('direction', 'above'), conditions.get('direction', 'above'))
                if not target:
                    continue
                key = f"{c}:{direction}:{target}"
                crossed = (direction == 'above' and price >= target) or \
                          (direction == 'below' and price <= target)
                if crossed and key not in state['fired_targets']:
                    state['fired_targets'].add(key)
                    return True
                if not crossed:
                    state['fired_targets'].discard(key)

    except Exception:
        raise
    return False


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================


def _three_widths(triples, gap):
    tw = max((len(str(a)) for a, _, _ in triples), default=0)   # ticker
    pw = max((len(str(b)) for _, b, _ in triples), default=0)   # price
    cw = max((len(str(c)) for _, _, c in triples), default=0)   # change
    return tw, pw, cw, tw + gap + pw + gap + cw                 # ...and the block width


def _fits_three(triples, cols, gap=3):
    """Do ticker + price + change fit together on ONE line at this width?"""
    return _three_widths(triples, gap)[3] <= cols


def _columns3(triples, cols, gap=3):
    """Three aligned columns — coin flush left, price and change each flush right —
    kept together as one centered block.

    On an ultra-wide wall (a big Matrix panel) there is room to show a coin, its
    price AND the day's change on ONE line, so the whole watchlist is a page of
    one-liners instead of a coin's name, price and change stacked over three rows.
    The price column and the change column each line up down the page. format_lines
    centers the block, so it sits together in the middle, not spread to the edges.
    """
    triples = [(str(a), str(b), str(c)) for a, b, c in triples]
    tw, pw, cw, block = _three_widths(triples, gap)
    inner = min(cols, block)
    lead = max(1, inner - pw - gap - cw)              # coin column width, incl. its gap
    out = []
    for a, b, c in triples:
        if len(a) > lead - 1:
            a = a[:max(0, lead - 1)]
        out.append((a.ljust(lead) + b.rjust(pw) + (' ' * gap) + c.rjust(cw))[:cols])
    return out


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_location=None):

    def t(s):
        return i18n.t(s, "crypto") if i18n is not None else s

    coins = [s.strip() for s in settings.get('crypto_list', '').split(',') if s.strip()]
    if not coins:
        return [format_lines('Crypto', t('No coins'), t('Configure'))]

    ccy, cur_sym, sep = _display_currency(i18n, get_location)
    vs = ccy.lower()

    try:
        r = _prices(coins, vs)
    except Exception:
        return [format_lines('Crypto', t('Error'), t('API fail'))]
    rows, cols = get_rows(), get_cols()
    no_color = settings.get('disable_colors', 'no') == 'yes'

    # Numbers follow the global Language (1,234.50 vs 1.234,50 vs 1 234,50).
    def n(v, d=2, grouping=True):
        if i18n is not None:
            return i18n.number(v, d, grouping)
        return f'{v:,.{d}f}' if grouping else f'{v:.{d}f}'

    def pct(v):
        return f"{'+' if v >= 0 else '-'}{n(abs(v), 1, grouping=False)}%"

    def price_str(price):
        body = n(price, 0) if price >= 1 else n(price, 4, grouping=False)
        return f'{cur_sym}{sep}{body}'

    tickers = _TICKERS

    def block(c):
        """The lines for one coin, sized to the display: price+change together on
        2+ row displays (with the name too when there are 3+ rows)."""
        d = r.get(c, {})
        price, chg = d.get(vs), d.get(f'{vs}_24h_change')
        sym = tickers.get(c, c)
        if price is None:
            return [f'{sym} Err'[:cols]]
        if chg is None:
            chg_str = 'N/A'
        else:
            # An arrow, not just a color: a color is nothing at all with colors disabled,
            # and nothing on a mono wall. ↑/↓ degrade to ^/v on a reel with no pictograph
            # flaps, which still reads. The color comes along too, when it can.
            arrow = '\u2191' if chg >= 0 else '\u2193'
            tile = '' if no_color else ('🟩' if chg >= 0 else '🟥')
            chg_str = f'{arrow}{tile} {pct(chg)}'
        if rows == 1:
            return [f'{sym} {price_str(price)}'[:cols]]
        if rows == 2:
            return [f'{sym} {price_str(price)}'[:cols], chg_str]
        return [sym[:cols], price_str(price), chg_str]   # ticker / price / change

    def coin_triple(c):
        """One coin as (ticker, price, change) for the wide, one-line-per-coin layout."""
        d = r.get(c, {})
        price, chg = d.get(vs), d.get(f'{vs}_24h_change')
        sym = tickers.get(c, c)
        if price is None:
            return (sym, 'Err', '')
        if chg is None:
            ch = 'N/A'
        else:
            arrow = '↑' if chg >= 0 else '↓'
            tile = '' if no_color else ('🟩' if chg >= 0 else '🟥')
            ch = f'{arrow}{tile} {pct(chg)}'
        return (sym, price_str(price), ch)

    # Ultra-wide wall (a big Matrix panel): a coin, its price AND change on ONE line,
    # so the watchlist is a page of one-liners — the prices line up in a column, and so
    # do the changes. Otherwise the name/price/change stack over the rows, as before.
    triples = [coin_triple(c) for c in coins]
    if _fits_three(triples, cols):
        return [format_lines(*_columns3(triples[i:i + rows], cols))
                for i in range(0, len(triples), rows)] or \
            [format_lines('Crypto', t('No data'), '')]

    lines_per = 1 if rows == 1 else (2 if rows == 2 else 3)
    per_page = max(1, rows // lines_per)   # how many coins fit on one page
    pages = []
    for i in range(0, len(coins), per_page):
        lines = []
        for c in coins[i:i + per_page]:
            b = block(c)
            lines += b + [''] * (lines_per - len(b))   # pad each block so coins align
        # The alignment padding is between coins; trailing blanks are not alignment,
        # they are just an off-center page. Drop them and let format_lines center.
        while lines and not lines[-1].strip():
            lines.pop()
        pages.append(format_lines(*lines))
    return pages or [format_lines('Crypto', t('No data'), '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The watchlist as quote rows: coin left, price right, the 24h change as a
# green/red chip (a color-coded price where a chip won't fit). More coins than
# rows rotate page by page, in the flap pages' order. Solid black background;
# adaptive down to 64x32.
# =============================================================================


_CV_TEXT = (238, 238, 244)                 # primary text
_CV_DIM = (150, 150, 158)                  # secondary text
_CV_UP, _CV_DOWN = (70, 215, 115), (245, 85, 70)         # change, as text
_CV_UP_CHIP, _CV_DOWN_CHIP = (18, 112, 58), (152, 40, 32)  # change, as a chip


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
    """A quiet two-line message on black (offline / not configured) — never a crash,
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


def _cv_common_font(canvas, texts, max_w, max_h):
    """ONE font that fits every ``texts`` entry in ``max_w`` x ``max_h`` — a column
    set per-row would ripple through sizes and read as a ransom note."""
    size = None
    for text in texts:
        f = _cv_fit(canvas, text, max_w, max_h)
        size = f.size if size is None else min(size, f.size)
    return canvas.font(size or 8)


def _cv_quote_rows(canvas, ImageDraw, rows_data):
    """Quote rows: coin flush left, price flush right, the 24h change as a
    green/red chip in an aligned right-hand column. A panel too narrow for chips
    colors the price instead — the direction survives, only the chrome goes.

    ``rows_data`` = [(coin, price_text, change_pct_or_None), ...]; a None change
    (an errored coin) renders dim, never as a fake zero. Every column uses one
    common font so the table reads down, not row by row."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    n = max(1, len(rows_data))
    chips = W >= 104                       # room for a chip column beside the price
    if n == 1:
        # A lone quote (the rotation's last page) can't fill the panel as a
        # table row — set it as a card instead: coin hung from the top edge,
        # price sitting on the bottom row, the chip riding top-right.
        sym, price, chg = rows_data[0]
        up = chg is not None and chg >= 0
        sf = _cv_fit(canvas, sym, int(W * 0.62), int(H * 0.55))
        _cv_text(draw, 3, 1, sym, sf, _CV_TEXT)
        if chips and chg is not None:
            pct = f'{"+" if chg >= 0 else ""}{chg:.1f}%'
            cf = _cv_fit(canvas, pct, int(W * 0.28), max(7, int(H * 0.30)))
            ch = _cv_ink(cf, pct) + 4
            chip_w = int(cf.getlength(pct)) + 6
            draw.rounded_rectangle([W - 3 - chip_w, 1, W - 3, 1 + ch], radius=2,
                                   fill=_CV_UP_CHIP if up else _CV_DOWN_CHIP)
            _cv_text(draw, W - 3 - chip_w + (chip_w - cf.getlength(pct)) / 2.0,
                     3, pct, cf, (255, 255, 255))
        pf = _cv_fit(canvas, price, W - 6, max(7, int(H * 0.42)))
        pcol = _CV_DIM if chg is None else \
            (_CV_TEXT if chips else (_CV_UP if up else _CV_DOWN))
        _cv_text(draw, W - 3 - pf.getlength(price), H - _cv_ink(pf, price),
                 price, pf, pcol)
        return img
    rh = H // n
    fh = max(7, min(rh - 3, int(rh * 0.80)))
    pcts = [f'{"+" if c >= 0 else ""}{c:.1f}%' for _s, _p, c in rows_data if c is not None]
    cf = _cv_common_font(canvas, pcts, int(W * 0.24), max(7, int(fh * 0.78))) \
        if (chips and pcts) else None
    chip_w = max((int(cf.getlength(p)) for p in pcts), default=0) + 6 if cf else 0
    right = W - 3 - (chip_w + 5 if cf else 0)
    sf = _cv_common_font(canvas, [s for s, _p, _c in rows_data], int(W * 0.30), fh)
    sym_w = max(sf.getlength(s) for s, _p, _c in rows_data)
    pf = _cv_common_font(canvas, [p for _s, p, _c in rows_data],
                         max(12, right - 3 - sym_w - 5), fh)
    if cf is not None and \
            max(pf.getlength(p) for _s, p, _c in rows_data) > right - 3 - sym_w - 5:
        # The price would have to shrink below legible to make room for the chip —
        # drop the chip column first and color the price instead.
        cf, chip_w = None, 0
        right = W - 3
        pf = _cv_common_font(canvas, [p for _s, p, _c in rows_data],
                             max(12, right - 3 - sym_w - 5), fh)
    # Uniform rows: every row gets the SAME ink-box height and the SAME gap between
    # boxes — even spacing beats touching the bottom edge (a spare row under the
    # table reads better than one lopsided gap).
    row_ink = max(max(_cv_ink(sf, s), _cv_ink(pf, p),
                      (_cv_ink(cf, f'{"+" if (c or 0) >= 0 else ""}{c:.1f}%') + 5)
                      if (cf and c is not None) else 0)
                  for s, p, c in rows_data)
    gap = max(1, (H - 2 - n * row_ink) // (n - 1)) if n > 1 else 0
    for i, (sym, price, chg) in enumerate(rows_data):
        ry = 1 + i * (row_ink + gap)
        up = chg is not None and chg >= 0
        if cf and chg is not None:
            pct = f'{"+" if chg >= 0 else ""}{chg:.1f}%'
            ch = _cv_ink(cf, pct) + 4
            cy = ry + (row_ink - ch) // 2
            draw.rounded_rectangle([W - 3 - chip_w, cy, W - 3, cy + ch], radius=2,
                                   fill=_CV_UP_CHIP if up else _CV_DOWN_CHIP)
            _cv_text(draw, W - 3 - chip_w + (chip_w - cf.getlength(pct)) / 2.0,
                     cy + 2, pct, cf, (255, 255, 255))
        _cv_text(draw, 3, ry + (row_ink - _cv_ink(sf, sym)) // 2, sym, sf, _CV_TEXT)
        if chg is None:
            pcol = _CV_DIM
        else:                              # no chip (narrow panel, or dropped): color the price
            pcol = _CV_TEXT if cf is not None else (_CV_UP if up else _CV_DOWN)
        _cv_text(draw, right - pf.getlength(price),
                 ry + (row_ink - _cv_ink(pf, price)) // 2, price, pf, pcol)
    return img


def fetch_matrix(settings, canvas, i18n=None, get_location=None):
    """The same watchlist as the flap pages, in the same order, as quote rows —
    rotating page by page when there are more coins than rows. One CoinGecko
    call covers the whole list; it is cached for a minute so page turns never
    re-hit the API, and the last good prices survive an outage."""
    import time
    from PIL import ImageDraw

    coins = [s.strip() for s in settings.get('crypto_list', '').split(',') if s.strip()]
    if not coins:
        canvas.frame(_cv_message(canvas, ImageDraw, 'CRYPTO', 'NO COINS'))
        return 60.0

    ccy, cur_sym, sep = _display_currency(i18n, get_location)
    vs = ccy.lower()

    # Numbers follow the global Language, like the flap view.
    def n(v, d=2, grouping=True):
        if i18n is not None:
            return i18n.number(v, d, grouping)
        return f'{v:,.{d}f}' if grouping else f'{v:.{d}f}'

    compact = canvas.width < 104           # narrow panel: $118.2K beats a 5px $118,234

    def price_str(price):
        if compact and price >= 100000:    # $117K — a .2K decimal can't fit at 8px and adds nothing
            return f'{cur_sym}{sep}{n(price / 1000.0, 0, grouping=False)}K'
        if compact and price >= 10000:
            return f'{cur_sym}{sep}{n(price / 1000.0, 1, grouping=False)}K'
        body = n(price, 0) if price >= 1 else n(price, 4, grouping=False)
        return f'{cur_sym}{sep}{body}'

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'r': None, 'ts': 0.0, 'sig': None, 'page': 0}
        setattr(fetch_matrix, '_state', st)
    sig = (tuple(coins), vs)
    now = time.time()
    if st['r'] is None or st['sig'] != sig or (now - st['ts']) >= 60.0:
        try:
            st['r'], st['sig'] = _prices(coins, vs), sig
        except Exception:
            if st['sig'] != sig:
                st['r'] = None             # old coins' prices are not these coins'
        st['ts'] = now                     # even after a failure: no hammering
    r = st['r']
    if not r:
        canvas.frame(_cv_message(canvas, ImageDraw, 'CRYPTO', 'OFFLINE'))
        return 60.0

    rows_data = []
    for c in coins:
        d = r.get(c, {})
        price, chg = d.get(vs), d.get(f'{vs}_24h_change')
        sym = _TICKERS.get(c, c)
        if price is None:
            rows_data.append((sym, 'ERR', None))
        else:
            rows_data.append((sym, price_str(price), chg))

    per = max(1, min(len(rows_data), canvas.height // 15))
    pages = [rows_data[i:i + per] for i in range(0, len(rows_data), per)]
    idx = st['page'] % len(pages)
    st['page'] = (st['page'] + 1) % len(pages)
    canvas.frame(_cv_quote_rows(canvas, ImageDraw, pages[idx]))
    if len(pages) > 1:
        try:
            dwell = float(settings.get('loop_delay', 5) or 5)
        except (TypeError, ValueError):
            dwell = 5.0
        return max(3.0, min(30.0, dwell))
    return 60.0
