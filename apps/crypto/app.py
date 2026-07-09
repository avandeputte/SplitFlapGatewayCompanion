def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    coins = [s.strip() for s in settings.get('crypto_list', '').split(',') if s.strip()]
    if not coins:
        return [format_lines('CRYPTO', 'NO COINS', 'CONFIGURE')]
    currency = settings.get('currency_symbol', '$').strip() or '$'
    try:
        r = requests.get(
            'https://api.coingecko.com/api/v3/simple/price',
            params={'ids': ','.join(coins), 'vs_currencies': 'usd', 'include_24hr_change': 'true'},
            timeout=10
        ).json()
    except Exception:
        return [format_lines('CRYPTO', 'ERROR', 'API FAIL')]
    rows, cols = get_rows(), get_cols()

    def price_str(price):
        return f'{currency}{price:,.0f}' if price >= 1 else f'{currency}{price:.4f}'

    def block(c):
        """The lines for one coin, sized to the display: price+change together on
        2+ row displays (with the name too when there are 3+ rows)."""
        d = r.get(c, {})
        price, chg = d.get('usd'), d.get('usd_24h_change')
        sym = c[:6].upper()
        if price is None:
            return [f'{sym} ERR']
        chg_str = (('🟩' if chg >= 0 else '🟥') + f' {chg:+.1f}%') if chg is not None else 'N/A'
        if rows == 1:
            return [f'{sym} {price_str(price)}']
        if rows == 2:
            return [f'{sym} {price_str(price)}', chg_str]
        return [c.upper()[:cols], price_str(price), chg_str]   # name / price / change

    lines_per = 1 if rows == 1 else (2 if rows == 2 else 3)
    per_page = max(1, rows // lines_per)   # how many coins fit on one page
    pages = []
    for i in range(0, len(coins), per_page):
        lines = []
        for c in coins[i:i + per_page]:
            b = block(c)
            lines += b + [''] * (lines_per - len(b))   # pad each block so coins align
        pages.append(format_lines(*lines))
    return pages or [format_lines('CRYPTO', 'NO DATA', '')]


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
                direction = conditions.get('direction', 'either')
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
                direction = conditions.get('direction', 'above')
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
