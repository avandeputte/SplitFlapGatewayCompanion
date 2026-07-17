def _columns(pairs, cols, gap=3):
    """Two aligned columns — `left` (ticker) flush, `right` (value) flush — kept
    CLOSE together rather than spread to the wall's edges.

    format_lines centres each line, so the block is only as wide as its content
    plus a small gap: on a wide wall the ticker and its price sit together in the
    middle instead of stranded at opposite edges. The value column still lines up
    down the page (every line is the same width, so centring keeps it aligned).
    A narrow wall falls back to the full width, trimming the ticker, never the value.
    """
    pairs = [(str(left), str(right)) for left, right in pairs]
    rw = max((len(r) for _, r in pairs), default=0)
    lw = max((len(l) for l, _ in pairs), default=0)
    inner = min(cols, lw + gap + rw)
    lspace = max(1, inner - rw)                       # ticker column width, incl. the gap
    out = []
    for left, right in pairs:
        if len(left) > lspace - 1:
            left = left[:max(0, lspace - 1)]
        out.append((left.ljust(lspace) + right.rjust(rw))[:cols])
    return out


def _three_widths(triples, gap):
    tw = max((len(str(a)) for a, _, _ in triples), default=0)   # ticker
    pw = max((len(str(b)) for _, b, _ in triples), default=0)   # price
    cw = max((len(str(c)) for _, _, c in triples), default=0)   # change
    return tw, pw, cw, tw + gap + pw + gap + cw                 # ...and the block width


def _fits_three(triples, cols, gap=3):
    """Do ticker + price + change fit together on ONE line at this width?"""
    return _three_widths(triples, gap)[3] <= cols


def _columns3(triples, cols, gap=3):
    """Three aligned columns — ticker flush left, price and change each flush
    right — kept together as one centred block.

    On an ultra-wide wall (a big Matrix panel) there is room to show a ticker,
    its price AND the day's change on ONE line, so the whole watchlist is a single
    page instead of flipping between a price page and a change page. The price
    column and the change column each line up down the page. format_lines centres
    the block, so — like _columns — it sits together in the middle, not spread to
    the wall's edges. Only used where the three columns actually fit.
    """
    triples = [(str(a), str(b), str(c)) for a, b, c in triples]
    tw, pw, cw, block = _three_widths(triples, gap)
    inner = min(cols, block)
    lead = max(1, inner - pw - gap - cw)              # ticker column width, incl. its gap
    out = []
    for a, b, c in triples:
        if len(a) > lead - 1:
            a = a[:max(0, lead - 1)]
        out.append((a.ljust(lead) + b.rjust(pw) + (' ' * gap) + c.rjust(cw))[:cols])
    return out


def _tz_of(info):
    """The exchange timezone from a yfinance fast_info, if it exposes one."""
    for key in ('timezone', 'exchangeTimezoneName'):
        try:
            v = info[key]
        except Exception:
            v = None
        if v:
            return str(v)
    return None


def _exchange_open(tz_name, now_utc):
    """Is an exchange in `tz_name` plausibly trading right now?

    Weekday and roughly 04:00-20:00 in the exchange's OWN time — a generous window
    that spans the regular session plus pre-/after-hours, so we only ever call a
    market 'closed' when it is CLEARLY shut (overnight, weekend). An unknown timezone
    counts as open: we never skip a refresh on a guess. (Public holidays are not
    modelled — the cost of an extra poll on a holiday is one stale-priced fetch.)
    """
    try:
        import pytz
        local = now_utc.astimezone(pytz.timezone(tz_name))
    except Exception:
        return True
    if local.weekday() >= 5:                          # Saturday / Sunday
        return False
    mins = local.hour * 60 + local.minute
    return 4 * 60 <= mins < 20 * 60


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import yfinance as yf
    from datetime import datetime, timezone

    def t(s):
        return i18n.t(s, "stocks") if i18n is not None else s

    tickers = [s.strip() for s in settings.get('stocks_list', '').split(',') if s.strip()]
    if not tickers:
        return [format_lines('Stocks', t('No tickers'), t('Configure'))]
    no_color = settings.get('disable_colors', 'no') == 'yes'
    rows, cols = get_rows(), get_cols()

    # Pause polling when every followed market is shut (overnight, weekend): yfinance is
    # the slow part, and a closed market's price does not move. We remember each ticker's
    # exchange timezone (fast_info carries it) and the last good pages, and simply re-show
    # them while all those markets are closed. Any unknown timezone counts as open, so we
    # never go stale on a guess; a settings change (different tickers / geometry) misses
    # the cache and refetches at once.
    market_hours = settings.get('market_hours_only', 'yes') == 'yes'
    st = getattr(fetch, '_state', None)
    if st is None:
        st = {'pages': None, 'tzs': None, 'sig': None}
        setattr(fetch, '_state', st)
    lang = getattr(i18n, 'lang', '') if i18n is not None else ''
    sig = (tuple(tickers), rows, cols, no_color, lang)
    now_utc = datetime.now(timezone.utc)
    if (market_hours and st['pages'] is not None and st['sig'] == sig and st['tzs']
            and not any(_exchange_open(tz, now_utc) for tz in st['tzs'])):
        return st['pages']
    tzs = set()

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
        triples = []                                 # (ticker, price, change)
        for sym in chunk:
            try:
                info = yf.Ticker(sym).fast_info
                price = info['lastPrice']
                prev = info['previousClose']
                tz = _tz_of(info)                # remember the exchange's hours
                if tz:
                    tzs.add(tz)
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
                triples.append((sym, f'{cs}{sep}{n(price, 2)}', f'{icon}{pct(chg)}'))
            except Exception:
                triples.append((sym, 'Err', 'Err'))
        # Ultra-wide wall (a big Matrix panel): ticker, price AND change on ONE line,
        # so the watchlist is a single page. Otherwise the price and the change each
        # get their own page - both number columns don't fit at this width. Either
        # way the prices (and the changes) line up in a column, to read down them.
        if _fits_three(triples, cols):
            pages.append(format_lines(*_columns3(triples, cols)))
        else:
            pages.append(format_lines(*_columns([(s, p) for s, p, _ in triples], cols)))
            pages.append(format_lines(*_columns([(s, c) for s, _, c in triples], cols)))
    result = pages or [format_lines('Stocks', t('No data'), '')]
    st['pages'], st['tzs'], st['sig'] = result, tzs, sig
    return result


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
