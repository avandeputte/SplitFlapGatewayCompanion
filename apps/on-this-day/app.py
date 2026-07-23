"""On This Day in History — one concise event, on a single page (byabbe.se)."""


# =============================================================================
# SHARED — the event DATA: today's list from byabbe.se with the bundled
# fallback. Both surfaces draw from the same [(year, description)] list.
# =============================================================================

_FALLBACK = [
    (1776, "Declaration of Independence signed"),
    (1969, "First moon landing by Apollo 11"),
    (1989, "Berlin Wall falls in Germany"),
    (1903, "Wright brothers first flight"),
    (1865, "Civil War ends in America"),
    (1945, "World War 2 ends"),
    (1963, "I Have a Dream speech by MLK"),
    (1912, "Titanic sinks on maiden voyage"),
    (1929, "Stock market crash Black Tuesday"),
    (1955, "Rosa Parks refuses to give up seat"),
]


def _clean(s):
    # No character filtering: the renderer degrades wall-aware at the last moment
    # (accents survive on reels that carry them). Filtering to ASCII here was
    # punching holes in names like "Dvořák" on walls that could have shown them.
    return s.strip()


def _events(settings):
    """Today's events as [(year, description)] in the wall's timezone — the shared
    list both surfaces draw from. Never raises: the bundled fallback stands in
    when byabbe.se is unreachable (or sends nothing)."""
    import urllib.request
    import json
    from datetime import datetime
    import pytz

    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)

    try:
        url = f"https://byabbe.se/on-this-day/{now.month}/{now.day}/events.json"
        req = urllib.request.Request(url, headers={"User-Agent": "SplitFlapGatewayCompanion/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        events = [(str(e.get("year", "")), _clean(e.get("description", "")))
                  for e in data.get("events", []) if e.get("description")]
        if not events:
            raise ValueError("no events")
    except Exception:
        events = [(str(y), _clean(d)) for y, d in _FALLBACK]
    return events


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def _split(text, width):
    words, lines, cur = text.split(), [], ''
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w[:width]
        elif not cur:
            cur = w[:width]
        else:
            cur += ' ' + w
    if cur:
        lines.append(cur)
    return lines


def fetch(settings, format_lines, get_rows, get_cols):
    import random

    cols, rows = get_cols(), get_rows()
    events = _events(settings)

    # Lead with the year (no wasted 'ON THIS DAY' header row) and keep each event
    # whole on its own page — up to three that fit, so the rotation shows more
    # than one thing that happened today.
    events = [(y, f'{y} {d}') for y, d in events]     # (year, "YEAR DESC")
    if rows == 1:
        picks = sorted(events, key=lambda e: len(e[1]))[:3]
        return [t[:cols].center(cols) for _y, t in picks]
    random.shuffle(events)
    pages = []
    for _y, t in events:
        if len(_split(t, cols)) <= rows:
            pages.append(format_lines(*_split(t, cols)))
        if len(pages) == 3:
            break
    if not pages:
        text = min(events, key=lambda e: len(e[1]))[1]
        # format_lines centers it; doing it here as well lands it below the middle.
        pages = [format_lines(*_split(text, cols)[:rows])]
    return pages


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The same events one at a time: a gold year chip (the year is the hook) with
# today's date opposite, the event wrapped large below. Advances through the
# list each redraw; the day's list is cached so the rotation doesn't re-ask
# the API every few seconds. Solid black background.
# =============================================================================

_CV_CHIP = (255, 198, 64)             # the gold year chip
_CV_CHIP_TXT = (0, 0, 0)              # solid black on gold — full contrast on the chip
_CV_TXT = (238, 240, 244)
_CV_DIM = (140, 146, 156)


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


def _cv_wrap_fit(canvas, text, max_w, max_h, max_lines, min_size=8):
    """Largest font at which the WHOLE of ``text`` wraps into <= ``max_lines`` lines
    fitting the box — shrinking (never below ``min_size``: staying readable beats
    staying complete) rather than silently dropping words. Returns
    (font, lines, line_height, gap); at the floor the tail may still be cut."""
    words_n = len(str(text or '').split())
    size = max(min_size, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = _cv_wrap(font, text, max_w, max_lines)
        b = font.getbbox('Ag')
        lh = b[3] - b[1]
        gap = max(1, lh // 6)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((font.getlength(ln) for ln in lines), default=0)
        complete = sum(len(ln.split()) for ln in lines) == words_n
        if size <= min_size or (total <= max_h and widest <= max_w and complete):
            if size <= min_size:                     # at the floor: drop lines, not below the box
                lines = lines[:max(1, int((max_h + gap) // (lh + gap)))]
            return font, lines, lh, gap
        size -= 1
    font = canvas.font(min_size)
    lines = _cv_wrap(font, text, max_w, max_lines)
    b = font.getbbox('Ag')
    return font, lines, b[3] - b[1], 1


def _cv_text(draw, x, y, text, font, fill):
    """Baseline-corrected text draw (y is the ink top, whatever the glyph bbox says)."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill)


def fetch_matrix(settings, canvas):
    import time
    from datetime import datetime
    from PIL import ImageDraw
    import pytz

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'i': 0, 'ts': 0.0, 'events': None}
        setattr(fetch_matrix, '_state', st)
    # One API call an hour (it's a daily list) — the rotation runs off the cache.
    if st['events'] is None or time.time() - st['ts'] > 3600:
        st['events'] = _events(settings)            # never raises: falls back internally
        st['ts'] = time.time()
    events = st['events']

    idx = st['i'] % len(events)
    st['i'] = (st['i'] + 1) % len(events)
    year, desc = events[idx]

    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    today = datetime.now(tz).strftime('%b %d').upper()

    W, H = int(canvas.width), int(canvas.height)
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3

    try:
        dwell = float(settings.get('loop_delay', 10) or 10)
    except (TypeError, ValueError):
        dwell = 10.0
    dwell = max(3.0, min(30.0, dwell))

    # A short panel has no room for the chip row — the year runs inline in gold instead.
    if H < 48:
        text = f'{year} {desc}'
        f, lines, lh, gap = _cv_wrap_fit(canvas, text, W - 2 * pad, H - 2, 3)
        if sum(len(ln.split()) for ln in lines) < len(text.split()):
            last = lines[-1]
            while last and f.getlength(last + '…') > W - 2 * pad:
                last = last[:-1]
            lines[-1] = last + '…'
        # First line's ink starts at row 1, the last line's ends at H-1: the
        # leading stretches (the font is at its cap) to span the whole panel.
        ob = f.getbbox(lines[-1] or '0')
        own = ob[3] - ob[1]
        k = len(lines)
        ys = [H - own]                       # the last line's ink top, on the floor
        if k > 1:
            pitch = (H - own - 1) / (k - 1)
            if pitch > 2 * lh + gap:         # too sparse to stretch honestly — keep
                pitch = lh + gap             # the natural leading, floor-anchored
            ys = [round((H - own) - pitch * (k - 1 - i)) for i in range(k)]
        for i, (y, ln) in enumerate(zip(ys, lines)):
            if i == 0 and ln.startswith(year):
                _cv_text(draw, pad, y, year, f, _CV_CHIP)
                _cv_text(draw, pad + f.getlength(year + ' '), y, ln[len(year):].strip(), f, _CV_TXT)
            else:
                _cv_text(draw, pad, y, ln, f, _CV_TXT)
        canvas.frame(img)
        return dwell

    # The gold year chip flush with the panel's top edge, today's date opposite.
    ch_h = max(11, int(H * 0.24))
    yf = _cv_fit(canvas, year, int(W * 0.4), ch_h - 4)
    yw = yf.getlength(year)
    yh = yf.getbbox(year)[3] - yf.getbbox(year)[1]
    draw.rounded_rectangle([pad, 0, pad + yw + 8, ch_h - 1], radius=3, fill=_CV_CHIP)
    _cv_text(draw, pad + 5, (ch_h - yh) // 2, year, yf, _CV_CHIP_TXT)
    df = _cv_fit(canvas, today, int(W * 0.35), max(6, int(ch_h * 0.55)))
    if pad + yw + 14 + df.getlength(today) <= W - pad:
        dh = df.getbbox(today)[3] - df.getbbox(today)[1]
        _cv_text(draw, W - pad - df.getlength(today), (ch_h - dh) // 2, today, df, _CV_DIM)

    # The event, wrapped as large as the room allows (ellipsis when it can't all
    # fit), anchored to the panel floor with the leading stretched up to the chip.
    body_top = ch_h + 2
    max_lines = 4 if H >= 60 else 3
    f, lines, lh, gap = _cv_wrap_fit(canvas, desc, W - 2 * pad, H - body_top, max_lines)
    if sum(len(ln.split()) for ln in lines) < len(str(desc).split()):
        last = lines[-1]
        while last and f.getlength(last + '…') > W - 2 * pad:
            last = last[:-1]
        lines[-1] = last + '…'
    ob = f.getbbox(lines[-1] or '0')
    own = ob[3] - ob[1]
    step = lh + gap
    if len(lines) > 1:
        step += max(0, min(lh, (H - own - body_top) // (len(lines) - 1) - step))
    y = H - own - step * (len(lines) - 1)
    for ln in lines:
        _cv_text(draw, pad, y, ln, f, _CV_TXT)
        y += step
    canvas.frame(img)
    return dwell
