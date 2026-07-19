"""Entity Board — Home Assistant entity states as rows on the split-flap wall.

A flap app: one row per entity, its name on the left and value on the right, followed by a
colour flap that reads as a status/threshold dot — green for an "on" state, and green / amber /
red by band for a numeric sensor with thresholds. States come through the injected
``get_ha_states`` helper (the backend reaches HA via the Supervisor proxy in the add-on, or
COMPANION_HA_URL/TOKEN standalone); renames and thresholds come from the same
``entity_id | Name | low,high`` config the canvas HA Dashboard uses. Rows past a screenful
paginate onto the next page of the loop.
"""

_ON = {'on', 'home', 'open', 'unlocked', 'playing', 'active', 'heat', 'cool', 'auto', 'detected'}
_OFF = {'off', 'away', 'closed', 'locked', 'idle', 'standby', 'paused', 'not_home', 'clear'}
_DEAD = {'unavailable', 'unknown', 'none', ''}
# Friendly labels in normal case — the split-flap renderer folds to the wall's caps itself.
_SHORT = {'unlocked': 'Open', 'locked': 'Locked', 'closed': 'Closed', 'not_home': 'Away',
          'detected': 'Motion', 'clear': 'Clear', 'standby': 'Idle', 'playing': 'Play', 'paused': 'Paused'}
_GREEN, _AMBER, _RED = '\U0001f7e9', '\U0001f7e8', '\U0001f7e5'   # colour flaps: 🟩 🟨 🟥


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
    """(text, colour-flap or ''). Numeric values with a threshold get a band colour."""
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


def _row(name, val, flap, cols):
    """`name` left, `val` right, an optional colour flap after it — clamped to `cols` cells."""
    right = f'{val}{flap}' if flap else str(val)
    rw = len(right)
    if rw >= cols:
        return right[:cols]
    left = str(name)[:cols - rw - 1]
    return (left.ljust(cols - rw) + right)[:cols]


def fetch(settings, format_lines, get_rows, get_cols, get_ha_states=None):
    cfg, cfg_order = _parse_config(settings.get('config', ''))
    ids = _entities(cfg_order)
    if not ids:
        return [format_lines('Pick entities', 'in settings')]

    states = {}
    try:
        for s in (get_ha_states() if get_ha_states else []):
            states[s.get('entity_id')] = s
    except Exception:
        states = {}

    cols, rows = get_cols(), max(1, get_rows())
    lines = []
    for eid in ids:
        s = states.get(eid, {})
        attrs = s.get('attributes') or {}
        cname, thr = cfg.get(eid, (None, None))
        name = str(cname or attrs.get('friendly_name') or eid.split('.', 1)[-1].replace('_', ' '))
        val, flap = _value(s.get('state'), thr)
        lines.append(_row(name, val, flap, cols))

    pages = [format_lines(*lines[i:i + rows], align='left') for i in range(0, len(lines), rows)]
    return pages or [format_lines('No data')]
