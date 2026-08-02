"""Snake — the classic, on the LED panel.

A matrix-only interactive game on the shared framework: it plays itself (a greedy
food-chaser that checks it isn't about to bite itself) until a player touches the
web-UI pad, steers with the held direction (never straight back into its own neck),
and drifts back to attract mode after the idle 'takeover' timeout. Eat to grow and
score; hit the wall or yourself and the board fades to black behind GAME OVER — any
key deals a new game. Sound only while a human plays. Every frame is a small batch
of on-device draw ops (body cells merged into runs of ``rect``), streamed as binary.
"""

import random

_BODY = (64, 200, 96)
_HEAD = (168, 240, 184)
_FOOD = (255, 90, 70)
_WALL = (52, 58, 74)
_DIRS = {'u': (0, -1), 'd': (0, 1), 'l': (-1, 0), 'r': (1, 0)}
_OPP = {'u': 'd', 'd': 'u', 'l': 'r', 'r': 'l'}
_CTRL = {'up': 'u', 'down': 'd', 'left': 'l', 'right': 'r'}
_FADE_STEPS = 16


def _play_sfx(play_sound, events, length):
    ev = next((e for e in ('death', 'eat') if e in events), None)
    if ev == 'eat':
        play_sound(notes=[[600 + 20 * (length % 12), 45]], vol=50)
    elif ev == 'death':
        play_sound(notes=[[330, 140], [247, 160], [165, 300]], vol=70)


def _spawn_food(st):
    rng = random.Random(st['score'] * 31 + len(st['snake']) * 7 + st['step'])
    taken = set(st['snake'])
    free = [(x, y) for x in range(1, st['cols'] - 1) for y in range(1, st['rows'] - 1)
            if (x, y) not in taken]
    st['food'] = free[rng.randrange(len(free))] if free else None


def _new_game(st):
    cx, cy = st['cols'] // 2, st['rows'] // 2
    st.update(snake=[(cx - i, cy) for i in range(3)], dir='r', grow=0, score=0,
              step=0, phase='play', fade=0, freeze_presses=None, sfx=[])
    _spawn_food(st)


def _state(cols, rows):
    st = getattr(_state, '_st', None)
    if st is None or st['cols'] != cols or st['rows'] != rows:
        st = _state._st = {'cols': cols, 'rows': rows}
        _new_game(st)
    return st


def _safe(st, cell):
    x, y = cell
    if not (1 <= x < st['cols'] - 1 and 1 <= y < st['rows'] - 1):
        return False
    # the tail cell vacates this tick (unless we just ate), so it does not block
    body = st['snake'] if st['grow'] else st['snake'][:-1]
    return cell not in body


