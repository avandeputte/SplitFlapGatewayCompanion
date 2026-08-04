"""Simon — the memory game where the sound IS the game.

A matrix-only interactive game on the shared framework, but unlike the arcade set the
speaker carries it: the machine plays a growing sequence of lit strips, each with its
own note, and the player echoes it back on the web-UI pad. Four full-height color
strips map left-to-right to ◄ ▲ ▼ ► (each strip wears its arrow), a correct press
echoes its strip and note, finishing a round appends a step and replays, and ONE
wrong press ends the game — fade to black behind GAME OVER, any key deals again. Go
idle mid-echo and the machine replays the sequence as a hint rather than failing you.
In attract mode it demos itself (and, framework rule, stays silent); a human hears
every note. Each frame is a handful of rects, streamed as binary.

The fetch cadence is the metronome: every call renders ONE beat of the state machine
(a lit step, the dark gap between steps, an echo flash) and returns exactly that
beat's duration, so playback timing rides the render loop with no clocks kept here.
"""

import random

_STRIPS = [((64, 200, 96), 262), ((255, 82, 82), 330),      # ◄ green C4   ▲ red E4
           ((255, 210, 64), 392), ((80, 168, 240), 523)]    # ▼ yellow G4  ► blue C5
_CTRL = {'left': 0, 'up': 1, 'down': 2, 'right': 3}
_ARROWS = ('l', 'u', 'd', 'r')
_INK = (14, 16, 22)                                          # the glyph on a lit strip
_TXT = (150, 150, 158)
_ATTRACT_CAP = 8                                             # demo restarts past this length
_HINT_AFTER = 70                                             # idle input frames -> replay
_FADE_STEPS = 16


def _extend(st):
    """Append the next step — deterministic per (game number, position), so a replayed
    game number rebuilds the same melody but every NEW game gets its own."""
    rng = random.Random(st['games'] * 977 + len(st['seq']) * 13)
    st['seq'].append(rng.randrange(4))


def _new_game(st):
    st.update(seq=[], cursor=0, sp=0, show_lit=False, phase='show', score=0,
              fade=0, freeze_presses=None, idle=0, flash=None, sfx=[])
    st['games'] = st.get('games', 0) + 1
    _extend(st)


def _state(W, H):
    st = getattr(_state, '_st', None)
    if st is None or st.get('W') != W or st.get('H') != H:
        st = _state._st = {'W': W, 'H': H}
        _new_game(st)
    return st


def _play_sfx(play_sound, events):
    ev = next((e for e in ('lose', 'win_round') if e in events), None)
    if ev == 'lose':
        play_sound(notes=[[110, 260], [98, 340]], vol=70)
    elif ev == 'win_round':
        play_sound(notes=[[523, 70], [659, 70], [784, 130]], vol=55)
    else:
        step = next((e for e in events if isinstance(e, int)), None)
        if step is not None:
            play_sound(notes=[[_STRIPS[step][1], 200]], vol=55)


def _draw(canvas, st, lit=None, dim=1.0):
    """The four strips (the lit one at full color), each with its arrow, and the
    score + a progress dot per sequence step along the bottom."""
    W, H = st['W'], st['H']

    def d(col, f=1.0):
        f = f * dim
        return col if f >= 1.0 else canvas.dim(col, f)

    canvas.clear((0, 0, 0))
    gap = 1
    sw = (W - 3 * gap) // 4
    x0 = (W - 4 * sw - 3 * gap) // 2
    for i, (col, _note) in enumerate(_STRIPS):
        x = x0 + i * (sw + gap)
        canvas.rect(x, 0, sw, H, d(col, 1.0 if i == lit else 0.22), fill=True)
        # the strip's arrow, drawn in dark ink so it reads lit or dim
        cx, cy, a = x + sw // 2, H // 2, max(3, min(6, sw // 5))
        kind = _ARROWS[i]
        if kind == 'l':
            canvas.triangle(cx - a, cy, cx + a, cy - a, cx + a, cy + a, d(_INK), fill=True)
        elif kind == 'r':
            canvas.triangle(cx + a, cy, cx - a, cy - a, cx - a, cy + a, d(_INK), fill=True)
        elif kind == 'u':
            canvas.triangle(cx, cy - a, cx - a, cy + a, cx + a, cy + a, d(_INK), fill=True)
        else:
            canvas.triangle(cx, cy + a, cx - a, cy - a, cx + a, cy - a, d(_INK), fill=True)
    if W >= 96:                                        # on a tiny panel the 8px score would
        canvas.text(W - 2, 1, str(st['score']), d(_TXT), size=8, align='right')   # bury a strip
    # progress: one dot per step, filled once echoed (input) / played (show)
    done = st['cursor'] if st['phase'] == 'input' else st['sp']
    n = len(st['seq'])
    if n and W >= n * 4 + 8:
        px = W // 2 - (n * 4 - 2) // 2
        for k in range(n):
            on = k < done
            canvas.rect(px + k * 4, H - 3, 2, 2, d((240, 244, 252) if on else (86, 94, 116)),
                        fill=True)


