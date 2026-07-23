"""Home Assistant — entity states as rows on the split-flap wall.

A flap app: one row per entity, its name on the left and value on the right, followed by a
color flap that reads as a status/threshold dot — green for an "on" state, and green / amber /
red by band for a numeric sensor with thresholds. States come through the injected
``get_ha_states`` helper (the backend reaches HA via the Supervisor proxy in the add-on, or
COMPANION_HA_URL/TOKEN standalone); renames and thresholds come from the same
``entity_id | Name | low,high`` config the canvas HA Dashboard uses. Rows past a screenful
paginate onto the next page of the loop.
"""


# =============================================================================
# SHARED — the entity DATA: config parsing, state classification and the
# threshold banding. Both surfaces show the same entities in the same order.
# =============================================================================

_ON = {'on', 'home', 'open', 'unlocked', 'playing', 'active', 'heat', 'cool', 'auto', 'detected'}
_OFF = {'off', 'away', 'closed', 'locked', 'idle', 'standby', 'paused', 'not_home', 'clear'}
_DEAD = {'unavailable', 'unknown', 'none', ''}
# Friendly labels in normal case — the split-flap renderer folds to the wall's caps itself.
_SHORT = {'unlocked': 'Open', 'locked': 'Locked', 'closed': 'Closed', 'not_home': 'Away',
          'detected': 'Motion', 'clear': 'Clear', 'standby': 'Idle', 'playing': 'Play', 'paused': 'Paused'}
_GREEN, _AMBER, _RED = '\U0001f7e9', '\U0001f7e8', '\U0001f7e5'   # color flaps: 🟩 🟨 🟥


def _parse_config(text):
    """`entity_id | Name | low,high` per line -> {eid: (name|None, (lo,hi)|None)} + ordered ids."""
    cfg, order = {}, []
    for line in str(text or '').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split('|')]
        eid = parts[0]
        if not eid:
            continue
        name = parts[1] if len(parts) > 1 and parts[1] else None
        thr = None
        if len(parts) > 2 and parts[2]:
            try:
                nums = [float(x) for x in parts[2].split(',')[:2]]
                if len(nums) == 2:
                    thr = (min(nums), max(nums))
            except ValueError:
                pass
        cfg[eid] = (name, thr)
        order.append(eid)
    return cfg, order


def _entities(cfg_order):
    """Entities to show, in config order, deduped and capped."""
    out = []
    for it in cfg_order:
        eid = str(it).split('|')[0].strip()
        if eid and eid not in out:
            out.append(eid)
    return out[:12]


def _value(state, thr):
    """(text, color-flap or ''). Numeric values with a threshold get a band color."""
    st = str(state or '').lower()
    if st in _DEAD:
        return '--', ''
    if st in _ON:
        return _SHORT.get(st, st.replace('_', ' ').title())[:8], _GREEN
    if st in _OFF:
        return _SHORT.get(st, st.replace('_', ' ').title())[:8], ''
    try:
        f = float(state)
        txt = f'{round(f)}' if abs(f) >= 10 else f'{f:.1f}'
        if thr:
            lo, hi = thr
            return txt, (_RED if f > hi else _GREEN if f < lo else _AMBER)
        return txt, ''
    except (TypeError, ValueError):
        return str(state).replace('_', ' ').title()[:8], ''


def _items(settings, get_ha_states):
    """The board's rows, ready for either surface: [(name, value, flap, unit)] in config order.
    ``flap`` is the shared color classification ('' = neutral); unit comes from HA's attributes
    (the flap rows drop it for space, the panel cards have room for it)."""
    cfg, cfg_order = _parse_config(settings.get('config', ''))
    ids = _entities(cfg_order)
    states = {}
    try:
        for s in (get_ha_states() if get_ha_states else []):
            states[s.get('entity_id')] = s
    except Exception:
        states = {}
    out = []
    for eid in ids:
        s = states.get(eid, {})
        attrs = s.get('attributes') or {}
        cname, thr = cfg.get(eid, (None, None))
        name = str(cname or attrs.get('friendly_name') or eid.split('.', 1)[-1].replace('_', ' '))
        val, flap = _value(s.get('state'), thr)
        out.append((name, val, flap, str(attrs.get('unit_of_measurement') or '')))
    return out


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def _row(name, val, flap, cols):
    """`name` left, `val` right, an optional color flap after it — clamped to `cols` cells."""
    right = f'{val}{flap}' if flap else str(val)
    rw = len(right)
    if rw >= cols:
        return right[:cols]
    left = str(name)[:cols - rw - 1]
    return (left.ljust(cols - rw) + right)[:cols]


