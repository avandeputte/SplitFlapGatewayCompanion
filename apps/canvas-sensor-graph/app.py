"""Sensor Graph — any Home Assistant sensor as big type over its own history line.

What the Stock Graph does for a ticker, this does for a sensor: the recent history
as a dim filled area across the full panel, the live value (with its unit) in big
bold letters on top, and the change over the window beneath it. The window SEEDS
from Home Assistant's history API (``get_ha_history``) the first time an entity
draws — so the card shows a full, meaningful line immediately, even as a brief
playlist slot — and every fetch afterwards appends the live state read via
``get_ha_states`` (both ride the Supervisor proxy in the add-on). While HA has no
history to give (restart, recorder off), it degrades to pure live sampling and
quietly retries the seed about once a minute; a grown ``window`` setting re-seeds
to cover it. Give it several entities (one per line) and it rotates like a board,
one per ``rotate_seconds``, with position dots along the bottom; each keeps its
own history.

Config lines are ``entity_id | Label | lo,hi`` — label optional (falls back to the
HA friendly name), and the optional lo,hi band pins the graph's scale AND colors
the value: green inside the band, red outside. Without a band the scale hugs the
window and the color follows the trend. Non-numeric states (on/off/unavailable)
show as a text card until numbers arrive. Frame-push rendering (PUT
/api/canvas/frame), one Pillow image per call.
"""

_UP = (54, 210, 120)      # LED-legible green
_DN = (255, 82, 82)       # LED-legible red
_INK = (238, 242, 250)    # near-white — the big value
_MUTE = (150, 160, 182)   # label / unit


def _parse_config(text):
    """``entity_id | Label | lo,hi`` per line -> ordered [(eid, label|None, (lo,hi)|None)].
    (The same shape the Entity Board reads, so a config moves between the two apps.)"""
    out = []
    for line in str(text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split('|')]
        if not parts[0]:
            continue
        label = parts[1] if len(parts) > 1 and parts[1] else None
        band = None
        if len(parts) > 2 and parts[2]:
            try:
                nums = [float(x) for x in parts[2].split(',')[:2]]
                if len(nums) == 2:
                    band = (min(nums), max(nums))
            except ValueError:
                pass
        out.append((parts[0], label, band))
    return out


def _fit_ink(canvas, text, max_cap, max_w):
    """Largest bundled font whose ink fits a cap height and width (the Stock Graph's
    sizing, kept local so the two cards stay independent)."""
    max_cap, max_w = max(6.0, max_cap), max(6.0, max_w)
    est = min(max_cap / 0.66, max_w / (0.60 * max(1, len(text))))
    size = max(8, int(est) + 8)
    font = canvas.font(size)
    for _ in range(300):
        l, t, r, b = font.getbbox(text)
        if ((b - t) <= max_cap and (r - l) <= max_w) or size <= 8:
            break
        size -= 1
        font = canvas.font(size)
    l, t, r, b = font.getbbox(text)
    return {"font": font, "w": r - l, "h": b - t, "l": l, "t": t}


def _fit_label(canvas, text, max_cap, max_w, floor=10):
    """``_fit_ink`` with a legibility floor: below ~10px the bold face's counters close
    up (A/X/Y render as solid blobs on a panel), so a label too long for the width is
    ELLIPSIZED at the floor size instead of shrunk past it. Returns (text, metrics)."""
    m = _fit_ink(canvas, text, max_cap, max_w)
    if m["font"].size >= floor or len(text) <= 1:
        return text, m
    t = text
    while len(t) > 1:
        t = t[:-1]
        cand = t.rstrip() + '…'
        m = _fit_ink(canvas, cand, max_cap, max_w)
        if m["font"].size >= floor:
            return cand, m
    return text[:1], m


def _shadow(draw, x, y, text, m, fill):
    """Text with a 1px dark outline so it carries over the graph behind it."""
    ox, oy = x - m["l"], y - m["t"]
    for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        draw.text((ox + dx, oy + dy), text, fill=(0, 0, 0), font=m["font"], anchor="la")
    draw.text((ox, oy), text, fill=fill, font=m["font"], anchor="la")


def _notice(canvas, ImageDraw, title, sub):
    img = canvas.blank((0, 0, 0))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    W, H = canvas.width, canvas.height
    title, T = _fit_label(canvas, title, H * 0.34, W - 4)
    S = _fit_ink(canvas, sub, H * 0.22, W - 4)
    y = (H - (T["h"] + 2 + S["h"])) / 2.0
    _shadow(d, (W - T["w"]) / 2.0, y, title, T, _MUTE)
    _shadow(d, (W - S["w"]) / 2.0, y + T["h"] + 2, sub, S, canvas.dim(_MUTE, 0.8))
    return img


def _fmt(v):
    """A sensor value at sensible precision: 21.52 -> '21.52', 612.0 -> '612'."""
    s = f'{v:.4g}'
    return s.rstrip('0').rstrip('.') if '.' in s else s


