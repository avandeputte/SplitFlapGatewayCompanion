"""Pong — the first arcade game, on the panel it was born for.

A matrix-only interactive game on the shared framework: you take the LEFT paddle
(held up/down on the web-UI pad), the right paddle is the machine's, and in attract
mode both sides play themselves until someone touches the pad. The AI tracks the
ball a shade slower than the ball can travel, so sharp angles beat it — the paddle
repaints the return angle by where the ball lands on it, and every return in a rally
adds pace until somebody misses. First to 7 wins; the board fades to black behind
the verdict and any key deals a new match. Attract play never ends — a finished
demo match quietly starts over. Sound only while a human plays. Each frame is a
dozen draw-ops, streamed as binary.
"""

import random

_INK = (240, 244, 252)
_NET = (86, 94, 116)
_SCORE = (150, 150, 158)
_WIN_SCORE = 7
_FADE_STEPS = 16


def _play_sfx(play_sound, events):
    ev = next((e for e in ('win', 'point', 'paddle', 'wall') if e in events), None)
    if ev == 'paddle':
        play_sound(notes=[[523, 25]], vol=45)
    elif ev == 'wall':
        play_sound(notes=[[330, 20]], vol=35)
    elif ev == 'point':
        play_sound(notes=[[196, 110], [165, 150]], vol=60)
    elif ev == 'win':
        play_sound(notes=[[523, 90], [659, 90], [784, 90], [1047, 200]], vol=70)


def _serve(st, toward):
    """Center the ball and send it at the side that just conceded (classic serve)."""
    W, H = st['W'], st['H']
    rng = random.Random(st['sl'] * 13 + st['sr'] * 29 + toward)
    v = 1.55
    st['rally'] = 0
    st['ball'] = {'x': W / 2.0, 'y': H / 2.0,
                  'vx': v * (1 if toward > 0 else -1) * 0.85,
                  'vy': v * rng.uniform(0.35, 0.75) * rng.choice((1, -1))}


def _new_game(st):
    H = st['H']
    st.update(ly=H / 2.0, ry=H / 2.0, sl=0, sr=0, phase='play', fade=0,
              freeze_presses=None, winner=None, sfx=[], tick=0, aim_l=0.0, aim_r=0.0)
    _serve(st, toward=random.Random(st['W']).choice((-1, 1)))


def _state(W, H):
    st = getattr(_state, '_st', None)
    if st is None or st.get('W') != W or st.get('H') != H:
        st = _state._st = {'W': W, 'H': H}
        _new_game(st)
    return st


def _track(y, target, speed):
    """Move a paddle center toward ``target``, capped at ``speed`` per tick."""
    return y + max(-speed, min(speed, target - y))


