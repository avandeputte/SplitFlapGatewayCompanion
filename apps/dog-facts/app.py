"""A random dog fact (keyless: dogapi.dog).

The sibling of `cat-facts`, and deliberately identical in the ways that matter — the same
settings, the same evenly-balanced layout — because the two sit next to each other in the app
list and there is no reason for one to behave differently from the other.

One thing IS different, and it is the whole reason this file is not a copy. catfact.ninja takes
a `max_length` parameter, so the cat app can simply ask for a fact that fits the wall. dogapi
has no such parameter: it sends whatever it sends, and some of its facts are a paragraph long.
So this asks for a HANDFUL and picks the best one that fits, rather than taking the first and
paginating a wall of text at somebody.
"""


# =============================================================================
# SHARED — the handful of facts and the choosing. Both surfaces pick from the
# same batch with the same rule; only "fits" differs per surface.
# =============================================================================

def _fetch_facts(settings):
    """The handful of candidate facts and the configured max length, as
    ``(facts, max_len)``. Five facts is enough to usually find one that fits
    without making the wall wait on a bigger response; raises on network
    trouble."""
    import requests
    try:
        max_len = int(float(settings.get('max_length', '120') or 120))
    except (TypeError, ValueError):
        max_len = 120
    max_len = max(40, min(250, max_len))
    d = requests.get('https://dogapi.dog/api/v2/facts',
                     params={'limit': 5}, timeout=8).json()
    facts = [str(((item or {}).get('attributes') or {}).get('body', '') or '')
             for item in (d.get('data') or [])]
    return facts, max_len


def _pick(facts, max_len, fits_one_page):
    """Choose the fact to show, out of the handful the API sent.

    dogapi cannot be asked for a short one, so the choosing happens here — and it is worth
    doing properly, because a fact that does not fit the wall does not get shorter, it gets
    PAGINATED, and a passer-by then reads two thirds of a sentence about beagles.

    So: prefer the LONGEST fact that still lands on a single page. Longest, not shortest,
    because a fact that fills the wall is a better use of it than three words floating in the
    middle — but never at the cost of spilling onto a second page. If nothing fits (the wall is
    small, or the API sent five paragraphs), fall back to the shortest, which at least
    paginates the least. ``fits_one_page(text)`` is the wall's own answer — the engine's
    pagination, so this app never has to know how the layout wraps."""
    clean = [f.strip() for f in facts if f and f.strip()]
    if not clean:
        return ''
    allowed = [f for f in clean if len(f) <= max_len] or clean
    onepage = [f for f in allowed if fits_one_page(f)]
    return max(onepage, key=len) if onepage else min(allowed, key=len)


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])
    try:
        facts, max_len = _fetch_facts(settings)
        text = _pick(facts, max_len, lambda f: len(paginate(f)) == 1)
        if not text:
            return [format_lines('Dog fact', 'No data', '')]
        return paginate(text)   # no title — just the fact
    except Exception:
        return [format_lines('Dog fact', 'Offline', '')]


# =============================================================================
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# A typographic card: a little drawn paw print and sky-blue label over a thin
# rule (the cat app's twin, told apart by its color), the fact wrapped at the
# largest font that fits (paginating across redraws when even ~7px can't hold
# it). Black background; the paw drops away on tiny panels.
# =============================================================================

_ACCENT = (110, 185, 255)     # sky blue — the paw and label


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
    """Draw the dog fact as a typographic card, turning body pages each redraw
    when the panel can't hold the whole fact. The panel renders any length, so
    "fits one page" is simply "any" here — _pick then keeps its longest-allowed
    preference. The fact renews on the app's refresh_minutes cadence; a fetch
    failure keeps the last fact on screen."""
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
            facts, max_len = _fetch_facts(settings)
            got = _pick(facts, max_len, lambda f: True)
        except Exception:
            got = None
        if got:
            st.update(data=got, ts=now, page=0)
        else:
            st['ts'] = now - ttl + 60.0        # keep any stale fact; retry in ~a minute
            if st['data'] is None:
                canvas.frame(canvas.message('Dog fact', 'Offline'))
                return 60.0
    W, H = canvas.width, canvas.height
    if getattr(canvas, "can_gtext", False) and H >= 96:
        _cv_ops(canvas, 'DOG FACT', st['data'])
        st['page'] = 0
        return max(30.0, min(300.0, ttl - (now - st['ts'])))
    img, n = canvas.text_card('DOG FACT', st['data'], st['page'],
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
