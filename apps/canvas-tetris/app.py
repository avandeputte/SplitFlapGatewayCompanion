"""Tetris — horizontal, for a wide LED panel.

Classic Tetris turned on its side: gravity pulls LEFT, pieces enter from the right and
pile against the left wall, and a full COLUMN (every row filled) clears and pulls the rest
leftward. It plays itself in attract mode and hands over to a player on the web-UI control
pad — up/down move the piece, right rotates, left is a soft drop — with tones on the
gateway speaker. Every frame is one small batch of on-device draw ops, streamed as binary
ops for game-rate latency; game over fades the well to black behind a GAME OVER + score
overlay, and any key starts a new game.
"""

import random

# The 7 tetrominoes as (row, col) offsets — row is the short (vertical) axis, col runs
# along gravity (toward 0 = left). Colors are the classic set, high-saturation so they
# read as their hue on an additive LED panel.
_SHAPES = {
    'I': [(0, 0), (0, 1), (0, 2), (0, 3)],
    'O': [(0, 0), (0, 1), (1, 0), (1, 1)],
    'T': [(0, 0), (0, 1), (0, 2), (1, 1)],
    'S': [(0, 1), (0, 2), (1, 0), (1, 1)],
    'Z': [(0, 0), (0, 1), (1, 1), (1, 2)],
    'J': [(0, 0), (1, 0), (1, 1), (1, 2)],
    'L': [(0, 2), (1, 0), (1, 1), (1, 2)],
}
_COLORS = {'I': (0, 232, 232), 'O': (240, 216, 40), 'T': (180, 72, 224),
           'S': (64, 212, 88), 'Z': (240, 64, 56), 'J': (64, 116, 240), 'L': (240, 150, 40)}
_KINDS = list(_SHAPES)
_FADE_STEPS = 16


def _rot(cells):
    """The offsets rotated 90° clockwise, normalized back to non-negative coordinates."""
    turned = [(c, -r) for r, c in cells]
    mr = min(r for r, _ in turned)
    mc = min(c for _, c in turned)
    return [(r - mr, c - mc) for r, c in turned]


def _rotations(kind):
    outs = [_SHAPES[kind]]
    for _ in range(3):
        outs.append(_rot(outs[-1]))
    return outs


_ROTS = {k: _rotations(k) for k in _KINDS}


def _cells(kind, rot, r, c):
    return [(r + dr, c + dc) for dr, dc in _ROTS[kind][rot % 4]]


def _fits(st, kind, rot, r, c):
    for rr, cc in _cells(kind, rot, r, c):
        if not (0 <= rr < st['rows'] and 0 <= cc < st['cols']):
            return False
        if st['grid'][rr][cc] is not None:
            return False
    return True


def _move(st, dr, dc):
    p = st['piece']
    if _fits(st, p['kind'], p['rot'], p['r'] + dr, p['c'] + dc):
        p['r'] += dr
        p['c'] += dc
        return True
    return False


def _rotate(st):
    p = st['piece']
    nr = (p['rot'] + 1) % 4
    for kick in (0, -1, 1, -2, 2):                     # simple vertical wall-kicks
        if _fits(st, p['kind'], nr, p['r'] + kick, p['c']):
            p['rot'], p['r'] = nr, p['r'] + kick
            return True
    return False


def _bag_next(st):
    """The next kind from a shuffled 7-bag (every piece once before any repeats)."""
    if not st['bag']:
        st['bag'] = list(_KINDS)
        random.Random(st['score'] * 7 + st['lines'] * 13 + st['level']).shuffle(st['bag'])
    return st['bag'].pop()


def _spawn(st):
    kind = _bag_next(st)
    cells = _ROTS[kind][0]
    h = max(r for r, _ in cells) + 1
    w = max(c for _, c in cells) + 1
    r0, c0 = (st['rows'] - h) // 2, st['cols'] - w      # enter flush against the right edge
    st['piece'] = {'kind': kind, 'rot': 0, 'r': r0, 'c': c0}
    st['plan'] = None
    if not _fits(st, kind, 0, r0, c0):                 # no room to enter — the well is full
        st['phase'], st['fade'] = 'gameover', 0


def _clear(st):
    """Remove every full column and pull the rest leftward (gravity). Returns the count."""
    rows, cols = st['rows'], st['cols']
    full = {c for c in range(cols) if all(st['grid'][r][c] is not None for r in range(rows))}
    if not full:
        return 0
    keep = [c for c in range(cols) if c not in full]
    for r in range(rows):
        st['grid'][r] = [st['grid'][r][c] for c in keep] + [None] * len(full)
    return len(full)


