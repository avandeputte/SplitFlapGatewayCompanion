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
    kept together as one centred block.

    On an ultra-wide wall (a big Matrix panel) there is room to show a coin, its
    price AND the day's change on ONE line, so the whole watchlist is a page of
    one-liners instead of a coin's name, price and change stacked over three rows.
    The price column and the change column each line up down the page. format_lines
    centres the block, so it sits together in the middle, not spread to the edges.
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
    import requests

    def t(s):
        return i18n.t(s, "crypto") if i18n is not None else s

    coins = [s.strip() for s in settings.get('crypto_list', '').split(',') if s.strip()]
    if not coins:
        return [format_lines('Crypto', t('No coins'), t('Configure'))]

    # Price in the local currency: where you are (Location) -> your Language -> USD.
    # CoinGecko quotes natively in the target currency, so no FX conversion is needed.
    loc = get_location() if get_location is not None else None
    ccy = loc.get('currency') if isinstance(loc, dict) and loc.get('ok') else None
    if not ccy and i18n is not None:
        ccy = i18n.base_currency()
    ccy = (ccy or 'USD').upper()
    vs = ccy.lower()
    # With i18n the display can render € £ ¥ (Windows-1252); without it the modules
    # are the basic charset, so use '$' for USD and the ASCII ISO code otherwise.
    cur_sym = i18n.currency_symbol(ccy) if i18n is not None else ('$' if ccy == 'USD' else ccy)
    sep = '' if cur_sym != ccy else ' '    # 3-letter-code fallback reads better spaced

    try:
        r = requests.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={'ids': ','.join(coins), 'vs_currencies': vs, 'include_24hr_change': 'true'},
            timeout=10
        ).json()
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

    # CoinGecko is queried by id slug ('bitcoin'), but a slug is not a display
    # name: uppercasing and cutting it made "BITCOI". Show the ticker people know
    # and fall back to the slug as written (the wall folds case if it has to).
    tickers = {'bitcoin': 'BTC', 'ethereum': 'ETH', 'tether': 'USDT',
               'binancecoin': 'BNB', 'solana': 'SOL', 'ripple': 'XRP',
               'cardano': 'ADA', 'dogecoin': 'DOGE', 'tron': 'TRX',
               'polkadot': 'DOT', 'litecoin': 'LTC', 'monero': 'XMR',
               'chainlink': 'LINK', 'shiba-inu': 'SHIB', 'avalanche-2': 'AVAX'}

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
            # An arrow, not just a colour: a colour is nothing at all with colours disabled,
            # and nothing on a mono wall. ↑/↓ degrade to ^/v on a reel with no pictograph
            # flaps, which still reads. The colour comes along too, when it can.
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
        # they are just an off-centre page. Drop them and let format_lines centre.
        while lines and not lines[-1].strip():
            lines.pop()
        pages.append(format_lines(*lines))
    return pages or [format_lines('Crypto', t('No data'), '')]


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