def _step(st, move=None, auto=False):
    """One tick: paddles, ball flight, wall/paddle bounces, points, match end."""
    W, H = st['W'], st['H']
    ph = max(8, H // 4)                                # paddle height
    b = st['ball']
    st['tick'] = st.get('tick', 0) + 1
    pspeed = 2.6
    # The machine follows the ball only while it approaches, at a pace a fraction
    # under the steepest return — a well-angled shot is how points are won. It aims
    # a fresh OFFSET from its paddle center every approach (re-rolled when the ball
    # turns), so returns leave at varied angles: two machines never flatline into a
    # dead-center forever-rally, and a wide aim near a steep ball is how one misses.
    aspeed = 1.45
    if b['vx'] < 0 and st.get('last_vx', 0) >= 0:
        rng = random.Random(st['tick'] * 7 + st['sl'] * 3 + st['sr'])
        st['aim_l'] = rng.uniform(-0.95, 0.95) * (ph / 2)
    elif b['vx'] > 0 and st.get('last_vx', 0) <= 0:
        rng = random.Random(st['tick'] * 11 + st['sr'] * 3 + st['sl'])
        st['aim_r'] = rng.uniform(-0.95, 0.95) * (ph / 2)
    st['last_vx'] = b['vx']
    if auto:
        st['ly'] = _track(st['ly'], b['y'] + st['aim_l'] if b['vx'] < 0 else H / 2.0, aspeed)
    elif move:
        st['ly'] += pspeed if move == 'd' else -pspeed
    st['ry'] = _track(st['ry'], b['y'] + st['aim_r'] if b['vx'] > 0 else H / 2.0, aspeed)
    st['ly'] = max(ph / 2 + 1, min(H - ph / 2 - 1, st['ly']))
    st['ry'] = max(ph / 2 + 1, min(H - ph / 2 - 1, st['ry']))

    b['x'] += b['vx']
    b['y'] += b['vy']
    if b['y'] <= 1 or b['y'] >= H - 2:
        b['vy'] = -b['vy']
        b['y'] = max(1, min(H - 2, b['y']))
        st['sfx'].append('wall')

    # Paddle faces: left at x=4, right at x=W-5 — a CROSSING window (not one-sided),
    # so a ball already past the face can't be caught from behind. A hit repaints
    # the angle by the landing offset (edge = steep) and adds pace for the rally.
    speed = 1.55 + 0.12 * min(8, st['rally'])
    if b['vx'] < 0 and 2 <= b['x'] <= 4.5 and abs(b['y'] - st['ly']) <= ph / 2 + 1:
        off = (b['y'] - st['ly']) / (ph / 2)
        b['vx'], b['vy'] = speed * 0.85, speed * off * 0.9
        b['x'] = 4
        st['rally'] += 1
        st['sfx'].append('paddle')
    elif b['vx'] > 0 and W - 5.5 <= b['x'] <= W - 3 and abs(b['y'] - st['ry']) <= ph / 2 + 1:
        off = (b['y'] - st['ry']) / (ph / 2)
        b['vx'], b['vy'] = -speed * 0.85, speed * off * 0.9
        b['x'] = W - 5
        st['rally'] += 1
        st['sfx'].append('paddle')
    elif b['x'] < -2:                                  # past the left edge: right scores
        st['sr'] += 1
        st['sfx'].append('point')
        _point_over(st, auto, winner='r', toward=-1)
    elif b['x'] > W + 2:
        st['sl'] += 1
        st['sfx'].append('point')
        _point_over(st, auto, winner='l', toward=1)


def _point_over(st, auto, winner, toward):
    if max(st['sl'], st['sr']) >= _WIN_SCORE:
        if auto:
            _new_game(st)                              # attract: straight into a fresh match
        else:
            st['phase'], st['fade'] = 'gameover', 0
            st['winner'] = winner
            st['sfx'].append('win')
    else:
        _serve(st, toward)


def _draw(canvas, st, dim=1.0):
    W, H = st['W'], st['H']
    ph = max(8, H // 4)

    def d(col):
        return col if dim >= 1.0 else canvas.dim(col, dim)

    canvas.clear((0, 0, 0))
    for y in range(0, H, 6):                           # the net, dashed down the middle
        canvas.rect(W // 2 - 1, y, 1, 3, d(_NET), fill=True)
    canvas.text(W // 2 - 5, 1, str(st['sl']), d(_SCORE), size=8, align='right')
    canvas.text(W // 2 + 5, 1, str(st['sr']), d(_SCORE), size=8)
    canvas.rect(2, int(st['ly'] - ph / 2), 2, ph, d(_INK), fill=True)
    canvas.rect(W - 4, int(st['ry'] - ph / 2), 2, ph, d(_INK), fill=True)
    b = st['ball']
    canvas.rect(int(b['x']) - 1, int(b['y']) - 1, 2, 2, d(_INK), fill=True)


def _draw_gameover(canvas, W, H, st, appear):
    a = max(0.0, min(1.0, appear))
    verdict = 'YOU WIN' if st['winner'] == 'l' else 'AI WINS'
    canvas.text(W // 2, H // 2 - 9, verdict, canvas.dim((255, 210, 63), a), size=10,
                align='center')
    canvas.text(W // 2, H // 2 + 3, f"{st['sl']} - {st['sr']}",
                canvas.dim((240, 240, 244), a), size=8, align='center')


def fetch_canvas(settings, canvas, controls=None, play_sound=None):
    W, H = canvas.width, canvas.height
    st = _state(W, H)

    playing = controls is not None and controls.active(within=canvas.num(settings, 'takeover', 30, 5, 120))
    events = list(controls.events) if controls is not None else []
    held = controls.dir if (controls and controls.dir) else None
    move = {'up': 'u', 'down': 'd'}.get(held)
    presses = controls.presses if controls is not None else 0

    if not playing and st.get('phase', 'play') != 'play':
        _new_game(st)                                  # attract resets a finished match
    if playing and ('start' in events or 'coin' in events):
        _new_game(st)

    if playing and st.get('phase') == 'gameover':
        if st.get('freeze_presses') is None:
            st['freeze_presses'] = presses
        st['fade'] = min(st.get('fade', 0) + 1, _FADE_STEPS)
        if presses > st['freeze_presses']:             # any key → new match
            _new_game(st)
        else:
            _draw(canvas, st, dim=max(0.0, 1 - st['fade'] / _FADE_STEPS))
            _draw_gameover(canvas, W, H, st, st['fade'] / (_FADE_STEPS * 0.5))
            canvas.show()
            return 0.08

    if playing and 'pause' in events:
        st['paused'] = not st.get('paused', False)

    if not (playing and st.get('paused')):
        _step(st, move=move, auto=not playing)

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