def _lock(st):
    p = st['piece']
    col = _COLORS[p['kind']]
    for rr, cc in _cells(p['kind'], p['rot'], p['r'], p['c']):
        st['grid'][rr][cc] = col
    cleared = _clear(st)
    if cleared:
        st['lines'] += cleared
        st['score'] += (0, 100, 300, 500, 800)[min(cleared, 4)] * st['level']
        st['level'] = 1 + st['lines'] // 10
        st['sfx'].append('tetris' if cleared >= 4 else 'clear')
    else:
        st['sfx'].append('lock')
    _spawn(st)


def _new_game(st):
    cols, rows = st['cols'], st['rows']
    st.update(grid=[[None] * cols for _ in range(rows)], score=0, lines=0, level=1,
              grav=0, phase='play', fade=0, freeze_presses=None, paused=False,
              bag=[], sfx=[])
    _spawn(st)


def _state(cols, rows):
    st = getattr(_state, '_st', None)
    if st is None or st['cols'] != cols or st['rows'] != rows:
        st = _state._st = {'cols': cols, 'rows': rows}
        _new_game(st)
    return st


# ===== ATTRACT AI — a light greedy player so the demo looks like a game ==========

def _eval_board(grid, rows, cols):
    """Score a settled board (higher is better). ``reach[r]`` is how far right row r's pile
    extends; a FLAT VERTICAL face (every row equally deep) completes columns, so reward a
    high floor (``min(reach)``) and punish the deepest row, holes, and raggedness. A hole is
    an empty cell covered from the right (a leftward-falling piece can't reach it)."""
    holes = 0
    reach = [0] * rows
    for r in range(rows):
        last = -1
        for c in range(cols):
            if grid[r][c] is not None:
                last = c
        reach[r] = last + 1
        seen = False
        for c in range(cols - 1, -1, -1):              # right → left (anti-gravity → gravity)
            if grid[r][c] is not None:
                seen = True
            elif seen:
                holes += 1
    bump = sum(abs(reach[r] - reach[r + 1]) for r in range(rows - 1))
    return min(reach) * 4.0 - max(reach) * 3.0 - holes * 12.0 - bump * 0.6


