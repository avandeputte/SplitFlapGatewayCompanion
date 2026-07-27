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
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
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


def fetch_matrix(settings, canvas):
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
