"""Aquarium — a living reef drawn ON the panel with canvas draw-ops.

A canvas app that shows off the on-device draw vocabulary instead of pushing a
whole picture: a gradient water column, a few rising bubbles (circle), and fish
blitted from a sprite ATLAS — a couple dozen draw-ops a frame, not a frame of
pixels. The fish tiles are generated once with Pillow and uploaded to the panel's
atlas; each frame just says "blit fish 3 at (x, y)". On a wall without the sprite
op the fish fall back to being drawn from ops (ellipse + triangle). On a
compositing wall (``canvas.can_composite``) it adds what the ops surface newly allows:
additive **godrays** shimmering down from the surface and a soft **glow** around the
bubbles — per-color alpha + the additive blend mode ride the binary stream as batch
alpha (0x15), and (``aa_ok``) the bubbles are anti-aliased too, still at game rate
(no per-frame HTTP). Kept deliberately lean — every op is rendered by the wall each
frame, and the big LCD pays for each one (weeds were cut, bubbles are few).
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
    st['bubbles'] = []
    st['sig'] = (W, H, tile, n)


def fetch_canvas(settings, canvas):
    import random
    W, H = canvas.width, canvas.height
    # Every size and speed scales from the LED design height/width (64/256), so the same
    # scene reads identically on a 256x64 LED wall (all factors resolve to 1) and a
    # 1280x800 LCD (manifest lcd_ops: this draws live ops at native resolution there).
    mv = max(1.0, H / 64.0)                                    # vertical motion/stroke scale
    mh = max(1.0, W / 256.0)                                   # horizontal motion scale
    k = max(1, int(mv))                                        # integer stroke/radius scale
    tile = max(8, min(max(22, H * 22 // 64), min(240, H // 3))) & ~1   # the visible fish size
    # Keep the sprite SHEET small: draw the tiles at a capped source size and let the wall
    # scale the blit up on-device (integer 1-4). A 240px LCD fish from an 80px tile is a
    # ~190 KB sheet, not ~1.7 MB — the big sheet stalled the LCD's one-shot atlas upload
    # (which sits right under the panel's 2 MB atlas cap). On the LED walls tile<=20, so the
    # scale stays 1 and the source tile stays the visible tile: the sprite path is unchanged.
    blit_scale = max(1, min(4, -(-tile // 128)))
    src_tile = max(8, (tile // blit_scale) & ~1)

    n = canvas.num(settings, 'fish', 6, 1, 16)
    water = _WATER.get(str(settings.get('water', 'reef') or 'reef').lower(), _WATER['reef'])

    st = getattr(fetch_canvas, '_state', None)
    if st is None or st.get('sig') != (W, H, tile, n):
        st = st or {}
        setattr(fetch_canvas, '_state', st)
        _reset(st, W, H, n, tile)
        st['frame'] = 0
        st['atlas'] = None
    st['frame'] += 1
    frame = st['frame']

    # Re-assert the fish sheet EVERY frame — the fish are then blit-by-index. It is cheap: the
    # sheet is named by its content, so the pixels upload once and each frame only re-binds it (a
    # tiny op). Binding every frame is what keeps the fish rendering even after another canvas app's
    # sheet evicted ours from the shared atlas library — the batch never relies on a sticky bind.
    use_sprites = bool(getattr(canvas, 'can_sprite', False))
    if use_sprites:
        if st.get('tiles_for') != src_tile:                  # draw the sprites once per size, not per frame
            st['tiles'] = _fish_tiles(src_tile)
            st['tiles_for'] = src_tile
        canvas.upload_atlas(st['tiles'], persist=True)

    top, bot = water
    canvas.gradient(0, 0, W, H, top, bot, 'v')                 # the water column

    glow = bool(getattr(canvas, 'can_composite', False))
    aa = bool(getattr(canvas, 'aa_ok', False))     # smooth strokes only where they stay binary
    if glow:                                                   # godrays: additive light shafts
        canvas.blend('add')                                   # from the surface, slowly drifting
        for i in range(3):
            bx = int((i + 0.5) * W / 3 + math.sin(frame * 0.02 + i * 2.1) * W * 0.06)
            wtop, wbot = max(2, W // 22), max(4, W // 9)
            ray = [(bx - wtop, 0), (bx + wtop, 0), (bx + wbot, H), (bx - wbot, H)]
            canvas.poly(ray, (150, 205, 255, 30), fill=True)  # low-alpha, sums to a soft shaft
        canvas.blend('over')

    # bubbles: spawn near the floor, rise, pop at the top. Advance first, then draw, so the
    # additive halo and the crisp bubble land at the same position (no 1px glow offset).
    # A sparse column — every bubble costs the wall two circles a frame (glow + crisp).
    if random.random() < 0.25:
        st['bubbles'].append([random.uniform(2, W - 2), float(H), random.choice((1, 1, 2))])
    keep = []
    for b in st['bubbles']:
        b[1] -= (0.8 + b[2] * 0.3) * mv
        if b[1] > 0:
            keep.append(b)
    st['bubbles'] = keep[-12:]
    if glow:
        canvas.blend('add')
        for b in st['bubbles']:
            canvas.circle(int(b[0]), int(b[1]), b[2] * k + k, (90, 150, 210, 70), fill=True)
        canvas.blend('over')
    for b in st['bubbles']:
        canvas.circle(int(b[0]), int(b[1]), b[2] * k, (200, 235, 255), aa=aa)

    for f in st['fish']:                                       # drift the fish, wrap at the edges
        f['x'] += f['d'] * f['sp'] * mh
        y = int(f['y'] + math.sin(frame * 0.1 + f['ph']) * f['amp'] * mv)
        if f['d'] > 0 and f['x'] > W:
            f['x'] = -tile
        elif f['d'] < 0 and f['x'] < -tile:
            f['x'] = W
        x = int(f['x'])
        if use_sprites:
            canvas.sprite(2 * f['p'] + (0 if f['d'] > 0 else 1), x, y, scale=blit_scale)
        else:                                                  # no atlas: draw the fish from ops
            body, fin = _FISH[f['p']]
            cy = y + tile // 2
            canvas.ellipse(x + tile // 2, cy, tile // 3, tile // 4, body, fill=True)
            tx = x + tile if f['d'] > 0 else x
            canvas.triangle(tx, cy, tx - f['d'] * tile // 3, cy - tile // 4,
                            tx - f['d'] * tile // 3, cy + tile // 4, fin, fill=True)

    canvas.show()
    # ~10 fps on an LED panel; a huge panel (the 1280x800 LCD) renders each ops batch far
    # slower than it draws — measured ~2 fps drain — so pace to what it can actually show:
    # fewer, honest frames instead of a backlog of stale ones (jerky and seconds late).
    return 0.10 if W * H <= 131072 else 0.40
