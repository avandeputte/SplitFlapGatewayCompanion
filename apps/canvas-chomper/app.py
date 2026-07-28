"""Chomper — a Pac-Man-style maze chase the panel plays by itself.

A matrix-only canvas app, and the ops-surface showcase: every frame is one small batch
of on-device draw ops — wall cells merged into runs of ``rect``, pellets as ``pixel``,
the chomper as a firmware-3.5 filled ``arc`` whose mouth wedge opens and shuts as it
runs (a ``circle`` with a ``triangle`` bite on older walls), ghosts as circle + rect
with a ``poly`` skirt. The game simulates itself: the chomper chases the nearest pellet
by breadth-first search (attract mode), ghosts chase the chomper — and flee, blue, while
a power pellet is up — side tunnels wrap arcade-style, and lives and levels turn over
forever. When a player touches the web-UI control pad the chomper hands over to them (the
`controls` helper), with tones on the gateway speaker (`play_sound`); it drifts back to
attract mode after a few idle seconds.
"""

import random

_WALL = (44, 76, 235)
_DOT = (255, 196, 140)
_PAC = (255, 210, 40)
_FRIGHT = (56, 90, 255)
_EYE = (240, 244, 252)
_GHOSTS = [(255, 60, 45), (255, 150, 200), (48, 200, 255), (255, 176, 0)]
_DIRS = {'u': (0, -1), 'd': (0, 1), 'l': (-1, 0), 'r': (1, 0)}
_DIR_DEG = {'u': 0, 'r': 90, 'd': 180, 'l': 270}   # the wall's gauge convention
_FRIGHT_STEPS = 40
_CTRL = {'up': 'u', 'down': 'd', 'left': 'l', 'right': 'r'}