def _plan(st):
    """The best (rotation, row) for the current piece — simulate every landing (place,
    clear full columns) and score the resulting board."""
    kind = st['piece']['kind']
    rows, cols = st['rows'], st['cols']
    rng = random.Random(st['score'] * 3 + st['lines'] + st['level'])
    best, best_s = (0, (rows - 1) // 2), -1e18
    for rot in range(4):
        cells = _ROTS[kind][rot]
        h = max(r for r, _ in cells) + 1
        c0 = cols - (max(c for _, c in cells) + 1)
        for r0 in range(rows - h + 1):
            if not _fits(st, kind, rot, r0, c0):
                continue
            c = c0
            while _fits(st, kind, rot, r0, c - 1):     # slide left to the landing column
                c -= 1
            g = [row[:] for row in st['grid']]
            for dr, dc in cells:
                g[r0 + dr][c + dc] = 1
            full = [cc for cc in range(cols) if all(g[r][cc] is not None for r in range(rows))]
            if full:                                   # apply the clear before scoring
                fs = set(full)
                keep = [cc for cc in range(cols) if cc not in fs]
                for r in range(rows):
                    g[r] = [g[r][cc] for cc in keep] + [None] * len(full)
            s = _eval_board(g, rows, cols) + len(full) * 220 + rng.random()
            if s > best_s:
                best_s, best = s, (rot, r0)
    return best


def _attract(st):
    """One tick of the demo: snap the piece to its planned orientation/row, then glide left
    a cell at a time so the placement is visible, locking when it can go no further."""
    p = st['piece']
    if st.get('plan') is None:
        st['plan'] = _plan(st)
    tr, trow = st['plan']
    for _ in range(4):
        if p['rot'] == tr:
            break
        if not _rotate(st):
            break
    for _ in range(st['rows']):
        if p['r'] == trow or not _move(st, 1 if p['r'] < trow else -1, 0):
            break
    for _ in range(5):                                 # glide left fast so the demo keeps moving
        if not _move(st, 0, -1):
            _lock(st)
            return


# ===== RENDER ===================================================================

def _block(canvas, x, y, cell, col):
    """One settled/active cell — filled with a 1px gridline gap, plus a light top-left
    edge for a tiled look on the bigger cells."""
    canvas.rect(x, y, cell - 1, cell - 1, col, fill=True)
    if cell >= 5:
        hi = tuple(min(255, int(v * 1.3) + 24) for v in col)
        canvas.rect(x, y, cell - 1, 1, hi, fill=True)
        canvas.rect(x, y, 1, cell - 1, hi, fill=True)


def _draw_board(canvas, st, gx, gy, cell, dim=1.0):
    def d(c):
        return canvas.dim(c, dim) if dim < 1.0 else c

    canvas.clear((0, 0, 0))
    for r in range(st['rows']):
        row = st['grid'][r]
        for c in range(st['cols']):
            if row[c] is not None:
                _block(canvas, gx + c * cell, gy + r * cell, cell, d(row[c]))
    if st.get('phase') != 'gameover':
        p = st['piece']
        col = d(_COLORS[p['kind']])
        for rr, cc in _cells(p['kind'], p['rot'], p['r'], p['c']):
            _block(canvas, gx + cc * cell, gy + rr * cell, cell, col)
    if canvas.width >= 128:
        canvas.shadow_text(canvas.width - 2, 0, str(st['score']), d((255, 255, 255)), 8, align="right")


def _draw_gameover(canvas, W, H, score, appear):
    a = max(0.0, min(1.0, appear))
    go, sc = canvas.dim((120, 200, 255), a), canvas.dim((235, 235, 245), a)
    f1 = canvas.fit("GAME OVER", W - 4, max(8, H // 3))
    txt = "SCORE " + str(score)
    f2 = canvas.fit(txt, W - 4, max(8, H // 4))
    y0 = (H - (f1 + 2 + f2)) // 2
    canvas.shadow_text(W // 2, y0, "GAME OVER", go, f1, align="center")
    canvas.shadow_text(W // 2, y0 + f1 + 2, txt, sc, f2, align="center")


def _play_sfx(play_sound, sfx):
    ev = next((e for e in ('tetris', 'clear', 'lock') if e in sfx), None)
    if ev == 'tetris':
        play_sound(notes=[[523, 80], [659, 80], [784, 80], [1046, 200]], vol=72)
    elif ev == 'clear':
        play_sound(notes=[[659, 90], [988, 150]], vol=66)
    elif ev == 'lock':
        play_sound(notes=[[300, 35]], vol=32)


def _grav_period(st, settings):
    try:
        speed = max(1, min(10, int(float(settings.get('speed', 5) or 5))))
    except (TypeError, ValueError):
        speed = 5
    return max(2, 13 - st['level'] - speed)            # frames per left-step; faster each level


def fetch_matrix(settings, canvas, controls=None, play_sound=None):
    W, H = canvas.width, canvas.height
    cell = max(4, H // 8)                               # ~8 rows tall on any panel
    cols = max(12, W // cell)
    rows = max(6, H // cell)
    gx, gy = (W - cols * cell) // 2, (H - rows * cell) // 2
    st = _state(cols, rows)

    playing = controls is not None and controls.active()
    events = list(controls.events) if controls is not None else []
    taps = list(controls.taps) if controls is not None else []
    presses = controls.presses if controls is not None else 0

    if not playing and st.get('phase', 'play') != 'play':
        _new_game(st)                                  # attract resets a finished game
    if playing and ('start' in events or 'coin' in events):
        _new_game(st)

    if playing and st.get('phase') == 'gameover':
        if st.get('freeze_presses') is None:
            st['freeze_presses'] = presses
        st['fade'] = min(st.get('fade', 0) + 1, _FADE_STEPS)
        if presses > st['freeze_presses']:             # any key → new game
            _new_game(st)
        else:
            _draw_board(canvas, st, gx, gy, cell, dim=max(0.0, 1 - st['fade'] / _FADE_STEPS))
            _draw_gameover(canvas, W, H, st['score'], st['fade'] / (_FADE_STEPS * 0.5))
            canvas.show()
            return 0.08

    if playing and 'pause' in events:
        st['paused'] = not st.get('paused', False)

    if playing and not st.get('paused'):
        for tap in taps:                               # discrete per-press moves
            if tap == 'up':
                _move(st, -1, 0)
            elif tap == 'down':
                _move(st, 1, 0)
            elif tap == 'right':
                _rotate(st)
            elif tap == 'left':
                if not _move(st, 0, -1):               # soft drop; lock if it can't go left
                    _lock(st)
        st['grav'] += 1
        if st['grav'] >= _grav_period(st, settings):
            st['grav'] = 0
            if not _move(st, 0, -1):
                _lock(st)
    elif not playing:
        _attract(st)

    sfx, st['sfx'] = st.get('sfx', []), []
    if play_sound and sfx:
        _play_sfx(play_sound, sfx)

    _draw_board(canvas, st, gx, gy, cell)
    if playing and st.get('paused'):
        canvas.rect(W // 2 - 3, H // 2 - 4, 2, 8, (255, 255, 255), fill=True)
        canvas.rect(W // 2 + 1, H // 2 - 4, 2, 8, (255, 255, 255), fill=True)

    canvas.show()
    try:
        speed = max(1, min(10, int(float(settings.get('speed', 5) or 5))))
    except (TypeError, ValueError):
        speed = 5
    hold = max(0.06, 0.30 - 0.02 * speed)
    return max(0.05, hold * 0.7) if playing else hold
