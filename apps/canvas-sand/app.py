"""Falling Sand — a pour-and-pile toy, one grain per LED.

Not a game: grains pour from a moving spout, fall, tumble down slopes and settle
into dunes whose color drifts around the hue wheel as the pour runs — layered
sediment bands build up on their own. In attract mode the spout wanders by itself;
touch the web-UI pad and it's yours — left/right steer the pour, and any fresh key
press dumps a celebratory burst. When the pile swallows most of the panel the scene
fades to black and a fresh pour begins where the colors left off.

Per-pixel work, so this is a Pillow frame-push app (PUT /api/canvas/frame) like the
Stock Graph, not a draw-ops one: the settled world lives in a persistent image that
only gets new pixels written, and each frame is that image plus the grains still in
the air. The simulation only ever steps AIRBORNE grains — settled sand is finished
pixels — which is what keeps a 256-wide panel cheap.
"""

import colorsys
import random

_HUES = 128                                            # palette resolution around the wheel
_MAX_AIR = 600                                         # airborne-grain budget (spawns wait)
_FADE_STEPS = 14


def _palette():
    """The hue wheel at LED-friendly saturation — precomputed once."""
    pal = []
    for i in range(_HUES):
        r, g, b = colorsys.hsv_to_rgb(i / _HUES, 0.82, 1.0)
        pal.append((int(r * 255), int(g * 255), int(b * 255)))
    return pal


_PAL = _palette()


def _fresh(st):
    """A clean floor: empty grid, empty air, the persistent image wiped — the hue
    cursor deliberately survives, so each new pour starts where the last ended."""
    from PIL import Image
    W, H = st['W'], st['H']
    st.update(grid=bytearray(W * H), air=[], settled=0, phase='pour', fade=0,
              img=Image.new('RGB', (W, H), (0, 0, 0)), tick=0)


def _state(W, H):
    st = getattr(_state, '_st', None)
    if st is None or st.get('W') != W or st.get('H') != H:
        st = _state._st = {'W': W, 'H': H, 'hue': 0.0, 'pour_x': W / 2.0,
                           'seen_presses': None}
        _fresh(st)
    return st


def _spawn(st, n, spread=1):
    """Pour ``n`` grains around the spout. They all take the CURRENT hue — the wheel
    turns per tick (in fetch), not per grain, so the pile builds sediment bands
    instead of rainbow speckle."""
    rng = random.Random(st['tick'] * 17 + st['settled'] * 3 + n)
    W = st['W']
    for _ in range(n):
        if len(st['air']) >= _MAX_AIR:
            return
        x = int(st['pour_x']) + rng.randint(-spread, spread)
        if 0 <= x < W and not st['grid'][x]:            # spout row (y=0) must be free
            st['air'].append([x, 0, 1 + int(st['hue']) % _HUES])


def _settle(st, x, y, pi):
    idx = y * st['W'] + x
    if st['grid'][idx]:                                 # two airborne grains on one cell merge
        return
    st['grid'][idx] = pi
    st['img'].putpixel((x, y), _PAL[pi - 1])
    st['settled'] += 1


def _sim(st):
    """One gravity tick for every airborne grain: straight down, else tumble to a
    free diagonal, else settle where it stands. Settled sand never moves again."""
    W, H = st['W'], st['H']
    grid = st['grid']
    rng = random.Random(st['tick'])
    keep = []
    for g in st['air']:
        x, y, pi = g
        ny = y + 1
        if ny >= H:
            _settle(st, x, y, pi)
            continue
        if not grid[ny * W + x]:
            g[1] = ny
            keep.append(g)
            continue
        first = rng.choice((-1, 1))                     # coin-flip which slope to try first
        for dx in (first, -first):
            nx = x + dx
            if 0 <= nx < W and not grid[ny * W + nx] and not grid[y * W + nx]:
                g[0], g[1] = nx, ny
                keep.append(g)
                break
        else:
            _settle(st, x, y, pi)
    st['air'] = keep


def fetch_canvas(settings, canvas, controls=None, play_sound=None):
    from PIL import Image, ImageEnhance

    W, H = canvas.width, canvas.height
    st = _state(W, H)
    st['tick'] += 1
    st['hue'] += 0.12                                   # the wheel turns once per tick

    playing = controls is not None and controls.active(within=canvas.num(settings, 'takeover', 30, 5, 120))
    held = controls.dir if (controls and controls.dir) else None
    presses = controls.presses if controls is not None else 0

    # When the pile has swallowed most of the panel, fade the scene out and repour.
    if st['phase'] == 'fade':
        st['fade'] += 1
        st['img'] = ImageEnhance.Brightness(st['img']).enhance(0.78)
        if st['fade'] >= _FADE_STEPS:
            _fresh(st)
        canvas.frame(st['img'])
        return 0.09

    # The spout: a slow sine wander by itself, held left/right when a player has it.
    if playing:
        if held == 'left':
            st['pour_x'] -= 2.0
        elif held == 'right':
            st['pour_x'] += 2.0
    else:
        import math
        st['pour_x'] = W * (0.5 + 0.38 * math.sin(st['tick'] * 0.045))
    st['pour_x'] = max(1.0, min(W - 2.0, st['pour_x']))

    # Grains per tick, scaled to panel width so every size fills on a similar clock.
    rate = max(1, ((canvas.num(settings, 'speed', 5, 1, 10) + 1) * W) // 128)
    if st.get('seen_presses') is None:
        st['seen_presses'] = presses
    if playing and presses > st['seen_presses']:        # any fresh key: a celebratory dump
        _spawn(st, 40, spread=3)
    st['seen_presses'] = presses
    _spawn(st, rate)
    _sim(st)

    if st['settled'] >= 0.80 * W * (H - 1):
        st['phase'], st['fade'] = 'fade', 0

    frame = st['img'].copy()                            # settled world + the air on top
    for x, y, pi in st['air']:
        frame.putpixel((x, y), _PAL[pi - 1])
    canvas.frame(frame)
    return 0.09 if playing else 0.11
