"""An inspirational quote (keyless: DummyJSON quotes)."""


# =============================================================================
# SHARED — the quote itself: fetched from DummyJSON, best-of-three under the
# configured length. Both surfaces show what this returns.
# =============================================================================

def _best_quote(settings):
    """Up to three draws from the API, keeping the first under the configured max
    length (the API can't filter by length), else the shortest seen. Returns
    ``(quote, author)`` or None; raises on network trouble."""
    import requests
    try:
        max_len = int(float(settings.get('max_length', '150') or 150))
    except (TypeError, ValueError):
        max_len = 150
    max_len = max(40, min(300, max_len))

    def one():
        d = requests.get('https://dummyjson.com/quotes/random', timeout=8).json()
        return str(d.get('quote', '') or '').strip(), str(d.get('author', '') or '').strip()

    best = None
    for _ in range(3):
        q, a = one()
        if not q:
            continue
        if len(q) <= max_len:
            best = (q, a)
            break
        if best is None or len(q) < len(best[0]):
            best = (q, a)
    return best


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])
    try:
        best = _best_quote(settings)
        if not best:
            return [format_lines('Quote', 'No data', '')]
        q, a = best
        text = f'{q}  - {a}' if a else q
        return paginate(f'Quote: {text}')
    except Exception:
        return [format_lines('Quote', 'Offline', '')]


# =============================================================================
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# A typographic quote card: a drawn quotation mark and gold label over a thin
# rule, the quote wrapped at the largest font that fits (paginating across
# redraws when even ~7px can't hold it), the author bottom-right in the accent.
# Black background; the motif and the reserved author line drop away on tiny
# panels (the author then flows with the text).
# =============================================================================

_ACCENT = (255, 200, 80)      # gold — the quote mark, label and author


def _cv_motif(canvas, draw, x, y, s):
    """The app's accent mark: a drawn opening quotation mark, ~``s`` px tall.
    Returns the width it consumed."""
    f = canvas.font(int(s * 2.2))
    b = f.getbbox('“')
    draw.text((x - b[0], y - b[1]), '“', font=f, fill=_ACCENT)
    return b[2] - b[0]


def _cv_state():
    """The card state kept across redraws: the current quote and which page is up."""
    st = getattr(_cv_state, '_st', None)
    if st is None:
        st = _cv_state._st = {'data': None, 'ts': 0.0, 'page': 0}
    return st


def _ops_motif(canvas, x, y, s):
    """The opening quotation mark as a large gtext glyph — the gtext card's twin of
    _cv_motif. Returns the width it consumed."""
    qsz = int(s * 1.5)
    canvas.gtext(x, y - int(qsz * 0.12), '“', color=_ACCENT, size=qsz)
    return int(canvas.text_width('“', qsz))


def _cv_ops(canvas, label, body, sub=None):
    """The tall-panel quote card drawn with on-device scalable text (gtext) +
    geometry instead of a pushed pixel frame — crisp at the LCD's native resolution.
    The quote-mark+label header over a rule, the quote as big wrapped text centered
    in the body, and the author bottom-right in the accent (mirrors the PIL
    text_card layout below)."""
    W, H = canvas.width, canvas.height
    canvas.clear((0, 0, 0))
    m = max(6, int(W * 0.03))
    lab_h = max(9, int(H * 0.12))
    ms = int(lab_h * 1.3)
    top = max(4, int(H * 0.06))
    gap = max(4, int(W * 0.02))
    mw = _ops_motif(canvas, m, top, ms)
    lsz = canvas.fit_gtext(label, W - m - (m + mw + gap), lab_h)
    canvas.gtext(m + mw + gap, top + (ms - lsz) // 2 - int(lsz * 0.08), label,
                 color=_ACCENT, size=lsz)
    ry = top + ms + max(3, int(H * 0.028))
    canvas.line(m, ry, W - 1 - m, ry, color=tuple(c // 3 for c in _ACCENT))
    by0 = ry + max(4, int(H * 0.045))
    by1 = H - max(6, int(H * 0.05))
    if sub:
        asz = max(8, int(H * 0.075))
        ay = H - max(5, int(H * 0.04)) - asz
        canvas.gtext(W - m, ay, sub, color=_ACCENT, size=asz, align="right")
        by1 = ay - max(3, int(H * 0.02))
    size, lines = canvas.fit_wrap_gtext(body, W - 2 * m, by1 - by0, max_lines=6)
    step = int(size * 1.18)
    y = by0 + max(0, (by1 - by0 - step * len(lines)) // 2)
    for ln in lines:
        canvas.gtext(W // 2, y, ln, color=(238, 238, 244), size=size, align="center")
        y += step
    canvas.show()


def fetch_canvas(settings, canvas):
    """Draw the quote as a typographic card, turning body pages each redraw when
    the panel can't hold the whole quote. The quote itself renews on the app's
    refresh_minutes cadence; a fetch failure keeps the last quote on screen."""
    import time
    st = _cv_state()
    try:
        mins = float(settings.get('refresh_minutes', 30) or 30)
    except (TypeError, ValueError):
        mins = 30.0
    ttl = max(60.0, mins * 60.0)
    now = time.time()
    if st['data'] is None or (st['page'] == 0 and now - st['ts'] >= ttl):
        try:
            got = _best_quote(settings)
        except Exception:
            got = None
        if got:
            st.update(data=got, ts=now, page=0)
        else:
            st['ts'] = now - ttl + 60.0        # keep any stale quote; retry in ~a minute
            if st['data'] is None:
                canvas.frame(canvas.message('Quote', 'Offline'))
                return 60.0
    q, a = st['data']
    W, H = canvas.width, canvas.height
    if getattr(canvas, "can_gtext", False) and H >= 96:
        _cv_ops(canvas, 'QUOTE', q, sub=f'— {a}' if a else None)
        st['page'] = 0
        return max(30.0, min(300.0, ttl - (now - st['ts'])))
    img, n = canvas.text_card('QUOTE', q, st['page'], accent=_ACCENT,
                              motif=lambda d, mx, my, ms: _cv_motif(canvas, d, mx, my, ms),
                              sub=f'— {a}' if a else None)
    canvas.frame(img)
    st['page'] = (st['page'] + 1) % n
    if n > 1:
        try:
            d = float(settings.get('loop_delay', 7) or 7)
        except (TypeError, ValueError):
            d = 7.0
        return max(6.0, min(30.0, d))
    return max(30.0, min(300.0, ttl - (now - st['ts'])))
