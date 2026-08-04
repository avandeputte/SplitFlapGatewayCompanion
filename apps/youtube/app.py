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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# The channel as a stats card: a red play button beside the channel name, the
# subscriber count large (or the recent-upload count when there's no API key),
# and the latest upload's title along the bottom of a tall panel. Red accent,
# solid black background; slow data, slow cadence.
# =============================================================================

_CV_RED = (255, 40, 40)               # the play-button red
_CV_TXT = (238, 240, 244)
_CV_DIM = (145, 150, 160)


def _cv_trim(font, s, max_w):
    """``s`` trimmed with an ellipsis until it fits ``max_w`` (never past empty)."""
    if font.getlength(s) <= max_w:
        return s
    while s and font.getlength(s + '…') > max_w:
        s = s[:-1]
    return (s + '…') if s else ''


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (no channel / feed unreachable). Local: this card
    sizes its lines at H*0.30/0.20 (canvas.message uses 0.32/0.22) and keeps the
    app's own text colors."""
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


def _cv_play_button(draw, x, y, w, h):
    """The rounded red play button with its white triangle."""
    draw.rounded_rectangle([x, y, x + w - 1, y + h - 1], radius=max(2, h // 4), fill=_CV_RED)
    tw, th = max(3, w // 3), max(4, h // 2)
    tx, ty = x + (w - tw) // 2 + 1, y + (h - th) // 2
    draw.polygon([(tx, ty), (tx + tw, ty + th // 2), (tx, ty + th)], fill=(255, 255, 255))


def fetch_canvas(settings, canvas, i18n=None):
    from PIL import ImageDraw

    channel_id = str(settings.get('yt_channel_id', '') or '').strip()
    if not channel_id or channel_id == 'UC...':
        canvas.frame(_cv_message(canvas, ImageDraw, 'YouTube', 'Set a channel ID'))
        return 120.0

    st = getattr(fetch_canvas, '_state', None)
    if st is None:
        st = {'feed': None}
        setattr(fetch_canvas, '_state', st)
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
    pad = 3                            # side margin; the header ink rides row 1

    if H >= 96:                        # tall LCD — full name, hero count, full latest title
        pad = 6
        # Header: play button + channel name WRAPPED (up to two lines, never clipped).
        head_h = int(H * 0.20)
        bh = max(10, int(H * 0.12))
        bw = int(bh * 1.45)
        _cv_play_button(draw, pad, pad + max(0, (head_h - bh) // 2), bw, bh)
        nx = pad + bw + 6
        nf, nlines = canvas.wrap_fit(str(name or channel_id), W - pad - nx, head_h, 2)
        nlh = canvas.ink(nf, 'Ag')
        ngap = max(2, nlh // 6)
        ny = pad + max(0, (head_h - (len(nlines) * nlh + (len(nlines) - 1) * ngap)) // 2)
        for ln in nlines:
            canvas.text_top(draw, nx, ny, ln, nf, _CV_TXT)
            ny += nlh + ngap
        head_bot = pad + head_h

        # Latest upload — the WHOLE title wrapped (2-3 lines), a red caption above —
        # owns the floor. Secondary to the count, so a modest size.
        title = titles[0] if titles else ''
        ltf, lat_lines = canvas.wrap_fit(title, W - 2 * pad, int(H * 0.24), 3) if title \
            else (None, [])
        llh = canvas.ink(ltf, 'Ag') if lat_lines else 0
        lgap = max(2, llh // 6)
        lat_blk = (len(lat_lines) * llh + (len(lat_lines) - 1) * lgap) if lat_lines else 0
        cf_cap = canvas.fit_font('LATEST', int(W * 0.5), max(8, int(H * 0.08))) if lat_lines else None
        cap_h = (canvas.ink(cf_cap, 'LATEST') + 4) if lat_lines else 0
        lat_top = H - pad - lat_blk

        # The count — hero — fills the band between the header and the latest block.
        hero_top = head_bot + 4
        hero_bot = (lat_top - cap_h - 6) if lat_lines else (H - pad)
        lf = canvas.fit_font(label, W - 2 * pad, int(H * 0.10))
        if lf.getlength(label) > W - 2 * pad:
            label = ''                 # can't fit the caption — the count carries it
        lbl_h = (canvas.ink(lf, label) + 3) if label else 0
        cf = canvas.fit_font(big, W - 2 * pad, max(1, (hero_bot - hero_top) - lbl_h))
        ch = canvas.ink(cf, big)
        hy = hero_top + max(0, ((hero_bot - hero_top) - ch - lbl_h) // 2)
        canvas.text_top(draw, (W - cf.getlength(big)) / 2.0, hy, big, cf, _CV_TXT)
        if label:
            canvas.text_top(draw, (W - lf.getlength(label)) / 2.0, hy + ch + 3, label, lf, _CV_RED)

        if lat_lines:
            canvas.text_top(draw, pad, lat_top - cap_h, 'LATEST', cf_cap, _CV_RED)
            y = lat_top
            for ln in lat_lines:
                canvas.text_top(draw, pad, y, ln, ltf, _CV_DIM)
                y += llh + lgap
        canvas.frame(img)
        return 120.0

    # Header: play button + channel name.
    bh = max(8, int(H * 0.20))
    bw = int(bh * 1.45)
    _cv_play_button(draw, pad, 1, bw, bh)
    nf = canvas.fit_font('Ag', W, max(7, bh - 1))
    ns = _cv_trim(nf, str(name or channel_id), W - pad - (pad + bw + 4))
    nh = nf.getbbox('Ag')[3] - nf.getbbox('Ag')[1]
    canvas.text_top(draw, pad + bw + 4, 1 + max(0, (bh - nh) // 2), ns, nf, _CV_TXT)
    top = 1 + bh + 2

    # The latest upload earns the bottom row only where the panel is tall enough.
    title = (titles[0] if titles else '') if H >= 48 else ''
    tf = canvas.fit_font('Ag', W, max(9, int(H * 0.14))) if title else None
    th = (tf.getbbox('Ag')[3] - tf.getbbox('Ag')[1] + 2) if title else 0

    # The number, large, with its label under (beside, on a squat panel).
    body_h = H - top - 1 - th
    lf = canvas.fit_font(label, W - 2 * pad, max(6, int(H * 0.13)))
    if lf.getlength(label) > W - 2 * pad:
        label = ''            # the caption can't fit at the 8px floor — the count carries it
    lh = (lf.getbbox(label)[3] - lf.getbbox(label)[1]) if label else 0
    stacked = bool(label) and body_h >= lh + 12
    cf = canvas.fit_font(big, W - 2 * pad, body_h - (lh + 2 if stacked else 0))
    ch = cf.getbbox(big)[3] - cf.getbbox(big)[1]
    if stacked:
        # Centered above the title row; with no title beneath, the block itself
        # sinks to the panel's bottom edge.
        y = top + (max(0, (body_h - ch - lh - 2) // 2) if title
                   else max(0, body_h - ch - lh - 2 + 1))
        canvas.text_top(draw, (W - cf.getlength(big)) / 2.0, y, big, cf, _CV_TXT)
        canvas.text_top(draw, (W - lf.getlength(label)) / 2.0, y + ch + 2, label, lf, _CV_RED)
    else:
        # Beside the number; shorten (or drop) the label rather than clip it off-panel.
        short = ''
        if label:
            short = label if cf.getlength(big) + 4 + lf.getlength(label) <= W - 2 * pad else \
                ('SUBS' if subs is not None else 'UPLOADS')
            if cf.getlength(big) + 4 + lf.getlength(short) > W - 2 * pad:
                short = ''
        total = cf.getlength(big) + (4 + lf.getlength(short) if short else 0)
        x = max(pad, (W - total) / 2.0)
        y = top + (max(0, (body_h - ch) // 2) if title else max(0, body_h - ch + 1))
        canvas.text_top(draw, x, y, big, cf, _CV_TXT)
        if short:
            canvas.text_top(draw, x + cf.getlength(big) + 4, y + max(0, ch - lh), short, lf, _CV_RED)

    if title:
        canvas.text_top(draw, pad, H - 1 - (th - 2), _cv_trim(tf, title, W - 2 * pad), tf, _CV_DIM)

    canvas.frame(img)
    return 120.0