def _auto_dir(st):
    """Attract AI: head greedily toward the food, but never into a cell that kills —
    prefer the food axis, fall back to any safe turn, else march on (and die honestly)."""
    hx, hy = st['snake'][0]
    fx, fy = st['food'] or (st['cols'] // 2, st['rows'] // 2)
    prefs = []
    if fx != hx:
        prefs.append('r' if fx > hx else 'l')
    if fy != hy:
        prefs.append('d' if fy > hy else 'u')
    prefs += [d for d in _DIRS if d not in prefs]
    for d in prefs:
        if d == _OPP[st['dir']]:
            continue
        dx, dy = _DIRS[d]
        if _safe(st, (hx + dx, hy + dy)):
            return d
    return st['dir']


def _step(st, want=None, auto=False):
    st['step'] += 1
    if auto:
        st['dir'] = _auto_dir(st)
    elif want and want != _OPP[st['dir']]:              # never straight back into the neck
        st['dir'] = want
    dx, dy = _DIRS[st['dir']]
    hx, hy = st['snake'][0]
    head = (hx + dx, hy + dy)
    if not _safe(st, head):
        if auto:
            _new_game(st)                              # attract: straight into a fresh demo
        else:
            st['phase'], st['fade'] = 'gameover', 0
            st['sfx'].append('death')
        return
    st['snake'].insert(0, head)
    if st['grow']:
        st['grow'] -= 1
    else:
        st['snake'].pop()
    if st['food'] and head == st['food']:
        st['score'] += 10
        st['grow'] += 2
        st['sfx'].append('eat')
        _spawn_food(st)


def _runs(cells):
    """Consecutive horizontal cells merged into (x, y, length) runs — a long snake
    draws in a handful of rects instead of one per segment."""
    out = []
    for x, y in sorted(cells, key=lambda c: (c[1], c[0])):
        if out and out[-1][1] == y and out[-1][0] + out[-1][2] == x:
            out[-1][2] += 1
        else:
            out.append([x, y, 1])
    return out


def _draw(canvas, st, cell, ox, oy, dim=1.0):
    W, H = canvas.width, canvas.height

    def d(col):
        return col if dim >= 1.0 else canvas.dim(col, dim)

    canvas.clear((0, 0, 0))
    canvas.rect(ox, oy, st['cols'] * cell, st['rows'] * cell, d(_WALL))   # the border wall
    body = st['snake'][1:]
    for x, y, n in _runs(body):
        canvas.rect(ox + x * cell, oy + y * cell, n * cell - 1, cell - 1, d(_BODY), fill=True)
    hx, hy = st['snake'][0]
    canvas.rect(ox + hx * cell, oy + hy * cell, cell - 1, cell - 1, d(_HEAD), fill=True)
    if st['food']:
        fx, fy = st['food']
        canvas.rect(ox + fx * cell, oy + fy * cell, cell - 1, cell - 1, d(_FOOD), fill=True)
    canvas.text(W - 2, 1, str(st['score']), d((150, 150, 158)), size=8, align='right')


def _draw_gameover(canvas, W, H, score, appear, best=0, new_best=False):
    a = max(0.0, min(1.0, appear))
    title, tc = ('NEW BEST!', (255, 210, 63)) if new_best else ('GAME OVER', (255, 82, 82))
    canvas.text(W // 2, H // 2 - 9, title, canvas.dim(tc, a), size=10, align='center')
    tail = f' · BEST {best}' if best and not new_best and W >= 96 else ''
    canvas.text(W // 2, H // 2 + 3, f'SCORE {score}{tail}', canvas.dim((240, 240, 244), a),
                size=8, align='center')


def fetch_matrix(settings, canvas, controls=None, play_sound=None, game_store=None):
    W, H = canvas.width, canvas.height
    cell = max(3, H // 12)                              # ~12 rows of play on any panel
    cols, rows = max(15, W // cell), max(8, H // cell)
    ox, oy = (W - cols * cell) // 2, (H - rows * cell) // 2
    st = _state(cols, rows)

    playing = controls is not None and controls.active(within=canvas.num(settings, 'takeover', 30, 5, 120))
    events = list(controls.events) if controls is not None else []
    want = _CTRL.get(controls.dir) if (controls and controls.dir) else None
    presses = controls.presses if controls is not None else 0

    if not playing and st.get('phase', 'play') != 'play':
        _new_game(st)                                  # attract resets a finished game
    if playing and ('start' in events or 'coin' in events):
        _new_game(st)

    if playing and st.get('phase') == 'gameover':
        if st.get('freeze_presses') is None:
            st['freeze_presses'] = presses
            st['new_best'] = bool(game_store and game_store.best(st['score']))
            st['best_score'] = int(game_store.get('high', 0) or 0) if game_store else 0
        st['fade'] = min(st.get('fade', 0) + 1, _FADE_STEPS)
        if presses > st['freeze_presses']:             # any key → new game
            _new_game(st)
        else:
            _draw(canvas, st, cell, ox, oy, dim=max(0.0, 1 - st['fade'] / _FADE_STEPS))
            _draw_gameover(canvas, W, H, st['score'], st['fade'] / (_FADE_STEPS * 0.5),
                               best=st.get('best_score', 0), new_best=st.get('new_best', False))
            canvas.show()
            return 0.08

    if playing and 'pause' in events:
        st['paused'] = not st.get('paused', False)

    if not (playing and st.get('paused')):
        _step(st, want=want, auto=not playing)

    sfx, st['sfx'] = st.get('sfx', []), []
    if playing and play_sound and sfx:                 # sound only while a human plays
        _play_sfx(play_sound, sfx, len(st['snake']))

    _draw(canvas, st, cell, ox, oy)
    if playing and st.get('paused'):
        canvas.rect(W // 2 - 3, H // 2 - 4, 2, 8, (255, 255, 255), fill=True)
        canvas.rect(W // 2 + 1, H // 2 - 4, 2, 8, (255, 255, 255), fill=True)

    canvas.show()
    speed = canvas.num(settings, 'speed', 5, 1, 10)
    hold = max(0.06, 0.30 - 0.022 * speed)
    return max(0.05, hold * 0.7) if playing else hold
