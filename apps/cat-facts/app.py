"""A random cat fact (keyless: catfact.ninja)."""


# =============================================================================
# SHARED — the cat fact itself. Both surfaces show what this returns.
# =============================================================================

def _fetch_fact(settings):
    """One fact under the configured max length — catfact.ninja honors
    ``max_length``, so we can simply ask for a display-friendly fact. Returns
    the text ('' when the response is empty); raises on network trouble."""
    import requests
    try:
        max_len = int(float(settings.get('max_length', '120') or 120))
    except (TypeError, ValueError):
        max_len = 120
    max_len = max(40, min(250, max_len))
    d = requests.get('https://catfact.ninja/fact',
                     params={'max_length': max_len}, timeout=8).json()
    return str(d.get('fact', '') or '').strip()


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])
    try:
        text = _fetch_fact(settings)
        if not text:
            return [format_lines('Cat fact', 'No data', '')]
        return paginate(text)   # no title — just the fact
    except Exception:
        return [format_lines('Cat fact', 'Offline', '')]


# =============================================================================
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# A typographic card: a little drawn paw print and orange label over a thin
# rule, the fact wrapped at the largest font that fits (paginating across
# redraws when even ~7px can't hold it). Black background; the paw drops away
# on tiny panels.
# =============================================================================

_ACCENT = (255, 165, 70)      # orange — the paw and label


def _cv_motif(draw, x, y, s):
    """The app's accent mark: a little paw print — the main pad with three toes.
    Returns the width it consumed."""
    draw.ellipse([x + s * 0.16, y + s * 0.58, x + s * 0.84, y + s], fill=_ACCENT)
    toe = max(2.0, s * 0.26)
    for tx, ty in ((0.00, 0.20), (0.37, 0.00), (0.74, 0.20)):
        draw.ellipse([x + tx * s, y + ty * s, x + tx * s + toe, y + ty * s + toe], fill=_ACCENT)
    return s


def _cv_state():
    """The card state kept across redraws: the current fact and which page is up."""
    st = getattr(_cv_state, '_st', None)
    if st is None:
        st = _cv_state._st = {'data': None, 'ts': 0.0, 'page': 0}
    return st


def _ops_motif(canvas, x, y, s):
    """The paw print — the main pad and three toes — as geometry ops (the gtext
    card's twin of _cv_motif)."""
    canvas.ellipse(x + s * 0.50, y + s * 0.79, s * 0.34, s * 0.21, color=_ACCENT, fill=True)
    toe = max(2.0, s * 0.26)
    for tx, ty in ((0.00, 0.20), (0.37, 0.00), (0.74, 0.20)):
        canvas.circle(x + tx * s + toe / 2.0, y + ty * s + toe / 2.0, toe / 2.0,
                      color=_ACCENT, fill=True)


def _cv_ops(canvas, label, body):
    """The tall-panel card drawn with on-device scalable text (gtext) + geometry
    instead of a pushed pixel frame — crisp at the LCD's native resolution, a few
    hundred bytes a frame. Same paw+label header over a rule, then the fact as big
    wrapped text centered in the body (mirrors the PIL text_card layout below)."""
    W, H = canvas.width, canvas.height
    canvas.clear((0, 0, 0))
    m = max(6, int(W * 0.03))
    lab_h = max(9, int(H * 0.12))
    ms = int(lab_h * 1.3)
    top = max(4, int(H * 0.06))
    gap = max(4, int(W * 0.02))
    _ops_motif(canvas, m, top, ms)
    lsz = canvas.fit_gtext(label, W - m - (m + ms + gap), lab_h)
    canvas.gtext(m + ms + gap, top + (ms - lsz) // 2 - int(lsz * 0.08), label,
                 color=_ACCENT, size=lsz)
    ry = top + ms + max(3, int(H * 0.028))
    canvas.line(m, ry, W - 1 - m, ry, color=tuple(c // 3 for c in _ACCENT))
    by0 = ry + max(4, int(H * 0.045))
    by1 = H - max(6, int(H * 0.05))
    size, lines = canvas.fit_wrap_gtext(body, W - 2 * m, by1 - by0, max_lines=7)
    step = int(size * 1.18)
    y = by0 + max(0, (by1 - by0 - step * len(lines)) // 2)
    for ln in lines:
        canvas.gtext(W // 2, y, ln, color=(238, 238, 244), size=size, align="center")
        y += step
    canvas.show()


def fetch_canvas(settings, canvas):
    """Draw the cat fact as a typographic card, turning body pages each redraw
    when the panel can't hold the whole fact. The fact renews on the app's
    refresh_minutes cadence; a fetch failure keeps the last fact on screen."""
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
            got = _fetch_fact(settings)
        except Exception:
            got = None
        if got:
            st.update(data=got, ts=now, page=0)
        else:
            st['ts'] = now - ttl + 60.0        # keep any stale fact; retry in ~a minute
            if st['data'] is None:
                canvas.frame(canvas.message('Cat fact', 'Offline'))
                return 60.0
    W, H = canvas.width, canvas.height
    if getattr(canvas, "can_gtext", False) and H >= 96:
        _cv_ops(canvas, 'CAT FACT', st['data'])
        st['page'] = 0
        return max(30.0, min(300.0, ttl - (now - st['ts'])))
    img, n = canvas.text_card('CAT FACT', st['data'], st['page'],
                              accent=_ACCENT, motif=_cv_motif)
    canvas.frame(img)
    st['page'] = (st['page'] + 1) % n
    if n > 1:
        try:
            d = float(settings.get('loop_delay', 7) or 7)
        except (TypeError, ValueError):
            d = 7.0
        return max(6.0, min(30.0, d))
    return max(30.0, min(300.0, ttl - (now - st['ts'])))
