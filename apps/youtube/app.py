"""YouTube channel stats — real subscribers with an API key, recent uploads without."""


# =============================================================================
# SHARED — the channel DATA: the keyless RSS feed (name + recent uploads) and
# the optional Data-API subscriber count. Both surfaces show the same numbers.
# =============================================================================

def _channel_feed(channel_id):
    """(channel name, [recent upload titles]) out of the keyless RSS feed.
    Raises on network trouble — callers decide the fallback."""
    import requests
    import xml.etree.ElementTree as ET
    url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
    r = requests.get(url, timeout=10)
    root = ET.fromstring(r.content)
    ns = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
    name = root.find('a:title', ns).text
    titles = []
    for entry in root.findall('a:entry', ns):
        t = entry.find('a:title', ns)
        titles.append((t.text or '') if t is not None else '')
    return name, titles


def _subscriber_count(channel_id, api_key):
    """The real subscriber count via the Data API, or None (bad key, quota, network —
    the keyless upload count stands in either way)."""
    import requests
    try:
        cr = requests.get(
            'https://www.googleapis.com/youtube/v3/channels',
            params={'part': 'statistics', 'id': channel_id, 'key': api_key},
            timeout=8).json()
        return int(cr['items'][0]['statistics']['subscriberCount'])
    except Exception:
        return None


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    def t(s):
        return i18n.t(s, "media") if i18n is not None else s

    channel_id = settings.get('yt_channel_id', '')
    if not channel_id:
        return [format_lines('YouTube', t('No channel'), t('Set ID'))]
    try:
        name, titles = _channel_feed(channel_id)
        # The keyless RSS feed carries no subscriber count — with an API key we
        # show real subs; without one we say what we actually counted: recent
        # uploads. ("N videos" from a 15-entry feed was neither subs nor videos.)
        count = None
        api_key = settings.get('yt_api_key', '')
        if api_key:
            subs = _subscriber_count(channel_id, api_key)
            if subs is not None:
                n = i18n.number(subs, 0) if i18n is not None else f'{subs:,}'
                count = f'{n} {t("subs")}'
        if count is None:
            count = f'{len(titles)} {t("recent uploads")}'
        rows = get_rows()
        if rows >= 4 and titles:
            # The feed carries the latest upload — worth a line when the wall is tall.
            title = titles[0]
            extra = [t('Latest'), title[:get_cols()]] if title else []
            return [format_lines('YouTube', name, count, *extra[:rows - 3])]
        return [format_lines('YouTube', name, count)]
    except Exception:
        return [format_lines('YouTube', t('Error'), t('Check ID'))]


def trigger(settings, conditions):
    """Fire when a new video is posted or a video crosses a view milestone."""
    import requests
    import xml.etree.ElementTree as ET

    channel_id = settings.get('yt_channel_id', '')
    api_key = settings.get('yt_api_key', '')
    condition_type = conditions.get('condition_type', 'new_video')
    if not channel_id:
        return False

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'last_video_id': None, 'fired_milestones': set()}
        setattr(trigger, '_state', state)

    try:
        url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
        r = requests.get(url, timeout=10)
        root = ET.fromstring(r.content)
        ns = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
        entries = root.findall('a:entry', ns)
        if not entries:
            return False
        latest_id_el = entries[0].find('yt:videoId', ns)
        if latest_id_el is None:
            return False
        vid_id = latest_id_el.text

        if condition_type == 'new_video':
            if state['last_video_id'] is None:
                state['last_video_id'] = vid_id
                return False
            if vid_id != state['last_video_id']:
                state['last_video_id'] = vid_id
                return True

        elif condition_type == 'view_milestone' and api_key:
            milestone = int(conditions.get('view_milestone', 1000000))
            # Check view count via YouTube Data API
            vr = requests.get(
                'https://www.googleapis.com/youtube/v3/videos',
                params={'part': 'statistics', 'id': vid_id, 'key': api_key},
                timeout=8
            ).json()
            items = vr.get('items', [])
            if not items:
                return False
            views = int(items[0].get('statistics', {}).get('viewCount', 0))
            key = f"{vid_id}:{milestone}"
            if views >= milestone and key not in state['fired_milestones']:
                state['fired_milestones'].add(key)
                return True

    except Exception:
        raise
    return False


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The channel as a stats card: a red play button beside the channel name, the
# subscriber count large (or the recent-upload count when there's no API key),
# and the latest upload's title along the bottom of a tall panel. Red accent,
# solid black background; slow data, slow cadence.
# =============================================================================

