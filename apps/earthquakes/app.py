"""Recent significant earthquakes worldwide (USGS FDSN, keyless)."""


# =============================================================================
# SHARED — the quake DATA both surfaces read: one USGS FDSN call and the
# place-string split.
# =============================================================================


def _quakes(minmag, limit=5):
    """The latest significant quakes from USGS FDSN (keyless), newest first."""
    import requests
    data = requests.get('https://earthquake.usgs.gov/fdsnws/event/1/query',
                        params={'format': 'geojson', 'orderby': 'time', 'limit': limit,
                                'minmagnitude': minmag}, timeout=8).json()
    return data.get('features', []) or []


def _split_place(place):
    """USGS's '134 km E of Bitung, Indonesia' as ('134 km E', 'Bitung, Indonesia').
    Matched on the folded text and sliced from the original — USGS writes 'of' in
    lowercase and the place keeps its own casing. ('', place) when there is no
    distance prefix."""
    folded = place.upper()
    if ' OF ' in folded:
        cut = folded.index(' OF ')
        return place[:cut].strip(), place[cut + 4:]
    return '', place


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
    from datetime import datetime, timezone
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "quake") if i18n is not None else s

    try:
        minmag = str(settings.get('min_magnitude', '4.5') or '4.5')
        feats = _quakes(minmag)
        now = datetime.now(timezone.utc).timestamp()
        pages = []
        for ft in feats[:5]:
            p = ft.get('properties', {}) or {}
            mag = p.get('mag')
            place = str(p.get('place', '') or t('Unknown'))
            if isinstance(mag, (int, float)):
                # Severity at a glance: a color square renders everywhere —
                # colored pixels on a matrix wall, the color FLAP on a real one.
                tile = '🟥' if mag >= 7 else '🟧' if mag >= 6 else '🟨' if mag >= 5 else '🟩'
                mags = f'{tile} M{mag:.1f}'
            else:
                mags = 'M?'
            ago = ''
            ms = p.get('time')
            if isinstance(ms, (int, float)):
                mins = int((now - ms / 1000) / 60)
                ago = f'{mins}m {t("ago")}' if mins < 120 else f'{mins // 60}h {t("ago")}'
            # "134 km E of Bitung, Indonesia": the distance heads the line and the
            # location name gets the remaining rows, so it isn't cut off.
            dist, loc = _split_place(place)
            if dist:
                head = f'{mags} {dist}'
            else:
                head = f'{mags}  {ago}'.strip()
            if rows == 1:
                pages.append(f'{mags} {loc}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(head, *_wrap(loc, cols, 1)))
            else:
                pages.append(format_lines(head, *_wrap(loc, cols, rows - 1)))
        return pages or [format_lines('Earthquakes', t('None recent'), '')]
    except Exception:
        return [format_lines('Earthquakes', t('Offline'), '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# One quake per card: the magnitude big and color-coded by severity, the place
# beside it, distance/age dim below, and a 0-9 magnitude bar along the bottom
# filled to the quake. Cards advance through the same five quakes the flap
# pages show. Solid black background; adaptive down to 64x32.
# =============================================================================


_CV_TEXT = (238, 238, 244)                 # primary text
_CV_DIM = (150, 150, 158)                  # secondary text
_CV_TRACK = (44, 46, 52)                   # the magnitude bar's unfilled track
# Severity colors, same cut points as the flap tiles (M7+ red ... under M5 green).
_CV_SEVERITY = ((7.0, (242, 64, 50)), (6.0, (255, 142, 40)),
                (5.0, (250, 210, 60)), (0.0, (95, 212, 115)))


def _cv_mag_color(mag):
    if not isinstance(mag, (int, float)):
        return _CV_DIM
    for limit, color in _CV_SEVERITY:
        if mag >= limit:
            return color
    return _CV_SEVERITY[-1][1]


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


def _cv_ink(font, text):
    """Ink height of ``text`` in ``font``."""
    b = font.getbbox(text or '0')
    return b[3] - b[1]


def _cv_text(draw, x, y, text, font, fill):
    """Draw with the ink's TOP at ``y`` (bbox-corrected), left edge at ``x``."""
    draw.text((x, y - font.getbbox(text or '0')[1]), text, font=font, fill=fill, anchor='la')


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
    """The largest font at which ``text`` wraps into <= ``max_lines`` lines that fit
    ``max_w`` x ``max_h``. Returns (font, lines, line_height, gap)."""
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
    """A quiet two-line message on black (offline / nothing recent) — never a
    crash, never a blank panel."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    h1 = _cv_ink(f1, line1)
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = _cv_ink(f2, line2) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    _cv_text(draw, (W - f1.getlength(line1)) / 2.0, y, line1, f1, _CV_TEXT)
    if line2:
        _cv_text(draw, (W - f2.getlength(line2)) / 2.0, y + h1 + gap, line2, f2, _CV_DIM)
    return img


def _cv_quake_card(canvas, ImageDraw, mag, loc, dist, ago):
    """One quake: the magnitude big in its severity color, the place beside it
    (wrapped), the distance/age line dim below — and a magnitude bar (0-9 scale)
    along the bottom, filled to the quake in the same color. A small panel
    stacks instead: magnitude + age up top, ONE legible place line below."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    col = _cv_mag_color(mag)
    ms = f'M{mag:.1f}' if isinstance(mag, (int, float)) else 'M?'

    bar_h = max(3, H // 10)
    by1 = H - 1                            # the bar sits on the bottom row
    by0 = by1 - bar_h
    draw.rectangle([2, by0, W - 3, by1], fill=_CV_TRACK)
    if isinstance(mag, (int, float)):
        frac = min(1.0, max(0.0, float(mag) / 9.0))
        fill_w = round((W - 5) * frac)
        if fill_w > 0:
            draw.rectangle([2, by0, 2 + fill_w, by1], fill=col)
    # scale ticks at 3, 6 and 9 keep the bar honest
    for tick in (3, 6, 9):
        tx = 2 + round((W - 5) * tick / 9.0)
        draw.line([(tx, by0), (tx, by1)], fill=(0, 0, 0))

    area_h = by0 - 3                       # everything above the bar (one dark row)

    if W < 96:
        # Stacked: the place gets the full width for one line it can actually
        # hold — falling back to its last comma segment (the country/state)
        # rather than a smaller alphabet. The magnitude hangs from the top edge.
        mf = _cv_fit(canvas, ms, int(W * 0.60), int(area_h * 0.60))
        mh = _cv_ink(mf, ms)
        _cv_text(draw, 3, 1, ms, mf, col)
        if ago:
            aw = W - 6 - mf.getlength(ms) - 4
            af = _cv_fit(canvas, ago, aw, max(7, int(mh * 0.55)))
            if af.size < 7 and ' ' in ago:
                ago = ago.split()[0]       # '2H' still answers "when?"
                af = _cv_fit(canvas, ago, aw, max(7, int(mh * 0.55)))
            _cv_text(draw, W - 3 - af.getlength(ago), 1 + (mh - _cv_ink(af, ago)) / 2.0,
                     ago, af, _CV_DIM)
        line_h = max(7, area_h - 1 - mh - 2)
        lf = _cv_fit(canvas, loc, W - 6, line_h)
        if lf.size < 7 and ',' in loc:
            loc = loc.rsplit(',', 1)[-1].strip()
            lf = _cv_fit(canvas, loc, W - 6, line_h)
        _cv_text(draw, 3, 1 + mh + 2 + max(0, (line_h - _cv_ink(lf, loc)) / 2.0),
                 loc, lf, _CV_TEXT)
        return img

    sub = '  '.join(x for x in (dist, ago) if x)
    mf = _cv_fit(canvas, ms, int(W * 0.40), area_h)
    mw, mh = mf.getlength(ms), _cv_ink(mf, ms)
    _cv_text(draw, 3, max(1.0, (area_h - mh) / 2.0), ms, mf, col)

    rx = 3 + mw + 6
    rw = W - 3 - rx
    show_sub = H >= 44 and sub
    sub_f = _cv_fit(canvas, sub, rw, max(7, int(H * 0.15))) if show_sub else None
    sub_h = _cv_ink(sub_f, sub) if show_sub else 0
    loc_h = area_h - 1 - ((sub_h + 2) if show_sub else 0)
    lf, lines, lh, gap = _cv_wrap_fit(canvas, loc, rw, loc_h, 2)
    # The place hangs from the top edge; the dim distance/age line sits just
    # above the bar — the card spends its whole height, no centered slack.
    y = 1.0
    for ln in lines:
        _cv_text(draw, rx, y, ln, lf, _CV_TEXT)
        y += lh + gap
    if show_sub:
        _cv_text(draw, rx, by0 - 2 - sub_h, sub, sub_f, _CV_DIM)
    return img


def fetch_matrix(settings, canvas, i18n=None):
    """The same five USGS quakes as the flap pages, in the same order, one card at
    a time — advancing each redraw like the flap page turn. The feed is cached
    for five minutes; the last good list survives an outage."""
    import time
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "quake") if i18n is not None else s

    minmag = str(settings.get('min_magnitude', '4.5') or '4.5')
    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'feats': None, 'ts': 0.0, 'minmag': None, 'i': 0}
        setattr(fetch_matrix, '_state', st)
    now = time.time()
    if st['feats'] is None or st['minmag'] != minmag or (now - st['ts']) >= 300.0:
        try:
            st['feats'], st['minmag'] = _quakes(minmag), minmag
        except Exception:
            if st['minmag'] != minmag:
                st['feats'] = None         # another threshold's list is not this one
        st['ts'] = now                     # even after a failure: no hammering
    feats = (st['feats'] or [])[:5]
    if not feats:
        canvas.frame(_cv_message(canvas, ImageDraw, 'EARTHQUAKES',
                                 t('None recent').upper()))
        return 300.0

    idx = st['i'] % len(feats)
    st['i'] = (st['i'] + 1) % len(feats)
    p = feats[idx].get('properties', {}) or {}
    mag = p.get('mag')
    place = str(p.get('place', '') or t('Unknown'))
    dist, loc = _split_place(place)
    ago = ''
    ms = p.get('time')
    if isinstance(ms, (int, float)):
        mins = int((now - ms / 1000) / 60)
        ago = (f'{mins}M {t("ago")}' if mins < 120 else f'{mins // 60}H {t("ago")}').upper()
    canvas.frame(_cv_quake_card(canvas, ImageDraw, mag, loc.upper(), dist.upper(), ago))
    if len(feats) > 1:
        try:
            dwell = float(settings.get('loop_delay', 6) or 6)
        except (TypeError, ValueError):
            dwell = 6.0
        return max(3.0, min(30.0, dwell))
    return 60.0
