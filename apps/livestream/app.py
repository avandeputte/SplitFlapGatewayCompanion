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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# The same rotation as the flaps: a status card first (red LIVE dot, the
# concurrent-viewer count large, the channel name under it), then each authored
# comment as a quote card. Stream numbers are cached ~25s so a 5s slide rotation
# doesn't hammer the API. Red accent, solid black background.
# =============================================================================

_CV_RED = (255, 70, 60)               # the LIVE red
_CV_TXT = (238, 240, 244)
_CV_DIM = (145, 150, 160)


def _cv_wrap(font, text, max_w, max_lines):
    """Greedy word-wrap of ``text`` to pixel width ``max_w``, at most ``max_lines`` lines.
    Stays local: canvas.wrap hyphen-splits overlong words, which lets canvas.wrap_fit
    settle on a larger font and cut the tail (channel names would lose words)."""
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
    size = max(8, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = _cv_wrap(font, text, max_w, max_lines)
        b = font.getbbox('Ag')
        lh = b[3] - b[1]
        gap = max(1, lh // 6)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((font.getlength(ln) for ln in lines), default=0)
        if size <= 8 or (total <= max_h and widest <= max_w):
            return font, lines, lh, gap
        size -= 1
    font = canvas.font(8)
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


def _cv_status_card(canvas, ImageDraw, viewers, name, time_s, i18n):
    """The LIVE card: red dot + LIVE, the viewer count large (or the channel name when
    the stream isn't live), the channel small at the bottom."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3                            # side margin; the header ink rides row 1

    hf = canvas.fit_font('LIVE', W - 2 * pad, max(7, int(H * 0.16)))
    hh = hf.getbbox('LIVE')[3] - hf.getbbox('LIVE')[1]
    r = max(2, hh // 2 - 1)
    cy = 1 + hh // 2
    draw.ellipse([pad, cy - r, pad + 2 * r, cy + r], fill=_CV_RED)
    canvas.text_top(draw, pad + 2 * r + 3, 1, 'LIVE', hf, _CV_RED)
    live_end = pad + 2 * r + 3 + hf.getlength('LIVE')
    if time_s and live_end + 6 + hf.getlength(time_s) <= W - pad:
        canvas.text_top(draw, W - pad - hf.getlength(time_s), 1, time_s, hf, _CV_DIM)
    top = 1 + hh + 2

    show_name_row = bool(name) and H >= 48
    nf = canvas.fit_font('Ag', W, max(6, int(H * 0.14))) if show_name_row else None
    nh = (nf.getbbox('Ag')[3] - nf.getbbox('Ag')[1] + 2) if show_name_row else 0

    body_h = H - top - 1 - nh
    if viewers is not None:
        count = i18n.number(int(viewers), 0) if i18n is not None else f'{int(viewers):,}'
        label = 'WATCHING NOW'
        lf = canvas.fit_font(label, W - 2 * pad, max(6, int(H * 0.12))) if H >= 48 else None
        lh2 = (lf.getbbox(label)[3] - lf.getbbox(label)[1] + 2) if lf else 0
        cf = canvas.fit_font(count, W - 2 * pad, body_h - lh2)
        ch = cf.getbbox(count)[3] - cf.getbbox(count)[1]
        # Centered between header and channel row; with no channel row beneath, the
        # count block itself sinks to the panel's bottom edge.
        y = top + (max(0, (body_h - ch - lh2) // 2) if show_name_row
                   else max(0, body_h - ch - lh2 + 1))
        canvas.text_top(draw, (W - cf.getlength(count)) / 2.0, y, count, cf, _CV_TXT)
        if lf:
            canvas.text_top(draw, (W - lf.getlength(label)) / 2.0, y + ch + 2, label, lf, _CV_DIM)
    else:
        body_h = H - top - 1                     # no channel row beneath the hero
        big, lines, lh3, gap = _cv_wrap_fit(canvas, name or 'Livestream', W - 2 * pad,
                                            body_h, 2)
        block = len(lines) * lh3 + (len(lines) - 1) * gap
        if len(lines) > 1:                       # spread slack into leading, then sink
            gap += min(max(0, body_h - block) // (len(lines) - 1), max(2, lh3 // 3))
            block = len(lines) * lh3 + (len(lines) - 1) * gap
        lb = big.getbbox(lines[-1] or '0')
        block += (lb[3] - lb[1]) - lh3           # anchor on the last line's real ink
        y = top + max(0, body_h - block)
        for ln in lines:
            canvas.text_top(draw, (W - big.getlength(ln)) / 2.0, y, ln, big, _CV_TXT)
            y += lh3 + gap
        show_name_row = False
        nh = 0

    if show_name_row and viewers is not None:
        ns = _cv_trim(nf, str(name), W - 2 * pad)
        canvas.text_top(draw, (W - nf.getlength(ns)) / 2.0, H - 1 - (nh - 2), ns, nf, _CV_DIM)
    return img


def _cv_comment_card(canvas, ImageDraw, lines_in):
    """One authored comment slide: a red quote mark, the text wrapped and centered."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3                            # side margin; the quote mark's ink rides row 1
    qf = canvas.fit_font('“', int(W * 0.2), max(8, int(H * 0.22)))
    canvas.text_top(draw, pad, 1, '“', qf, _CV_RED)
    qh = qf.getbbox('“')[3]
    text = ' '.join(lines_in)
    body_top = 1 + max(0, qh - 4)
    # As many lines as the height affords, the leftover fed into the leading and the
    # block let down to the panel's bottom edge.
    avail = H - 1 - body_top
    f, lines, lh, gap = _cv_wrap_fit(canvas, text, W - 2 * pad, avail, max(3, avail // 8))
    if ' '.join(lines) != text:
        # The 8px floor cut the tail — say so with an ellipsis, never silently.
        last = lines[-1]
        while last and f.getlength(last + '…') > W - 2 * pad:
            last = last[:-1].rstrip()
        lines[-1] = (last + '…') if last else '…'
    block = len(lines) * lh + (len(lines) - 1) * gap
    if len(lines) > 1:
        gap += min(max(0, avail - block) // (len(lines) - 1), max(2, lh // 3))
        block = len(lines) * lh + (len(lines) - 1) * gap
    lb = f.getbbox(lines[-1] or '0')
    block += (lb[3] - lb[1]) - lh                # anchor on the last line's real ink
    y = body_top + max(0, avail - block)
    for ln in lines:
        canvas.text_top(draw, (W - f.getlength(ln)) / 2.0, y, ln, f, _CV_TXT)
        y += lh + gap
    return img


def _cv_ink_bot(s):
    """How far a line's ink reaches below its gtext y, as a fraction of size:
    descenders (and commas) hang to ~1.14, a descender-free line stops at the
    ~0.94 baseline — the ops stand-in for the PIL cards' getbbox anchoring."""
    return 1.14 if any(c in 'gjpqy,;()[]{}@Q' for c in str(s)) else 0.94


def _cv_status_ops(canvas, viewers, name, time_s, i18n, W, H):
    """The LIVE card as on-device DRAW OPS — the gtext-era twin of _cv_status_card,
    for the LCD (manifest ``lcd_ops``): an AA dot and scalable type drawn by the
    wall at native resolution instead of a 256x160 pixel frame upscaled x5. Same
    card: red dot + LIVE and the clock up top, the viewer count the hero (or the
    channel name when the stream isn't live), the channel dim on the bottom edge."""
    aa = bool(getattr(canvas, 'aa_ok', False))
    canvas.clear((0, 0, 0))
    pad = max(3, int(W * 0.012))

    hsz = canvas.fit_gtext('LIVE', int(W * 0.4), max(10, int(H * 0.20)))
    hh = int(hsz * 0.76)                   # the header caps' ink height (0.18..0.94)
    r = max(2, hh // 2 - 1)
    cy = 1 + hh // 2                       # dot centered on the LIVE ink
    canvas.circle(pad + r, cy, r, color=_CV_RED, fill=True, aa=aa)
    hx = pad + 2 * r + max(3, int(W * 0.01))
    hy = 1 - int(hsz * 0.18)               # header ink rides row 1
    canvas.gtext(hx, hy, 'LIVE', color=_CV_RED, size=hsz)
    live_end = hx + canvas.text_width('LIVE', hsz)
    if time_s and live_end + max(6, int(W * 0.02)) + canvas.text_width(time_s, hsz) <= W - pad:
        canvas.gtext(W - pad, hy, time_s, color=_CV_DIM, size=hsz, align='right')
    top = 1 + hh + max(2, int(H * 0.02))

    show_name_row = bool(name) and viewers is not None
    nsz = canvas.fit_gtext(str(name or ''), W - 2 * pad, max(8, int(H * 0.13))) \
        if show_name_row else 0
    nh = int(nsz * _cv_ink_bot(name)) - int(nsz * 0.17) + max(2, int(H * 0.012)) \
        if show_name_row else 0

    body_h = H - top - 1 - nh
    if viewers is not None:
        count = i18n.number(int(viewers), 0) if i18n is not None else f'{int(viewers):,}'
        label = 'WATCHING NOW'
        lsz = canvas.fit_gtext(label, W - 2 * pad, max(8, int(H * 0.13)))
        lh = int(lsz * 0.76) + max(3, int(H * 0.02))
        csz = canvas.fit_gtext(count, W - 2 * pad, int((body_h - lh) * 0.95))
        ch = int(csz * 0.89)               # digit ink incl. a grouping comma's tail
        # Centered between header and channel row, ink-anchored (gtext ink starts
        # ~0.18*size below the given y).
        y = top + max(0, (body_h - ch - lh) // 2)
        canvas.gtext(W // 2, y - int(csz * 0.18), count, color=_CV_TXT, size=csz,
                     align='center')
        canvas.gtext(W // 2, y + ch + max(3, int(H * 0.02)) - int(lsz * 0.18), label,
                     color=_CV_DIM, size=lsz, align='center')
    else:
        body_h = H - top - 1               # no channel row beneath the hero
        csz, lines = canvas.fit_wrap_gtext(name or 'Livestream', W - 2 * pad,
                                           body_h, max_lines=2)
        lstep = int(csz * 1.18)
        last_ink = int(csz * (_cv_ink_bot(lines[-1]) - 0.17))
        if len(lines) > 1:                 # spread slack into leading, then sink
            block = (len(lines) - 1) * lstep + last_ink
            lstep += min(csz // 3, max(0, body_h - block) // (len(lines) - 1))
        block = (len(lines) - 1) * lstep + last_ink
        y = top + max(0, body_h - block)   # the hero sinks to the bottom edge
        for ln in lines:
            canvas.gtext(W // 2, y - int(csz * 0.17), ln, color=_CV_TXT, size=csz,
                         align='center')
            y += lstep
        show_name_row = False

    if show_name_row:
        ns = str(name)
        while ns and canvas.text_width(ns + '…', nsz) > W - 2 * pad:
            ns = ns[:-1]
        ns = ns if ns == str(name) else ns + '…'
        canvas.gtext(W // 2, H - 1 - int(nsz * _cv_ink_bot(ns)), ns, color=_CV_DIM,
                     size=nsz, align='center')
    canvas.show()


def _cv_comment_ops(canvas, lines_in, W, H):
    """One authored comment slide as on-device DRAW OPS — the gtext-era twin of
    _cv_comment_card, for the LCD (manifest ``lcd_ops``): the red quote mark, the
    text wrapped and centered in scalable type, floor-anchored with the leftover
    height fed into the leading — crisp at native resolution."""
    canvas.clear((0, 0, 0))
    pad = max(3, int(W * 0.012))
    # DejaVu's '“' is a big glyph (ink ~0.20..0.93 of its box) — size it like the
    # PIL card's ink-height fit and the mark scales with the panel.
    qsz = canvas.fit_gtext('“', int(W * 0.2), max(10, int(H * 0.30)))
    canvas.gtext(pad, 1 - int(qsz * 0.20), '“', color=_CV_RED, size=qsz)
    text = ' '.join(lines_in)
    body_top = 1 + max(0, int(qsz * 0.93) - max(4, int(H * 0.025)))
    avail = H - 1 - body_top
    size, lines = canvas.fit_wrap_gtext(text, W - 2 * pad, avail, max_lines=5)
    if sum(len(ln.split()) for ln in lines) < len(text.split()):
        last = lines[-1]                   # cut at the 8px floor — say so, never silently
        while last and canvas.text_width(last + '…', size) > W - 2 * pad:
            last = last[:-1].rstrip()
        lines[-1] = (last + '…') if last else '…'
    lstep = int(size * 1.18)
    last_ink = int(size * (_cv_ink_bot(lines[-1]) - 0.17))
    if len(lines) > 1:                     # spread slack into leading, then sink
        block = (len(lines) - 1) * lstep + last_ink
        lstep += min(size // 3, max(0, avail - block) // (len(lines) - 1))
    block = (len(lines) - 1) * lstep + last_ink
    y = body_top + max(0, avail - block)   # the last line's ink lands on the bottom edge
    for ln in lines:
        canvas.gtext(W // 2, y - int(size * 0.17), ln, color=_CV_TXT, size=size,
                     align='center')
        y += lstep
    canvas.show()


def fetch_canvas(settings, canvas, i18n=None):
    from datetime import datetime
    import time
    import pytz
    from PIL import ImageDraw

    cid = str(settings.get('yt_channel_id', '') or '').strip()
    api_key = str(settings.get('yt_api_key', '') or '').strip()
    video_id = str(settings.get('yt_video_id', '') or '').strip()
    slides = _comment_slides(settings.get('livestream_comments', ''))

    st = getattr(fetch_canvas, '_state', None)
    if st is None:
        st = {'i': 0, 'ts': 0.0, 'name': None, 'viewers': None}
        setattr(fetch_canvas, '_state', st)
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

    W, H = canvas.width, canvas.height
    if getattr(canvas, 'can_gtext', False) and H >= 96:
        # The big-panel path: the same slide rotation as live ops at native
        # resolution (crisp TTF type + an AA dot) instead of an upscaled frame.
        if not deck:
            _cv_comment_ops(canvas, ['Livestream — no data'], W, H)
            return 30.0
        slide = deck[st['i'] % len(deck)]
        st['i'] = (st['i'] + 1) % len(deck)
        if slide[0] == 'status':
            _cv_status_ops(canvas, st['viewers'], st['name'], time_s, i18n, W, H)
        else:
            _cv_comment_ops(canvas, slide[1], W, H)
        return canvas.num(settings, 'loop_delay', 5.0, 3.0, 30.0)

    if not deck:
        canvas.frame(_cv_comment_card(canvas, ImageDraw, ['Livestream — no data']))
        return 30.0

    slide = deck[st['i'] % len(deck)]
    st['i'] = (st['i'] + 1) % len(deck)
    if slide[0] == 'status':
        canvas.frame(_cv_status_card(canvas, ImageDraw, st['viewers'], st['name'], time_s, i18n))
    else:
        canvas.frame(_cv_comment_card(canvas, ImageDraw, slide[1]))

    return canvas.num(settings, 'loop_delay', 5.0, 3.0, 30.0)
