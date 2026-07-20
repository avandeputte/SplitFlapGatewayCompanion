"""HA Dashboard — a grid of Home Assistant entity cards, drawn on the panel with canvas ops.

A canvas app: it reads entity states through the injected ``get_ha_states`` helper (the
backend reaches HA via the Supervisor proxy in the add-on, or COMPANION_HA_URL/TOKEN
standalone), then draws a black card per entity with a DEVICE ICON from a generated sprite
atlas, a name, and the value — the card border and the value coloured by state, or by
per-entity thresholds for numeric sensors.

Bright content on BLACK reads best on an LED panel. Text goes through the injected ``canvas``
helpers — ``canvas.shadow_text`` (drop-shadow + CP1252 filter), ``canvas.face``/``canvas.fit``
(snap to the bundled faces {8,9,10,13,18,20}); the shared atlas is re-uploaded every draw.
"""

import math

_MAGENTA = (255, 0, 255)
_DOMAIN = {'light': 0, 'switch': 1, 'sensor': 2, 'binary_sensor': 3, 'lock': 4, 'cover': 5,
           'climate': 6, 'fan': 7, 'media_player': 8, 'person': 9}
_N_ICONS = 11
_ON = {'on', 'home', 'open', 'unlocked', 'playing', 'active', 'heat', 'cool', 'auto', 'detected'}
_OFF = {'off', 'away', 'closed', 'locked', 'idle', 'standby', 'paused', 'not_home', 'clear'}
_DEAD = {'unavailable', 'unknown', 'none', ''}
# An LED panel is additive on black, so a colour only reads as its hue when the OFF channels
# stay low (high saturation). Pale mixes like a light blue with r=110 read as tinted white — so
# these push the off channels down and keep one or two channels bright.
_GREEN, _GREY, _BLUE, _RED, _AMBER = (46, 220, 90), (150, 160, 175), (48, 140, 255), (255, 60, 45), (255, 176, 0)
_SHORT = {'unlocked': 'OPEN', 'locked': 'LOCK', 'closed': 'SHUT', 'not_home': 'AWAY',
          'detected': 'DET', 'clear': 'CLR', 'standby': 'IDLE', 'playing': 'PLAY', 'paused': 'PAUS'}


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


def _value(state, attrs, thr, cp):
    """(text, colour). Numeric values with a threshold colour green/amber/red by band.
    ``cp`` filters units/text to the panel's charset (pass ``canvas.cp``)."""
    st = str(state or '').lower()
    if st in _DEAD:
        return '--', _GREY
    if st in _ON or st in _OFF:
        return _SHORT.get(st, st.upper())[:5], (_GREEN if st in _ON else _GREY)
    try:
        f = float(state)
        unit = cp((attrs or {}).get('unit_of_measurement', '')).strip()
        unit = unit if len(unit) <= 2 else ''                  # keep °F/%/W; drop long units (the name says it)
        txt = f'{round(f)}{unit}' if abs(f) >= 10 else f'{f:.1f}{unit}'
        if thr:
            lo, hi = thr
            col = _RED if f > hi else _GREEN if f < lo else _AMBER
        else:
            col = _BLUE
        return txt, col
    except (TypeError, ValueError):
        return cp(state).upper()[:6], _BLUE