def fetch(settings, format_lines, get_rows, get_cols, get_ha_states=None):
    items = _items(settings, get_ha_states)
    if not items:
        return [format_lines('Pick entities', 'in settings')]

    cols, rows = get_cols(), max(1, get_rows())
    lines = [_row(name, val, flap, cols) for name, val, flap, _unit in items]

    pages = [format_lines(*lines[i:i + rows], align='left') for i in range(0, len(lines), rows)]
    return pages or [format_lines('No data')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The same entities as value cards on a grid: each card carries a status bar on
# its left edge (the shared green/amber/red classification), the entity name
# small above its value + unit. Cards past a screenful paginate, exactly like
# the flap rows do. Solid black between the near-black cards.
# =============================================================================

_CV_CARD = (22, 24, 28)               # the card face, barely above black
_CV_NAME = (140, 146, 156)            # entity name — quiet gray caps
_CV_VAL = (235, 238, 243)             # neutral value
_CV_UNIT = (150, 156, 166)            # the unit beside the value
_CV_BAND = {_GREEN: (75, 215, 120), _AMBER: (255, 185, 60), _RED: (255, 92, 78)}
_CV_IDLE = (70, 74, 84)               # status bar with nothing to say


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 5px)."""
    size = max(5, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 5 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_trim(font, s, max_w):
    """``s`` trimmed with an ellipsis until it fits ``max_w`` (never past empty)."""
    if font.getlength(s) <= max_w:
        return s
    while s and font.getlength(s + '…') > max_w:
        s = s[:-1]
    return (s + '…') if s else ''


def _cv_text(draw, x, y, text, font, fill):
    """Baseline-corrected text draw (y is the ink top, whatever the glyph bbox says)."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill)


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (nothing configured / no data)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.30))
    b1 = f1.getbbox(line1)
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.20)) if line2 else None
    h1 = b1[3] - b1[1]
    h2 = (f2.getbbox(line2)[3] - f2.getbbox(line2)[1]) if line2 else 0
    y = (H - (h1 + (3 if line2 else 0) + h2)) / 2.0
    _cv_text(draw, (W - f1.getlength(line1)) / 2.0, y, line1, f1, _CV_VAL)
    if line2:
        _cv_text(draw, (W - f2.getlength(line2)) / 2.0, y + h1 + 3, line2, f2, _CV_NAME)
    return img


def _cv_card_draw(canvas, draw, x, y, w, h, name, val, flap, unit):
    """One value card: status bar on the left edge, name small over value + unit."""
    draw.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=2, fill=_CV_CARD)
    draw.rectangle([x, y + 1, x + 1, y + h - 2], fill=_CV_BAND.get(flap, _CV_IDLE))
    tx = x + 5
    tw = w - 8
    name_h = max(6, int(h * 0.30))
    nf = _cv_fit(canvas, 'AG', tw, name_h)          # size by cap height, then trim to width
    ns = _cv_trim(nf, str(name).upper(), tw)
    _cv_text(draw, tx, y + 2, ns, nf, _CV_NAME)
    nh = nf.getbbox('AG')[3] - nf.getbbox('AG')[1]
    vy = y + 2 + nh + 2
    vh = y + h - 2 - vy
    vf = _cv_fit(canvas, val, tw, max(7, vh))
    vw = vf.getlength(val)
    _cv_text(draw, tx, vy, val, vf, _CV_BAND.get(flap, _CV_VAL) if flap else _CV_VAL)
    if unit and vw + 3 + nf.getlength(unit) <= tw:
        vb = vf.getbbox(val)
        ub = nf.getbbox(unit)
        _cv_text(draw, tx + vw + 3, vy + (vb[3] - vb[1]) - (ub[3] - ub[1]) - 1, unit, nf, _CV_UNIT)


def fetch_matrix(settings, canvas, get_ha_states=None):
    from PIL import ImageDraw

    items = _items(settings, get_ha_states)
    W, H = int(canvas.width), int(canvas.height)
    if not items:
        canvas.frame(_cv_message(canvas, ImageDraw, 'HOME ASSISTANT', 'Pick entities in settings'))
        return 60.0

    # Grid geometry: cards need ~64px of width and ~20px of height to breathe;
    # a small panel simply shows fewer per page and rotates.
    gcols = max(1, min(3, W // 64))
    grows = max(1, H // 20)
    # Don't reserve rows the items can never fill — the cards grow taller instead.
    grows = max(1, min(grows, (min(len(items), gcols * grows) + gcols - 1) // gcols))
    per = gcols * grows
    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'page': 0}
        setattr(fetch_matrix, '_state', st)
    pages = max(1, (len(items) + per - 1) // per)
    page = st['page'] % pages
    st['page'] = (st['page'] + 1) % pages
    show = items[page * per:(page + 1) * per]

    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    gap = 2
    cw = (W - gap * (gcols - 1)) // gcols
    ch = (H - gap * (grows - 1)) // grows
    for i, (name, val, flap, unit) in enumerate(show):
        r, c = divmod(i, gcols)
        _cv_card_draw(canvas, draw, c * (cw + gap), r * (ch + gap), cw, ch, name, val, flap, unit)
    canvas.frame(img)
    return 10.0 if pages > 1 else 15.0
