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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# A ticker card: one headline at a time in real type, under a source-accented
# red masthead with a position counter, advancing through the SAME titles the
# flap pages show. The feed is polled gently (cached ~5 min); the rotation is
# per redraw. Black background, no gradient.
# =============================================================================

_MAST = (185, 30, 30)                       # the masthead red
_WHITE = (240, 240, 244)
_GRAY = (150, 150, 158)


def _cv_news_ops(canvas, feed_url, title, idx, n, W, H):
    """The ticker card as on-device DRAW OPS — the gtext-era twin of the PIL path
    below, for the LCD (manifest ``lcd_ops``): the red masthead with its source chip
    and position dots, the headline in scalable type, rendered by the wall at native
    resolution instead of a 256x160 frame upscaled x5. Same composition as the pixel
    card, every measure a fraction of the panel."""
    canvas.clear((0, 0, 0))
    src = _source_tag(feed_url)

    # Masthead: source badge on red, pagination dots on the right (this headline lit).
    bar_h = max(9, int(H * 0.22))
    pad = max(4, int(W * 0.016))
    ssz = canvas.fit_gtext(src, int(W * 0.5), bar_h - max(3, int(bar_h * 0.22)))
    chip_w = int(canvas.text_width(src, ssz)) + 2 * pad
    canvas.rect(0, 0, chip_w, bar_h, color=_MAST, fill=True)
    # valign='ink-center' drops the source label's real ink onto the chip's mid-line.
    canvas.gtext(pad, bar_h // 2, src, color=_WHITE, size=ssz, valign='ink-center')
    canvas.line(0, bar_h, W - 1, bar_h, color=_MAST, t=max(1, int(H * 0.008)))
    d = max(2, int(H * 0.015))                   # base dot side; the lit one is 2d
    step = 2 * d
    if chip_w + 3 * d + n * step < W - d:
        dy = (bar_h - d) // 2
        dx = W - d - n * step
        for j in range(n):
            if j == idx:                          # the current headline: the masthead red,
                canvas.rect(dx + j * step - d // 2, dy - d // 2,       # and a hair bigger
                            2 * d, 2 * d, color=_MAST, fill=True)
            else:
                canvas.rect(dx + j * step, dy, d, d, color=(95, 95, 102), fill=True)

    # The headline, as big as it wraps whole — floor-anchored like the pixel path,
    # the leading stretched (never past half a line) toward the masthead.
    m = max(3, int(W * 0.012))
    top = bar_h + max(2, int(H * 0.02))
    floor = max(2, int(H * 0.015))
    avail = H - top - floor
    size, lines = canvas.fit_wrap_gtext(title, W - 2 * m, avail, max_lines=5)
    if sum(len(ln.split()) for ln in lines) < len(str(title).split()):
        # cut off at the 8px floor — mark the tail with a … (ellipsize trims it to fit)
        lines[-1] = canvas.ellipsize(lines[-1] + '…', size, W - 2 * m)
    lstep = int(size * 1.18)
    block = canvas.gtext_block_height(lines, size)
    if len(lines) > 1:                  # stretch the leading (never past half a line)
        extra = min(size // 2, max(0, avail - block) // (len(lines) - 1))
        lstep += extra
        block += (len(lines) - 1) * extra
    y = H - floor - block
    for ln in lines:
        canvas.gtext(m, y, ln, color=_WHITE, size=size)
        y += lstep
    canvas.show()


def fetch_canvas(settings, canvas):
    """Draw one headline per hold under a red masthead, advancing each redraw. The feed itself
    is refetched at most every five minutes; each headline holds ~8s."""
    import time
    from PIL import ImageDraw

    feed_url = settings.get('feed_url', 'https://feeds.bbci.co.uk/news/rss.xml')
    st = getattr(fetch_canvas, '_state', None)
    if st is None:
        st = {'ts': 0.0, 'url': None, 'titles': [], 'i': 0}
        setattr(fetch_canvas, '_state', st)

    now = time.time()
    if st['url'] != feed_url or (now - st['ts']) > 300:
        try:
            st['titles'] = _headlines(feed_url)
            st['ts'] = now
            st['url'] = feed_url
        except Exception:
            if not st['titles']:
                canvas.frame(canvas.message('NEWS UNAVAILABLE', 'CHECK FEED URL', color=_WHITE))
                return 60.0
            st['ts'] = now                      # keep showing the stale list; retry in 5 min

    titles = st['titles']
    if not titles:
        canvas.frame(canvas.message('NEWS', 'NO HEADLINES', color=_WHITE))
        return 120.0
    idx = st['i'] % len(titles)
    st['i'] = (st['i'] + 1) % len(titles)
    title = titles[idx]

    W, H = canvas.width, canvas.height
    if getattr(canvas, 'can_gtext', False) and H >= 96:
        # The big-panel path: live ops at native resolution (crisp masthead + TTF headline).
        _cv_news_ops(canvas, feed_url, title, idx, len(titles), W, H)
        return 8.0
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    # Masthead: source badge on red, pagination dots on the right (this headline lit).
    src = _source_tag(feed_url)
    bar_h = max(9, int(H * 0.22))
    sf = canvas.fit_font(src, int(W * 0.5), bar_h - 3)
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
    top = bar_h + 2
    max_lines = 3 if H >= 48 else 2
    if H >= 96:
        # Tall panel: completeness beats size — a line budget deep enough that the
        # whole headline fits (three LCD-height lines were cutting it mid-sentence).
        max_lines = max(4, (H - top) // 14)
    # canvas.wrap_fit already ellipsizes a title the line budget cuts short.
    nf, lines = canvas.wrap_fit(title, W - 6, H - top, max_lines)
    lh = canvas.ink(nf, 'Ag')
    gap = max(1, lh // 6)
    # The block rides the panel floor, its leading stretched (the font is already
    # at its cap) toward the masthead — but never past lh//2 of extra air, which
    # would tear the headline into strips with a hole between the lines.
    ob = nf.getbbox(lines[-1] or '0')
    own = ob[3] - ob[1]
    step = lh + gap
    if len(lines) > 1:
        step += max(0, min(lh // 2, (H - own - top) // (len(lines) - 1) - step))
    ny = H - own - step * (len(lines) - 1)
    for ln in lines:
        draw.text((3, ny - nf.getbbox(ln)[1]), ln, font=nf, fill=_WHITE)
        ny += step

    canvas.frame(img)
    return 8.0
