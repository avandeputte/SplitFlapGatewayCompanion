"""A random piece of advice (keyless: Advice Slip)."""


# =============================================================================
# SHARED — the advice slip itself. Both surfaces show what this returns.
# =============================================================================

def _fetch_advice():
    """One slip from the Advice Slip API, as plain text ('' when the response is
    empty); raises on network trouble."""
    import requests
    d = requests.get('https://api.adviceslip.com/advice', timeout=8).json()
    return str((d.get('slip') or {}).get('advice', '') or '').strip()


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])
    try:
        text = _fetch_advice()
        if not text:
            return [format_lines('Advice', 'No data', '')]
        return paginate(f'Advice: {text}')
    except Exception:
        return [format_lines('Advice', 'Offline', '')]


# =============================================================================
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# A typographic card: a small drawn compass rose and teal label over a thin
# rule, the advice wrapped at the largest font that fits (paginating across
# redraws when even ~7px can't hold it). Black background; the motif drops
# away on tiny panels.
# =============================================================================

_ACCENT = (80, 220, 190)      # teal — the compass and label


def _cv_motif(draw, x, y, s):
    """The app's accent mark: a little compass rose — the ring, a NE-SW needle
    and its pivot. Returns the width it consumed."""
    draw.ellipse([x, y, x + s - 1, y + s - 1], outline=_ACCENT)
    cx, cy = x + (s - 1) / 2.0, y + (s - 1) / 2.0
    d = (s - 1) * 0.30
    draw.line([(cx - d, cy + d), (cx + d, cy - d)], fill=_ACCENT)
    draw.rectangle([cx - 1, cy - 1, cx + 1, cy + 1], fill=_ACCENT)
    return s


def _cv_state():
    """The card state kept across redraws: the current slip and which page is up."""
    st = getattr(_cv_state, '_st', None)
    if st is None:
        st = _cv_state._st = {'data': None, 'ts': 0.0, 'page': 0}
    return st


def fetch_canvas(settings, canvas):
    """Draw the advice as a typographic card, turning body pages each redraw when
    the panel can't hold the whole slip. The slip renews on the app's
    refresh_minutes cadence; a fetch failure keeps the last slip on screen."""
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
            got = _fetch_advice()
        except Exception:
            got = None
        if got:
            st.update(data=got, ts=now, page=0)
        else:
            st['ts'] = now - ttl + 60.0        # keep any stale slip; retry in ~a minute
            if st['data'] is None:
                canvas.frame(canvas.message('Advice', 'Offline'))
                return 60.0
    img, n = canvas.text_card('ADVICE', st['data'], st['page'],
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
