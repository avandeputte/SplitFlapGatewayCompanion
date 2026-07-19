"""HA Dashboard — a grid of Home Assistant entity cards, drawn on the panel with canvas ops.

A canvas app: it reads entity states through the injected ``get_ha_states`` helper (the
backend reaches HA via the Supervisor proxy in the add-on, or COMPANION_HA_URL/TOKEN
standalone), then draws a roundrect card per entity with a DEVICE ICON from a generated
sprite atlas, the value, and the name — coloured by state.

On-device text draws CP1252 glyphs (``_cp`` keeps what the panel can draw); sizes snap to the
bundled faces {8,9,10,13,18,20}; the shared atlas is re-uploaded every draw.
"""

import math

_MAGENTA = (255, 0, 255)
_FACES = (8, 9, 10, 13, 18, 20)
_SHADOW = (6, 7, 10)
# domain -> icon index in the atlas; anything else -> the generic dot (last tile).
_DOMAIN = {'light': 0, 'switch': 1, 'sensor': 2, 'binary_sensor': 3, 'lock': 4, 'cover': 5,
           'climate': 6, 'fan': 7, 'media_player': 8, 'person': 9}
_N_ICONS = 11
_ON = {'on', 'home', 'open', 'unlocked', 'playing', 'active', 'heat', 'cool', 'auto', 'detected'}
_OFF = {'off', 'away', 'closed', 'locked', 'idle', 'standby', 'paused', 'not_home', 'clear'}
_DEAD = {'unavailable', 'unknown', 'none', ''}
_GREEN, _GREY, _BLUE, _RED = (70, 205, 120), (120, 128, 150), (90, 160, 245), (235, 120, 110)


def _face(sz):
    ok = [s for s in _FACES if s <= sz]
    return max(ok) if ok else 8


def _cp(s):
    """Keep only CP1252-representable characters — the on-device font's charset. Degree signs
    and Latin accents pass through; anything else (the firmware can't draw it) is dropped."""
    return str(s).encode('cp1252', 'ignore').decode('cp1252')


def _txt(canvas, x, y, s, color, size, align='left'):
    s = _cp(s)
    if not s:
        return
    canvas.text(x + 1, y + 1, s, _SHADOW, size=size, align=align)
    canvas.text(x, y, s, color, size=size, align=align)


def _icons(s):
    """The device-icon atlas (on magenta), indexed by _DOMAIN; last tile is the generic dot."""
    from PIL import Image, ImageDraw
    W = max(1, int(s * 0.09))
    C = (230, 235, 245)
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
    d.rounded_rectangle([s * 0.14, s * 0.34, s * 0.86, s * 0.66], radius=int(s * 0.16), fill=(90, 190, 120))
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
    d.rounded_rectangle([s * 0.26, s * 0.44, s * 0.74, s * 0.82], radius=int(s * 0.08), fill=(210, 180, 90))
    out.append(im)

    im, d = blank()                                            # 5 cover — blinds
    for yy in (0.22, 0.40, 0.58, 0.76):
        d.line([s * 0.18, s * yy, s * 0.82, s * yy], fill=C, width=W)
    out.append(im)

    im, d = blank()                                            # 6 climate — thermometer
    d.rounded_rectangle([s * 0.42, s * 0.16, s * 0.58, s * 0.70], radius=int(s * 0.08), fill=C)
    d.ellipse([s * 0.36, s * 0.62, s * 0.64, s * 0.90], fill=(235, 110, 100))
    d.rectangle([s * 0.47, s * 0.30, s * 0.53, s * 0.74], fill=(235, 110, 100))
    out.append(im)

    im, d = blank()                                            # 7 fan — blades
    c = s / 2.0
    for a in range(3):
        ang = a * 2.09
        d.polygon([(c, c), (c + math.cos(ang) * s * 0.34, c + math.sin(ang) * s * 0.34),
                   (c + math.cos(ang + 0.6) * s * 0.30, c + math.sin(ang + 0.6) * s * 0.30)], fill=(120, 190, 240))
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
    d.ellipse([s * 0.32, s * 0.32, s * 0.68, s * 0.68], fill=(150, 160, 185))
    out.append(im)
    return out


def _entities(settings):
    raw = settings.get('entities', '')
    items = raw if isinstance(raw, list) else str(raw or '').split(',')
    out = []
    for it in items:
        eid = str(it).split('|')[0].strip()
        if eid:
            out.append(eid)
    return out[:12]


# Short, legible labels for verbose on/off-type states (a small panel has no room for "LOCKED").
_SHORT = {'unlocked': 'OPEN', 'locked': 'LOCK', 'closed': 'SHUT', 'not_home': 'AWAY',
          'detected': 'DET', 'clear': 'CLR', 'standby': 'IDLE', 'playing': 'PLAY', 'paused': 'PAUS'}


def _value(state, attrs):
    st = str(state or '').lower()
    if st in _DEAD:
        return '--', _GREY
    if st in _ON or st in _OFF:
        return _SHORT.get(st, st.upper())[:5], (_GREEN if st in _ON else _GREY)
    try:
        f = float(state)
        unit = _cp((attrs or {}).get('unit_of_measurement', '')).strip()[:2]
        return (f'{round(f)}{unit}' if abs(f) >= 10 else f'{f:.1f}{unit}'), _BLUE
    except (TypeError, ValueError):
        return _cp(state).upper()[:6], _BLUE


def fetch(settings, format_lines, get_rows, get_cols, canvas=None, get_ha_states=None):
    if canvas is None:
        return None
    W, H = canvas.width, canvas.height
    use_sprites = bool(getattr(canvas, 'can_sprite', False))
    canvas.gradient(0, 0, W, H, (16, 18, 30), (5, 6, 14), 'v')

    ids = _entities(settings)
    if not ids:
        _txt(canvas, W // 2, H // 2 - 5, 'Pick entities', (210, 216, 232), _face(min(13, H // 3)), align='center')
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
    tile = max(8, min(18, min(cw, ch) // 2)) & ~1

    if use_sprites:
        canvas.upload_atlas(_icons(tile))

    for i, eid in enumerate(ids):
        r, c = divmod(i, cols)
        x, y = c * cw, r * ch
        s = states.get(eid, {})
        domain = eid.split('.')[0]
        attrs = s.get('attributes') or {}
        name = _cp(attrs.get('friendly_name') or eid.split('.', 1)[-1].replace('_', ' '))
        val, col = _value(s.get('state'), attrs)

        canvas.roundrect(x + 1, y + 1, cw - 2, ch - 2, 3, (26, 30, 48), fill=True)
        canvas.roundrect(x + 1, y + 1, cw - 2, ch - 2, 3, col, fill=False)
        if use_sprites:
            canvas.sprite(_DOMAIN.get(domain, _N_ICONS - 1), x + 3, y + 3)

        vf = _face(min(13, ch - tile - 6)) if ch >= tile + 14 else _face(min(13, ch - 4))
        vy = y + 3 + (tile if ch >= tile + 14 else 0) + 1
        _txt(canvas, x + cw // 2, vy, val, col, vf, align='center')
        if ch >= tile + 20:
            _txt(canvas, x + cw // 2, y + ch - 9, name[:max(3, cw // 5)], (176, 184, 206), 8, align='center')

    canvas.show()
    return 12.0