def _draw_gameover(canvas, W, H, score, appear, best=0, new_best=False):
    a = max(0.0, min(1.0, appear))
    title, tc = ('NEW BEST!', (255, 210, 63)) if new_best else ('GAME OVER', (255, 82, 82))
    canvas.text(W // 2, H // 2 - 9, title, canvas.dim(tc, a), size=10, align='center')
    tail = f' · BEST {best}' if best and not new_best and W >= 96 else ''
    canvas.text(W // 2, H // 2 + 3, f'SCORE {score}{tail}', canvas.dim((240, 240, 244), a),
                size=8, align='center')


def fetch_canvas(settings, canvas, controls=None, play_sound=None, game_store=None):
    W, H = canvas.width, canvas.height
    st = _state(W, H)

    playing = controls is not None and controls.active(within=canvas.num(settings, 'takeover', 30, 5, 120))
    events = list(controls.events) if controls is not None else []
    taps = [t for t in (controls.taps or []) if t in _CTRL] if controls is not None else []
    presses = controls.presses if controls is not None else 0

    if not playing and st.get('phase', 'show') == 'gameover':
        _new_game(st)                                  # attract resets a finished game
    if playing and ('start' in events or 'coin' in events):
        _new_game(st)

    speed = canvas.num(settings, 'speed', 5, 1, 10)
    on_hold = max(0.16, 0.44 - 0.028 * speed)          # a lit step's beat
    gap_hold = max(0.06, on_hold * 0.3)

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

    sfx = []

    if st['phase'] == 'show':
        # One beat per fetch: lit step (with its note), then the dark gap between steps.
        if not st['show_lit']:
            st['show_lit'] = True
            step = st['seq'][st['sp']]
            sfx.append(step)
            _draw(canvas, st, lit=step)
            canvas.show()
            if playing and play_sound:
                _play_sfx(play_sound, sfx)
            return on_hold
        st['show_lit'] = False
        st['sp'] += 1
        if st['sp'] >= len(st['seq']):
            st['phase'], st['cursor'], st['idle'] = 'input', 0, 0
        _draw(canvas, st)
        canvas.show()
        return gap_hold

    # -- input: the player (or the attract demo) echoes the sequence -----------------
    if not playing:
        # the demo answers correctly, one echo every third frame (readable pace),
        # and restarts a too-long melody so the attract loop stays watchable
        st['demo_t'] = st.get('demo_t', 0) + 1
        taps = []
        if st['demo_t'] % 3 == 0:
            taps = [('left', 'up', 'down', 'right')[st['seq'][st['cursor']]]]
        if len(st['seq']) > _ATTRACT_CAP:
            _new_game(st)
            taps = []

    flash = None
    for t in taps:
        want = st['seq'][st['cursor']]
        got = _CTRL[t]
        if got == want:
            st['cursor'] += 1
            st['idle'] = 0
            flash = got
            sfx.append(got)
            if st['cursor'] >= len(st['seq']):         # round complete: grow and replay
                st['score'] += 10
                sfx = ['win_round']
                _extend(st)
                st['phase'], st['sp'], st['show_lit'] = 'show', 0, False
                break
        else:
            if playing:
                st['phase'], st['fade'] = 'gameover', 0
                sfx = ['lose']
            else:
                _new_game(st)
            break

    if st['phase'] == 'input':
        st['idle'] += 1
        if st['idle'] >= _HINT_AFTER and playing:      # stuck? replay the melody as a hint
            st['phase'], st['sp'], st['show_lit'], st['cursor'] = 'show', 0, False, 0

    if playing and play_sound and sfx:                 # sound only while a human plays
        _play_sfx(play_sound, sfx)

    _draw(canvas, st, lit=flash)
    canvas.show()
    return 0.12 if flash is not None else 0.09
