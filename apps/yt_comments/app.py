"""Newest YouTube comments on the followed video, one per page."""


# =============================================================================
# SHARED — the comment DATA: the commentThreads call both surfaces page through.
# =============================================================================

def _comments(settings):
    """The followed video's newest top-level comments as [(author, text)], newest
    first. Raises on network/API trouble — callers decide the fallback."""
    import requests
    r = requests.get(
        'https://www.googleapis.com/youtube/v3/commentThreads',
        params={'part': 'snippet', 'videoId': settings.get('yt_video_id', ''),
                'key': settings.get('yt_api_key', ''), 'maxResults': 10, 'order': 'time'},
        timeout=10
    ).json()
    out = []
    for item in r.get('items', []):
        s = item['snippet']['topLevelComment']['snippet']
        # textOriginal is the comment as typed; textDisplay is HTML — its
        # entities (&#39;) and tags (<br>) would land on the flaps verbatim.
        out.append((s['authorDisplayName'], s.get('textOriginal') or s.get('textDisplay', '')))
    return out


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols):
    video_id = settings.get('yt_video_id', '')
    api_key = settings.get('yt_api_key', '')
    if not video_id or not api_key:
        return [format_lines('Comments', 'Missing', 'Config')]
    try:
        pages = []
        cols = get_cols()
        rows = get_rows()
        for author, text in _comments(settings):
            author = author[:cols]
            # split text into lines that fit the display
            text_lines = [text[j:j + cols] for j in range(0, len(text), cols)]
            text_lines = text_lines[:rows - 1]  # leave room for author
            lines = [author] + text_lines
            pages.append(format_lines(*lines[:rows]))
        return pages or [format_lines('Comments', 'None found', '')]
    except Exception:
        return [format_lines('Comments', 'Error', 'Check config')]


def trigger(settings, conditions):
    """Fire when a new comment appears on the followed video."""
    import requests

    video_id = settings.get('yt_video_id', '')
    api_key = settings.get('yt_api_key', '')
    keyword = conditions.get('keyword', '').upper().strip()
    if not video_id or not api_key:
        return False

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'seen_ids': set()}
        setattr(trigger, '_state', state)

    try:
        r = requests.get(
            'https://www.googleapis.com/youtube/v3/commentThreads',
            params={'part': 'snippet', 'videoId': video_id, 'key': api_key,
                    'maxResults': 5, 'order': 'time'},
            timeout=10
        ).json()
        for item in r.get('items', []):
            cid = item.get('id', '')
            if cid in state['seen_ids']:
                continue
            state['seen_ids'].add(cid)
            if not keyword:
                return True
            s = item['snippet']['topLevelComment']['snippet']
            text = (s.get('textOriginal') or s.get('textDisplay', '')).upper()
            if keyword in text:
                return True
        if len(state['seen_ids']) > 500:
            state['seen_ids'] = set(list(state['seen_ids'])[-200:])
    except Exception:
        raise
    return False


# =============================================================================
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# The same comments, one text card at a time: the author in the accent color
# over a hairline rule, the comment wrapped large below, a quiet i/N page mark
# in the corner. Rotates through the list on the loop delay; holds the last
# fetched comments across a network hiccup. Solid black background.
# =============================================================================

_CV_AUTHOR = (85, 200, 255)           # the author accent — comment-thread blue
_CV_TXT = (238, 240, 244)
_CV_DIM = (120, 126, 136)
_CV_RULE = (55, 60, 70)


def _cv_wrap(font, text, max_w, max_lines):
    """Greedy word-wrap of ``text`` to pixel width ``max_w``, at most ``max_lines`` lines."""
    words, lines, cur = str(text or '').split(), [], ''
    for w in words:
        cand = f'{cur} {w}'.strip()
        if not cur or font.getlength(cand) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines[:max_lines] or ['']