def fetch_matrix(settings, canvas, get_ha_states=None, get_ha_history=None):
    import time
    from PIL import ImageDraw

    W, H = int(canvas.width), int(canvas.height)
    settings = settings or {}
    entities = _parse_config(settings.get('config', ''))
    if not entities:
        canvas.frame(_notice(canvas, ImageDraw, 'SENSOR GRAPH', 'Pick entities'))
        return 30.0
    window = canvas.num(settings, 'window', 60, 5, 1440) * 60.0
    poll = canvas.num(settings, 'polling_rate', 30, 5)
    dwell = canvas.num(settings, 'rotate_seconds', 8, 3)
    single = len(entities) == 1

    # -- state: per-entity rolling samples + a rotation cursor. Every call samples ALL
    # configured entities from one states read (so nothing misses a beat while another
    # rotates on screen); each entity draws from its own window.
    sigv = tuple(e[0] for e in entities)
    st = getattr(fetch_matrix, '_state', None)
    if st is None or st.get('sig') != sigv:
        st = {'sig': sigv, 'idx': 0,
              'hist': {k: v for k, v in (st or {}).get('hist', {}).items() if k in sigv}}
        setattr(fetch_matrix, '_state', st)

    states = {}
    try:
        for s in (get_ha_states() if get_ha_states else []):
            states[s.get('entity_id')] = s
    except Exception:
        states = {}

    now = time.time()
    seeds = st.setdefault('seeded', {})    # eid -> [covered_window_seconds, last_try]
    for eid, _label, _band in entities:
        hist = st['hist'].setdefault(eid, [])
        # Seed (or re-seed a grown window) from HA's own history, so the card is a full
        # line the moment it first draws — a playlist slot never starts empty. Failure
        # is quiet: live sampling carries on and the seed retries about once a minute.
        covered, tried = seeds.get(eid, (0.0, 0.0))
        if get_ha_history and covered < window and now - tried >= 60.0:
            seeds[eid] = [covered, now]
            try:
                got = get_ha_history(eid, int(window // 60)) or []
            except Exception:
                got = []
            if got:
                newest = got[-1][0]
                hist[:] = list(got) + [p for p in hist if p[0] > newest]
                seeds[eid] = [window, now]
        raw = (states.get(eid) or {}).get('state')
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        # a new point when the value moved or the poll interval passed — and prune
        if not hist or hist[-1][1] != v or now - hist[-1][0] >= poll * 0.9:
            hist.append((now, v))
        while hist and now - hist[0][0] > window:
            hist.pop(0)

    idx = st['idx'] % len(entities)
    st['idx'] = (idx + 1) % len(entities)
    eid, label, band = entities[idx]
    s = states.get(eid) or {}
    attrs = s.get('attributes') or {}
    name = str(label or attrs.get('friendly_name') or eid.split('.', 1)[-1].replace('_', ' ')).upper()
    unit = str(attrs.get('unit_of_measurement') or '').strip()
    hist = st['hist'].get(eid) or []

    if not hist:                                       # nothing numeric yet
        raw = str(s.get('state', 'NO DATA') if s else 'NO DATA').upper() or '—'
        canvas.frame(_notice(canvas, ImageDraw, name, raw))
        return float(dwell) if not single else float(poll)

    series = [v for _, v in hist]
    last = series[0 if len(series) == 1 else -1]
    first = series[0]
    delta = last - first
    if band:
        col = _UP if band[0] <= last <= band[1] else _DN
    else:
        col = _UP if delta >= 0 else _DN

    # -- compose: black panel, dim area chart full width, the numbers on top ---------
    img = canvas.blank((0, 0, 0))
    d = ImageDraw.Draw(img)
    gy0, gy1 = 1.0, H - 2.0
    lo = min(min(series), band[0]) if band else min(series)
    hi = max(max(series), band[1]) if band else max(series)
    if hi <= lo:
        hi = lo + 1.0
    span = hi - lo

    def yof(v):
        return gy1 - (v - lo) / span * (gy1 - gy0)

    n = len(series)
    if n == 1:
        pts = [(0.0, yof(last)), (float(W - 1), yof(last))]
    else:
        pts = [((W - 1) * (i / (n - 1)), yof(series[i])) for i in range(n)]
    d.polygon(pts + [(W - 1, gy1 + 1), (0, gy1 + 1)], fill=canvas.dim(col, 0.17))
    if band:                                           # the band edges, faint dashed
        for edge in band:
            by = yof(edge)
            for xx in range(0, W, 4):
                d.line([xx, by, xx + 1, by], fill=canvas.dim(_MUTE, 0.45))
    d.line(pts, fill=canvas.dim(col, 0.66), width=2 if H >= 48 else 1)

    d.fontmode = "1"
    value_str = _fmt(last) + (f' {unit}' if unit else '')
    delta_str = f"{'▲' if delta >= 0 else '▼'}{_fmt(abs(delta))}"
    pad, x, gap = 2, 3, 1
    bot = 3 if H >= 44 else 2
    if H < 44:
        V = _fit_ink(canvas, value_str, H * 0.50, int(W * 0.78))
        P = _fit_ink(canvas, delta_str, H * 0.34, int(W * 0.60))
        rows = [(value_str, V, _INK), (delta_str, P, col)]
    else:
        name, L = _fit_label(canvas, name, H * 0.18, int(W * 0.72))
        V = _fit_ink(canvas, value_str, H * 0.40, int(W * 0.78))
        P = _fit_ink(canvas, delta_str, H * 0.24, int(W * 0.60))
        rows = [(name, L, _MUTE), (value_str, V, _INK), (delta_str, P, col)]
    total = sum(m["h"] for _, m, _ in rows) + gap * (len(rows) - 1)
    y = pad + max(0.0, ((H - pad - bot) - total) / 2.0)
    for txt, m, c in rows:
        _shadow(d, x, y, txt, m, c)
        y += m["h"] + gap

    if not single:                                     # rotation dots, bottom-right
        step, r = 4, 1
        dx = W - 2 - (len(entities) * step - (step - 2 * r - 1))
        dy = H - 2 - 2 * r
        for k in range(len(entities)):
            d.ellipse([dx, dy, dx + 2 * r, dy + 2 * r],
                      fill=col if k == idx else canvas.dim(_MUTE, 0.5))
            dx += step

    canvas.frame(img)
    return float(dwell) if not single else float(poll)