def _play_sfx(play_sound, events, pellets):
    """Map game events to short tones on the wall speaker (fire-and-forget). The big
    jingles (power/death/level) win over a plain waka when several land in one frame."""
    ev = next((e for e in ('level', 'death', 'power', 'eat') if e in events), None) \
        or ('waka' if 'waka' in events else None)
    if ev == 'waka':
        play_sound(notes=[[520 if (pellets // 3) % 2 else 660, 45]], vol=45)
    elif ev == 'power':
        play_sound(notes=[[523, 90], [659, 90], [784, 150]], vol=70)
    elif ev == 'eat':
        play_sound(notes=[[880, 70], [1175, 110]], vol=70)
    elif ev == 'death':
        play_sound(notes=[[440, 150], [330, 170], [220, 320]], vol=75)
    elif ev == 'level':
        play_sound(notes=[[659, 110], [784, 110], [988, 110], [1319, 220]], vol=75)


def _maze(cols, rows, rng):
    """A classic arcade maze, randomized fresh every level: corridors exactly ONE cell
    wide and walls exactly ONE cell thick. A recursive-backtracker spanning tree over
    the odd-coordinate node lattice carves the corridors; braiding then gives most dead
    ends a second exit (loops to run in) while the rest stay as pockets. ``cols`` and
    ``rows`` must be odd so the outer wall stays one cell thick on every side.
    Returns (walls, corridor cells)."""
    walls = [[True] * cols for _ in range(rows)]
    node_set = {(x, y) for y in range(1, rows - 1, 2) for x in range(1, cols - 1, 2)}
    for x, y in node_set:
        walls[y][x] = False
    start = next(iter(node_set))
    stack, seen = [start], {start}
    while stack:
        x, y = stack[-1]
        nbrs = [(x + dx, y + dy) for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2))
                if (x + dx, y + dy) in node_set and (x + dx, y + dy) not in seen]
        if nbrs:
            nx, ny = nbrs[rng.randrange(len(nbrs))]
            walls[(y + ny) // 2][(x + nx) // 2] = False    # knock out the between wall
            seen.add((nx, ny))
            stack.append((nx, ny))
        else:
            stack.pop()
    for x, y in node_set:                                  # braid: every dead end opens
        exits = sum(1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                    if not walls[y + dy][x + dx])
        if exits == 1:
            cands = [(dx, dy) for dx, dy in ((2, 0), (-2, 0), (0, 2), (0, -2))
                     if (x + dx, y + dy) in node_set and walls[y + dy // 2][x + dx // 2]]
            if cands:
                dx, dy = cands[rng.randrange(len(cands))]
                walls[y + dy // 2][x + dx // 2] = False
    h_rows = [y for y in range(1, rows - 1, 2)]            # tunnels: randomized count and
    v_cols = [x for x in range(1, cols - 1, 2)]            # place, wrapping to the far edge
    rng.shuffle(h_rows)
    rng.shuffle(v_cols)
    for ty in h_rows[:rng.randint(1, 2 if rows >= 9 else 1)]:
        walls[ty][0] = False
        walls[ty][cols - 1] = False
    for tx in v_cols[:1]:                                  # and one vertical, arcade-plus
        walls[0][tx] = False
        walls[rows - 1][tx] = False
    cells = [(x, r) for r in range(rows) for x in range(cols) if not walls[r][x]]
    return walls, cells

def _wall_rects(walls):
    """The wall mask decomposed into a few maximal rectangles — greedy row runs grown
    downward — so the whole maze draws in a handful of rect ops."""
    rows, cols = len(walls), len(walls[0])
    used = [[False] * cols for _ in range(rows)]
    rects = []
    for r in range(rows):
        x = 0
        while x < cols:
            if walls[r][x] and not used[r][x]:
                x0 = x
                while x < cols and walls[r][x] and not used[r][x]:
                    x += 1
                w = x - x0
                h = 1
                while r + h < rows and all(walls[r + h][xx] and not used[r + h][xx]
                                           for xx in range(x0, x0 + w)):
                    h += 1
                for rr in range(r, r + h):
                    for xx in range(x0, x0 + w):
                        used[rr][xx] = True
                rects.append((x0, r, w, h))
            else:
                x += 1
    return rects


def _neighbors(walls, x, y):
    cols, rows = len(walls[0]), len(walls)
    for d, (dx, dy) in _DIRS.items():
        nx, ny = (x + dx) % cols, (y + dy) % rows      # both axes wrap: the tunnels
        if not walls[ny][nx]:
            yield d, nx, ny


def _bfs_dir(walls, start, targets, prefer=None):
    """The first step from ``start`` toward the nearest cell in ``targets`` —
    breadth-first, ties broken toward the current heading so the chomper doesn't
    jitter at junctions. None when nothing is reachable."""
    if not targets or start in targets:
        return None
    order = [prefer] + [d for d in _DIRS if d != prefer] if prefer else list(_DIRS)
    seen = {start}
    frontier = []
    for d in order:
        dx, dy = _DIRS[d]
        nx, ny = (start[0] + dx) % len(walls[0]), (start[1] + dy) % len(walls)
        if not walls[ny][nx]:
            frontier.append(((nx, ny), d))
            seen.add((nx, ny))
    while frontier:
        nxt = []
        for cell, first in frontier:
            if cell in targets:
                return first
            for _, nx, ny in _neighbors(walls, *cell):
                if (nx, ny) not in seen:
                    seen.add((nx, ny))
                    nxt.append(((nx, ny), first))
        frontier = nxt
    return None


def _reset_positions(st):
    st['pac'] = {'cell': st['pac_start'], 'dir': 'r', 'phase': 0}
    st['ghost_list'] = [{'cell': st['spawn'], 'dir': 'l', 'col': _GHOSTS[i % len(_GHOSTS)]}
                       for i in range(st['n_ghosts'])]
    st['fright'] = 0


def _new_level(st):
    rng = random.Random(st['level'] * 1000003 + st['cols'] * 1009 + st['rows'])
    st['walls'], cells = _maze(st['cols'], st['rows'], rng)
    st['wall_rects'] = _wall_rects(st['walls'])
    cx0, cy0 = st['cols'] / 2, st['rows'] / 2
    st['spawn'] = min(cells, key=lambda c: abs(c[0] - cx0) + abs(c[1] - cy0))
    # The chomper opens at bottom-center, arcade style — and a corridor away from the
    # power-pellet corners, so a round doesn't start with every ghost already blue.
    st['pac_start'] = min(cells, key=lambda c: abs(c[0] - cx0) + abs(c[1] - (st['rows'] - 2)))
    top_r, bot_r = 1, st['rows'] - 2
    st['dots'] = {cc for cc in cells if 0 < cc[0] < st['cols'] - 1
                  and 0 < cc[1] < st['rows'] - 1}                     # tunnels stay bare
    st['power'] = {(1, top_r), (st['cols'] - 2, top_r), (1, bot_r), (st['cols'] - 2, bot_r)}
    st['dots'] -= st['power']
    _reset_positions(st)


def _new_game(st):
    st.update(score=0, lives=3, level=1, step=0, want=None, paused=False,
              pellets=0, sfx=[])
    _new_level(st)


def _state(cols, rows, n_ghosts):
    st = getattr(_state, '_st', None)
    if (st is None or st['cols'] != cols or st['rows'] != rows
            or st['n_ghosts'] != n_ghosts):
        st = _state._st = {'cols': cols, 'rows': rows, 'n_ghosts': n_ghosts}
        _new_game(st)
    return st


def _open(st, cell, d):
    """The cell one step in direction ``d`` from ``cell`` if it is a corridor (x wraps
    through the side tunnels, y through the vertical one), else None."""
    walls = st['walls']
    dx, dy = _DIRS[d]
    nx, ny = (cell[0] + dx) % st['cols'], (cell[1] + dy) % st['rows']
    return None if walls[ny][nx] else (nx, ny)


def _step(st, want=None, auto=True):
    """One tick: the chomper moves (BFS toward pellets in attract mode, or toward the
    player's ``want`` direction in play mode — turning when that lane opens, else coasting
    on its heading), ghosts chase, collisions resolve across the move. Sound-worthy events
    accumulate in ``st['sfx']`` for the caller to play."""
    st['step'] += 1
    rng = random.Random(st['level'] * 100003 + st['step'])
    walls, pac = st['walls'], st['pac']
    px, py = pac['cell']
    if want:
        st['want'] = want

    if auto:
        frightened = st['fright'] > 0
        targets = ({g['cell'] for g in st['ghost_list']} if frightened and st['ghost_list']
                   else st['dots'] | st['power'])
        d = _bfs_dir(walls, (px, py), targets, prefer=pac['dir'])
    else:
        # Player steering, classic Pac-Man: turn to the held direction where that lane is
        # open, otherwise keep going on the current heading; stop dead at a wall.
        d = next((c for c in (st.get('want'), pac['dir']) if c and _open(st, (px, py), c)),
                 None)

    old_pac = pac['cell']
    if d:
        pac['cell'] = _open(st, (px, py), d)
        pac['dir'] = d
    pac['phase'] = (pac['phase'] + 1) % 4

    if pac['cell'] in st['dots']:
        st['dots'].discard(pac['cell'])
        st['score'] += 10
        st['pellets'] += 1
        if st['pellets'] % 3 == 0:
            st['sfx'].append('waka')
    elif pac['cell'] in st['power']:
        st['power'].discard(pac['cell'])
        st['score'] += 50
        st['fright'] = _FRIGHT_STEPS
        st['sfx'].append('power')
    if st['fright'] > 0:
        st['fright'] -= 1

    died = False
    for gi, g in enumerate(st['ghost_list']):
        old_g = g['cell']
        if (st['step'] + gi) % 5:                       # ghosts sit out every fifth tick
            gx, gy = g['cell']
            opts = [(d2, nx, ny) for d2, nx, ny in _neighbors(walls, gx, gy)]
            back = {'u': 'd', 'd': 'u', 'l': 'r', 'r': 'l'}[g['dir']]
            fwd = [o for o in opts if o[0] != back] or opts
            pacx, pacy = pac['cell']

            def dist(o):
                return abs(o[1] - pacx) + abs(o[2] - pacy)

            fwd.sort(key=dist, reverse=st['fright'] > 0)
            best = dist(fwd[0])
            pick = rng.choice([o for o in fwd if dist(o) == best])
            g['dir'], gx, gy = pick[0], pick[1], pick[2]
            g['cell'] = (gx, gy)
        hit = g['cell'] == pac['cell'] or (g['cell'] == old_pac and pac['cell'] == old_g)
        if hit:
            if st['fright'] > 0:
                st['score'] += 200                       # eaten: back to the spawn cell
                g['cell'] = st['spawn']
                st['sfx'].append('eat')
            else:
                died = True
    if died:
        st['lives'] -= 1
        st['sfx'].append('death')
        if st['lives'] <= 0:
            _new_game(st)
        else:
            _reset_positions(st)
    elif not st['dots'] and not st['power']:
        st['level'] += 1
        st['sfx'].append('level')
        _new_level(st)


def _draw_pac(canvas, x, y, r, d, phase):
    open_deg = (10, 34, 58, 34)[phase]
    if canvas.has_op('arc'):
        a = _DIR_DEG[d]
        canvas.arc(x, y, r, a + open_deg, a + 360 - open_deg, _PAC, fill=True)
        return
    canvas.circle(x, y, r, _PAC, fill=True)
    dx, dy = _DIRS[d]
    canvas.triangle(x, y, x + dx * r + dy * r, y + dy * r + dx * r,
                    x + dx * r - dy * r, y + dy * r - dx * r, (0, 0, 0), fill=True)


def _draw_ghost(canvas, x, y, r, col, d):
    canvas.circle(x, y - 1, r, col, fill=True)
    canvas.rect(x - r, y - 1, 2 * r + 1, r, col, fill=True)
    if r >= 3 and canvas.has_op('poly'):
        b = y + r - 1
        canvas.poly([(x - r, b - 1), (x - r, b + 1), (x - r // 2, b - 1), (x, b + 1),
                     (x + r // 2, b - 1), (x + r, b + 1), (x + r, b - 1)], col)
    dx, dy = _DIRS[d]
    ex = max(1, r // 2)
    canvas.pixel(x - ex + dx, y - 1 + dy, _EYE)
    canvas.pixel(x + ex + dx, y - 1 + dy, _EYE)


def fetch_matrix(settings, canvas, controls=None, play_sound=None):
    W, H = canvas.width, canvas.height
    # The grid is stretched edge-to-edge: as many ~4.5px cells as fit (odd counts, so
    # the outer wall stays one cell thick), each row/column mapped to pixel edges — no
    # dead margin anywhere, and a 64px panel plays six corridor rows.
    cols, rows = max(11, int(W / 4.5)), max(5, int(H / 4.5))
    cols -= 1 - (cols % 2)
    rows -= 1 - (rows % 2)
    xe = [round(i * W / cols) for i in range(cols + 1)]
    ye = [round(r * H / rows) for r in range(rows + 1)]
    try:
        n_ghosts = max(1, min(4, int(float(settings.get('ghosts', 4) or 4))))
    except (TypeError, ValueError):
        n_ghosts = 4
    st = _state(cols, rows, n_ghosts)

    # A human on the control pad takes over; after a few idle seconds it drifts back to
    # attract-mode auto-play. start/coin drops a fresh game, pause freezes it.
    playing = controls is not None and controls.active()
    if controls is not None:
        for ev in controls.events:
            if ev in ('start', 'coin'):
                _new_game(st)
            elif ev == 'pause':
                st['paused'] = not st.get('paused', False)
    want = _CTRL.get(controls.dir) if (controls and controls.dir) else None

    if not (playing and st.get('paused')):
        _step(st, want=want, auto=not playing)

    sfx, st['sfx'] = st.get('sfx', []), []             # play this frame's events
    if play_sound and sfx:
        _play_sfx(play_sound, sfx, st['pellets'])

    def cx(cell):
        return (xe[cell[0]] + xe[cell[0] + 1]) // 2

    def cy(cell):
        return (ye[cell[1]] + ye[cell[1] + 1]) // 2

    canvas.clear((0, 0, 0))
    for x0, r0, w0, h0 in st['wall_rects']:            # the maze in a handful of rects
        canvas.rect(xe[x0], ye[r0], xe[x0 + w0] - xe[x0], ye[r0 + h0] - ye[r0],
                    _WALL, fill=True)
    for cell in st['dots']:
        canvas.pixel(cx(cell), cy(cell), _DOT)
    if (st['step'] // 2) % 2 == 0:                     # power pellets blink
        for cell in st['power']:
            canvas.circle(cx(cell), cy(cell), 1, _DOT, fill=True)

    pr = 2                                             # 5px sprites, arcade-oversized for the lanes
    for g in st['ghost_list']:
        col = _FRIGHT if st['fright'] > 0 else g['col']
        _draw_ghost(canvas, cx(g['cell']), cy(g['cell']), pr, col, g['dir'])
    pac = st['pac']
    _draw_pac(canvas, cx(pac['cell']), cy(pac['cell']), pr, pac['dir'], pac['phase'])

    if W >= 128:                                       # score rides the top wall band
        canvas.shadow_text(2, 0, str(st['score']), (255, 255, 255), 8)
        for i in range(st['lives']):
            canvas.circle(W - 5 - i * 7, max(2, ye[1] // 2), 2, _PAC, fill=True)
    if playing and st.get('paused'):                   # two-bar pause glyph, centered
        canvas.rect(W // 2 - 3, H // 2 - 4, 2, 8, (255, 255, 255), fill=True)
        canvas.rect(W // 2 + 1, H // 2 - 4, 2, 8, (255, 255, 255), fill=True)

    canvas.show()
    try:
        speed = max(1, min(10, int(float(settings.get('speed', 5) or 5))))
    except (TypeError, ValueError):
        speed = 5
    # A live player gets the tightest tick the loop allows (input lag = one frame);
    # attract mode idles a touch slower to spare the wall.
    hold = max(0.06, 0.34 - 0.028 * speed)
    return max(0.05, hold * 0.7) if playing else hold
