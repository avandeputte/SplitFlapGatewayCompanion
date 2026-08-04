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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
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
_DOT_OFF = (70, 70, 76)       # inactive page dots


def _cv_motif(canvas, draw, x, y, s):
    """The app's accent mark: a W medallion — the ring with a W set inside it.
    Returns the width it consumed."""
    draw.ellipse([x, y, x + s - 1, y + s - 1], outline=_ACCENT)
    f = canvas.fit_font('W', s - 4, s - 4)
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
    f = canvas.fit_font(label, W - x - 3, hh)
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
    font, pages, lh, gap = canvas.card_pages(body, W - 6, H - top)
    dots = 1 < len(pages) <= 8 and H >= 44
    if dots:                                   # the dots take the bottom two rows
        font, pages, lh, gap = canvas.card_pages(body, W - 6, H - top - 3)
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


def _cv_tall(canvas, ImageDraw, t, title, mostread):
    """The 1.6:1 LCD card: the featured title big and WHOLE-WORD (wrap_fit shrinks
    so words stay whole — card_pages would hyphen-split it, 'Voyag-er 1') filling
    the upper band, the also-featured most-read titles as a ranked list below —
    one composition, not a slideshow of one-line cards."""
    W, H = int(canvas.width), int(canvas.height)
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 4
    items = [a for a in (mostread or []) if a]
    top = _cv_header(canvas, draw, (t('Featured') if title else t('Most read')).upper())

    lf = canvas.font(max(12, int(H * 0.085)))        # the list rows
    lih = canvas.ink(lf, 'Ag')
    row = lih + max(2, lih // 4)
    rx = pad + lf.getlength('0') + 6                 # title x, past the rank digit

    if title:
        items = items[:3]                            # a few — the hero keeps the room
        mlf = canvas.fit_font(t('Most read').upper(), W - 2 * pad, max(9, int(H * 0.085)))
        list_h = (canvas.ink(mlf, 'Ag') + 5 + len(items) * row) if items else 0
        title_h = H - top - list_h - (8 if items else 0)
        tf, tlines = canvas.wrap_fit(title, W - 2 * pad, max(1, title_h), 2)
        tlh = canvas.ink(tf, 'Ag')
        tgap = max(2, tlh // 6)
        blk = len(tlines) * tlh + (len(tlines) - 1) * tgap
        ty = top + max(0, (title_h - blk) // 2)
        for ln in tlines:
            canvas.text_top(draw, (W - tf.getlength(ln)) / 2.0, ty, ln, tf, _TEXT)
            ty += tlh + tgap
        if items:
            y = top + title_h + 8
            canvas.text_top(draw, pad, y, t('Most read').upper(), mlf, _ACCENT)
            y += canvas.ink(mlf, 'Ag') + 5
            for i, art in enumerate(items):
                canvas.text_top(draw, pad, y, f'{i + 1}', lf, _ACCENT)
                canvas.text_top(draw, rx, y, canvas.wrap(lf, art, W - pad - rx, 1)[0], lf, _TEXT)
                y += row
    else:                                            # no featured article — the list fills it
        items = items[:6] or ['']
        y = top + max(0, (H - top - 4 - row * len(items)) // 2)
        for i, art in enumerate(items):
            canvas.text_top(draw, pad, y, f'{i + 1}', lf, _ACCENT)
            canvas.text_top(draw, rx, y, canvas.wrap(lf, art, W - pad - rx, 1)[0], lf, _TEXT)
            y += row
    return img


def fetch_canvas(settings, canvas, i18n=None):
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
                canvas.frame(canvas.message('Wikipedia',
                                            t('Offline') if i18n is not None else 'Offline'))
                return 60.0
    title, mostread = st['data']

    if int(canvas.height) >= 96:                      # tall LCD — hero title + most-read list
        canvas.frame(_cv_tall(canvas, ImageDraw, t, title, mostread))
        try:
            d = float(settings.get('loop_delay', 8) or 8)
        except (TypeError, ValueError):
            d = 8.0
        return max(6.0, min(30.0, d))

    cards = []
    if title:
        cards.append((t('Featured').upper(), title))
    # A narrow panel drops the "#n" rank — "MOST READ" alone stays legible at
    # the 8px floor where "MOST READ #4" would not.
    ranked = canvas.width >= 96
    cards += [(f'{t("Most read").upper()} #{i + 1}' if ranked else t('Most read').upper(), art)
              for i, art in enumerate(mostread[:5])]
    if not cards:
        canvas.frame(canvas.message('Wikipedia', 'No data'))
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
