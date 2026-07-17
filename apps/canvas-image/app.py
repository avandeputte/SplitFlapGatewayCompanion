"""Mirror an image onto a Matrix panel (the raw-frame path of the `canvas` capability).

A canvas app: it renders a picture to the panel's exact pixel size and pushes it
as one full-colour frame (PUT /api/canvas/frame). With no URL set it draws a
colour-gradient demo, so it shows something out of the box. The image is fetched
and fitted once, then held (loop_delay), because a frame is the heaviest thing
you can send a wall.
"""


def _demo(Image, w, h):
    """A colour gradient — proof the panel is drawing true colour, no URL needed."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (int(255 * x / max(1, w - 1)),
                        int(255 * y / max(1, h - 1)),
                        int(255 * (1 - x / max(1, w - 1))))
    return img


def _fit(img, w, h, mode):
    iw, ih = img.size
    if mode == "contain":
        scale = min(w / iw, h / ih)
        img = img.resize((max(1, int(iw * scale)), max(1, int(ih * scale))))
        canvas_img = img.__class__.new("RGB", (w, h), (0, 0, 0))
        canvas_img.paste(img, ((w - img.size[0]) // 2, (h - img.size[1]) // 2))
        return canvas_img
    # cover: scale to fill, centre-crop
    scale = max(w / iw, h / ih)
    img = img.resize((max(1, int(iw * scale)), max(1, int(ih * scale))))
    left, top = (img.size[0] - w) // 2, (img.size[1] - h) // 2
    return img.crop((left, top, left + w, top + h))


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None
    from PIL import Image
    import io
    import requests

    w, h = canvas.width, canvas.height
    url = str(settings.get('image_url', '') or '').strip()

    # Cache the fitted frame so a static image isn't re-fetched every pass.
    state = getattr(fetch, '_state', None)
    key = (url, w, h, settings.get('fit'))
    if state and state.get('key') == key:
        canvas.frame(state['bytes'])
        return None

    try:
        if url:
            data = requests.get(url, timeout=10,
                                headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'}).content
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img = _fit(img, w, h, str(settings.get('fit', 'cover')))
        else:
            img = _demo(Image, w, h)
        raw = img.tobytes()
        fetch._state = {'key': key, 'bytes': raw}
        canvas.frame(raw)
    except Exception:
        canvas.frame(_demo(Image, w, h).tobytes())    # a broken URL still shows something
    return None
