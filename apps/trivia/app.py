"""Trivia plugin for Split-Flap Display."""


# =============================================================================
# SHARED — one question-and-answer, with the bundled classics as the offline
# fallback. Both surfaces show the same Q then the same A.
# =============================================================================

FALLBACK_QA = [
    ("What is the largest planet?", "Jupiter"),
    ("How many bones in a human body?", "206"),
    ("What is the speed of light?", "186000 mi/sec"),
    ("What year did WW2 end?", "1945"),
    ("What is the smallest country?", "Vatican City"),
    ("How many strings on a guitar?", "Six"),
    ("What is the hardest mineral?", "Diamond"),
    ("What gas do plants absorb?", "CO2"),
    ("How many legs does a spider have?", "Eight"),
    ("What is the largest ocean?", "Pacific"),
]


def _fetch_qa():
    """One ``(question, answer)`` from Open Trivia DB, or one of the bundled
    classics when the API is unreachable — this never raises."""
    import urllib.request
    import json
    import html
    import random
    try:
        url = "https://opentdb.com/api.php?amount=1&type=multiple"
        req = urllib.request.Request(url, headers={"User-Agent": "SplitFlap/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        result = data["results"][0]
        return html.unescape(result["question"]), html.unescape(result["correct_answer"])
    except Exception:
        return random.choice(FALLBACK_QA)


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols):
    cols = get_cols()
    rows = get_rows()

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

    question, answer = _fetch_qa()

    # No character filtering: the renderer degrades wall-aware at the last moment
    # (accents survive on reels that carry them). Filtering to ASCII here was
    # punching holes in trivia about "Beyoncé" on walls that could have shown her.
    q_lines = split_text(question, cols)
    pages = []
    for i in range(0, len(q_lines), rows):
        chunk = q_lines[i:i + rows]
        pages.append(format_lines(*chunk))

    a_lines = split_text(answer, cols)
    a_lines = ['Answer:'] + a_lines
    for i in range(0, len(a_lines), rows):
        chunk = a_lines[i:i + rows]
        pages.append(format_lines(*chunk))

    return pages or [format_lines('Trivia', 'No data', '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# Two typographic cards shown in turn: the question under a violet "?" badge,
# then the answer under a green "A" badge — the same quiz rhythm as the flap
# pages, paced by loop_delay. Body text wraps at the largest font that fits
# (paginating across redraws when even ~7px can't hold it). Black background;
# the badge drops away on tiny panels.
# =============================================================================

_VIOLET = (190, 130, 255)     # the question card
_GREEN = (110, 230, 130)      # the answer card
_TEXT = (238, 238, 244)       # the words themselves
_DIM = (150, 150, 158)        # the quiet second line of an offline message
_DOT_OFF = (70, 70, 76)       # inactive page dots


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 8px —
    smaller sizes render wrong-reading glyphs on the panel)."""
    size = max(8, int(max_h) + 3)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 8 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
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


def _cv_pages(canvas, text, max_w, max_h, min_size=8):
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


def _cv_badge(canvas, draw, x, y, s, char, accent):
    """The card's accent mark: a filled disc with the card's character punched
    through it in black. Returns the width it consumed."""
    draw.ellipse([x, y, x + s, y + s], fill=accent)
    f = _cv_fit(canvas, char, s - 3, s - 3)
    b = f.getbbox(char)
    draw.text((x + (s - (b[2] - b[0])) / 2.0 - b[0], y + (s - (b[3] - b[1])) / 2.0 - b[1]),
              char, font=f, fill=(0, 0, 0))
    return s


def _cv_header(canvas, draw, label, accent, badge):
    """Badge + label over a thin accent rule. Returns the y where the body
    starts; the badge drops away on small panels, the label never does."""
    W, H = canvas.width, canvas.height
    hh = max(7, int(H * 0.19))
    x = 3
    if W >= 96 and H >= 48:
        x += _cv_badge(canvas, draw, 3, 0, hh + 2, badge, accent) + 4
    f = _cv_fit(canvas, label, W - x - 3, hh)
    if f.getlength(label) > W - x - 3:
        # The width forced the font to its 8px floor and the label still
        # overflows — keep the band-height size and shorten by whole words
        # instead of clipping at the edge (a missing word beats a garbled one).
        f = _cv_fit(canvas, label, 10 ** 6, hh)
        words = str(label).split()
        while len(words) > 1 and f.getlength(' '.join(words)) > W - x - 3:
            words.pop()
        label = ' '.join(words)
        if f.getlength(label) > W - x - 3:
            f = _cv_fit(canvas, label, W - x - 3, hh)
    b = f.getbbox(label)
    draw.text((x, 1 - b[1]), label, font=f, fill=accent)
    ry = 1 + max(hh, b[3] - b[1]) + 2
    draw.line([(3, ry), (W - 4, ry)], fill=tuple(c // 3 for c in accent))
    return ry + 2


def _cv_card(canvas, ImageDraw, label, body, accent, badge, page):
    """One frame of a card: the header, then page ``page`` of the body at the
    largest font that fits, plus page dots where there is room.
    Returns (img, page_count)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    top = _cv_header(canvas, draw, label, accent, badge)
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
    # Center the block in the body region. A stretched multi-line page has no slack
    # left, so this equals filling; a short page (a one-word ANSWER) floats centered
    # instead of hugging the floor.
    ink_h = step * (len(lines) - 1) + (lb[3] - fb[1])
    y = top + max(0, bottom + 1 - top - ink_h) // 2 + base - fb[1]
    for ln in lines:
        draw.text(((W - font.getlength(ln)) / 2.0, y - base), ln, font=font, fill=_TEXT)
        y += step
    if dots:
        for i in range(n):
            c = accent if i == (page % n) else _DOT_OFF
            draw.rectangle([3 + i * 5, H - 2, 4 + i * 5, H - 1], fill=c)
    return img, n


def _cv_state():
    """The quiz state kept across redraws: the current Q&A, which card is up
    (question or answer) and which page of it."""
    st = getattr(_cv_state, '_st', None)
    if st is None:
        st = _cv_state._st = {'data': None, 'ts': 0.0, 'card': 0, 'page': 0}
    return st


def fetch_matrix(settings, canvas):
    """Question card, then answer card, each redraw a step — the panel's version
    of the flap pages' quiz rhythm, paced by loop_delay. A fresh question every
    ~5 minutes (the manifest's refresh cadence), and only between rounds, so an
    answer is never swapped out from under its question."""
    from PIL import ImageDraw
    import time
    st = _cv_state()
    now = time.time()
    if st['data'] is None or (st['card'] == 0 and st['page'] == 0
                              and now - st['ts'] >= 300.0):
        try:
            got = _fetch_qa()            # falls back to the classics by itself
        except Exception:
            got = None
        if got:
            st.update(data=got, ts=now, card=0, page=0)
        elif st['data'] is None:
            canvas.frame(_cv_message(canvas, ImageDraw, 'Trivia', 'Offline'))
            return 60.0
    question, answer = st['data']
    cards = [('TRIVIA', question, _VIOLET, '?'), ('ANSWER', answer, _GREEN, 'A')]
    label, body, accent, badge = cards[st['card'] % len(cards)]
    img, n = _cv_card(canvas, ImageDraw, label, body, accent, badge, st['page'])
    canvas.frame(img)
    st['page'] += 1
    if st['page'] >= n:
        st['page'] = 0
        st['card'] = (st['card'] + 1) % len(cards)
    try:
        d = float(settings.get('loop_delay', 10) or 10)
    except (TypeError, ValueError):
        d = 10.0
    return max(6.0, min(30.0, d))
