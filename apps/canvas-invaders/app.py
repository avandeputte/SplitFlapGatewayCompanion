"""Invaders — the marching fleet, on the LED panel.

A matrix-only interactive game on the shared framework: a rainbow fleet marches
side to side, drops a row at each turn and speeds up as it thins — the classic
heartbeat. Left/right steer the cannon, a TAP of up fires (one shot airborne at a
time, like the arcade); aliens bomb back, and now and then a magenta saucer worth
100 crosses the top. Deeper rows score less; clear the wave and the next one starts
lower and faster. Three lives with a READY? hold between them — but if the fleet
ever reaches the cannon row the invasion ends the game outright. In attract mode
the cannon plays itself (chases the nearest column, dodges bombs) and a finished
game quietly restarts. Sound only while a human plays; the march tick IS the score.
Every frame is a batch of small rects, streamed as binary.
"""

import random

_ROWS = [(216, 72, 255), (72, 220, 255), (96, 232, 96), (255, 210, 64), (255, 140, 60)]
_CANNON = (96, 232, 96)
_BOLT = (240, 244, 252)
_BOMB = (255, 120, 90)
_SAUCER = (255, 72, 220)
_TXT = (150, 150, 158)
_AW, _AH = 6, 5                                        # alien sprite cell
_PX = 9                                                # column pitch (cell + gap)
_FADE_STEPS = 16


def _play_sfx(play_sound, events):
    ev = next((e for e in ('death', 'invasion', 'level', 'life', 'saucer', 'hit', 'shoot',
                           'march', 'march2') if e in events), None)
    if ev in ('march', 'march2'):                      # the two-note heartbeat
        play_sound(notes=[[110 if ev == 'march' else 98, 40]], vol=35)
    elif ev == 'shoot':
        play_sound(notes=[[1046, 30]], vol=35)
    elif ev == 'hit':
        play_sound(notes=[[220, 50]], vol=50)
    elif ev == 'saucer':
        play_sound(notes=[[1568, 35], [1760, 35]], vol=40)
    elif ev == 'life':
        play_sound(notes=[[330, 120], [262, 200]], vol=65)
    elif ev == 'level':
        play_sound(notes=[[659, 90], [784, 90], [988, 90], [1319, 180]], vol=70)
    else:                                              # death / invasion
        play_sound(notes=[[392, 130], [262, 170], [131, 340]], vol=72)


