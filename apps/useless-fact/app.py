"""A random useless fact (keyless: uselessfacts)."""


# =============================================================================
# SHARED — the fact itself: fetched in the wall's language where the API has
# it, best-of-three under the configured length. Both surfaces show this.
# =============================================================================

def _best_fact(settings, i18n=None):
    """Up to three draws from the API, keeping the first under the configured max
    length (the API can't filter by length), else the shortest seen. The API
    serves English and German; the Language picks between them, everyone else
    gets English. Returns the text or None; raises on network trouble."""
    import requests
    try:
        max_len = int(float(settings.get('max_length', '140') or 140))
    except (TypeError, ValueError):
        max_len = 140
    max_len = max(40, min(280, max_len))

    lang = i18n.lang_base if i18n is not None else 'en'
    api_lang = lang if lang in ('en', 'de') else 'en'

    def one():
        d = requests.get('https://uselessfacts.jsph.pl/api/v2/facts/random',
                         params={'language': api_lang}, timeout=8).json()
        return str(d.get('text', '') or '').strip()

    best = None
    for _ in range(3):
        fact = one()
        if fact and len(fact) <= max_len:
            best = fact
            break
        if fact and (best is None or len(fact) < len(best)):
            best = fact
    return best


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])

    def t(s):
        return i18n.t(s, "facts") if i18n is not None else s

    try:
        best = _best_fact(settings, i18n)
        if not best:
            return [format_lines(t('Random fact'), t('No data'), '')]
        return paginate(best, t('Did you know'))
    except Exception:
        return [format_lines(t('Random fact'), t('Offline'), '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A typographic card: a little drawn lightbulb and yellow "DID YOU KNOW" label
# over a thin rule, the fact wrapped at the largest font that fits (paginating
# across redraws when even ~7px can't hold it). Black background; the bulb
# drops away on tiny panels.
# =============================================================================

_ACCENT = (255, 220, 70)      # bulb yellow — the motif and label
_TEXT = (238, 238, 244)       # the fact itself
_DIM = (150, 150, 158)        # the bulb's base + the quiet second message line
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
    """The app's accent mark: a little lightbulb — the glass, a gray base and
    three rays. Returns the width it consumed."""
    d = s * 0.62
    bx = x + (s - d) / 2.0
    draw.ellipse([bx, y + s * 0.16, bx + d, y + s * 0.16 + d], fill=_ACCENT)
    draw.rectangle([x + s * 0.38, y + s * 0.80, x + s * 0.60, y + s * 0.98], fill=_DIM)
    cx = x + s / 2.0
    draw.line([(cx, y), (cx, y + max(1.0, s * 0.08))], fill=_ACCENT)
    draw.line([(x + s * 0.06, y + s * 0.18), (x + s * 0.16, y + s * 0.28)], fill=_ACCENT)
    draw.line([(x + s * 0.94, y + s * 0.18), (x + s * 0.84, y + s * 0.28)], fill=_ACCENT)
    return s


def _cv_header(canvas, draw, label):
    """Motif + label over a thin accent rule. Returns the y where the body
    starts; the bulb drops away on small panels, the label never does."""
    W, H = canvas.width, canvas.height
    hh = max(7, int(H * 0.19))
    x = 3
    if W >= 96 and H >= 48:
        x += _cv_motif(canvas, draw, 3, 0, hh + 2) + 4
    f = _cv_fit(canvas, label, W - x - 3, hh)
    b = f.getbbox(label)
    draw.text((x, 1 - b[1]), label, font=f, fill=_ACCENT)
    ry = 1 + max(hh, b[3] - b[1]) + 2
    draw.line([(3, ry), (W - 4, ry)], fill=tuple(c // 3 for c in _ACCENT))
    return ry + 2


def _cv_card(canvas, ImageDraw, label, body, page):
    """One frame of the card: the header, then page ``page`` of the body at the
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
    """The card state kept across redraws: the current fact and which page is up."""
    st = getattr(_cv_state, '_st', None)
    if st is None:
        st = _cv_state._st = {'data': None, 'ts': 0.0, 'page': 0}
    return st


def fetch_matrix(settings, canvas, i18n=None):
    """Draw the fact as a typographic card, turning body pages each redraw when
    the panel can't hold the whole fact. The fact renews on the app's
    refresh_minutes cadence; a fetch failure keeps the last fact on screen."""
    from PIL import ImageDraw
    import time

    def t(s):
        return i18n.t(s, "facts") if i18n is not None else s

    st = _cv_state()
    try:
        mins = float(settings.get('refresh_minutes', 30) or 30)
    except (TypeError, ValueError):
        mins = 30.0
    ttl = max(60.0, mins * 60.0)
    now = time.time()
    if st['data'] is None or (st['page'] == 0 and now - st['ts'] >= ttl):
        try:
            got = _best_fact(settings, i18n)
        except Exception:
            got = None
        if got:
            st.update(data=got, ts=now, page=0)
        else:
            st['ts'] = now - ttl + 60.0        # keep any stale fact; retry in ~a minute
            if st['data'] is None:
                canvas.frame(_cv_message(canvas, ImageDraw, t('Random fact'), t('Offline')))
                return 60.0
    img, n = _cv_card(canvas, ImageDraw, t('Did you know').upper(), st['data'], st['page'])
    canvas.frame(img)
    st['page'] = (st['page'] + 1) % n
    if n > 1:
        try:
            d = float(settings.get('loop_delay', 7) or 7)
        except (TypeError, ValueError):
            d = 7.0
        return max(6.0, min(30.0, d))
    return max(30.0, min(300.0, ttl - (now - st['ts'])))
