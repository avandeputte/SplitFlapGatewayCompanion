"""Stock Graph — a market quote as big bold type over its own price chart.

A canvas app (surface: canvas), a sibling of the Lumina clock and Date Card. It pulls a symbol's
recent price history from yfinance (the same source as the flap ``stocks`` app — which already
reads history for its 52-week trigger) and paints the whole Matrix panel as one frame (PUT
/api/canvas/frame): the price line as a dim filled area across the FULL width in the background,
with the current value and the day's percentage in big bold letters on top. Green when up, red
when down, on a black (unlit) panel so the type carries.

Defaults to the Dow (``^DJI``). Any Yahoo symbol works — an index (``^GSPC`` S&P 500, ``^IXIC``
Nasdaq) or a ticker (``AAPL``) — and the Range picks the window (1D intraday … 1Y daily). The
big number tracks the live last price; the percentage and the faint baseline are measured from
the previous close (intraday) or the window's start (multi-day). History is fetched at most once
per refresh and paused overnight/weekends when the exchange is shut, so the panel goes quiet when
the price can't move. The bundled font and panel-sized helpers come from the injected ``canvas``.
"""

# Yahoo's ^-prefixed index symbols read badly on a panel — give the common ones a clean label.
_INDEX = {
    '^DJI': 'DOW', '^GSPC': 'S&P 500', '^IXIC': 'NASDAQ', '^RUT': 'RUSSELL',
    '^FTSE': 'FTSE', '^GDAXI': 'DAX', '^FCHI': 'CAC 40', '^N225': 'NIKKEI',
    '^HSI': 'HANG SENG', '^STOXX50E': 'EURO STOXX', '^VIX': 'VIX',
}

# Range -> (yfinance period, interval, baseline). 'prev' = previous close (an intraday chart is
# read against yesterday's close); 'first' = the window's opening point (a multi-day return).
_RANGE = {
    '1D': ('1d', '5m', 'prev'),
    '5D': ('5d', '30m', 'first'),
    '1M': ('1mo', '1d', 'first'),
    '6M': ('6mo', '1d', 'first'),
    '1Y': ('1y', '1d', 'first'),
}

_UP = (54, 210, 120)      # LED-legible green
_DN = (255, 82, 82)       # LED-legible red
_INK = (238, 242, 250)    # near-white — the big value
_MUTE = (150, 160, 182)   # label / range tag


def _scale(c, k):
    return tuple(max(0, min(255, int(c[i] * k))) for i in range(3))


def _tz_of(info):
    """The exchange timezone from a yfinance fast_info, if it exposes one (for market hours)."""
    for key in ('timezone', 'exchangeTimezoneName'):
        try:
            v = info[key]
        except Exception:
            v = None
        if v:
            return str(v)
    return None


def _exchange_open(tz_name, now_utc):
    """Is an exchange in ``tz_name`` plausibly trading now? Weekday and ~04:00-20:00 in its own
    time — a generous window (regular session plus pre/after hours). Unknown zone counts as open,
    so a refresh is never skipped on a guess. (Matches the flap ``stocks`` app.)"""
    try:
        import pytz
        local = now_utc.astimezone(pytz.timezone(tz_name))
    except Exception:
        return True
    if local.weekday() >= 5:
        return False
    mins = local.hour * 60 + local.minute
    return 4 * 60 <= mins < 20 * 60


def _fit(canvas, text, max_cap, max_w):
    """Largest bundled font whose ``text`` fits both a cap height and a width. Returns the font
    plus the text's ink metrics so it can be placed precisely (as the Date Card does)."""
    max_cap, max_w = max(6.0, max_cap), max(6.0, max_w)
    n = max(1, len(text))
    est = min(max_cap / 0.66, max_w / (0.60 * n))
    size = max(6, int(est) + 8)
    font = canvas.font(size)
    for _ in range(300):
        l, t, r, b = font.getbbox(text)
        if ((b - t) <= max_cap and (r - l) <= max_w) or size <= 6:
            break
        size -= 1
        font = canvas.font(size)
    l, t, r, b = font.getbbox(text)
    return {"font": font, "w": r - l, "h": b - t, "l": l, "t": t}


def _shadow(draw, x, y, text, m, fill, shadow=(0, 0, 0)):
    """Draw ``text`` at (x, y) with a cheap dark outline, so it stays legible over the graph.
    ``m`` is the metrics dict from ``_fit`` (anchor is top-left, corrected by its ink offset)."""
    ox, oy = x - m["l"], y - m["t"]
    for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        draw.text((ox + dx, oy + dy), text, fill=shadow, font=m["font"], anchor="la")
    draw.text((ox, oy), text, fill=fill, font=m["font"], anchor="la")


def _message(canvas, ImageDraw, title, sub):
    img = canvas.blank((0, 0, 0))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    W, H = canvas.width, canvas.height
    T = _fit(canvas, title, H * 0.34, W - 4)
    S = _fit(canvas, sub, H * 0.22, W - 4)
    total = T["h"] + 2 + S["h"]
    y = (H - total) / 2.0
    _shadow(d, (W - T["w"]) / 2.0, y, title, T, _MUTE)
    _shadow(d, (W - S["w"]) / 2.0, y + T["h"] + 2, sub, S, _scale(_MUTE, 0.8))
    return img


