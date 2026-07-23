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
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
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


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 5px)."""
    size = max(5, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 5 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


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


def _cv_text(draw, x, y, text, font, fill):
    """Baseline-corrected text draw (y is the ink top, whatever the glyph bbox says)."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill)


def _cv_trim(font, s, max_w):
    """``s`` trimmed with an ellipsis until it fits ``max_w`` (never past empty)."""
    if font.getlength(s) <= max_w:
        return s
    while s and font.getlength(s + '…') > max_w:
        s = s[:-1]
    return (s + '…') if s else ''


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (missing config / API error / no comments)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.30))
    b1 = f1.getbbox(line1)
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.20)) if line2 else None
    h1 = b1[3] - b1[1]
    h2 = (f2.getbbox(line2)[3] - f2.getbbox(line2)[1]) if line2 else 0
    y = (H - (h1 + (3 if line2 else 0) + h2)) / 2.0
    _cv_text(draw, (W - f1.getlength(line1)) / 2.0, y, line1, f1, _CV_TXT)
    if line2:
        _cv_text(draw, (W - f2.getlength(line2)) / 2.0, y + h1 + 3, line2, f2, _CV_DIM)
    return img


def fetch_matrix(settings, canvas):
    from PIL import ImageDraw

    if not settings.get('yt_video_id', '') or not settings.get('yt_api_key', ''):
        canvas.frame(_cv_message(canvas, ImageDraw, 'Comments', 'Set video ID + API key'))
        return 60.0

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'i': 0, 'last': None}
        setattr(fetch_matrix, '_state', st)
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
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3                                         # side margin; the top ink rides row 1

    # Author line in the accent color, a hairline rule under it.
    mark = f'{idx + 1}/{len(comments)}'
    af = _cv_fit(canvas, 'Ag', W, max(7, int(H * 0.16)))
    mf = _cv_fit(canvas, mark, int(W * 0.2), max(6, int(H * 0.11))) if H >= 48 else None
    mw = (mf.getlength(mark) + 4) if mf else 0
    an = _cv_trim(af, str(author), W - 2 * pad - mw)
    _cv_text(draw, pad, 1, an, af, _CV_AUTHOR)
    if mf:
        _cv_text(draw, W - pad - mf.getlength(mark), 2, mark, mf, _CV_DIM)
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
        _cv_text(draw, pad, y, ln, f, _CV_TXT)
        y += lh + gap
    canvas.frame(img)

    try:
        dwell = float(settings.get('loop_delay', 8) or 8)
    except (TypeError, ValueError):
        dwell = 8.0
    return max(3.0, min(30.0, dwell))
