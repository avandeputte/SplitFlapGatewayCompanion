"""Today on Wikipedia — featured article & most-read (keyless: Wikimedia REST)."""


# =============================================================================
# SHARED — today's feed from the Language's own Wikipedia edition. Both
# surfaces show the same featured article and the same most-read list.
# =============================================================================

def _feed(settings, i18n=None):
    """Today's featured-article title and the most-read titles, in the feed's
    own order, as ``(title, mostread)``. Pulled from the language's own
    Wikipedia edition (fr.wikipedia, de.wikipedia, ...); the English variants
    all use en.wikipedia. Raises on network trouble."""
    import requests
    from datetime import datetime
    import pytz
    wl = i18n.lang_base if i18n is not None else 'en'
    try:
        tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone('US/Eastern')
    now = datetime.now(tz)
    d = requests.get(f'https://{wl}.wikipedia.org/api/rest_v1/feed/featured/{now:%Y/%m/%d}',
                     headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'}, timeout=10).json()
    tfa = d.get('tfa') or {}
    title = str(tfa.get('normalizedtitle', '') or '')
    mostread = [str(a.get('normalizedtitle', '') or '')
                for a in ((d.get('mostread') or {}).get('articles', []) or [])]
    return title, [a for a in mostread if a]


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def _wrap(text, cols, maxlines):
    words, lines, cur = text.split(), [], ''
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= cols:
            cur = f'{cur} {w}'.strip()
        else:
            lines.append(cur)
            cur = w[:cols]
            if len(lines) >= maxlines:
                break
    if cur and len(lines) < maxlines:
        lines.append(cur)
    return lines[:maxlines] or ['']


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "content") if i18n is not None else s

    try:
        title, mostread = _feed(settings, i18n)
        pages = []
        if title:
            if rows == 1:
                pages.append(f'Wiki {title}'[:cols].center(cols))
            else:
                pages.append(format_lines(f'Wiki {t("Featured")}', *_wrap(title, cols, rows - 1)))

        if rows >= 4 and mostread:
            # A tall wall shows the whole list at once. One article per page spent
            # four rows on a title that fits in one, and made you wait through three
            # page turns to read what is really just a three-line list.
            slots = rows - 1                      # one row is the header
            pages.append(format_lines(f'Wiki {t("Most read")}',
                                      *[_wrap(a, cols, 1)[0] for a in mostread[:slots]]))
        else:
            for art in mostread[:3]:
                if rows == 1:
                    pages.append(f'Wiki {art}'[:cols].center(cols))
                else:
                    pages.append(format_lines(f'Wiki {t("Most read")}', *_wrap(art, cols, rows - 1)))
        return pages or [format_lines('Wikipedia', 'No data', '')]
    except Exception:
        return [format_lines('Wikipedia', 'Offline', '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A slideshow of typographic cards: the featured article first, then the
# most-read titles one per card with their rank in the label — the same items
# in the same order as the flap pages, paced by loop_delay. Each card carries
# a drawn W medallion and a steel-blue label over a thin rule; the title wraps
# at the largest font that fits. Black background; the medallion drops away on
# tiny panels.
# =============================================================================

_ACCENT = (150, 175, 225)     # steel blue — the medallion and labels
_TEXT = (238, 238, 244)       # the article titles
_DIM = (150, 150, 158)        # the quiet second line of an offline message
_DOT_OFF = (70, 70, 76)       # inactive page dots


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


def _cv_wrap(font, text, max_w):
    """Greedy word-wrap to pixel width ``max_w`` — hard-splitting any word wider
    than the panel so nothing ever draws off the edge."""
    lines, cur = [], ''
    for word in str(text or '').split():
        w = word
        while font.getlength(w) > max_w and len(w) > 1:
            cut = len(w)
            while cut > 1 and font.getlength(w[:cut]) > max_w:
                cut -= 1
            if cur:
                lines.append(cur)
                cur = ''
            lines.append(w[:cut])
            w = w[cut:]
        cand = f'{cur} {w}'.strip()
        if not cur or font.getlength(cand) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or ['']


def _cv_pages(canvas, text, max_w, max_h, min_size=7):
    """The largest font (>= ``min_size``) at which the WHOLE text wraps into
    ``max_w`` x ``max_h`` — one page. When even ``min_size`` can't hold it, wrap
    at ``min_size`` and split the lines into pages to rotate through across
    redraws. Returns (font, pages, line_h, gap)."""
    size = max(min_size, int(max_h))
    while size >= min_size:
        font = canvas.font(size)
        lines = _cv_wrap(font, text, max_w)
        b = font.getbbox('Ag')
        lh, gap = b[3] - b[1], max(1, (b[3] - b[1]) // 6)
        if (len(lines) * lh + (len(lines) - 1) * gap <= max_h
                and max(font.getlength(ln) for ln in lines) <= max_w):
            return font, [lines], lh, gap
        size -= 1
    font = canvas.font(min_size)
    lines = _cv_wrap(font, text, max_w)
    b = font.getbbox('Ag')
    lh, gap = b[3] - b[1], max(1, (b[3] - b[1]) // 6)
    per = max(1, (max_h + gap) // (lh + gap))
    return font, [lines[i:i + per] for i in range(0, len(lines), per)] or [['']], lh, gap


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (offline / no data) — never a crash, never a blank panel."""
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
    draw.text(((W - f1.getlength(line1)) / 2.0, y - b1[1]), line1, font=f1, fill=_TEXT)
    if line2:
        y += h1 + gap
        draw.text(((W - f2.getlength(line2)) / 2.0, y - f2.getbbox(line2)[1]),
                  line2, font=f2, fill=_DIM)
    return img


def _cv_motif(canvas, draw, x, y, s):
    """The app's accent mark: a W medallion — the ring with a W set inside it.
    Returns the width it consumed."""
    draw.ellipse([x, y, x + s - 1, y + s - 1], outline=_ACCENT)
    f = _cv_fit(canvas, 'W', s - 4, s - 4)
    b = f.getbbox('W')
    draw.text((x + (s - 1 - (b[2] - b[0])) / 2.0 - b[0],
               y + (s - 1 - (b[3] - b[1])) / 2.0 - b[1]), 'W', font=f, fill=_ACCENT)
    return s


def _cv_header(canvas, draw, label):
    """Medallion + label over a thin accent rule. Returns the y where the body
    starts; the medallion drops away on small panels, the label never does."""
    W, H = canvas.width, canvas.height
    hh = max(7, int(H * 0.19))
    x = 3
    if W >= 96 and H >= 48:
        x += _cv_motif(canvas, draw, 3, 0, hh + 3) + 4
    f = _cv_fit(canvas, label, W - x - 3, hh)
    b = f.getbbox(label)
    draw.text((x, 1 - b[1]), label, font=f, fill=_ACCENT)
    ry = 1 + max(hh, b[3] - b[1]) + 2
    draw.line([(3, ry), (W - 4, ry)], fill=tuple(c // 3 for c in _ACCENT))
    return ry + 2


def _cv_card(canvas, ImageDraw, label, body, page):
    """One frame of a card: the header, then page ``page`` of the body at the
    largest font that fits, plus page dots where there is room.
    Returns (img, page_count)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    top = _cv_header(canvas, draw, label)
    font, pages, lh, gap = _cv_pages(canvas, body, W - 6, H - top)
    dots = 1 < len(pages) <= 8 and H >= 44
    if dots:                                   # the dots take the bottom two rows
        font, pages, lh, gap = _cv_pages(canvas, body, W - 6, H - top - 3)
        dots = 1 < len(pages) <= 8
    n = len(pages)
    lines = pages[page % n]
    base = font.getbbox('Ag')[1]
    bottom = H - 4 if dots else H - 1          # the last row body ink may light
    fb = font.getbbox(lines[0] or '0')
    lb = font.getbbox(lines[-1] or '0')
    step = lh + gap
    if len(lines) > 1:
        # The font is already at its cap, so fill by leading: stretch the line
        # gaps (up to one line-height) until the block spans the whole region.
        span = (len(lines) - 1) * step + (lb[3] - base) - (fb[1] - base)
        step += max(0, min(lh, (bottom + 1 - top - span) // (len(lines) - 1)))
    # Anchor the block to the floor; any leftover rides under the header rule.
    y = bottom + 1 - (lb[3] - base) - step * (len(lines) - 1)
    for ln in lines:
        draw.text(((W - font.getlength(ln)) / 2.0, y - base), ln, font=font, fill=_TEXT)
        y += step
    if dots:
        for i in range(n):
            c = _ACCENT if i == (page % n) else _DOT_OFF
            draw.rectangle([3 + i * 5, H - 2, 4 + i * 5, H - 1], fill=c)
    return img, n


def _cv_state():
    """The slideshow state kept across redraws: today's feed, which card is up
    and which page of it."""
    st = getattr(_cv_state, '_st', None)
    if st is None:
        st = _cv_state._st = {'data': None, 'ts': 0.0, 'card': 0, 'page': 0}
    return st


def fetch_matrix(settings, canvas, i18n=None):
    """One card per redraw — the featured article, then the top most-read
    titles — paced by loop_delay. The feed renews hourly (the manifest's
    refresh cadence) and only between laps of the slideshow; a fetch failure
    keeps yesterday's feed on screen."""
    from PIL import ImageDraw
    import time

    def t(s):
        return i18n.t(s, "content") if i18n is not None else s

    st = _cv_state()
    now = time.time()
    if st['data'] is None or (st['card'] == 0 and st['page'] == 0
                              and now - st['ts'] >= 3600.0):
        try:
            got = _feed(settings, i18n)
        except Exception:
            got = None
        if got and (got[0] or got[1]):
            st.update(data=got, ts=now, card=0, page=0)
        else:
            st['ts'] = now - 3600.0 + 120.0    # keep any stale feed; retry in ~2 minutes
            if st['data'] is None:
                canvas.frame(_cv_message(canvas, ImageDraw, 'Wikipedia',
                                         t('Offline') if i18n is not None else 'Offline'))
                return 60.0
    title, mostread = st['data']
    cards = []
    if title:
        cards.append((t('Featured').upper(), title))
    cards += [(f'{t("Most read").upper()} #{i + 1}', art)
              for i, art in enumerate(mostread[:5])]
    if not cards:
        canvas.frame(_cv_message(canvas, ImageDraw, 'Wikipedia', 'No data'))
        return 300.0
    label, body = cards[st['card'] % len(cards)]
    img, n = _cv_card(canvas, ImageDraw, label, body, st['page'])
    canvas.frame(img)
    st['page'] += 1
    if st['page'] >= n:
        st['page'] = 0
        st['card'] = (st['card'] + 1) % len(cards)
    try:
        d = float(settings.get('loop_delay', 8) or 8)
    except (TypeError, ValueError):
        d = 8.0
    return max(6.0, min(30.0, d))