def _icons(s):
    """The device-icon atlas (on magenta), indexed by _DOMAIN; last tile is the generic dot."""
    from PIL import Image, ImageDraw
    W = max(1, int(s * 0.09))
    C = (232, 236, 246)
    out = []

    def blank():
        im = Image.new('RGB', (s, s), _MAGENTA)
        return im, ImageDraw.Draw(im)

    im, d = blank()                                            # 0 light — bulb
    d.ellipse([s * 0.24, s * 0.14, s * 0.76, s * 0.66], fill=(255, 214, 90))
    d.rectangle([s * 0.38, s * 0.62, s * 0.62, s * 0.82], fill=C)
    d.line([s * 0.40, s * 0.86, s * 0.60, s * 0.86], fill=C, width=W)
    out.append(im)

    im, d = blank()                                            # 1 switch — toggle
    d.rounded_rectangle([s * 0.14, s * 0.34, s * 0.86, s * 0.66], radius=int(s * 0.16), fill=(90, 200, 130))
    d.ellipse([s * 0.52, s * 0.36, s * 0.52 + s * 0.28, s * 0.36 + s * 0.28], fill=(245, 250, 255))
    out.append(im)

    im, d = blank()                                            # 2 sensor — dial
    d.arc([s * 0.16, s * 0.20, s * 0.84, s * 0.88], 200, 340, fill=C, width=W)
    d.line([s * 0.5, s * 0.62, s * 0.66, s * 0.36], fill=(255, 200, 90), width=W)
    out.append(im)

    im, d = blank()                                            # 3 binary_sensor — ring
    d.ellipse([s * 0.26, s * 0.26, s * 0.74, s * 0.74], outline=C, width=W)
    d.ellipse([s * 0.42, s * 0.42, s * 0.58, s * 0.58], fill=C)
    out.append(im)

    im, d = blank()                                            # 4 lock — padlock
    d.arc([s * 0.30, s * 0.16, s * 0.70, s * 0.56], 180, 360, fill=C, width=W)
    d.rounded_rectangle([s * 0.26, s * 0.44, s * 0.74, s * 0.82], radius=int(s * 0.08), fill=(230, 195, 95))
    out.append(im)

    im, d = blank()                                            # 5 cover — blinds
    for yy in (0.22, 0.40, 0.58, 0.76):
        d.line([s * 0.18, s * yy, s * 0.82, s * yy], fill=C, width=W)
    out.append(im)

    im, d = blank()                                            # 6 climate — thermometer
    d.rounded_rectangle([s * 0.42, s * 0.16, s * 0.58, s * 0.70], radius=int(s * 0.08), fill=C)
    d.ellipse([s * 0.36, s * 0.62, s * 0.64, s * 0.90], fill=(240, 120, 110))
    d.rectangle([s * 0.47, s * 0.30, s * 0.53, s * 0.74], fill=(240, 120, 110))
    out.append(im)

    im, d = blank()                                            # 7 fan — blades
    c = s / 2.0
    for a in range(3):
        ang = a * 2.09
        d.polygon([(c, c), (c + math.cos(ang) * s * 0.34, c + math.sin(ang) * s * 0.34),
                   (c + math.cos(ang + 0.6) * s * 0.30, c + math.sin(ang + 0.6) * s * 0.30)], fill=(130, 195, 245))
    d.ellipse([c - s * 0.08, c - s * 0.08, c + s * 0.08, c + s * 0.08], fill=C)
    out.append(im)

    im, d = blank()                                            # 8 media_player — play
    d.polygon([(s * 0.36, s * 0.24), (s * 0.36, s * 0.76), (s * 0.76, s * 0.5)], fill=C)
    out.append(im)

    im, d = blank()                                            # 9 person
    d.ellipse([s * 0.36, s * 0.16, s * 0.64, s * 0.44], fill=C)
    d.pieslice([s * 0.22, s * 0.50, s * 0.78, s * 1.06], 180, 360, fill=C)
    out.append(im)

    im, d = blank()                                            # 10 generic — dot
    d.ellipse([s * 0.32, s * 0.32, s * 0.68, s * 0.68], fill=(160, 168, 190))
    out.append(im)
    return out


def fetch(settings, format_lines, get_rows, get_cols, canvas=None, get_ha_states=None):
    if canvas is None:
        return None
    W, H = canvas.width, canvas.height
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
    try:
        cols = int(float(settings.get('columns', 0) or 0))
    except (TypeError, ValueError):
        cols = 0
    if cols < 1:
        cols = 1 if n == 1 else 2 if n <= 4 else 3 if n <= 9 else 4
    cols = max(1, min(6, cols))
    rows = max(1, math.ceil(n / cols))
    cw, ch = W // cols, H // rows
    tile = max(8, min(16, min(cw, ch) // 2)) & ~1
    show_name = ch >= tile + 12

    if use_sprites:
        canvas.upload_atlas(_icons(tile))

    for i, eid in enumerate(ids):
        r, c = divmod(i, cols)
        x, y = c * cw, r * ch
        s = states.get(eid, {})
        domain = eid.split('.')[0]
        attrs = s.get('attributes') or {}
        cname, thr = cfg.get(eid, (None, None))
        name = canvas.cp(cname or attrs.get('friendly_name') or eid.split('.', 1)[-1].replace('_', ' '))
        val, col = _value(s.get('state'), attrs, thr, canvas.cp)

        canvas.roundrect(x + 1, y + 1, cw - 2, ch - 2, 3, col, fill=False)   # black card, coloured border
        top_h = ch - (10 if show_name else 0)                                # icon + value share the top band
        vx0 = x + 3
        if use_sprites:
            canvas.sprite(_DOMAIN.get(domain, _N_ICONS - 1), x + 3, y + max(2, (top_h - tile) // 2))
            vx0 = x + 3 + tile + 2
        vf = canvas.fit(val, (x + cw - 3) - vx0, top_h - 3)          # fit the value in the space right of the icon
        canvas.shadow_text((vx0 + x + cw - 3) // 2, y + max(2, (top_h - vf) // 2), val, col, vf, align='center')
        if show_name:
            canvas.shadow_text(x + cw // 2, y + ch - 11, name[:max(4, (cw - 4) // 5)], (222, 228, 242), 8, align='center')

    canvas.show()
    return 12.0
