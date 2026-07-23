"""News Headlines via RSS plugin for Split-Flap Display."""


# =============================================================================
# SHARED — the feed: fetch and parse the RSS/Atom titles. Both surfaces read
# the same list, so a wall and a panel carry the same headlines in the same
# order.
# =============================================================================

def _headlines(feed_url):
    """The feed's headline titles, in feed order (up to ten). Raises on a network
    or parse failure — the caller decides what unavailable looks like."""
    import urllib.request
    import xml.etree.ElementTree as ET

    req = urllib.request.Request(feed_url, headers={"User-Agent": "SplitFlap/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    # Handle both RSS and Atom feeds
    items = root.findall('.//item')
    if not items:
        items = root.findall('.//{http://www.w3.org/2005/Atom}entry')
    titles = []
    for item in items[:10]:
        title_el = item.find('title')
        if title_el is None:
            title_el = item.find('{http://www.w3.org/2005/Atom}title')
        if title_el is not None and title_el.text:
            titles.append(title_el.text.strip())
    return titles


# Hostname labels that aren't the outlet's name as a reader knows it — the CDN/legal
# label a feed happens to live on maps to the masthead people recognize.
_SOURCE_ALIASES = {
    'bbci': 'BBC', 'bbc': 'BBC',
    'nytimes': 'NY TIMES',
    'theguardian': 'GUARDIAN',
    'reutersagency': 'REUTERS',
    'apnews': 'AP',
    'washingtonpost': 'WASH POST',
    'aljazeera': 'AL JAZEERA',
}


def _source_tag(feed_url):
    """A short badge for the feed's source, from its hostname: the first label that
    isn't plumbing ('feeds', 'www', 'rss'), aliased to the recognizable masthead
    ('bbci' → 'BBC') and uppercased."""
    from urllib.parse import urlparse
    try:
        host = urlparse(str(feed_url)).hostname or ''
    except Exception:
        host = ''
    skip = {'www', 'feeds', 'feed', 'rss', 'news', 'api'}
    for label in host.split('.'):
        if label and label not in skip:
            return _SOURCE_ALIASES.get(label, label.upper()[:12])
    return 'NEWS'


# =============================================================================
# SPLIT-FLAP — fetch() and the keyword trigger, unique to the flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols):
    cols = get_cols()
    rows = get_rows()
    feed_url = settings.get('feed_url', 'https://feeds.bbci.co.uk/news/rss.xml')

    def split_text(text, width):
        words = text.split()
        lines = []
        current = ''
        for word in words:
            if current and len(current) + 1 + len(word) > width:
                lines.append(current)
                current = word
            elif not current:
                current = word[:width]
            else:
                current += ' ' + word
        if current:
            lines.append(current)
        return lines

    try:
        titles = _headlines(feed_url)
    except Exception:
        titles = ['News unavailable', 'Check feed URL']

    pages = []
    for title in titles:
        # No character filtering here: the renderer degrades wall-aware at the last
        # moment (accents survive on reels that carry them, é->E only where they
        # don't). Filtering to ASCII in the app was punching holes in "Zürich" on
        # walls that could have shown it.
        lines = split_text(title, cols)
        for i in range(0, len(lines), rows):
            chunk = lines[i:i + rows]
            pages.append(format_lines(*chunk))

    return pages or [format_lines('News', 'No headlines', '')]


def trigger(settings, conditions):
    """Fire when a headline containing the configured keyword appears."""
    import urllib.request
    import xml.etree.ElementTree as ET

    keywords_str = conditions.get('keywords', '').upper().strip()
    keywords = [k.strip() for k in keywords_str.split(',') if k.strip()]
    feed_url = settings.get('feed_url', 'https://feeds.bbci.co.uk/news/rss.xml')

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'seen_titles': set()}
        setattr(trigger, '_state', state)

    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "SplitFlap/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        items = root.findall('.//item')
        if not items:
            items = root.findall('.//{http://www.w3.org/2005/Atom}entry')

        for item in items[:10]:
            title_el = item.find('title')
            if title_el is None:
                title_el = item.find('{http://www.w3.org/2005/Atom}title')
            if title_el is None or not title_el.text:
                continue
            title = title_el.text.strip()
            if title in state['seen_titles']:
                continue
            state['seen_titles'].add(title)
            # If no keywords configured, fire on any new headline
            if not keywords:
                return True
            # Keywords are folded above, so fold the headline to compare: a title is
            # now stored as written, and 'TARIFF' is not in 'Trump tariff latest'.
            if any(kw in title.upper() for kw in keywords):
                return True

        # Prune seen set
        if len(state['seen_titles']) > 200:
            state['seen_titles'] = set(list(state['seen_titles'])[-100:])
    except Exception:
        raise
    return False


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A ticker card: one headline at a time in real type, under a source-accented
# red masthead with a position counter, advancing through the SAME titles the
# flap pages show. The feed is polled gently (cached ~5 min); the rotation is
# per redraw. Black background, no gradient.
# =============================================================================

_MAST = (185, 30, 30)                       # the masthead red
_WHITE = (240, 240, 244)
_GRAY = (150, 150, 158)


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


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (feed unreachable)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    b1 = f1.getbbox(line1)
    h1 = b1[3] - b1[1]
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = (f2.getbbox(line2)[3] - f2.getbbox(line2)[1]) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    draw.text(((W - f1.getlength(line1)) / 2.0, y - b1[1]), line1, font=f1, fill=_WHITE)
    if line2:
        y += h1 + gap
        draw.text(((W - f2.getlength(line2)) / 2.0, y - f2.getbbox(line2)[1]), line2, font=f2, fill=_GRAY)
    return img


def fetch_matrix(settings, canvas):
    """Draw one headline per hold under a red masthead, advancing each redraw. The feed itself
    is refetched at most every five minutes; each headline holds ~8s."""
    import time
    from PIL import ImageDraw

    feed_url = settings.get('feed_url', 'https://feeds.bbci.co.uk/news/rss.xml')
    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'ts': 0.0, 'url': None, 'titles': [], 'i': 0}
        setattr(fetch_matrix, '_state', st)

    now = time.time()
    if st['url'] != feed_url or (now - st['ts']) > 300:
        try:
            st['titles'] = _headlines(feed_url)
            st['ts'] = now
            st['url'] = feed_url
        except Exception:
            if not st['titles']:
                canvas.frame(_cv_message(canvas, ImageDraw, 'NEWS UNAVAILABLE', 'CHECK FEED URL'))
                return 60.0
            st['ts'] = now                      # keep showing the stale list; retry in 5 min

    titles = st['titles']
    if not titles:
        canvas.frame(_cv_message(canvas, ImageDraw, 'NEWS', 'NO HEADLINES'))
        return 120.0
    idx = st['i'] % len(titles)
    st['i'] = (st['i'] + 1) % len(titles)
    title = titles[idx]

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    # Masthead: source badge on red, pagination dots on the right (this headline lit).
    src = _source_tag(feed_url)
    bar_h = max(9, int(H * 0.22))
    sf = _cv_fit(canvas, src, int(W * 0.5), bar_h - 3)
    sb = sf.getbbox(src)
    chip_w = int(sf.getlength(src)) + 8
    draw.rectangle([0, 0, chip_w, bar_h - 1], fill=_MAST)
    draw.text((4, (bar_h - 1 - (sb[3] - sb[1])) / 2.0 - sb[1]), src, font=sf, fill=_WHITE)
    draw.line([(0, bar_h), (W - 1, bar_h)], fill=_MAST)
    n = len(titles)
    step = 4                                     # 2px dot + 2px gap
    if chip_w + 6 + n * step < W - 2:
        dy = (bar_h - 1 - 2) // 2
        dx = W - 2 - n * step
        for j in range(n):
            if j == idx:                          # the current headline: the masthead red, and a
                draw.rectangle([dx + j * step - 1, dy - 1,          # hair bigger — color, not just
                                dx + j * step + 2, dy + 2], fill=_MAST)   # brightness, marks it
            else:
                draw.rectangle([dx + j * step, dy, dx + j * step + 1, dy + 1],
                               fill=(95, 95, 102))

    # The headline, as big as it wraps — mixed case is the point on this panel.
    top = bar_h + 3
    body_h = H - top - 2
    max_lines = 3 if H >= 48 else 2
    nf, lines, lh, gap = _cv_wrap_fit(canvas, title, W - 6, body_h, max_lines)
    # A title the wrap had to cut short gets an ellipsis on its last line.
    if ' '.join(lines) != ' '.join(str(title).split()):
        ln = lines[-1]
        while ln and nf.getlength(ln + '…') > W - 6:
            ln = ln[:-1].rstrip()
        lines[-1] = (ln + '…') if ln else '…'
    block = len(lines) * lh + (len(lines) - 1) * gap
    ny = top + max(0.0, (body_h - block) / 2.0)
    for ln in lines:
        draw.text((3, ny - nf.getbbox(ln)[1]), ln, font=nf, fill=_WHITE)
        ny += lh + gap

    canvas.frame(img)
    return 8.0
