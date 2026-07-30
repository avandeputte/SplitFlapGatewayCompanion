"""Breakout — paddle, ball and rainbow bricks, on the LED panel.

A matrix-only interactive game on the shared framework: the paddle tracks the ball by
itself (with a touch of lag, so the demo occasionally loses) until a player takes over
— the held left/right steers, up launches a served ball — and drifts back to attract
mode after the idle 'takeover' timeout. Bricks score by row and repaint the ball's
angle off the paddle by where it lands; clearing the wall deals the next level, a step
faster. Three lives: losing one holds on a READY? screen for a key, losing the last
fades the board to black behind GAME OVER + score — any key deals a new game. Sound
only while a human plays. On a compositing wall the ball wears a soft additive glow.
Every frame is a small ops batch, streamed as binary.
"""

import random

_ROWS = [(240, 68, 56), (255, 154, 60), (255, 210, 63), (64, 200, 96), (80, 168, 240)]
_PADDLE = (80, 184, 240)
_BALL = (240, 244, 252)
_TXT = (150, 150, 158)
_FADE_STEPS = 16


def _play_sfx(play_sound, events):
    ev = next((e for e in ('death', 'level', 'life', 'brick', 'paddle') if e in events), None)
    if ev == 'paddle':
        play_sound(notes=[[440, 30]], vol=40)
    elif ev == 'brick':
        play_sound(notes=[[660, 40]], vol=50)
    elif ev == 'life':
        play_sound(notes=[[330, 120], [262, 200]], vol=65)
    elif ev == 'level':
        play_sound(notes=[[659, 90], [784, 90], [988, 90], [1319, 180]], vol=70)
    elif ev == 'death':
        play_sound(notes=[[392, 130], [262, 170], [165, 320]], vol=72)


def _build_wall(st):
    W = st['W']
    n_rows = len(_ROWS) if st['H'] >= 48 else 3
    bw, gap = max(8, W // 16), 1
    n_cols = max(4, (W - 2) // (bw + gap))
    x0 = (W - n_cols * (bw + gap) + gap) // 2
    st['bricks'] = [{'x': x0 + c * (bw + gap), 'y': 2 + r * 4, 'w': bw, 'h': 3, 'row': r}
                    for r in range(n_rows) for c in range(n_cols)]


def _serve(st):
    st.update(ball=None, held=True)                    # ball rides the paddle until launched


def _new_level(st, level):
    st['level'] = level
    _build_wall(st)
    _serve(st)


def _new_game(st):
    W = st['W']
    st.update(px=W / 2, pw=max(10, W // 8), score=0, lives=3, phase='play', fade=0,
              freeze_presses=None, sfx=[])
    _new_level(st, 1)


def _state(W, H):
    st = getattr(_state, '_st', None)
    if st is None or st.get('W') != W or st.get('H') != H:
        st = _state._st = {'W': W, 'H': H}
        _new_game(st)
    return st


def _launch(st):
    rng = random.Random(st['score'] * 17 + st['level'] * 5 + st['lives'])
    v = 1.2 + 0.12 * min(10, st['level'])
    ang = rng.uniform(-0.5, 0.5)
    st['ball'] = {'x': st['px'], 'y': st['H'] - 7.0, 'vx': v * ang, 'vy': -v}
    st['held'] = False


def _step(st, move, auto=False, launch=False):
    """One tick: paddle, held/served ball, wall+brick+paddle bounces, lives."""
    W, H = st['W'], st['H']
    pw = st['pw']
    pspeed = 3.2
    if auto:
        target = st['ball']['x'] if st['ball'] else st['px']
        # the demo trails the ball slightly, so it (rarely) misses and rotates the score
        st['px'] += max(-pspeed * 0.8, min(pspeed * 0.8, target - st['px']))
    elif move:
        st['px'] += pspeed if move == 'r' else -pspeed
    st['px'] = max(pw / 2 + 1, min(W - pw / 2 - 1, st['px']))

    if st['held']:
        if auto or launch:
            _launch(st)
        else:
            return
    b = st['ball']
    b['x'] += b['vx']
    b['y'] += b['vy']
    if b['x'] <= 1 or b['x'] >= W - 2:
        b['vx'] = -b['vx']
        b['x'] = max(1, min(W - 2, b['x']))
    if b['y'] <= 1:
        b['vy'] = abs(b['vy'])

    hit = None
    for br in st['bricks']:
        if br['x'] - 1 <= b['x'] <= br['x'] + br['w'] and br['y'] - 1 <= b['y'] <= br['y'] + br['h']:
            hit = br
            break
    if hit is not None:
        st['bricks'].remove(hit)
        st['score'] += (len(_ROWS) - hit['row']) * 10
        st['sfx'].append('brick')
        b['vy'] = -b['vy']
        if not st['bricks']:
            st['sfx'].append('level')
            _new_level(st, st['level'] + 1)
            return

    py = H - 4
    if b['vy'] > 0 and py - 1 <= b['y'] <= py + 1 and abs(b['x'] - st['px']) <= st['pw'] / 2 + 1:
        # the paddle repaints the angle: edge hits send it steep, center sends it up
        off = (b['x'] - st['px']) / (st['pw'] / 2)
        speed = (b['vx'] ** 2 + b['vy'] ** 2) ** 0.5
        b['vx'] = speed * off * 0.85
        b['vy'] = -max(0.6 * speed, (speed ** 2 - b['vx'] ** 2) ** 0.5)
        b['y'] = py - 1
        st['sfx'].append('paddle')
    elif b['y'] >= H - 1:
        st['lives'] -= 1
        if auto:
            if st['lives'] <= 0:
                _new_game(st)                          # attract: straight into a fresh demo
            else:
                _serve(st)
        elif st['lives'] <= 0:
            st['phase'], st['fade'] = 'gameover', 0
            st['sfx'].append('death')
        else:
            st['phase'] = 'ready'                      # hold for a key between lives
            st['sfx'].append('life')
            _serve(st)


def _draw(canvas, st, dim=1.0):
    W, H = st['W'], st['H']

    def d(col):
        return col if dim >= 1.0 else canvas.dim(col, dim)

    canvas.clear((0, 0, 0))
    for br in st['bricks']:
        canvas.rect(br['x'], br['y'], br['w'], br['h'], d(_ROWS[br['row'] % len(_ROWS)]), fill=True)
    canvas.rect(int(st['px'] - st['pw'] / 2), H - 3, int(st['pw']), 2, d(_PADDLE), fill=True)
    if st['held']:
        bx, by = int(st['px']), H - 6
    else:
        bx, by = int(st['ball']['x']), int(st['ball']['y'])
    if dim >= 1.0 and getattr(canvas, 'can_composite', False):
        canvas.blend('add')                            # a soft additive halo around the ball
        canvas.circle(bx, by, 2, (60, 62, 70), fill=True)
        canvas.blend('over')
    canvas.rect(bx - 1, by - 1, 2, 2, d(_BALL), fill=True)
    canvas.text(2, H - 11, '•' * max(0, st['lives'] - 1), d(_PADDLE), size=8)
    canvas.text(W - 2, H - 11, str(st['score']), d(_TXT), size=8, align='right')


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
    launch = bool(controls and ('up' in (controls.taps or []) or held == 'up'))
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
        _step(st, move, auto=not playing, launch=launch)

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
