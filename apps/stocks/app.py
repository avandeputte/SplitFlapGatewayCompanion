def _row(left, right, cols):
    """One full-width line: `left` flush left, `right` flush right.

    format_lines centres each line horizontally, so a line that is ALREADY `cols` wide
    passes through untouched — that is what pins these columns. The left part is trimmed
    to make room, never the right: the number is the thing you are reading.
    """
    left, right = str(left), str(right)
    if len(right) >= cols:
        return right[:cols]
    left = left[:cols - len(right) - 1]
    return left + ' ' * (cols - len(left) - len(right)) + right


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import yfinance as yf

    def t(s):
        return i18n.t(s, "stocks") if i18n is not None else s

    tickers = [s.strip() for s in settings.get('stocks_list', '').split(',') if s.strip()]
    if not tickers:
        return [format_lines('Stocks', t('No tickers'), t('Configure'))]
    no_color = settings.get('disable_colors', 'no') == 'yes'
    rows, cols = get_rows(), get_cols()

    # Each ticker is quoted in its exchange's own currency (AAPL->USD, VOD.L->GBP,
    # SAP.DE->EUR); show that currency's symbol, not a single hardcoded one.
    def sym_for(cur):
        # With i18n the display can render € £ ¥ (Windows-1252); without it the modules
        # are the basic charset, so use '$' for USD and the ASCII ISO code otherwise.
        if i18n is not None:
            return i18n.currency_symbol(cur)
        return '$' if cur == 'USD' else cur

    # Numbers follow the global Language (1,234.50 vs 1.234,50 vs 1 234,50).
    def n(v, d=2, grouping=True):
        if i18n is not None:
            return i18n.number(v, d, grouping)
        return f'{v:,.{d}f}' if grouping else f'{v:.{d}f}'

    def pct(v):
        return f"{'+' if v >= 0 else '-'}{n(abs(v), 1, grouping=False)}%"

    pages = []
    for i in range(0, len(tickers), rows):
        chunk = tickers[i:i+rows]
        price_lines, change_lines = [], []
        for sym in chunk:
            try:
                info = yf.Ticker(sym).fast_info
                price = info['lastPrice']
                prev = info['previousClose']
                try:
                    cur = (info['currency'] or 'USD')
                except Exception:
                    cur = 'USD'
                if cur in ('GBp', 'GBX'):      # London quotes pence, not pounds
                    price, prev, cur = price / 100, prev / 100, 'GBP'
                chg = ((price - prev) / prev) * 100
                cs = sym_for(cur)
                sep = '' if cs != cur else ' '   # 3-letter-code fallback reads better spaced
                # An arrow, not just a colour. A colour says "good"/"bad" only if you can
                # see it — it is nothing at all with colours disabled, and nothing on a
                # mono wall. ↑/↓ degrade to ^/v on a reel with no pictograph flaps, which
                # still reads. The colour comes along as well when it can.
                arrow = '\u2191' if chg >= 0 else '\u2193'
                icon = arrow if no_color else arrow + ('🟩' if chg >= 0 else '🟥')
                # Ticker flush left, price flush right: prices line up in a column and
                # you can read down them, which is what a list of stocks is for.
                price_lines.append(_row(sym, f'{cs}{sep}{n(price, 2)}', cols))
                change_lines.append(_row(sym, f'{icon}{pct(chg)}', cols))
            except Exception:
                price_lines.append(_row(sym, 'Err', cols))
                change_lines.append(_row(sym, 'Err', cols))
        # No padding: two tickers on a five-row wall are centred by format_lines.
        pages.append(format_lines(*price_lines))
        pages.append(format_lines(*change_lines))
    return pages or [format_lines('Stocks', t('No data'), '')]


def trigger(settings, conditions):
    """Fire when any followed ticker moves beyond the configured threshold or hits a price target."""
    import yfinance as yf
    import time as _time

    condition_type = conditions.get('condition_type', 'pct_change')
    tickers = [s.strip() for s in settings.get('stocks_list', '').split(',') if s.strip()]
    if not tickers:
        return False

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'fired_targets': set(), '52w_cache': {}}
        setattr(trigger, '_state', state)

    try:
        for sym in tickers:
            t = yf.Ticker(sym)
            info = t.fast_info
            price = info['lastPrice']
            prev = info['previousClose']

            if condition_type == 'pct_change':
                threshold = float(conditions.get('threshold', 3))
                # The direction select is shared between condition types; map the
                # price-target vocabulary so no combination is silently dead.
                direction = {'above': 'up', 'below': 'down'}.get(
                    conditions.get('direction', 'either'), conditions.get('direction', 'either'))
                if not prev:
                    continue
                chg = ((price - prev) / prev) * 100
                if direction == 'up' and chg >= threshold:
                    return True
                if direction == 'down' and chg <= -threshold:
                    return True
                if direction == 'either' and abs(chg) >= threshold:
                    return True

            elif condition_type == 'price_target':
                target = float(conditions.get('price_target', 0))
                direction = {'up': 'above', 'down': 'below', 'either': 'above'}.get(
                    conditions.get('direction', 'above'), conditions.get('direction', 'above'))
                if not target:
                    continue
                key = f"{sym}:{direction}:{target}"
                crossed = (direction == 'above' and price >= target) or \
                          (direction == 'below' and price <= target)
                if crossed and key not in state['fired_targets']:
                    state['fired_targets'].add(key)
                    return True
                if not crossed and key in state['fired_targets']:
                    state['fired_targets'].discard(key)

            elif condition_type == '52w_extreme':
                extreme = conditions.get('extreme', 'high')
                # Cache 52w high/low for 1 hour to avoid expensive history fetches
                cached = state['52w_cache'].get(sym)
                now = _time.time()
                if cached and (now - cached['ts']) < 3600:
                    week52_high, week52_low = cached['high'], cached['low']
                else:
                    hist = yf.Ticker(sym).history(period='1y')
                    if hist.empty:
                        continue
                    week52_high = hist['High'].max()
                    week52_low = hist['Low'].min()
                    state['52w_cache'][sym] = {'high': week52_high, 'low': week52_low, 'ts': now}

                key_h = f"{sym}:52wh"
                key_l = f"{sym}:52wl"
                if extreme in ('high', 'either') and price >= week52_high * 0.995:
                    if key_h not in state['fired_targets']:
                        state['fired_targets'].add(key_h)
                        return True
                else:
                    state['fired_targets'].discard(key_h)
                if extreme in ('low', 'either') and price <= week52_low * 1.005:
                    if key_l not in state['fired_targets']:
                        state['fired_targets'].add(key_l)
                        return True
                else:
                    state['fired_targets'].discard(key_l)

            elif condition_type == 'market_hours':
                from datetime import datetime
                import pytz
                event = conditions.get('market_event', 'open')
                et = pytz.timezone('US/Eastern')
                now = datetime.now(et)
                # Skip weekends
                if now.weekday() >= 5:
                    return False
                hour, minute = now.hour, now.minute
                key = f"market:{event}:{now.strftime('%Y-%m-%d')}"
                if event == 'open' and hour == 9 and 30 <= minute < 35:
                    if key not in state['fired_targets']:
                        state['fired_targets'].add(key)
                        return True
                elif event == 'close' and hour == 16 and minute < 5:
                    if key not in state['fired_targets']:
                        state['fired_targets'].add(key)
                        return True
                return False

    except Exception:
        raise
    return False