def _pull(sym, period, interval):
    """(closes, last, prev, tz) from yfinance, or None on failure. ``closes`` drops NaN bars and
    ends on the live last price so the line's tip matches the big number."""
    import yfinance as yf
    tk = yf.Ticker(sym)
    hist = tk.history(period=period, interval=interval)
    closes = [float(c) for c in hist['Close'].tolist() if c == c]
    if not closes:
        return None
    last, prev, tz = None, None, None
    try:
        info = tk.fast_info
        lp, pc = info['lastPrice'], info['previousClose']
        last = float(lp) if lp == lp else None
        prev = float(pc) if pc == pc else None
        tz = _tz_of(info)
    except Exception:
        pass
    if last is None:
        last = closes[-1]
    else:
        closes[-1] = last
    return closes, last, prev, tz


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None                                    # not a canvas wall
    from datetime import datetime, timezone
    from PIL import ImageDraw

    W, H = int(canvas.width), int(canvas.height)
    settings = settings or {}
    raw_sym = str(settings.get('graph_symbol') or '').strip()
    sym = (raw_sym or '^DJI')
    rng = str(settings.get('graph_range') or '1D').strip().upper()
    if rng not in _RANGE:
        rng = '1D'
    period, interval, base_mode = _RANGE[rng]
    try:
        poll = max(30, int(float(settings.get('polling_rate', 60) or 60)))
    except (TypeError, ValueError):
        poll = 60
    market_only = str(settings.get('market_hours_only', 'yes')).strip().lower() in ('yes', 'on', '1', 'true')
    label = (str(settings.get('graph_label') or '').strip()
             or _INDEX.get(sym.upper()) or sym.upper().lstrip('^'))

    # -- data: cached per (symbol, range, size); refetched every `poll`s, and paused while the
    # exchange is shut. We always draw (from cache if fresh), so re-entering a playlist slot
    # repaints the graph rather than leaving the previous app's frame up.
    st = getattr(fetch, '_state', None)
    if st is None:
        st = {}
        setattr(fetch, '_state', st)
    sig = (sym, rng, W, H)
    now_utc = datetime.now(timezone.utc)
    have = bool(st.get('series')) and st.get('sig') == sig
    age = (now_utc - st['at']).total_seconds() if st.get('at') else 1e9
    closed = market_only and have and st.get('tz') and not _exchange_open(st['tz'], now_utc)

    if not have or (age >= poll and not closed):
        try:
            got = _pull(sym, period, interval)
        except Exception:
            got = None
        if got:
            closes, last, prev, tz = got
            st.update(series=closes, last=last, prev=prev, tz=tz, sig=sig, at=now_utc)
            have = True
        elif not have:
            canvas.frame(_message(canvas, ImageDraw, label, 'NO DATA'))
            return 60.0

    series, last = st['series'], st['last']
    base = float(st['prev']) if (base_mode == 'prev' and st.get('prev')) else series[0]
    chg = last - base
    pct = (chg / base * 100.0) if base else 0.0
    up = chg >= 0
    col = _UP if up else _DN

    # -- compose: black panel, dim area chart across the full width, then the type on top --------
    img = canvas.blank((0, 0, 0))
    d = ImageDraw.Draw(img)

    gy0, gy1 = 1.0, H - 2.0
    lo = min(min(series), base)
    hi = max(max(series), base)
    if hi <= lo:
        hi = lo + 1.0
    span = hi - lo

    def yof(v):
        return gy1 - (v - lo) / span * (gy1 - gy0)

    n = len(series)
    pts = [((W - 1) * (i / max(1, n - 1)), yof(series[i])) for i in range(n)]

    d.polygon(pts + [(W - 1, gy1 + 1), (0, gy1 + 1)], fill=_scale(col, 0.17))   # dim fill under the line
    by = yof(base)
    for xx in range(0, W, 4):                                                   # faint dashed baseline
        d.line([xx, by, xx + 1, by], fill=_scale(_MUTE, 0.45))
    d.line(pts, fill=_scale(col, 0.66), width=2 if H >= 48 else 1)              # the price line

    # -- the numbers, biggest thing on the panel, left-aligned over the chart -------------------
    d.fontmode = "1"                                   # crisp 1-bit type — no anti-aliased fuzz
    dec = 2 if abs(last) >= 1 else 4
    value_str = f'{last:,.{dec}f}'
    pct_str = f"{'▲' if up else '▼'}{abs(pct):.2f}%"

    pad, x = 2, 3
    wbudget = int(W * 0.66)
    if H < 44:
        # A short panel can't carry three lines and a tag legibly — give the two things that
        # matter (value + percentage) the whole height; the graph still says which way and how far.
        V = _fit(canvas, value_str, H * 0.50, int(W * 0.72))
        P = _fit(canvas, pct_str, H * 0.40, int(W * 0.72))
        total = V["h"] + 1 + P["h"]
        y = max(float(pad), (H - total) / 2.0)
        _shadow(d, x, y, value_str, V, _INK)
        _shadow(d, x, y + V["h"] + 1, pct_str, P, col)
    else:
        L = _fit(canvas, label, H * 0.20, wbudget)
        V = _fit(canvas, value_str, H * 0.42, wbudget)
        P = _fit(canvas, pct_str, H * 0.30, wbudget)
        gap = 2
        total = L["h"] + V["h"] + P["h"] + 2 * gap
        y = max(float(pad), (H - total) / 2.0)
        _shadow(d, x, y, label, L, _MUTE)
        y += L["h"] + gap
        _shadow(d, x, y, value_str, V, _INK)
        y += V["h"] + gap
        _shadow(d, x, y, pct_str, P, col)

        R = _fit(canvas, rng, H * 0.16, W * 0.22)      # range tag, top-right
        _shadow(d, W - pad - R["w"], pad, rng, R, _scale(_MUTE, 0.8))

    canvas.frame(img)

    # Hold: one refresh per poll while trading; idle long when the market's shut.
    if market_only and st.get('tz') and not _exchange_open(st['tz'], now_utc):
        return 900.0
    return float(poll)
