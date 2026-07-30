"""Photo Frame — a slideshow of the microSD card's photos (and movies) on the panel.

The first app built on the gateway's fw-3.10 microSD support: the gateway's own Files
tab manages the card; this gives it a *display* use. Each dwell the app advances to the
next item in the configured folder (``canvas.sd_list``). A photo is pulled off the card
(``canvas.sd_get``) and frame-pushed — EXIF-rotated, resized to fill (center-crop) or
letterbox onto a dim blurred backdrop, LRU-cached so a small carousel re-downloads
nothing. An MPGA movie (fw 3.13, ``canvas.can_sd_anim``) is not fetched at all: the wall
streams it straight off the card (``canvas.play_anim_path``), looping on-device until
the rotation moves on. The folder listing refreshes about once a minute so newly dropped
files join the rotation without a restart. Without a card (or with an empty folder) it
shows a friendly hint card instead. Matrix-only — flaps can't show a photo.
"""

import io
import random

_IMG_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')
_MOV_EXTS = ('.mpg', '.mpga')            # the wall's own animation format, streamed from card
_LIST_TTL = 12          # frames between folder re-lists (~2 min at the 10 s default dwell)
_CACHE_MAX = 8          # decoded frames kept, LRU


def _media_in(canvas, folder):
    """The image (and, on a fw-3.13 wall, movie) files in ``folder`` (falling back to
    the card root), sorted by name."""
    exts = _IMG_EXTS + (_MOV_EXTS if getattr(canvas, 'can_sd_anim', False) else ())
    for path in (folder, '/'):
        rows = canvas.sd_list(path)
        names = sorted(str(r.get('name', '')) for r in rows
                       if not r.get('dir') and str(r.get('name', '')).lower().endswith(exts))
        if names:
            base = path.rstrip('/')
            return [f"{base}/{n}" for n in names]
        if path == folder and any(True for _ in rows):
            return []                          # the folder exists but holds no media
    return []


def _render(canvas, raw, fit):
    """Decode + lay out one photo as a panel-sized PIL image (None if undecodable)."""
    from PIL import Image, ImageFilter, ImageOps
    W, H = canvas.width, canvas.height
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert('RGB')   # phones store rotation in EXIF
    except Exception:
        return None
    if fit == 'contain':
        # Letterbox on a dim, blurred stretch of the photo itself — black bars read as
        # dead panel; a soft echo of the image reads as a frame.
        back = img.resize((W, H)).filter(ImageFilter.GaussianBlur(6)).point(lambda v: v // 3)
        fg = ImageOps.contain(img, (W, H))
        back.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
        return back
    return ImageOps.fit(img, (W, H))           # cover: fill the panel, center-cropped


def fetch_matrix(settings, canvas):
    dwell = canvas.num(settings, 'dwell', 10, 3, 300)
    if not getattr(canvas, 'can_sd', False):
        canvas.frame(canvas.message('PHOTO FRAME', 'No microSD card on this wall'))
        return 30
    folder = '/' + str(settings.get('folder', '/photos') or '/photos').strip().strip('/')
    fit = str(settings.get('fit', 'cover') or 'cover').strip().lower()
    shuffle = str(settings.get('shuffle', 'no')).strip().lower() in ('1', 'true', 'yes', 'on')

    st = getattr(fetch_matrix, '_state', None)
    if st is None or st.get('sig') != (canvas.width, canvas.height, folder, shuffle):
        st = {'sig': (canvas.width, canvas.height, folder, shuffle),
              'paths': [], 'age': _LIST_TTL, 'i': -1, 'cache': {}}
        setattr(fetch_matrix, '_state', st)

    st['age'] += 1
    if st['age'] >= _LIST_TTL:                 # refresh the listing; keep our place on no change
        st['age'] = 0
        paths = _media_in(canvas, folder)
        if shuffle:
            random.Random(len(paths) * 1009 + sum(map(len, paths))).shuffle(paths)
        if paths != st['paths']:
            st['paths'], st['i'] = paths, -1
    if not st['paths']:
        canvas.frame(canvas.message('PHOTO FRAME', f'No photos in {folder} — use the'
                                                   ' gateway Files tab to add some'))
        return 30

    # Advance; skip anything that vanished or won't decode/play (up to one full lap).
    for _ in range(len(st['paths'])):
        st['i'] = (st['i'] + 1) % len(st['paths'])
        path = st['paths'][st['i']]
        if path.lower().endswith(_MOV_EXTS):
            # A movie never crosses the wire: the wall streams it off its own card and
            # loops until the next item (a photo frame or another movie) replaces it.
            if canvas.play_anim_path(path):     # {} = the wall refused (non-2xx)
                return dwell
            continue                           # unplayable (deleted, bad file) — next item
        key = (path, fit)
        img = st['cache'].pop(key, None)       # pop+reinsert = LRU touch
        if img is None:
            raw = canvas.sd_get(path)
            img = _render(canvas, raw, fit) if raw else None
        if img is not None:
            st['cache'][key] = img
            while len(st['cache']) > _CACHE_MAX:
                st['cache'].pop(next(iter(st['cache'])))
            canvas.frame(img)
            return dwell
    canvas.frame(canvas.message('PHOTO FRAME', 'No readable photos on the card'))
    return 30
