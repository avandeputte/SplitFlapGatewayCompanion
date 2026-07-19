"""Aquarium — a living reef drawn ON the panel with canvas draw-ops.

A canvas app that shows off the on-device draw vocabulary instead of pushing a
whole picture: a gradient water column, swaying weeds (polyline), rising bubbles
(circle), and fish blitted from a sprite ATLAS — a few dozen draw-ops a frame, not
a frame of pixels. The fish tiles are generated once with Pillow and uploaded to
the panel's atlas; each frame just says "blit fish 3 at (x, y)". On a wall without
the sprite op the fish fall back to being drawn from ops (ellipse + triangle).
"""

import math

# Each fish: (body, fin) — kept clear of pink so it reads on a small panel.
_FISH = [((255, 140, 45), (255, 210, 120)), ((70, 170, 255), (200, 240, 255)),
         ((80, 220, 140), (210, 255, 220)), ((255, 205, 70), (255, 245, 190)),
         ((240, 120, 90), (255, 200, 170))]
_MAGENTA = (255, 0, 255)                 # the atlas transparent key
_WATER = {
    'reef': ((28, 140, 190), (8, 55, 105)),
    'deep': ((12, 60, 105), (2, 12, 34)),
    'dusk': ((70, 55, 125), (16, 18, 52)),
}
_WEED = (36, 150, 96)


def _fish_tiles(s):
    """The sprite atlas: each palette right- then left-facing, on magenta."""
    from PIL import Image, ImageDraw
    tiles = []
    for body, fin in _FISH:
        im = Image.new('RGB', (s, s), _MAGENTA)
        d = ImageDraw.Draw(im)
        cy = s / 2.0
        d.polygon([(s * 0.22, cy), (s * 0.02, cy - s * 0.24), (s * 0.02, cy + s * 0.24)], fill=fin)  # tail (left)
        d.ellipse([s * 0.18, cy - s * 0.24, s * 0.80, cy + s * 0.24], fill=body)                     # body
        d.polygon([(s * 0.40, cy - s * 0.22), (s * 0.62, cy - s * 0.22), (s * 0.51, cy - s * 0.40)], fill=fin)  # dorsal fin
        d.ellipse([s * 0.64, cy - s * 0.12, s * 0.64 + s * 0.16, cy + s * 0.04], fill=(255, 255, 255))          # eye
        d.ellipse([s * 0.69, cy - s * 0.07, s * 0.69 + s * 0.07, cy], fill=(15, 15, 15))
        tiles.append(im)                                       # right-facing (head to the right)
        tiles.append(im.transpose(Image.FLIP_LEFT_RIGHT))      # left-facing
    return tiles


def _reset(st, W, H, n, tile):
    """(Re)seed the scene for a new panel size / fish count."""
    import random
    rng = random.Random(1234)                                  # steady layout, not a new shuffle each restart
    st['fish'] = []
    for _ in range(n):
        d = rng.choice((1, -1))
        st['fish'].append({
            'p': rng.randrange(len(_FISH)),
            'x': rng.uniform(0, W), 'y': rng.uniform(tile, max(tile + 1, H - tile)),
            'd': d, 'sp': rng.uniform(0.35, 0.9), 'amp': rng.uniform(1.0, 2.4),
            'ph': rng.uniform(0, 6.28),
        })
    st['weeds'] = [(int(x), rng.uniform(0, 6.28)) for x in
                   range(4, max(5, W), max(10, W // 6))]
    st['bubbles'] = []
    st['sig'] = (W, H, tile, n)


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None
    import random
    W, H = canvas.width, canvas.height
    tile = max(8, min(22, H // 3)) & ~1                        # even, ~a third of the panel

    try:
        n = max(1, min(16, int(float(settings.get('fish', 6) or 6))))
    except (TypeError, ValueError):
        n = 6
    water = _WATER.get(str(settings.get('water', 'reef') or 'reef').lower(), _WATER['reef'])

    st = getattr(fetch, '_state', None)
    if st is None or st.get('sig') != (W, H, tile, n):
        st = st or {}
        setattr(fetch, '_state', st)
        _reset(st, W, H, n, tile)
        st['frame'] = 0
        st['atlas'] = None
    st['frame'] += 1
    frame = st['frame']

    # Upload the sprite atlas once (and re-assert every few seconds in case another
    # canvas app overwrote it) — the fish are then just blit-by-index.
    use_sprites = bool(getattr(canvas, 'can_sprite', False))
    if use_sprites and (st.get('atlas') != tile or frame % 60 == 1):
        canvas.upload_atlas(_fish_tiles(tile))
        st['atlas'] = tile

    top, bot = water
    canvas.gradient(0, 0, W, H, top, bot, 'v')                 # the water column

    for x, ph in st['weeds']:                                  # swaying weeds along the floor
        sway = math.sin(frame * 0.08 + ph) * (W * 0.02)
        h = int(H * 0.32)
        pts = [(x, H), (x + sway * 0.4, H - h * 0.5), (x + sway, H - h)]
        canvas.polyline(pts, _WEED)

    # bubbles: spawn near the floor, rise, pop at the top
    if random.random() < 0.5:
        st['bubbles'].append([random.uniform(2, W - 2), float(H), random.choice((1, 1, 2))])
    keep = []
    for b in st['bubbles']:
        b[1] -= 0.8 + b[2] * 0.3
        if b[1] > 0:
            canvas.circle(int(b[0]), int(b[1]), b[2], (200, 235, 255))
            keep.append(b)
    st['bubbles'] = keep[-40:]

    for f in st['fish']:                                       # drift the fish, wrap at the edges
        f['x'] += f['d'] * f['sp']
        y = int(f['y'] + math.sin(frame * 0.1 + f['ph']) * f['amp'])
        if f['d'] > 0 and f['x'] > W:
            f['x'] = -tile
        elif f['d'] < 0 and f['x'] < -tile:
            f['x'] = W
        x = int(f['x'])
        if use_sprites:
            canvas.sprite(2 * f['p'] + (0 if f['d'] > 0 else 1), x, y)
        else:                                                  # no atlas: draw the fish from ops
            body, fin = _FISH[f['p']]
            cy = y + tile // 2
            canvas.ellipse(x + tile // 2, cy, tile // 3, tile // 4, body, fill=True)
            tx = x + tile if f['d'] > 0 else x
            canvas.triangle(tx, cy, tx - f['d'] * tile // 3, cy - tile // 4,
                            tx - f['d'] * tile // 3, cy + tile // 4, fin, fill=True)

    canvas.show()
    return 0.12                                                # ~8 fps
