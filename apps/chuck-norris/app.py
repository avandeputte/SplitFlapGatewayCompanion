"""Chuck Norris facts plugin for Split-Flap Display."""


# =============================================================================
# SHARED — the fact itself, with the bundled classics as the offline fallback.
# Both surfaces show what this returns.
# =============================================================================

FALLBACK = [
    "Chuck Norris counted to infinity twice",
    "Chuck Norris can slam a revolving door",
    "Chuck Norris makes onions cry",
    "When Chuck Norris does pushups he pushes the Earth down",
    "Chuck Norris can hear sign language",
    "Chuck Norris won a staring contest with the sun",
    "Time waits for no one except Chuck Norris",
    "Chuck Norris can cut a knife with butter",
    "Chuck Norris can speak Braille",
    "Chuck Norris beat the sun in a staring contest",
]


def _fetch_fact():
    """One fact from chucknorris.io, or one of the bundled classics when the
    API is unreachable — this never raises and never comes back empty."""
    import urllib.request
    import json
    import random
    try:
        url = "https://api.chucknorris.io/jokes/random"
        req = urllib.request.Request(url, headers={"User-Agent": "SplitFlap/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return data["value"]
    except Exception:
        return random.choice(FALLBACK)


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols):
    cols = get_cols()

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

    text = _fetch_fact()

    # Remove characters not in the flap set. Case-insensitively: a flap set lists the
    # glyphs a module CARRIES, not the case the wall shows — the companion folds case at
    # the last moment (renderer.fold), and only for a wall with no lowercase flaps. Testing
    # membership case-sensitively would blank every lowercase letter here, long before the
    # display got its say.
    allowed = set(" ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;:%'.,/?*")
    text = ''.join(c if c.upper() in allowed else ' ' for c in text)

    lines = split_text(text, cols)
    rows = get_rows()
    pages = []
    for i in range(0, len(lines), rows):
        chunk = lines[i:i + rows]
        pages.append(format_lines(*chunk))
    return pages or [format_lines('Chuck Norris', 'Facts', '')]


# =============================================================================
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# A typographic card: a drawn starburst (the impact mark) and red-orange label
# over a thin rule, the fact wrapped at the largest font that fits (paginating
# across redraws when even ~7px can't hold it) — and shown UNFILTERED, because
# the panel renders every character the API sends. Black background; the burst
# drops away on tiny panels.
# =============================================================================

_ACCENT = (255, 90, 60)       # red-orange — the starburst and label
_TEXT = (238, 238, 244)       # the fact itself
_DOT_OFF = (70, 70, 76)       # inactive page dots


def _cv_motif(canvas, draw, x, y, s):
    """The app's accent mark: an eight-point starburst — the impact mark.
    Returns the width it consumed."""
    import math
    cx, cy, r = x + s / 2.0, y + s / 2.0, s / 2.0
    pts = []
    for i in range(16):
        rad = r if i % 2 == 0 else r * 0.42
        ang = math.pi * i / 8.0 - math.pi / 2.0
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    draw.polygon(pts, fill=_ACCENT)
    return s


def _cv_header(canvas, draw, label):
    """Motif + label over a thin accent rule. Returns the y where the body
    starts; the burst drops away on small panels, the label never does."""
    W, H = canvas.width, canvas.height
    hh = max(7, int(H * 0.19))
    x = 3
    if H < 40:
        # A 32-tall panel: even at the 8px floor the label would crowd out the
        # fact itself — the burst alone carries the header, the label goes.
        _cv_motif(canvas, draw, 3, 0, hh + 2)
        ry = 1 + hh + 2
        draw.line([(3, ry), (W - 4, ry)], fill=tuple(c // 3 for c in _ACCENT))
        return ry + 2
    if W >= 96 and H >= 48:
        x += _cv_motif(canvas, draw, 3, 0, hh + 2) + 4
    f = canvas.fit_font(label, W - x - 3, hh)
    if f.getlength(label) > W - x - 3:
        # The width forced the font to its 8px floor and the label still
        # overflows — keep the band-height size and shorten by whole words
        # instead of clipping at the edge (a missing word beats a garbled one).
        f = canvas.fit_font(label, 10 ** 6, hh)
        words = str(label).split()
        while len(words) > 1 and f.getlength(' '.join(words)) > W - x - 3:
            words.pop()
        label = ' '.join(words)
        if f.getlength(label) > W - x - 3:
            f = canvas.fit_font(label, W - x - 3, hh)
    b = f.getbbox(label)
    draw.text((x, 1 - b[1]), label, font=f, fill=_ACCENT)
    ry = 1 + max(hh, b[3] - b[1]) + 2
    draw.line([(3, ry), (W - 4, ry)], fill=tuple(c // 3 for c in _ACCENT))
    return ry + 2


def _cv_card(canvas, ImageDraw, label, body, page):
    """One frame of the card: the header, then page ``page`` of the body at the
    largest font that fits (never below 8px), plus page dots where there is room.
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
    """The card state kept across redraws: the current fact and which page is up."""
    st = getattr(_cv_state, '_st', None)
    if st is None:
        st = _cv_state._st = {'data': None, 'ts': 0.0, 'page': 0}
    return st


def _ops_motif(canvas, x, y, s):
    """The eight-point starburst (the impact mark) as a filled polygon op — the
    gtext card's twin of _cv_motif."""
    import math
    cx, cy, r = x + s / 2.0, y + s / 2.0, s / 2.0
    pts = []
    for i in range(16):
        rad = r if i % 2 == 0 else r * 0.42
        ang = math.pi * i / 8.0 - math.pi / 2.0
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    canvas.poly(pts, color=_ACCENT, fill=True)


def _cv_ops(canvas, label, body):
    """The tall-panel card drawn with on-device scalable text (gtext) + geometry
    instead of a pushed pixel frame — crisp at the LCD's native resolution, a few
    hundred bytes a frame. Same burst+label header over a rule, then the fact as big
    wrapped text centered in the body (mirrors the PIL _cv_card layout below)."""
    W, H = canvas.width, canvas.height
    canvas.clear((0, 0, 0))
    m = max(6, int(W * 0.03))
    lab_h = max(9, int(H * 0.12))
    ms = int(lab_h * 1.3)
    top = max(4, int(H * 0.06))
    gap = max(4, int(W * 0.02))
    _ops_motif(canvas, m, top, ms)
    lsz = canvas.fit_gtext(label, W - m - (m + ms + gap), lab_h)
    canvas.gtext(m + ms + gap, top + (ms - lsz) // 2 - int(lsz * 0.08), label,
                 color=_ACCENT, size=lsz)
    ry = top + ms + max(3, int(H * 0.028))
    canvas.line(m, ry, W - 1 - m, ry, color=tuple(c // 3 for c in _ACCENT))
    by0 = ry + max(4, int(H * 0.045))
    by1 = H - max(6, int(H * 0.05))
    size, lines = canvas.fit_wrap_gtext(body, W - 2 * m, by1 - by0, max_lines=7)
    step = int(size * 1.18)
    y = by0 + max(0, (by1 - by0 - step * len(lines)) // 2)
    for ln in lines:
        canvas.gtext(W // 2, y, ln, color=_TEXT, size=size, align="center")
        y += step
    canvas.show()


def fetch_canvas(settings, canvas):
    """Draw the fact as a typographic card, turning body pages each redraw when
    the panel can't hold it whole. A fresh fact every ~5 minutes (the manifest's
    refresh cadence) — _fetch_fact falls back to the bundled classics offline,
    so there is always something to show."""
    from PIL import ImageDraw
    import time
    st = _cv_state()
    ttl = 300.0
    now = time.time()
    if st['data'] is None or (st['page'] == 0 and now - st['ts'] >= ttl):
        try:
            got = _fetch_fact()          # falls back to the classics by itself
        except Exception:
            got = None
        if got:
            st.update(data=got, ts=now, page=0)
        elif st['data'] is None:
            canvas.frame(canvas.message('Chuck Norris', 'Offline'))
            return 60.0
    W, H = canvas.width, canvas.height
    if getattr(canvas, "can_gtext", False) and H >= 96:
        _cv_ops(canvas, 'CHUCK NORRIS', st['data'])
        st['page'] = 0
        return max(30.0, min(300.0, ttl - (now - st['ts'])))
    img, n = _cv_card(canvas, ImageDraw, 'CHUCK NORRIS', st['data'], st['page'])
    canvas.frame(img)
    st['page'] = (st['page'] + 1) % n
    if n > 1:
        try:
            d = float(settings.get('loop_delay', 10) or 10)
        except (TypeError, ValueError):
            d = 10.0
        return max(6.0, min(30.0, d))
    return max(30.0, min(300.0, ttl - (now - st['ts'])))
