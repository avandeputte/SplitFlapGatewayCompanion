"""Livestream mode — rotates subs, viewers, and comment slides."""


# =============================================================================
# SHARED — the stream DATA: channel name (keyless RSS), concurrent viewers
# (Data API), and the authored comment slides. Both surfaces rotate the same
# three kinds of slide built from these.
# =============================================================================

def _channel_title(cid):
    """The channel's display name out of the keyless RSS feed, or None when the feed
    carries no name. Raises on network trouble — callers decide what a miss means."""
    import re
    import urllib.request
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    req = urllib.request.Request(url, headers={"User-Agent": "SplitFlap/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = resp.read().decode()
    name = re.search(r'<name>(.+?)</name>', body)
    return name.group(1) if name else None


def _live_viewers(api_key, video_id):
    """Concurrent viewers of the live video via the Data API, or None when the video
    isn't live (no liveStreamingDetails). Raises on network trouble."""
    import requests
    url = f"https://www.googleapis.com/youtube/v3/videos?part=liveStreamingDetails&id={video_id}&key={api_key}"
    data = requests.get(url, timeout=5).json()
    items = data.get('items', [])
    if items:
        v = items[0].get('liveStreamingDetails', {}).get('concurrentViewers')
        if v is not None:
            return int(v)
    return None


def _comment_slides(raw):
    """The configured comment textarea -> [[line, ...], ...]: blank-line-separated
    blocks, up to 3 non-empty lines each."""
    raw = str(raw or '').strip().replace('\r\n', '\n').replace('\r', '\n')
    out = []
    for block in (b for b in raw.split('\n\n') if b.strip()):
        out.append([l.strip() for l in block.split('\n') if l.strip()][:3])
    return out


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    from datetime import datetime
    import pytz

    def t(s):
        return i18n.t(s, "media") if i18n is not None else s

    pages = []
    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)
    # 12h/24h follows the language, not a hardcoded strftime("%I:%M %p").
    time_str = i18n.time(now) if i18n is not None else now.strftime("%I:%M %p").lstrip("0")
    cols = get_cols()

    # YouTube subs
    cid = settings.get('yt_channel_id', '').strip()
    if cid:
        try:
            name = _channel_title(cid) or cid[:cols]
            pages.append({'text': format_lines(time_str, name[:cols], "YouTube"), 'style': 'ltr'})
        except Exception:
            pass

    # Concurrent viewers
    api_key = settings.get('yt_api_key', '').strip()
    video_id = settings.get('yt_video_id', '').strip()
    if api_key and video_id:
        try:
            v = _live_viewers(api_key, video_id)
            if v is not None:
                # Grouping follows the language: 1,234 / 1.234 / 1 234.
                count = i18n.number(int(v), 0) if i18n is not None else f"{int(v):,}"
                pages.append({'text': format_lines(t("Watching now"), count, t("Live viewers")), 'style': 'diagonal'})
        except Exception:
            pass

    # Comment slides
    raw = settings.get('livestream_comments', '').strip()
    if raw:
        styles = ['outside_in', 'spiral', 'anti_diagonal', 'rtl', 'rain', 'center_out']
        for i, block in enumerate(_comment_slides(raw)):
            lines = list(block)
            while len(lines) < 3:
                lines.append('')
            page = ''.join(l[:cols].center(cols) for l in lines)
            pages.append({'text': page, 'style': styles[i % len(styles)]})

    return pages or [format_lines("Livestream", time_str, t("No data"))]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The same rotation as the flaps: a status card first (red LIVE dot, the
# concurrent-viewer count large, the channel name under it), then each authored
# comment as a quote card. Stream numbers are cached ~25s so a 5s slide rotation
# doesn't hammer the API. Red accent, solid black background.
# =============================================================================

_CV_RED = (255, 70, 60)               # the LIVE red
_CV_TXT = (238, 240, 244)
_CV_DIM = (145, 150, 160)


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


def _cv_wrap_fit(canvas, text, max_w, max_h, max_lines):
    """Largest font at which ``text`` wraps into <= ``max_lines`` lines fitting the box.
    Returns (font, lines, line_height, gap)."""
    size = max(5, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = _cv_wrap(font, text, max_w, max_lines)
        b = font.getbbox('Ag')
        lh = b[3] - b[1]
        gap = max(1, lh // 6)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((font.getlength(ln) for ln in lines), default=0)
        if size <= 5 or (total <= max_h and widest <= max_w):
            return font, lines, lh, gap
        size -= 1
    font = canvas.font(5)
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


def _cv_status_card(canvas, ImageDraw, viewers, name, time_s, i18n):
    """The LIVE card: red dot + LIVE, the viewer count large (or the channel name when
    the stream isn't live), the channel small at the bottom."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3

    hf = _cv_fit(canvas, 'LIVE', W - 2 * pad, max(7, int(H * 0.16)))
    hh = hf.getbbox('LIVE')[3] - hf.getbbox('LIVE')[1]
    r = max(2, hh // 2 - 1)
    cy = pad + hh // 2
    draw.ellipse([pad, cy - r, pad + 2 * r, cy + r], fill=_CV_RED)
    _cv_text(draw, pad + 2 * r + 3, pad, 'LIVE', hf, _CV_RED)
    live_end = pad + 2 * r + 3 + hf.getlength('LIVE')
    if time_s and live_end + 6 + hf.getlength(time_s) <= W - pad:
        _cv_text(draw, W - pad - hf.getlength(time_s), pad, time_s, hf, _CV_DIM)
    top = pad + hh + 2

    show_name_row = bool(name) and H >= 48
    nf = _cv_fit(canvas, 'Ag', W, max(6, int(H * 0.14))) if show_name_row else None
    nh = (nf.getbbox('Ag')[3] - nf.getbbox('Ag')[1] + 2) if show_name_row else 0

    body_h = H - top - pad - nh
    if viewers is not None:
        count = i18n.number(int(viewers), 0) if i18n is not None else f'{int(viewers):,}'
        label = 'WATCHING NOW'
        lf = _cv_fit(canvas, label, W - 2 * pad, max(6, int(H * 0.12))) if H >= 48 else None
        lh2 = (lf.getbbox(label)[3] - lf.getbbox(label)[1] + 2) if lf else 0
        cf = _cv_fit(canvas, count, W - 2 * pad, body_h - lh2)
        ch = cf.getbbox(count)[3] - cf.getbbox(count)[1]
        y = top + max(0, (body_h - ch - lh2) // 2)
        _cv_text(draw, (W - cf.getlength(count)) / 2.0, y, count, cf, _CV_TXT)
        if lf:
            _cv_text(draw, (W - lf.getlength(label)) / 2.0, y + ch + 2, label, lf, _CV_DIM)
    else:
        big, lines, lh3, gap = _cv_wrap_fit(canvas, name or 'Livestream', W - 2 * pad,
                                            body_h, 2)
        block = len(lines) * lh3 + (len(lines) - 1) * gap
        y = top + max(0, (body_h - block) // 2)
        for ln in lines:
            _cv_text(draw, (W - big.getlength(ln)) / 2.0, y, ln, big, _CV_TXT)
            y += lh3 + gap
        show_name_row = False
        nh = 0

    if show_name_row and viewers is not None:
        ns = _cv_trim(nf, str(name), W - 2 * pad)
        _cv_text(draw, (W - nf.getlength(ns)) / 2.0, H - pad - (nh - 2), ns, nf, _CV_DIM)
    return img


def _cv_comment_card(canvas, ImageDraw, lines_in):
    """One authored comment slide: a red quote mark, the text wrapped and centered."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3
    qf = _cv_fit(canvas, '“', int(W * 0.2), max(8, int(H * 0.22)))
    _cv_text(draw, pad, pad - 2, '“', qf, _CV_RED)
    qh = qf.getbbox('“')[3]
    text = ' '.join(lines_in)
    body_top = pad + max(0, qh - 4)
    f, lines, lh, gap = _cv_wrap_fit(canvas, text, W - 2 * pad, H - body_top - pad, 3)
    block = len(lines) * lh + (len(lines) - 1) * gap
    y = body_top + max(0, (H - body_top - pad - block) // 2)
    for ln in lines:
        _cv_text(draw, (W - f.getlength(ln)) / 2.0, y, ln, f, _CV_TXT)
        y += lh + gap
    return img


def fetch_matrix(settings, canvas, i18n=None):
    from datetime import datetime
    import time
    import pytz
    from PIL import ImageDraw

    cid = str(settings.get('yt_channel_id', '') or '').strip()
    api_key = str(settings.get('yt_api_key', '') or '').strip()
    video_id = str(settings.get('yt_video_id', '') or '').strip()
    slides = _comment_slides(settings.get('livestream_comments', ''))

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'i': 0, 'ts': 0.0, 'name': None, 'viewers': None}
        setattr(fetch_matrix, '_state', st)
    # The slide rotation redraws every few seconds; the stream numbers only need
    # refreshing on the app's own ~30s cadence.
    if time.time() - st['ts'] > 25:
        st['ts'] = time.time()
        if cid:
            try:
                st['name'] = _channel_title(cid) or cid
            except Exception:
                pass                              # keep the last known name
        if api_key and video_id:
            try:
                st['viewers'] = _live_viewers(api_key, video_id)
            except Exception:
                pass                              # keep the last known count

    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)
    time_s = i18n.time(now, ampm_space=False) if i18n is not None else now.strftime('%I:%M%p').lstrip('0')

    deck = []
    if cid or api_key and video_id:
        deck.append(('status',))
    deck += [('comment', b) for b in slides]
    if not deck:
        canvas.frame(_cv_comment_card(canvas, ImageDraw, ['Livestream — no data']))
        return 30.0

    slide = deck[st['i'] % len(deck)]
    st['i'] = (st['i'] + 1) % len(deck)
    if slide[0] == 'status':
        canvas.frame(_cv_status_card(canvas, ImageDraw, st['viewers'], st['name'], time_s, i18n))
    else:
        canvas.frame(_cv_comment_card(canvas, ImageDraw, slide[1]))

    try:
        dwell = float(settings.get('loop_delay', 5) or 5)
    except (TypeError, ValueError):
        dwell = 5.0
    return max(3.0, min(30.0, dwell))