def _fleet_dims(W, H):
    """(columns, rows, row pitch, starting oy, drop) — a 64-row panel carries the
    full four-row fleet with marching room; a 32-row one gets a two-row skirmish."""
    ncols = max(4, min(11, (W - 14) // _PX))
    if H >= 48:
        return ncols, 4, 8, 9, 3
    return ncols, 2, 7, 8, 2


def _new_wave(st, level):
    W = st['W']
    ncols, nrows, py, oy0, drop = _fleet_dims(W, st['H'])
    st.update(level=level, alive={(r, c) for r in range(nrows) for c in range(ncols)},
              ox=float((W - ncols * _PX + (_PX - _AW)) // 2),
              oy=float(oy0 + min(drop * (level - 1), drop * 3)),
              dir=1, tick=0, wiggle=0, bolt=None, bombs=[], saucer=None, saucer_in=200)
    st.update(ncols=ncols, nrows=nrows, py=py, drop=drop)


def _new_game(st):
    st.update(cx=st['W'] / 2.0, score=0, lives=3, phase='play', fade=0,
              freeze_presses=None, sfx=[])
    _new_wave(st, 1)


def _state(W, H):
    st = getattr(_state, '_st', None)
    if st is None or st.get('W') != W or st.get('H') != H:
        st = _state._st = {'W': W, 'H': H}
        _new_game(st)
    return st


def _alien_xy(st, r, c):
    return st['ox'] + c * _PX, st['oy'] + r * st['py']


def _bottom_aliens(st):
    """The lowest live alien of each column — the ones allowed to drop bombs."""
    low = {}
    for r, c in st['alive']:
        if c not in low or r > low[c]:
            low[c] = r
    return [(r, c) for c, r in low.items()]


def _march(st):
    """The fleet's step: sideways every few ticks (faster as it thins), drop + flip at
    the edges. Returns True when the fleet stepped this tick (the march beat)."""
    W = st['W']
    total = st['ncols'] * st['nrows']
    pace = max(1, round(8 * len(st['alive']) / max(1, total)) - (st['level'] - 1) // 2)
    st['tick'] += 1
    if st['tick'] % pace:
        return False
    xs = [_alien_xy(st, r, c)[0] for r, c in st['alive']]
    if not xs:
        return False
    step = 2 * st['dir']
    if min(xs) + step < 2 or max(xs) + _AW + step > W - 2:
        st['dir'] = -st['dir']
        st['oy'] += st['drop']
    else:
        st['ox'] += step
    st['wiggle'] ^= 1
    return True


def _step(st, move, fire, auto=False):
    """One tick: cannon, march, bolt, bombs, saucer, collisions, wave/lives."""
    W, H = st['W'], st['H']
    cy = H - 4                                         # cannon top row
    rng = random.Random(st['tick'] * 31 + st['score'] * 7 + len(st['alive']))

    if auto:
        move, fire = _auto_pilot(st, cy, rng)
    if move:
        st['cx'] += 2 if move == 'r' else -2
    st['cx'] = max(4, min(W - 4, st['cx']))

    if fire and st['bolt'] is None:
        st['bolt'] = {'x': st['cx'], 'y': cy - 1.0}
        st['sfx'].append('shoot')

    if _march(st):
        st['sfx'].append('march' if st['wiggle'] else 'march2')
        # a stepping fleet may bomb: from the lowest alien of a random column
        if len(st['bombs']) < 2 + st['level'] // 2 and rng.random() < 0.65:
            r, c = rng.choice(_bottom_aliens(st))
            x, y = _alien_xy(st, r, c)
            st['bombs'].append({'x': x + _AW / 2, 'y': y + _AH + 1.0})

    # The saucer: worth 100, crosses the top on its own clock.
    if st['saucer'] is None:
        st['saucer_in'] -= 1
        if st['saucer_in'] <= 0:
            st['saucer'] = {'x': -8.0, 'v': 1.5}
            st['saucer_in'] = 200 + rng.randrange(120)
    else:
        st['saucer']['x'] += st['saucer']['v']
        if st['tick'] % 3 == 0:                        # warble, not a note per frame
            st['sfx'].append('saucer')
        if st['saucer']['x'] > W + 8:
            st['saucer'] = None

    b = st['bolt']
    if b is not None:
        b['y'] -= 3
        hit = None
        for r, c in st['alive']:
            x, y = _alien_xy(st, r, c)
            if x - 1 <= b['x'] <= x + _AW and y <= b['y'] <= y + _AH + 1:
                hit = (r, c)
                break
        if hit is not None:
            st['alive'].discard(hit)
            st['score'] += (st['nrows'] - hit[0]) * 10
            st['bolt'] = None
            st['sfx'].append('hit')
            if not st['alive']:
                st['sfx'].append('level')
                _new_wave(st, st['level'] + 1)
                return
        elif st['saucer'] is not None and 0 <= b['y'] <= 4 and \
                st['saucer']['x'] - 1 <= b['x'] <= st['saucer']['x'] + 7:
            st['saucer'] = None
            st['bolt'] = None
            st['score'] += 100
            st['sfx'].append('hit')
        elif b['y'] < 0:
            st['bolt'] = None

    hit_cannon = False
    for bomb in st['bombs']:
        bomb['y'] += 2
        if bomb['y'] >= cy and abs(bomb['x'] - st['cx']) <= 4:
            hit_cannon = True
    st['bombs'] = [bb for bb in st['bombs'] if bb['y'] < H and
                   not (bb['y'] >= cy and abs(bb['x'] - st['cx']) <= 4)]

    invaded = any(_alien_xy(st, r, c)[1] + _AH >= cy for r, c in st['alive'])
    if invaded or hit_cannon:
        if invaded:
            st['lives'] = 0                            # the fleet landed — no lives save that
        else:
            st['lives'] -= 1
        if auto:
            if st['lives'] <= 0:
                _new_game(st)                          # attract: straight into a fresh demo
            else:
                st['bombs'], st['bolt'] = [], None
        elif st['lives'] <= 0:
            st['phase'], st['fade'] = 'gameover', 0
            st['sfx'].append('invasion' if invaded else 'death')
        else:
            st['phase'] = 'ready'                      # hold for a key between lives
            st['bombs'], st['bolt'] = [], None
            st['sfx'].append('life')


def _auto_pilot(st, cy, rng):
    """Attract AI: dodge the nearest falling bomb, else chase the nearest live column
    and fire whenever lined up with the shot slot free."""
    threat = None
    for bomb in st['bombs']:
        if cy - bomb['y'] <= 10 and abs(bomb['x'] - st['cx']) <= 5:
            threat = bomb
            break
    if threat is not None:
        return ('l' if threat['x'] >= st['cx'] else 'r'), False
    if not st['alive']:
        return None, False
    tx = min((abs(_alien_xy(st, r, c)[0] + _AW / 2 - st['cx']), _alien_xy(st, r, c)[0] + _AW / 2)
             for r, c in st['alive'])[1]
    if abs(tx - st['cx']) > 2:
        return ('r' if tx > st['cx'] else 'l'), False
    return None, st['bolt'] is None


def _draw(canvas, st, dim=1.0):
    W, H = st['W'], st['H']
    cy = H - 4

    def d(col):
        return col if dim >= 1.0 else canvas.dim(col, dim)

    canvas.clear((0, 0, 0))
    canvas.text(2, 1, str(st['score']), d(_TXT), size=8)
    canvas.text(W - 2, 1, '•' * max(0, st['lives'] - 1), d(_CANNON), size=8, align='right')
    if st['saucer'] is not None:
        canvas.rect(int(st['saucer']['x']), 1, 7, 2, d(_SAUCER), fill=True)
        canvas.rect(int(st['saucer']['x']) + 2, 0, 3, 1, d(_SAUCER), fill=True)
    w = st['wiggle']
    for r, c in st['alive']:
        x, y = _alien_xy(st, r, c)
        col = d(_ROWS[r % len(_ROWS)])
        canvas.rect(int(x), int(y), _AW, _AH - 2, col, fill=True)          # body
        canvas.rect(int(x) + (0 if w else 1), int(y) + _AH - 2, 2, 2, col, fill=True)   # legs
        canvas.rect(int(x) + _AW - (2 if w else 3), int(y) + _AH - 2, 2, 2, col, fill=True)
    if st['bolt'] is not None:
        canvas.rect(int(st['bolt']['x']), int(st['bolt']['y']), 1, 3, d(_BOLT), fill=True)
    for bomb in st['bombs']:
        canvas.rect(int(bomb['x']), int(bomb['y']), 1, 2, d(_BOMB), fill=True)
    cx = int(st['cx'])
    canvas.rect(cx - 3, cy + 1, 7, 2, d(_CANNON), fill=True)               # base
    canvas.rect(cx, cy - 1, 1, 2, d(_CANNON), fill=True)                   # barrel


def _draw_ready(canvas, W, H):
    canvas.text(W // 2, H // 2 - 4, 'READY?', (255, 210, 63), size=10, align='center')


def _draw_gameover(canvas, W, H, score, appear):
    a = max(0.0, min(1.0, appear))
    canvas.text(W // 2, H // 2 - 9, 'GAME OVER', canvas.dim((255, 82, 82), a), size=10,
                align='center')
    canvas.text(W // 2, H // 2 + 3, f'SCORE {score}', canvas.dim((240, 240, 244), a), size=8,
                align='center')


def fetch_matrix(settings, canvas, controls=None, play_sound=None):
    W, H = canvas.width, canvas.height
    st = _state(W, H)

    playing = controls is not None and controls.active(within=canvas.num(settings, 'takeover', 30, 5, 120))
    events = list(controls.events) if controls is not None else []
    held = controls.dir if (controls and controls.dir) else None
    move = {'left': 'l', 'right': 'r'}.get(held)
    fire = bool(controls and ('up' in (controls.taps or []) or held == 'up'))
    presses = controls.presses if controls is not None else 0

    if not playing and st.get('phase', 'play') != 'play':
        _new_game(st)                                  # attract resets a finished game
    if playing and ('start' in events or 'coin' in events):
        _new_game(st)

    # Between-lives READY? and the game-over screen — a live player only; both wait
    # for a fresh key PRESS (press-edge, so a held direction can't skip them).
    if playing and st.get('phase') in ('ready', 'gameover'):
        if st.get('freeze_presses') is None:
            st['freeze_presses'] = presses
        if st['phase'] == 'gameover':
            st['fade'] = min(st.get('fade', 0) + 1, _FADE_STEPS)
        if presses > st['freeze_presses']:
            if st['phase'] == 'gameover':
                _new_game(st)
            else:
                st['phase'], st['freeze_presses'] = 'play', None
        else:
            if st['phase'] == 'gameover':
                _draw(canvas, st, dim=max(0.0, 1 - st['fade'] / _FADE_STEPS))
                _draw_gameover(canvas, W, H, st['score'], st['fade'] / (_FADE_STEPS * 0.5))
            else:
                _draw(canvas, st)
                _draw_ready(canvas, W, H)
            canvas.show()
            return 0.08

    if playing and 'pause' in events:
        st['paused'] = not st.get('paused', False)

    if not (playing and st.get('paused')):
        _step(st, move, fire, auto=not playing)

    sfx, st['sfx'] = st.get('sfx', []), []
    if playing and play_sound and sfx:                 # sound only while a human plays
        _play_sfx(play_sound, sfx)

    _draw(canvas, st)
    if playing and st.get('paused'):
        canvas.rect(W // 2 - 3, H // 2 - 4, 2, 8, (255, 255, 255), fill=True)
        canvas.rect(W // 2 + 1, H // 2 - 4, 2, 8, (255, 255, 255), fill=True)

    canvas.show()
    speed = canvas.num(settings, 'speed', 5, 1, 10)
    hold = max(0.05, 0.24 - 0.018 * speed)
    return max(0.045, hold * 0.75) if playing else hold
