"""Home Assistant — entity states as rows on the split-flap wall.

A flap app: one row per entity, its name on the left and value on the right, followed by a
color flap that reads as a status/threshold dot — green for an "on" state, and by
threshold for a numeric sensor: a ``lo,hi`` comfort band (green inside, red outside),
``<warn,bad`` lower-is-better or ``>warn,good`` higher-is-better (green / amber / red,
so a CO₂ card and a battery card can each be green on their own side). States come through the injected
``get_ha_states`` helper (the backend reaches HA via the Supervisor proxy in the add-on, or
COMPANION_HA_URL/TOKEN standalone); renames and thresholds come from the same
``entity_id | Name | thresholds`` config the Sensor Graph shares. Rows past a screenful
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
    """``entity_id | Name | thresholds`` per line -> {eid: (name|None, parsed-band|None)}
    + ordered ids. The thresholds grammar (band / ``<`` / ``>`` polarity): ``_parse_band``."""
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
        cfg[eid] = (name, _parse_band(parts[2] if len(parts) > 2 else ''))
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


def _parse_band(txt):
    """The threshold field, one grammar for polarity (the Sensor Graph reads the same):
    ``lo,hi`` a comfort BAND (green inside, red outside); ``<limit`` / ``<warn,bad``
    lower-is-better (green below, amber between, red above); ``>floor`` / ``>warn,good``
    higher-is-better (mirrored). Returns ('band'|'low'|'high', a, b) with a <= b, or None."""
    t = str(txt or '').strip()
    if not t:
        return None
    mode = 'band'
    if t[0] in '<>':
        mode = 'low' if t[0] == '<' else 'high'
        t = t[1:]
    try:
        nums = sorted(float(x) for x in t.split(',')[:2] if x.strip() != '')
    except ValueError:
        return None
    if not nums or (mode == 'band' and len(nums) < 2):   # a band needs both edges
        return None
    return (mode, nums[0], nums[-1])


def _band_color(thr, f):
    """'' | green | amber | red for a numeric value under a parsed threshold."""
    mode, a, b = thr
    if mode == 'band':
        return _GREEN if a <= f <= b else _RED
    if mode == 'low':                                    # lower is better
        return _GREEN if f < a else _RED if f > b or a == b else _AMBER
    return _GREEN if f > b else _RED if f < a or a == b else _AMBER   # higher is better


def _value(state, thr):
    """(text, color-flap or ''). Numeric values with a threshold get a polarity color."""
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
        return txt, (_band_color(thr, f) if thr else '')
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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# A grid of entity CARDS drawn with canvas ops: a black card per entity with a
# device icon from a generated sprite atlas, the name, and the value — the card
# border and the value colored by state, or by the per-entity thresholds. Ops +
# a persisted named atlas render on-device (firmware text, one small bind per
# draw), so the grid costs almost nothing on the wire.
# =============================================================================

import math

_MAGENTA = (255, 0, 255)
_DOMAIN = {'light': 0, 'switch': 1, 'sensor': 2, 'binary_sensor': 3, 'lock': 4, 'cover': 5,
           'climate': 6, 'fan': 7, 'media_player': 8, 'person': 9}
_N_ICONS = 11
# An LED panel is additive on black, so a color only reads as its hue when the OFF channels
# stay low (high saturation). Pale mixes like a light blue with r=110 read as tinted white — so
# these push the off channels down and keep one or two channels bright.
_C_GREEN, _C_GRAY, _C_BLUE, _C_RED, _C_AMBER = (46, 220, 90), (150, 160, 175), (48, 140, 255), (255, 60, 45), (255, 176, 0)
_MX_SHORT = {'unlocked': 'OPEN', 'locked': 'LOCK', 'closed': 'SHUT', 'not_home': 'AWAY',
          'detected': 'DET', 'clear': 'CLR', 'standby': 'IDLE', 'playing': 'PLAY', 'paused': 'PAUS'}


def _mx_value(state, attrs, thr, cp):
    """(text, color). Numeric values with a threshold color by the shared polarity
    grammar (band / lower-is-better / higher-is-better — same classes as the flap
    view's ``_band_color``). ``cp`` filters units/text to the panel's charset."""
    st = str(state or '').lower()
    if st in _DEAD:
        return '--', _C_GRAY
    if st in _ON or st in _OFF:
        return _MX_SHORT.get(st, st.upper())[:5], (_C_GREEN if st in _ON else _C_GRAY)
    try:
        f = float(state)
        unit = cp((attrs or {}).get('unit_of_measurement', '')).strip()
        unit = unit if len(unit) <= 2 else ''                  # keep °F/%/W; drop long units (the name says it)
        txt = f'{round(f)}{unit}' if abs(f) >= 10 else f'{f:.1f}{unit}'
        if thr:
            col = {_GREEN: _C_GREEN, _AMBER: _C_AMBER, _RED: _C_RED}[_band_color(thr, f)]
        else:
            col = _C_BLUE
        return txt, col
    except (TypeError, ValueError):
        return cp(state).upper()[:6], _C_BLUE


def _cv_gauge(canvas, x, y, size, state, thr, col):
    """A banded numeric entity's dial, drawn in the icon slot with the ``arc``
    op: a dim 270° track opening at the bottom, the value's sweep in the band color
    (the same green/amber/red as the value text). The gauge maps the value onto
    [lo - span/2 .. hi + span/2], so the band itself is the dial's middle half.
    Returns False for a non-numeric state — the caller falls back to the icon."""
    try:
        f = float(state)
    except (TypeError, ValueError):
        return False
    _mode, lo, hi = thr                                       # dial spans the a..b zone
    span = (hi - lo) or max(abs(hi) * 0.5, 1.0)
    frac = max(0.0, min(1.0, (f - (lo - span * 0.5)) / (span * 2.0)))
    cx, cy = x + size // 2, y + size // 2
    r = max(4, size // 2 - 1)
    th = max(2, size // 5)
    canvas.arc(cx, cy, r, -135, 135, (56, 60, 72), t=th)      # the dim track
    end = int(round(-135 + 270 * frac))
    if end > -135:                                            # skip a zero-length sweep at the floor
        canvas.arc(cx, cy, r, -135, end, col, t=th)
    return True


_ICON_C = (232, 236, 246)
# The 11 device-icon geometries in ONE fractional shape table, so a shape is described
# once and BOTH surfaces render it: the atlas bitmap (_cv_icons, via _icon_atlas) and the
# scalable vector ops (_ops_icon, via _icon_ops). Round shapes are stored as PIL-native
# bboxes (x0,y0,x1,y1) — the atlas draws them verbatim (the tiles stay byte-for-byte); the
# ops renderer converts a bbox to canvas's centre/radius form and adds 90° to arc angles
# (canvas arc's convention vs PIL's). 'cdisc' is a centre+radius disc (the fan hub, where
# the source used c±r); 'blades' is the 3-blade fan fan-out (shared trig, _fan_blades).
_ICON_ORDER = ('light', 'switch', 'sensor', 'binary_sensor', 'lock', 'cover',
               'climate', 'fan', 'media_player', 'person', 'generic')
_ICON_SHAPES = {
    'light': (('disc', 0.24, 0.14, 0.76, 0.66, (255, 214, 90)),
              ('rect', 0.38, 0.62, 0.62, 0.82, _ICON_C),
              ('line', 0.40, 0.86, 0.60, 0.86, _ICON_C)),
    'switch': (('rrect', 0.14, 0.34, 0.86, 0.66, 0.16, (90, 200, 130)),
               ('disc', 0.52, 0.36, 0.80, 0.64, (245, 250, 255))),
    'sensor': (('arc', 0.16, 0.20, 0.84, 0.88, 200, 340, _ICON_C),
               ('line', 0.5, 0.62, 0.66, 0.36, (255, 200, 90))),
    'binary_sensor': (('ring', 0.26, 0.26, 0.74, 0.74, _ICON_C),
                      ('disc', 0.42, 0.42, 0.58, 0.58, _ICON_C)),
    'lock': (('arc', 0.30, 0.16, 0.70, 0.56, 180, 360, _ICON_C),
             ('rrect', 0.26, 0.44, 0.74, 0.82, 0.08, (230, 195, 95))),
    'cover': (('line', 0.18, 0.22, 0.82, 0.22, _ICON_C),
              ('line', 0.18, 0.40, 0.82, 0.40, _ICON_C),
              ('line', 0.18, 0.58, 0.82, 0.58, _ICON_C),
              ('line', 0.18, 0.76, 0.82, 0.76, _ICON_C)),
    'climate': (('rrect', 0.42, 0.16, 0.58, 0.70, 0.08, _ICON_C),
                ('disc', 0.36, 0.62, 0.64, 0.90, (240, 120, 110)),
                ('rect', 0.47, 0.30, 0.53, 0.74, (240, 120, 110))),
    'fan': (('blades', (130, 195, 245)),
            ('cdisc', 0.5, 0.5, 0.08, _ICON_C)),
    'media_player': (('poly', ((0.36, 0.24), (0.36, 0.76), (0.76, 0.5)), _ICON_C),),
    'person': (('disc', 0.36, 0.16, 0.64, 0.44, _ICON_C),
               ('pie', 0.22, 0.50, 0.78, 1.06, 180, 360, _ICON_C)),
    'generic': (('disc', 0.32, 0.32, 0.68, 0.68, (160, 168, 190)),),
}


def _fan_blades(cx, cy, s):
    """The 3 fan-blade triangles as absolute-pixel vertex lists about hub (cx, cy) — shared
    by the atlas bitmap and the vector ops so the blades match exactly."""
    out = []
    for a in range(3):
        ang = a * 2.09
        out.append([(cx, cy),
                    (cx + math.cos(ang) * s * 0.34, cy + math.sin(ang) * s * 0.34),
                    (cx + math.cos(ang + 0.6) * s * 0.30, cy + math.sin(ang + 0.6) * s * 0.30)])
    return out


def _icon_atlas(d, prim, s, w):
    """Draw one _ICON_SHAPES primitive onto a magenta atlas tile via PIL — the exact bboxes/
    endpoints the hand-rolled tiles used, so _cv_icons stays byte-for-byte identical."""
    kind = prim[0]
    if kind in ('disc', 'ring'):
        _, x0, y0, x1, y1, col = prim
        d.ellipse([s * x0, s * y0, s * x1, s * y1],
                  **({'fill': col} if kind == 'disc' else {'outline': col, 'width': w}))
    elif kind == 'cdisc':
        _, cx, cy, r, col = prim
        d.ellipse([s * cx - s * r, s * cy - s * r, s * cx + s * r, s * cy + s * r], fill=col)
    elif kind == 'rect':
        _, x0, y0, x1, y1, col = prim
        d.rectangle([s * x0, s * y0, s * x1, s * y1], fill=col)
    elif kind == 'rrect':
        _, x0, y0, x1, y1, rad, col = prim
        d.rounded_rectangle([s * x0, s * y0, s * x1, s * y1], radius=int(s * rad), fill=col)
    elif kind == 'line':
        _, x0, y0, x1, y1, col = prim
        d.line([s * x0, s * y0, s * x1, s * y1], fill=col, width=w)
    elif kind == 'poly':
        _, pts, col = prim
        d.polygon([(s * px, s * py) for px, py in pts], fill=col)
    elif kind == 'arc':
        _, x0, y0, x1, y1, a0, a1, col = prim
        d.arc([s * x0, s * y0, s * x1, s * y1], a0, a1, fill=col, width=w)
    elif kind == 'pie':
        _, x0, y0, x1, y1, a0, a1, col = prim
        d.pieslice([s * x0, s * y0, s * x1, s * y1], a0, a1, fill=col)
    elif kind == 'blades':
        for blade in _fan_blades(s * 0.5, s * 0.5, s):
            d.polygon(blade, fill=prim[1])


def _icon_ops(canvas, prim, x, y, s, t, aa):
    """Draw one _ICON_SHAPES primitive as scalable vector ops at (x, y) — a bbox becomes
    canvas's centre/radius; arc angles get +90 (canvas's convention vs PIL's)."""
    kind = prim[0]
    if kind in ('disc', 'ring'):
        _, x0, y0, x1, y1, col = prim
        canvas.circle(x + s * (x0 + x1) / 2, y + s * (y0 + y1) / 2, s * (x1 - x0) / 2,
                      col, fill=(kind == 'disc'), t=t, aa=aa)
    elif kind == 'cdisc':
        _, cx, cy, r, col = prim
        canvas.circle(x + s * cx, y + s * cy, s * r, col, fill=True, aa=aa)
    elif kind == 'rect':
        _, x0, y0, x1, y1, col = prim
        canvas.rect(x + s * x0, y + s * y0, s * (x1 - x0), s * (y1 - y0), col, fill=True)
    elif kind == 'rrect':
        _, x0, y0, x1, y1, rad, col = prim
        canvas.roundrect(x + s * x0, y + s * y0, s * (x1 - x0), s * (y1 - y0),
                         int(s * rad), col, fill=True)
    elif kind == 'line':
        _, x0, y0, x1, y1, col = prim
        canvas.line(x + s * x0, y + s * y0, x + s * x1, y + s * y1, col, t=t)
    elif kind == 'poly':
        _, pts, col = prim
        canvas.poly([(x + s * px, y + s * py) for px, py in pts], col, fill=True)
    elif kind in ('arc', 'pie'):
        _, x0, y0, x1, y1, a0, a1, col = prim
        canvas.arc(x + s * (x0 + x1) / 2, y + s * (y0 + y1) / 2, int(s * (x1 - x0) / 2),
                   a0 + 90, a1 + 90, col, t=t, fill=(kind == 'pie'))
    elif kind == 'blades':
        for blade in _fan_blades(x + s * 0.5, y + s * 0.5, s):
            canvas.poly(blade, prim[1], fill=True)


def _cv_icons(s):
    """The device-icon atlas (on magenta), one tile per _ICON_ORDER entry (indexed by
    _DOMAIN; last tile is the generic dot). Each tile is drawn from the shared _ICON_SHAPES
    table via _icon_atlas — the same geometry the vector ops render at native scale."""
    from PIL import Image, ImageDraw
    w = max(1, int(s * 0.09))
    out = []
    for domain in _ICON_ORDER:
        im = Image.new('RGB', (s, s), _MAGENTA)
        d = ImageDraw.Draw(im)
        for prim in _ICON_SHAPES[domain]:
            _icon_atlas(d, prim, s, w)
        out.append(im)
    return out


def _ops_icon(canvas, domain, x, y, s, aa):
    """The device icon drawn as vector ops at ``s`` px — the scalable twin of the _cv_icons
    atlas tile for the same domain, from the shared _ICON_SHAPES table so both surfaces show
    the same shapes and colors. (Atlas tiles are 8-16px bitmaps; blown up x5 on the LCD they
    would be exactly the pixelation this path exists to avoid.)"""
    t = max(1, int(s * 0.09))
    for prim in _ICON_SHAPES.get(domain, _ICON_SHAPES['generic']):
        _icon_ops(canvas, prim, x, y, s, t, aa)


def _cv_board_ops(canvas, settings, get_ha_states, W, H):
    """The card grid as on-device DRAW OPS — the gtext twin of the sprite path below, for
    the LCD (manifest ``lcd_ops``): AA vector icons, gauge dials and scalable-text values
    rendered by the wall at its native resolution instead of a 256x160 pixel frame upscaled
    x5. Same grid math, same card idiom — black cards, state-colored borders and values."""
    aa = bool(getattr(canvas, 'aa_ok', False))
    canvas.clear((0, 0, 0))

    cfg, cfg_order = _parse_config(settings.get('config', ''))
    ids = _entities(cfg_order)
    if not ids:
        size = canvas.fit_gtext('Pick entities', int(W * 0.8), int(H * 0.14))
        canvas.gtext(W // 2, H / 2, 'Pick entities', color=(210, 216, 232),
                     size=size, align='center', valign='ink-center')
        canvas.show()
        return 30.0

    states = {}
    try:
        for s in (get_ha_states() if get_ha_states else []):
            states[s.get('entity_id')] = s
    except Exception:
        states = {}

    n = len(ids)
    cols = canvas.num(settings, 'columns', 0)
    if cols < 1:
        cols = 1 if n == 1 else 2 if n <= 4 else 3 if n <= 9 else 4
    cols = max(1, min(6, cols))
    rows = max(1, math.ceil(n / cols))
    # Both grid edges land on the panel's edges (remainders spread by rounding), like the
    # sprite path's row bands — no dead rows or columns anywhere.
    redges = [round(r * H / rows) for r in range(rows + 1)]
    cedges = [round(c * W / cols) for c in range(cols + 1)]
    floor = max(9, int(H * 0.03))            # the readable-size floor, scaled to the wall

    for i, eid in enumerate(ids):
        r, c = divmod(i, cols)
        x = cedges[c] if c == 0 else cedges[c] + 1
        y = redges[r] if r == 0 else redges[r] + 1
        cw = cedges[c + 1] - x
        card_h = redges[r + 1] - y
        s = states.get(eid, {})
        domain = eid.split('.')[0]
        attrs = s.get('attributes') or {}
        cname, thr = cfg.get(eid, (None, None))
        name = str(cname or attrs.get('friendly_name') or eid.split('.', 1)[-1].replace('_', ' '))
        val, col = _mx_value(s.get('state'), attrs, thr, canvas.cp)

        rr = max(3, int(min(cw, card_h) * 0.05))
        for k in range(max(1, int(min(cw, card_h) * 0.012))):  # a border with weight at 800p
            canvas.roundrect(x + k, y + k, cw - 2 * k, card_h - 2 * k, max(2, rr - k),
                             col, fill=False)

        pad = max(3, int(min(cw, card_h) * 0.06))
        name_h = int(card_h * 0.26)
        top_h = card_h - name_h                                # icon + value share the top band
        tile = int(min(cw * 0.30, top_h * 0.58))
        iy = y + max(pad, (top_h - tile) // 2)
        # A thresholded numeric entity gets the live dial where its icon would sit — the
        # same _cv_gauge the sprite path draws, just at native scale.
        drew = (thr is not None and canvas.has_op('arc')
                and _cv_gauge(canvas, x + pad, iy, tile, s.get('state'), thr, col))
        if not drew:
            _ops_icon(canvas, domain, x + pad, iy, tile, aa)
        vx0 = x + pad + tile + pad
        slot_w = (x + cw - pad) - vx0
        vsize = canvas.fit_gtext(val, slot_w, int(top_h * 0.60))
        if vsize < floor:
            # The unit down to the bare number ("72" beats "72°F" crushed small), the
            # same last resort the sprite path takes.
            cut = next((k for k, ch in enumerate(val) if not (ch.isdigit() or ch in '-.')), len(val))
            if cut and val[:cut] != val:
                val = val[:cut]
                vsize = canvas.fit_gtext(val, slot_w, int(top_h * 0.60))
        canvas.gtext((vx0 + x + cw - pad) / 2, y + top_h / 2, val,
                     color=col, size=vsize, align='center', valign='ink-center')
        nsize = canvas.fit_gtext(name, cw - 2 * pad, int(name_h * 0.55))
        nm = name if nsize >= floor else canvas.ellipsize(name, floor, cw - 2 * pad)
        canvas.gtext(x + cw / 2, y + top_h + name_h / 2, nm,
                     color=(222, 228, 242), size=max(nsize, floor), align='center', valign='ink-center')
    canvas.show()
    return 12.0


def fetch_canvas(settings, canvas, get_ha_states=None):
    W, H = canvas.width, canvas.height
    if getattr(canvas, 'can_gtext', False) and H >= 96:
        # The big-panel path: live ops at native resolution (crisp dials + TTF values).
        return _cv_board_ops(canvas, settings, get_ha_states, W, H)
    use_sprites = bool(getattr(canvas, 'can_sprite', False))
    canvas.clear((0, 0, 0))                                    # black — best contrast on the panel

    cfg, cfg_order = _parse_config(settings.get('config', ''))
    ids = _entities(cfg_order)
    if not ids:
        canvas.shadow_text(W // 2, H // 2 - 5, 'Pick entities', (210, 216, 232), canvas.face(min(13, H // 3)), align='center')
        canvas.show()
        return 30.0

    states = {}
    try:
        for s in (get_ha_states() if get_ha_states else []):
            states[s.get('entity_id')] = s
    except Exception:
        states = {}

    n = len(ids)
    cols = canvas.num(settings, 'columns', 0)
    if cols < 1:
        cols = 1 if n == 1 else 2 if n <= 4 else 3 if n <= 9 else 4
    cols = max(1, min(6, cols))
    rows = max(1, math.ceil(n / cols))
    cw = W // cols
    # Row bands span the FULL panel height (the remainder spread by rounding):
    # the first card's border sits on row 0, the last on row H-1, one dark row
    # between bands — no dead rows above or below the grid.
    redges = [round(r * H / rows) for r in range(rows + 1)]
    ch = min(redges[r + 1] - redges[r] for r in range(rows))
    tile = max(8, min(16, min(cw, ch) // 2)) & ~1
    show_name = ch >= tile + 12
    # A card narrower than ~28px can't fit an icon AND a readable value — the value wins,
    # the icon sits out (a tiny 64x32 wall with six entities is values-only).
    if cw < 28:
        use_sprites = False

    if use_sprites:
        canvas.upload_atlas(_cv_icons(tile), persist=True)

    for i, eid in enumerate(ids):
        r, c = divmod(i, cols)
        x = c * cw
        y = redges[r] if r == 0 else redges[r] + 1       # border on row 0 up top…
        card_h = redges[r + 1] - y                       # …and on H-1 down below
        s = states.get(eid, {})
        domain = eid.split('.')[0]
        attrs = s.get('attributes') or {}
        cname, thr = cfg.get(eid, (None, None))
        name = canvas.cp(cname or attrs.get('friendly_name') or eid.split('.', 1)[-1].replace('_', ' '))
        val, col = _mx_value(s.get('state'), attrs, thr, canvas.cp)

        canvas.roundrect(x + 1, y, cw - 2, card_h, 3, col, fill=False)       # black card, colored border
        top_h = card_h - (10 if show_name else 0)                            # icon + value share the top band
        vx0 = x + 3
        if use_sprites:
            iy = y + max(2, (top_h - tile) // 2)
            # A thresholded numeric entity gets a live dial where its icon would sit —
            # the arc op (has_op gate); walls without it (and non-numeric states) keep the icon.
            drew = (thr is not None and canvas.has_op('arc')
                    and _cv_gauge(canvas, x + 3, iy, tile, s.get('state'), thr, col))
            if not drew:
                canvas.sprite(_DOMAIN.get(domain, _N_ICONS - 1), x + 3, iy)
            vx0 = x + 3 + tile + 2
        slot_w = (x + cw - 3) - vx0
        vf = canvas.fit(val, slot_w, top_h - 3)          # fit the value in the space right of the icon
        if len(val) * canvas.face_width(vf) > slot_w:
            # Even the smallest face would clip against the card border: strip
            # the unit down to the bare number ("72" beats "72°F" bleeding into
            # the edge), then truncate as the last resort.
            cut = next((k for k, ch in enumerate(val) if not (ch.isdigit() or ch in '-.')), len(val))
            if cut and val[:cut] != val:
                val = val[:cut]
                vf = canvas.fit(val, slot_w, top_h - 3)
            while len(val) > 1 and len(val) * canvas.face_width(vf) > slot_w:
                val = val[:-1]
        canvas.shadow_text((vx0 + x + cw - 3) // 2, y + max(2, (top_h - vf) // 2), val, col, vf, align='center')
        if show_name:
            canvas.shadow_text(x + cw // 2, y + card_h - 10, name[:max(4, (cw - 4) // 5)], (222, 228, 242), 8, align='center')

    canvas.show()
    return 12.0
