# =============================================================================
# SHARED — the quote itself (price/previous-close/currency/exchange-tz via
# yfinance) and the market-hours reasoning, used by both surfaces — plus the
# trigger (surface-independent by contract).
# =============================================================================


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
    modeled — the cost of an extra poll on a holiday is one stale-priced fetch.)
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


def _quote(sym):
    """One ticker's live quote — (price, previous_close, currency, exchange_tz).
    London pence quotes are normalized to pounds. Raises on a bad symbol or an
    outage; each surface decides what an error looks like on its own wall."""
    import yfinance as yf
    info = yf.Ticker(sym).fast_info
    price = info['lastPrice']
    prev = info['previousClose']
    tz = _tz_of(info)
    try:
        cur = (info['currency'] or 'USD')
    except Exception:
        cur = 'USD'
    if cur in ('GBp', 'GBX'):              # London quotes pence, not pounds
        price, prev, cur = price / 100, prev / 100, 'GBP'
    return price, prev, cur, tz


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


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================


def _columns(pairs, cols, gap=3):
    """Two aligned columns — `left` (ticker) flush, `right` (value) flush — kept
    CLOSE together rather than spread to the wall's edges.

    format_lines centers each line, so the block is only as wide as its content
    plus a small gap: on a wide wall the ticker and its price sit together in the
    middle instead of stranded at opposite edges. The value column still lines up
    down the page (every line is the same width, so centering keeps it aligned).
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
    right — kept together as one centered block.

    On an ultra-wide wall (a big Matrix panel) there is room to show a ticker,
    its price AND the day's change on ONE line, so the whole watchlist is a single
    page instead of flipping between a price page and a change page. The price
    column and the change column each line up down the page. format_lines centers
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


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
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
                price, prev, cur, tz = _quote(sym)
                if tz:
                    tzs.add(tz)                  # remember the exchange's hours
                chg = ((price - prev) / prev) * 100
                cs = sym_for(cur)
                sep = '' if cs != cur else ' '   # 3-letter-code fallback reads better spaced
                # An arrow, not just a color. A color says "good"/"bad" only if you can
                # see it — it is nothing at all with colors disabled, and nothing on a
                # mono wall. ↑/↓ degrade to ^/v on a reel with no pictograph flaps, which
                # still reads. The color comes along as well when it can.
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


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The watchlist as quote rows: ticker left, price right, the day's change as a
# green/red chip (a color-coded price where a chip won't fit). More tickers
# than rows rotate page by page, in the flap pages' order. Solid black
# background; adaptive down to 64x32.
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
    """Quote rows: symbol flush left, price flush right, the day's change as a
    green/red chip in an aligned right-hand column. A panel too narrow for chips
    colors the price instead — the direction survives, only the chrome goes.

    ``rows_data`` = [(symbol, price_text, change_pct_or_None), ...]; a None change
    (an errored symbol) renders dim, never as a fake zero. Every column uses one
    common font so the table reads down, not row by row."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    n = max(1, len(rows_data))
    chips = W >= 104                       # room for a chip column beside the price
    if n == 1:
        # A lone quote (the rotation's last page) can't fill the panel as a
        # table row — set it as a card instead: symbol hung from the top edge,
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


def fetch_matrix(settings, canvas, i18n=None):
    """The same watchlist as the flap pages, in the same order, as quote rows —
    rotating page by page when there are more tickers than rows. Quotes are
    cached and refreshed at the polling rate, so a page turn never re-hits
    yfinance; the last good prices survive an outage."""
    import time
    from PIL import ImageDraw

    tickers = [s.strip() for s in settings.get('stocks_list', '').split(',') if s.strip()]
    if not tickers:
        canvas.frame(_cv_message(canvas, ImageDraw, 'STOCKS', 'NO TICKERS'))
        return 60.0

    # Numbers and currency symbols follow the global Language, like the flap view.
    def n(v, d=2, grouping=True):
        if i18n is not None:
            return i18n.number(v, d, grouping)
        return f'{v:,.{d}f}' if grouping else f'{v:.{d}f}'

    def sym_for(cur):
        if i18n is not None:
            return i18n.currency_symbol(cur)
        return '$' if cur == 'USD' else cur

    try:
        poll = float(settings.get('polling_rate', 60) or 60)
    except (TypeError, ValueError):
        poll = 60.0

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'quotes': None, 'ts': 0.0, 'sig': None, 'page': 0}
        setattr(fetch_matrix, '_state', st)
    sig = tuple(tickers)
    now = time.time()
    if st['quotes'] is None or st['sig'] != sig or (now - st['ts']) >= max(30.0, poll):
        quotes = {}
        for s in tickers:
            try:
                price, prev, cur, _tz = _quote(s)
                quotes[s] = (price, prev, cur)
            except Exception:
                quotes[s] = None
        if all(v is None for v in quotes.values()) and st['sig'] == sig \
                and st['quotes'] is not None:
            pass                           # total outage: keep the last good prices
        else:
            st['quotes'], st['sig'] = quotes, sig
        st['ts'] = now                     # even after a failure: no hammering
    quotes = st['quotes'] or {}
    if all(quotes.get(s) is None for s in tickers):
        canvas.frame(_cv_message(canvas, ImageDraw, 'STOCKS', 'OFFLINE'))
        return 60.0

    compact = canvas.width < 104           # narrow panel: whole dollars beat tiny cents
    rows_data = []
    for s in tickers:
        q = quotes.get(s)
        if q is None:
            rows_data.append((s, 'ERR', None))
            continue
        price, prev, cur = q
        cs = sym_for(cur)
        sep = '' if cs != cur else ' '
        chg = ((price - prev) / prev) * 100 if prev else None
        body = n(price, 0) if (compact and price >= 100) else n(price, 2)
        rows_data.append((s, f'{cs}{sep}{body}', chg))

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
    return max(30.0, min(300.0, poll))