_CV_RED = (255, 40, 40)               # the play-button red
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
    """A quiet two-line message (no channel / feed unreachable)."""
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


def _cv_play_button(draw, x, y, w, h):
    """The rounded red play button with its white triangle."""
    draw.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=max(2, h // 4), fill=_CV_RED)
    tw, th = max(3, w // 3), max(4, h // 2)
    tx, ty = x + (w - tw) // 2 + 1, y + (h - th) // 2
    draw.polygon([(tx, ty), (tx + tw, ty + th // 2), (tx, ty + th)], fill=(255, 255, 255))


def fetch_matrix(settings, canvas, i18n=None):
    from PIL import ImageDraw

    channel_id = str(settings.get('yt_channel_id', '') or '').strip()
    if not channel_id or channel_id == 'UC...':
        canvas.frame(_cv_message(canvas, ImageDraw, 'YouTube', 'Set a channel ID'))
        return 120.0

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'feed': None}
        setattr(fetch_matrix, '_state', st)
    try:
        st['feed'] = _channel_feed(channel_id)
    except Exception:
        pass                                        # keep the last good feed across a hiccup
    if st['feed'] is None:
        canvas.frame(_cv_message(canvas, ImageDraw, 'YouTube', 'Check the channel ID'))
        return 60.0
    name, titles = st['feed']

    subs = None
    api_key = str(settings.get('yt_api_key', '') or '').strip()
    if api_key:
        subs = _subscriber_count(channel_id, api_key)
    if subs is not None:
        big = i18n.number(subs, 0) if i18n is not None else f'{subs:,}'
        label = 'SUBSCRIBERS'
    else:
        big = str(len(titles))
        label = 'RECENT UPLOADS'

    W, H = int(canvas.width), int(canvas.height)
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3

    # Header: play button + channel name.
    bh = max(8, int(H * 0.20))
    bw = int(bh * 1.45)
    _cv_play_button(draw, pad, pad, bw, bh)
    nf = _cv_fit(canvas, 'Ag', W, max(7, bh - 1))
    ns = _cv_trim(nf, str(name or channel_id), W - pad - (pad + bw + 4))
    nh = nf.getbbox('Ag')[3] - nf.getbbox('Ag')[1]
    _cv_text(draw, pad + bw + 4, pad + max(0, (bh - nh) // 2), ns, nf, _CV_TXT)
    top = pad + bh + 2

    # The latest upload earns the bottom row only where the panel is tall enough.
    title = (titles[0] if titles else '') if H >= 48 else ''
    tf = _cv_fit(canvas, 'Ag', W, max(9, int(H * 0.14))) if title else None
    th = (tf.getbbox('Ag')[3] - tf.getbbox('Ag')[1] + 2) if title else 0

    # The number, large, with its label under (beside, on a squat panel).
    body_h = H - top - pad - th
    lf = _cv_fit(canvas, label, W - 2 * pad, max(6, int(H * 0.13)))
    lh = lf.getbbox(label)[3] - lf.getbbox(label)[1]
    stacked = body_h >= lh + 12
    cf = _cv_fit(canvas, big, W - 2 * pad, body_h - (lh + 2 if stacked else 0))
    ch = cf.getbbox(big)[3] - cf.getbbox(big)[1]
    if stacked:
        y = top + max(0, (body_h - ch - lh - 2) // 2)
        _cv_text(draw, (W - cf.getlength(big)) / 2.0, y, big, cf, _CV_TXT)
        _cv_text(draw, (W - lf.getlength(label)) / 2.0, y + ch + 2, label, lf, _CV_RED)
    else:
        # Beside the number; shorten (or drop) the label rather than clip it off-panel.
        short = label if cf.getlength(big) + 4 + lf.getlength(label) <= W - 2 * pad else \
            ('SUBS' if subs is not None else 'UPLOADS')
        if cf.getlength(big) + 4 + lf.getlength(short) > W - 2 * pad:
            short = ''
        total = cf.getlength(big) + (4 + lf.getlength(short) if short else 0)
        x = max(pad, (W - total) / 2.0)
        y = top + max(0, (body_h - ch) // 2)
        _cv_text(draw, x, y, big, cf, _CV_TXT)
        if short:
            _cv_text(draw, x + cf.getlength(big) + 4, y + max(0, ch - lh), short, lf, _CV_RED)

    if title:
        _cv_text(draw, pad, H - pad - (th - 2), _cv_trim(tf, title, W - 2 * pad), tf, _CV_DIM)

    canvas.frame(img)
    return 120.0
