"""Flappy — the one-button side-scroller, on the LED panel.

A matrix-only interactive game on the shared framework: gravity pulls the bird down,
ANY key press flaps it up (the press-edge counter, so a held key is one flap), pipes
scroll in from the right and passing a pair scores. It plays itself in attract mode
(flapping whenever it sinks below the next gap's center) until a player presses, and
drifts back after the idle 'takeover' timeout. Hitting a pipe, the ground or the sky
fades the scene to black behind GAME OVER — the next press deals a new game. Sound
only while a human plays. Sky, pipes and bird are a small ops batch, streamed binary.
"""

import random

_SKY_TOP = (36, 62, 118)
_SKY_BOT = (66, 108, 164)
_PIPE = (64, 184, 88)
_PIPE_LIP = (96, 216, 120)
_BIRD = (255, 210, 64)
_EDGE = (40, 44, 60)                                   # dark outline so the bird pops on the sky
_WING = (240, 180, 40)
_GROUND = (150, 120, 70)
_FADE_STEPS = 16


def _play_sfx(play_sound, events):
    ev = next((e for e in ('death', 'score', 'flap') if e in events), None)
    if ev == 'flap':
        play_sound(notes=[[740, 35]], vol=40)
    elif ev == 'score':
        play_sound(notes=[[880, 60], [1175, 90]], vol=60)
    elif ev == 'death':
        play_sound(notes=[[392, 120], [262, 160], [175, 300]], vol=70)


def _new_game(st):
    H = st['H']
    st.update(y=H * 0.45, vy=0.0, pipes=[], dist=0, score=0, phase='play', fade=0,
              freeze_presses=None, sfx=[], seen_presses=None)


def _state(W, H):
    st = getattr(_state, '_st', None)
    if st is None or st.get('W') != W or st.get('H') != H:
        st = _state._st = {'W': W, 'H': H}
        _new_game(st)
    return st


def _gap_h(H):
    return max(14, int(H * 0.42))


def _spacing(W):
    return max(34, W // 3)


def _step(st, flap, auto=False):
    """One tick: gravity, scrolling pipes, spawning, scoring, collision."""
    W, H = st['W'], st['H']
    ground = H - 3
    if auto:
        # attract: flap when sinking below the next gap's center (or the screen middle)
        nxt = next((p for p in st['pipes'] if p['x'] + p['w'] >= W * 0.25), None)
        target = nxt['gap_y'] + _gap_h(H) * 0.5 if nxt else H * 0.45
        flap = st['y'] > target and st['vy'] >= 0
    # physics scale with panel height, so a 32px panel flaps as gently as a 64px one
    if flap:
        st['vy'] = -max(1.3, H * 0.028)
        st['sfx'].append('flap')
    st['vy'] = min(max(1.8, H * 0.04), st['vy'] + max(0.2, H * 0.0052))
    st['y'] += st['vy']

    dx = 2
    st['dist'] += dx
    for p in st['pipes']:
        p['x'] -= dx
    st['pipes'] = [p for p in st['pipes'] if p['x'] + p['w'] > 0]
    if not st['pipes'] or st['pipes'][-1]['x'] <= W - _spacing(W):
        rng = random.Random(st['dist'] * 13 + st['score'] * 7)
        gap_y = rng.randint(4, max(5, ground - _gap_h(H) - 4))
        st['pipes'].append({'x': W, 'w': max(6, W // 24), 'gap_y': gap_y, 'passed': False})

    bx = int(W * 0.25)
    by, r = st['y'], 2
    for p in st['pipes']:
        if not p['passed'] and p['x'] + p['w'] < bx - r:
            p['passed'] = True
            st['score'] += 1
            st['sfx'].append('score')
        if p['x'] - r < bx < p['x'] + p['w'] + r:
            if by - r < p['gap_y'] or by + r > p['gap_y'] + _gap_h(H):
                return _die(st, auto)
    if by + r >= ground or by - r <= 0:
        return _die(st, auto)


def _die(st, auto):
    if auto:
        _new_game(st)                                  # attract: straight into a fresh demo
    else:
        st['phase'], st['fade'] = 'gameover', 0
        st['sfx'].append('death')


def _draw(canvas, st, dim=1.0):
    W, H = st['W'], st['H']

    def d(col):
        return col if dim >= 1.0 else canvas.dim(col, dim)

    canvas.gradient(0, 0, W, H, d(_SKY_TOP), d(_SKY_BOT), 'v')
    gap = _gap_h(H)
    ground = H - 3
    for p in st['pipes']:
        x, w, gy = int(p['x']), p['w'], p['gap_y']
        canvas.rect(x, 0, w, gy, d(_PIPE), fill=True)
        canvas.rect(x - 1, gy - 2, w + 2, 2, d(_PIPE_LIP), fill=True)
        canvas.rect(x, gy + gap, w, ground - gy - gap, d(_PIPE), fill=True)
        canvas.rect(x - 1, gy + gap, w + 2, 2, d(_PIPE_LIP), fill=True)
    canvas.rect(0, ground, W, H - ground, d(_GROUND), fill=True)
    bx, by = int(W * 0.25), int(st['y'])
    canvas.circle(bx, by, 3, d(_EDGE), fill=True)                       # the outline ring
    canvas.circle(bx, by, 2, d(_BIRD), fill=True)
    canvas.pixel(bx + 1, by - 1, d(_EDGE))                              # the eye
    wing_up = st['vy'] < 0
    canvas.rect(bx - 2, by + (-2 if wing_up else 1), 2, 1, d(_WING), fill=True)
    canvas.text(W // 2, 1, str(st['score']), d((240, 240, 244)), size=8, align='center')


def _draw_gameover(canvas, W, H, score, appear, best=0, new_best=False):
    a = max(0.0, min(1.0, appear))
    title, tc = ('NEW BEST!', (255, 210, 63)) if new_best else ('GAME OVER', (255, 82, 82))
    canvas.text(W // 2, H // 2 - 9, title, canvas.dim(tc, a), size=10, align='center')
    tail = f' · BEST {best}' if best and not new_best and W >= 96 else ''
    canvas.text(W // 2, H // 2 + 3, f'SCORE {score}{tail}', canvas.dim((240, 240, 244), a),
                size=8, align='center')


def fetch_matrix(settings, canvas, controls=None, play_sound=None, game_store=None):
    W, H = canvas.width, canvas.height
    st = _state(W, H)

    playing = controls is not None and controls.active(within=canvas.num(settings, 'takeover', 30, 5, 120))
    events = list(controls.events) if controls is not None else []
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
            _draw(canvas, st, dim=max(0.0, 1 - st['fade'] / _FADE_STEPS))
            _draw_gameover(canvas, W, H, st['score'], st['fade'] / (_FADE_STEPS * 0.5),
                               best=st.get('best_score', 0), new_best=st.get('new_best', False))
            canvas.show()
            return 0.08

    # ONE button: any fresh press this frame flaps (press-edge, so holding = one flap).
    if st.get('seen_presses') is None:
        st['seen_presses'] = presses
    flap = playing and presses > st['seen_presses']
    st['seen_presses'] = presses

    _step(st, flap, auto=not playing)

    sfx, st['sfx'] = st.get('sfx', []), []
    if playing and play_sound and sfx:                 # sound only while a human plays
        _play_sfx(play_sound, sfx)

    _draw(canvas, st)
    canvas.show()
    speed = canvas.num(settings, 'speed', 5, 1, 10)
    hold = max(0.06, 0.26 - 0.018 * speed)
    return max(0.05, hold * 0.75) if playing else hold
