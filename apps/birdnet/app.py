"""Live bird detections from a BirdNET-Pi on the local network.

Three display modes shared by every surface: the latest detection, the last three
distinct species, or a leaderboard of today's most active species. The flap wall
writes them as text rows; a Matrix panel draws a detection card with a confidence
bar (and the leaderboard as a bar chart).
"""


# =============================================================================
# SHARED — the detection DATA: the BirdNET-Pi API call + confidence filter, and
# the trigger (surface-independent). Both surfaces show the same detections.
# =============================================================================

def _recent_detections(settings, limit):
    """GET /api/v1/detections/recent from the configured BirdNET-Pi, filtered to the
    configured minimum confidence. Raises on network trouble — callers pick the fallback."""
    import requests
    host = settings.get('birdnet_host', '')
    port = settings.get('birdnet_port', '80')
    min_conf = int(settings.get('min_confidence', '70')) / 100
    r = requests.get(f"http://{host}:{port}/api/v1/detections/recent?limit={limit}", timeout=10)
    r.raise_for_status()
    return [d for d in r.json() if d.get('confidence', 0) >= min_conf]


def trigger(settings, conditions):
    """Fire when a new bird detection matches the configured filter."""
    import requests

    host = settings.get('birdnet_host', '')
    port = settings.get('birdnet_port', '80')
    if not host:
        return False
    min_conf = int(settings.get('min_confidence', '70')) / 100
    filt = conditions.get('filter', 'any')
    species_query = conditions.get('species', '').lower().strip()
    watchlist_str = conditions.get('watchlist', '').lower()
    watchlist = [s.strip() for s in watchlist_str.split(',') if s.strip()]
    high_conf_threshold = float(conditions.get('high_confidence', 95)) / 100

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'last_id': None, 'seen_today': set()}
        setattr(trigger, '_state', state)

    try:
        r = requests.get(f"http://{host}:{port}/api/v1/detections/recent?limit=5", timeout=5)
        detections = [d for d in r.json() if d.get('confidence', 0) >= min_conf]
        if not detections:
            return False
        latest = detections[0]
        det_id = latest.get('id') or latest.get('timestamp') or latest.get('time')
        if det_id == state['last_id']:
            return False  # nothing new
        state['last_id'] = det_id
        species = latest.get('species', '')
        confidence = latest.get('confidence', 0)

        if filt == 'any':
            return True
        if filt == 'specific':
            return bool(species_query) and species_query in species.lower()
        if filt == 'new_today':
            if species not in state['seen_today']:
                state['seen_today'].add(species)
                return True
        if filt == 'first_today':
            import time as _time
            today = int(_time.time() // 86400)
            if state.get('last_fired_day') != today:
                state['last_fired_day'] = today
                return True
        if filt == 'watchlist':
            return bool(watchlist) and any(w in species.lower() for w in watchlist)
        if filt == 'high_confidence':
            return confidence >= high_conf_threshold
        if filt == 'busy_feeder':
            count = int(conditions.get('busy_count', 5))
            window_mins = int(conditions.get('busy_window', 10))
            import time as _time
            now_ts = _time.time()
            # Store recent detection timestamps
            if 'recent_times' not in state:
                state['recent_times'] = []
            state['recent_times'].append(now_ts)
            # Prune to window
            cutoff = now_ts - (window_mins * 60)
            state['recent_times'] = [t for t in state['recent_times'] if t >= cutoff]
            return len(state['recent_times']) >= count
        return False
    except Exception:
        raise


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols):
    from collections import Counter

    host = settings.get('birdnet_host', '')
    port = settings.get('birdnet_port', '80')
    mode = settings.get('display_mode', 'latest')
    if not host:
        # There is no defensible default here — a BirdNET-Pi lives at whatever
        # address YOUR network gave it (shipping the developer's LAN IP was worse).
        return [format_lines('BirdNET', 'No host set', 'Configure')]
    min_conf = int(settings.get('min_confidence', '70')) / 100
    leaderboard_count = int(settings.get('leaderboard_count', '3'))

    # Cache invalidation: re-fetch when settings change
    state = getattr(fetch, '_state', None)
    if state is None:
        state = {'last_sig': None, 'last_pages': None}
        setattr(fetch, '_state', state)
    sig = (host, port, mode, min_conf, leaderboard_count)
    if sig != state['last_sig']:
        state['last_pages'] = None
        state['last_sig'] = sig

    ABBREV_WORDS = {
        'northern', 'southern', 'eastern', 'western', 'american', 'common',
        'carolina', 'great', 'lesser', 'greater', 'little', 'dark',
        'rufous', 'spotted', 'striped',
    }

    def shorten_name(species, max_len):
        # Spell the whole name out when the wall has room for it — a wide Matrix panel
        # shows "Northern Cardinal", not "N. Cardinal". Abbreviate only to make it fit.
        if len(species) <= max_len:
            return species
        words = species.split()
        parts = []
        for word in words:
            if '-' in word:
                parts.append(''.join(p[0].upper() for p in word.split('-') if p))
            elif word.lower() in ABBREV_WORDS:
                parts.append(word[0].upper() + '.')
            else:
                parts.append(word)
        name = ' '.join(parts)
        if len(name) <= max_len:
            return name
        core = [p for p in parts if not (len(p) == 2 and p.endswith('.'))]
        name = ' '.join(core)
        if len(name) <= max_len:
            return name
        return name[:max_len]

    def vcenter(text, rows):
        """One line, centered. format_lines does the centering now — building the full
        page here would only take that job away from it."""
        return format_lines(text)

    rows = get_rows()
    cols = get_cols()
    try:
        limit = 50 if mode == 'leaderboard' else 10
        detections = _recent_detections(settings, limit)

        if not detections:
            pages = [format_lines('BirdNET', 'No detections', 'Check settings')]
            state['last_pages'] = pages
            return pages

        if mode == 'latest':
            bird = detections[0]
            conf = f"{int(bird['confidence'] * 100)}%"
            short = shorten_name(bird['species'], cols - len(conf) - 1)
            pages = [vcenter(f"{short} {conf}", rows)]

        elif mode == 'last_3':
            pages = []
            seen = set()
            for bird in detections:
                if bird['species'] not in seen:
                    seen.add(bird['species'])
                    conf = f"{int(bird['confidence'] * 100)}%"
                    short = shorten_name(bird['species'], cols - len(conf) - 1)
                    pages.append(vcenter(f"{short} {conf}", rows))
                    if len(pages) == 3:
                        break

        elif mode == 'leaderboard':
            species_count = Counter(d['species'] for d in detections)
            top = species_count.most_common(min(leaderboard_count, rows))
            lines = []
            for species, count in top:
                count_str = str(count)
                short = shorten_name(species, cols - len(count_str) - 1)
                lines.append(f"{count_str} {short}")
            while len(lines) < rows:
                lines.append('')
            pages = [format_lines(*lines[:rows])]

        else:
            pages = [format_lines('BirdNET', 'Unknown mode', '')]

        state['last_pages'] = pages
        return pages

    except Exception as e:
        if state['last_pages']:
            return state['last_pages']
        return [format_lines('BirdNET', 'Error', str(e)[:cols] if cols > 10 else 'Err')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The same detections as a field-guide card: the species name large with a
# confidence bar under it and the detection time in the corner ("latest" holds
# on the newest bird; "last_3" rotates the three distinct species). The
# leaderboard mode becomes a real bar chart of today's counts. Green accent,
# solid black background.
# =============================================================================

_CV_ACCENT = (95, 210, 120)           # BirdNET's leafy green
_CV_NAME = (238, 240, 244)
_CV_DIM = (140, 146, 156)
_CV_TRACK = (40, 44, 52)              # the confidence bar's unlit track
_CV_AMBER = (255, 185, 60)


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


def _cv_text(draw, x, y, text, font, fill):
    """Baseline-corrected text draw (y is the ink top, whatever the glyph bbox says)."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill)


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (no host / offline / nothing heard yet)."""
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
    _cv_text(draw, (W - f1.getlength(line1)) / 2.0, y, line1, f1, _CV_NAME)
    if line2:
        _cv_text(draw, (W - f2.getlength(line2)) / 2.0, y + h1 + 3, line2, f2, _CV_DIM)
    return img


def _cv_det_time(d):
    """HH:MM out of whatever time field this BirdNET build sends, or ''. """
    for k in ('time', 'Time', 'timestamp', 'Timestamp', 'date'):
        v = str(d.get(k) or '')
        if ':' in v:
            tail = v.split('T')[-1].split(' ')[-1]
            return tail[:5]
    return ''


def _cv_detection_card(canvas, ImageDraw, bird):
    """One detection: BIRDNET header + time, the species large, a confidence bar below."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3
    conf = float(bird.get('confidence') or 0)
    pct = f'{int(conf * 100)}%'
    col = _CV_ACCENT if conf >= 0.85 else _CV_AMBER

    top = 1                            # the header's ink rides row 1
    if H >= 48:                        # header row only where it doesn't crowd the name
        hf = _cv_fit(canvas, 'BIRDNET', W - 2 * pad, max(7, int(H * 0.14)))
        _cv_text(draw, pad, 1, 'BIRDNET', hf, _CV_ACCENT)
        when = _cv_det_time(bird)
        if when:
            _cv_text(draw, W - pad - hf.getlength(when), 1, when, hf, _CV_DIM)
        top = 1 + (hf.getbbox('BIRDNET')[3] - hf.getbbox('BIRDNET')[1]) + 3

    bar_h = max(3, min(7, H // 9))
    pf = _cv_fit(canvas, pct, max(24, int(W * 0.22)), max(8, bar_h + 4))
    ph = pf.getbbox(pct)[3] - pf.getbbox(pct)[1]

    # Bottom row geometry first: bar and percentage share a lane whose ink ends
    # on the panel's last row, and the name gets everything in between.
    lane = max(bar_h, ph)
    lane_top = H - lane
    by = lane_top + (lane - bar_h) // 2

    name = str(bird.get('species') or '?')
    nf, lines, lh, gap = _cv_wrap_fit(canvas, name, W - 2 * pad, lane_top - 2 - top, 2)
    block = len(lines) * lh + (len(lines) - 1) * gap
    # Centered under the header; with no header the name itself IS the top row.
    ny = top + (max(0, (lane_top - 2 - top - block) // 2) if H >= 48 else 0)
    for ln in lines:
        _cv_text(draw, pad, ny, ln, nf, _CV_NAME)
        ny += lh + gap

    # Confidence bar: unlit track, lit fill, the percentage at its right end.
    bw = W - 2 * pad - int(pf.getlength(pct)) - 4
    draw.rectangle([pad, by, pad + bw, by + bar_h - 1], fill=_CV_TRACK)
    fill_w = max(1, int(bw * min(1.0, conf)))
    draw.rectangle([pad, by, pad + fill_w, by + bar_h - 1], fill=col)
    _cv_text(draw, pad + bw + 4, lane_top + (lane - ph) // 2, pct, pf, col)
    return img


def _cv_leaderboard(canvas, ImageDraw, counts):
    """Today's most-heard species as horizontal count bars."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    pad = 3
    n = len(counts)
    row_h = (H - 1) // n
    top_count = max(c for _s, c in counts) or 1
    f = _cv_fit(canvas, 'Ag', W, max(6, min(10, row_h - 4)))
    lh = f.getbbox('Ag')[3] - f.getbbox('Ag')[1]
    cw = max(f.getlength(str(c)) for _s, c in counts)
    bar_h = max(2, min(4, row_h - lh - 3))
    content_h = lh + 1 + bar_h
    # Full-height rows: the first name's ink on row 1, the last row's bar ending on
    # the panel's bottom edge, the slack spread between the rows.
    span = (H - 1 - content_h) - 1
    for i, (species, count) in enumerate(counts):
        y = 1 + (round(i * span / (n - 1)) if n > 1 else 0)
        if n == 1:                              # a lone row splits: name up top, bar on the edge
            by_solo = H - bar_h
        bw = W - 2 * pad - cw - 4
        name = species
        if f.getlength(name) > bw - 5:
            while name and f.getlength(name + '…') > bw - 5:
                name = name[:-1]
            name += '…'
        _cv_text(draw, pad, y, name, f, _CV_NAME)
        _cv_text(draw, W - pad - f.getlength(str(count)), y, str(count), f, _CV_ACCENT)
        by = by_solo if n == 1 else y + lh + 1
        draw.rectangle([pad, by, pad + bw, by + bar_h - 1], fill=_CV_TRACK)
        draw.rectangle([pad, by, pad + max(1, int(bw * count / top_count)), by + bar_h - 1],
                       fill=_CV_ACCENT)
    return img


def fetch_matrix(settings, canvas):
    """The configured display mode on the panel: latest = one card, last_3 = a rotating card,
    leaderboard = a count bar chart. Holds the last good data across a network hiccup."""
    from collections import Counter
    from PIL import ImageDraw

    host = settings.get('birdnet_host', '')
    mode = settings.get('display_mode', 'latest')
    if not host:
        canvas.frame(_cv_message(canvas, ImageDraw, 'BIRDNET', 'No host set'))
        return 60.0

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'i': 0, 'last': None}
        setattr(fetch_matrix, '_state', st)

    try:
        detections = _recent_detections(settings, 50 if mode == 'leaderboard' else 10)
        st['last'] = detections
    except Exception:
        detections = st['last']
    if detections is None:
        canvas.frame(_cv_message(canvas, ImageDraw, 'BIRDNET', 'Offline'))
        return 30.0
    if not detections:
        canvas.frame(_cv_message(canvas, ImageDraw, 'BIRDNET', 'No detections yet'))
        return 30.0

    if mode == 'leaderboard':
        rows_fit = max(1, (canvas.height - 6) // 12)
        want = int(settings.get('leaderboard_count', '3'))
        top = Counter(d['species'] for d in detections).most_common(max(1, min(want, rows_fit)))
        canvas.frame(_cv_leaderboard(canvas, ImageDraw, top))
        return 15.0

    if mode == 'last_3':
        birds, seen = [], set()
        for d in detections:
            if d.get('species') not in seen:
                seen.add(d.get('species'))
                birds.append(d)
            if len(birds) == 3:
                break
        bird = birds[st['i'] % len(birds)]
        st['i'] = (st['i'] + 1) % len(birds)
        canvas.frame(_cv_detection_card(canvas, ImageDraw, bird))
        return 6.0

    canvas.frame(_cv_detection_card(canvas, ImageDraw, detections[0]))
    return 10.0