def _cv_wrap_fit(canvas, text, max_w, max_h, max_lines, min_size=8):
    """Largest font at which the WHOLE of ``text`` wraps into <= ``max_lines`` lines
    fitting the box — shrinking (never below ``min_size``: staying readable beats
    staying complete) rather than silently dropping words. Returns
    (font, lines, line_height, gap); at the floor the tail may still be cut."""
    words_n = len(str(text or '').split())
    size = max(min_size, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = _cv_wrap(font, text, max_w, max_lines)
        b = font.getbbox('Ag')
        lh = b[3] - b[1]
        gap = max(1, lh // 6)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((font.getlength(ln) for ln in lines), default=0)
        complete = sum(len(ln.split()) for ln in lines) == words_n
        if size <= min_size or (total <= max_h and widest <= max_w and complete):
            if size <= min_size:                     # at the floor: drop lines, not below the box
                lines = lines[:max(1, int((max_h + gap) // (lh + gap)))]
            return font, lines, lh, gap
        size -= 1
    font = canvas.font(min_size)
    lines = _cv_wrap(font, text, max_w, max_lines)
    b = font.getbbox('Ag')
    return font, lines, b[3] - b[1], 1


def _cv_trim(font, s, max_w):
    """``s`` trimmed with an ellipsis until it fits ``max_w`` (never past empty)."""
    if font.getlength(s) <= max_w:
        return s
    while s and font.getlength(s + '…') > max_w:
        s = s[:-1]
    return (s + '…') if s else ''


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (missing config / API error / no comments). Local:
    this card sizes its lines at H*0.30/0.20 (canvas.message uses 0.32/0.22) and
    keeps the app's own text colors."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = canvas.fit_font(line1, W - 4, int(H * 0.30))
    f2 = canvas.fit_font(line2, W - 4, int(H * 0.20)) if line2 else None
    h1 = canvas.ink(f1, line1)
    h2 = canvas.ink(f2, line2) if line2 else 0
    y = (H - (h1 + (3 if line2 else 0) + h2)) / 2.0
    canvas.text_top(draw, (W - f1.getlength(line1)) / 2.0, y, line1, f1, _CV_TXT)
    if line2:
        canvas.text_top(draw, (W - f2.getlength(line2)) / 2.0, y + h1 + 3, line2, f2, _CV_DIM)
    return img


def _cv_tall(canvas, ImageDraw, author, text, idx, total):
    """The 1.6:1 LCD card: a compact author header over a rule, then the comment
    wrapped as LARGE type filling the body — the comment IS the content, so the
    tall panel's height is spent on it, not banked as a void. Uses the shared
    ratio-jumped wrap_fit (the LED path's local fitter can't reach a fitting size
    from an LCD's tall start and falls to the 8px floor)."""
    W, H = int(canvas.width), int(canvas.height)
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 5

    mark = f'{idx + 1}/{total}'
    mf = canvas.fit_font(mark, int(W * 0.22), max(9, int(H * 0.09)))
    mw = mf.getlength(mark) + 6
    af = canvas.fit_font('Ag', W, int(H * 0.12))
    ah = canvas.ink(af, 'Ag')
    an = _cv_trim(af, str(author), W - 2 * pad - mw)
    canvas.text_top(draw, pad, pad, an, af, _CV_AUTHOR)
    canvas.text_top(draw, W - pad - mf.getlength(mark),
                    pad + max(0, (ah - canvas.ink(mf, mark)) // 2), mark, mf, _CV_DIM)
    ry = pad + ah + 4
    draw.line([(pad, ry), (W - pad - 1, ry)], fill=_CV_RULE)

    body_top = ry + 6
    avail = H - pad - body_top
    f, lines = canvas.wrap_fit(text, W - 2 * pad, avail, max(3, avail // 16))
    lh = canvas.ink(f, 'Ag')
    gap = max(2, lh // 5)
    block = len(lines) * lh + (len(lines) - 1) * gap
    y = body_top + max(0, (avail - block) // 2)     # the block centered in the body
    for ln in lines:
        canvas.text_top(draw, pad, y, ln, f, _CV_TXT)
        y += lh + gap
    return img


def _cv_tall_ops(canvas, author, text, idx, total, W, H):
    """The tall card as on-device DRAW OPS — the gtext-era twin of _cv_tall, for the
    LCD (manifest ``lcd_ops``): the author header and rule, the comment as large
    scalable type centered in the body — rendered by the wall at native resolution
    instead of a 256x160 frame upscaled x5. Same composition, every measure a
    fraction of the panel."""
    canvas.clear((0, 0, 0))
    pad = max(5, int(W * 0.02))

    # Header: author in the accent, the i/N mark opposite, a hairline rule under.
    # gtext's y is the ascent-box top (ink starts ~0.2 of the size below), so the
    # author's box is backed off to put its ink on the pad line like text_top did.
    mark = f'{idx + 1}/{total}'
    msz = canvas.fit_gtext(mark, int(W * 0.22), max(9, int(H * 0.09)))
    mw = int(canvas.text_width(mark, msz)) + max(6, int(W * 0.024))
    asz = max(9, int(H * 0.11))
    ay = pad - int(asz * 0.18)
    an = str(author)
    if canvas.text_width(an, asz) > W - 2 * pad - mw:      # _cv_trim, in gtext units
        while an and canvas.text_width(an + '…', asz) > W - 2 * pad - mw:
            an = an[:-1]
        an = (an + '…') if an else ''
    canvas.gtext(pad, ay, an, color=_CV_AUTHOR, size=asz)
    canvas.gtext(W - pad, ay + int(0.565 * (asz - msz)), mark,
                 color=_CV_DIM, size=msz, align='right')
    ry = pad + int(asz * 0.97) + max(4, int(H * 0.025))
    canvas.line(pad, ry, W - 1 - pad, ry, color=_CV_RULE, t=max(1, int(H * 0.006)))

    # The comment wrapped as LARGE as the body holds, the block centered in it —
    # an ellipsis owns the tail on the rare comment even the 8px floor can't hold.
    body_top = ry + max(6, int(H * 0.038))
    avail = H - pad - body_top
    max_lines = max(3, avail // max(1, int(H * 0.10)))
    size, lines = canvas.fit_wrap_gtext(text, W - 2 * pad, avail, max_lines=max_lines)
    if sum(len(ln.split()) for ln in lines) < len(str(text).split()):
        last = lines[-1]
        while last and canvas.text_width(last + '…', size) > W - 2 * pad:
            last = last[:-1]
        lines[-1] = last + '…'
    step = int(size * 1.2)
    block = (len(lines) - 1) * step + size
    y = body_top + max(0, (avail - block) // 2)
    for ln in lines:
        canvas.gtext(pad, y, ln, color=_CV_TXT, size=size)
        y += step
    canvas.show()


def fetch_canvas(settings, canvas):
    from PIL import ImageDraw

    if not settings.get('yt_video_id', '') or not settings.get('yt_api_key', ''):
        canvas.frame(_cv_message(canvas, ImageDraw, 'Comments', 'Set video ID + API key'))
        return 60.0

    st = getattr(fetch_canvas, '_state', None)
    if st is None:
        st = {'i': 0, 'last': None}
        setattr(fetch_canvas, '_state', st)
    try:
        st['last'] = _comments(settings)
    except Exception:
        pass                                        # keep the last good list across a hiccup
    comments = st['last']
    if comments is None:
        canvas.frame(_cv_message(canvas, ImageDraw, 'Comments', 'Check config'))
        return 30.0
    if not comments:
        canvas.frame(_cv_message(canvas, ImageDraw, 'Comments', 'None yet'))
        return 30.0

    idx = st['i'] % len(comments)
    st['i'] = (st['i'] + 1) % len(comments)
    author, text = comments[idx]

    W, H = int(canvas.width), int(canvas.height)
    if getattr(canvas, 'can_gtext', False) and H >= 96:
        # The big-panel path: live ops at native resolution (crisp TTF header + comment).
        _cv_tall_ops(canvas, author, text, idx, len(comments), W, H)
        try:
            dwell = float(settings.get('loop_delay', 8) or 8)
        except (TypeError, ValueError):
            dwell = 8.0
        return max(3.0, min(30.0, dwell))

    if int(canvas.height) >= 96:                     # tall LCD — the comment fills the body
        canvas.frame(_cv_tall(canvas, ImageDraw, author, text, idx, len(comments)))
        try:
            dwell = float(settings.get('loop_delay', 8) or 8)
        except (TypeError, ValueError):
            dwell = 8.0
        return max(3.0, min(30.0, dwell))

    W, H = int(canvas.width), int(canvas.height)
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3                                         # side margin; the top ink rides row 1

    # Author line in the accent color, a hairline rule under it.
    mark = f'{idx + 1}/{len(comments)}'
    af = canvas.fit_font('Ag', W, max(7, int(H * 0.16)))
    mf = canvas.fit_font(mark, int(W * 0.2), max(6, int(H * 0.11))) if H >= 48 else None
    mw = (mf.getlength(mark) + 4) if mf else 0
    an = _cv_trim(af, str(author), W - 2 * pad - mw)
    canvas.text_top(draw, pad, 1, an, af, _CV_AUTHOR)
    if mf:
        canvas.text_top(draw, W - pad - mf.getlength(mark), 2, mark, mf, _CV_DIM)
    ah = af.getbbox('Ag')[3] - af.getbbox('Ag')[1]
    ry = 1 + ah + 2
    draw.line([(pad, ry), (W - pad - 1, ry)], fill=_CV_RULE)

    # The comment itself, wrapped as large as the room allows — the line budget
    # comes from the height, and the wrapped block is let down to the panel's
    # bottom edge, the leftover spread into the leading, not banked as dark rows.
    body_top = ry + 3
    avail = H - 1 - body_top
    max_lines = max(2, avail // 8)
    f, lines, lh, gap = _cv_wrap_fit(canvas, text, W - 2 * pad, avail, max_lines)
    if sum(len(ln.split()) for ln in lines) < len(str(text).split()):
        last = lines[-1]                            # cut off — say so, don't just stop
        while last and f.getlength(last + '…') > W - 2 * pad:
            last = last[:-1]
        lines[-1] = last + '…'
    block = len(lines) * lh + (len(lines) - 1) * gap
    if len(lines) > 1:
        gap += min(max(0, avail - block) // (len(lines) - 1), max(2, lh // 3))
        block = len(lines) * lh + (len(lines) - 1) * gap
    lb = f.getbbox(lines[-1] or '0')                # anchor on the last line's REAL ink —
    block += (lb[3] - lb[1]) - lh                   # no descenders means less ink than 'Ag' says
    y = body_top + max(0, avail - block)
    for ln in lines:
        canvas.text_top(draw, pad, y, ln, f, _CV_TXT)
        y += lh + gap
    canvas.frame(img)

    try:
        dwell = float(settings.get('loop_delay', 8) or 8)
    except (TypeError, ValueError):
        dwell = 8.0
    return max(3.0, min(30.0, dwell))
