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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
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


def _cv_wrap(font, text, max_w, max_lines):
    """Greedy word-wrap of ``text`` to pixel width ``max_w``, at most ``max_lines`` lines.
    Stays local: canvas.wrap hyphen-splits overlong words, which lets canvas.wrap_fit
    settle on a larger font and cut the tail (place names would lose words)."""
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
    size = max(8, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = _cv_wrap(font, text, max_w, max_lines)
        b = font.getbbox('Ag')
        lh = b[3] - b[1]
        gap = max(1, lh // 6)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((font.getlength(ln) for ln in lines), default=0)
        if size <= 8 or (total <= max_h and widest <= max_w):
            return font, lines, lh, gap
        size -= 1
    font = canvas.font(8)
    lines = _cv_wrap(font, text, max_w, max_lines)
    b = font.getbbox('Ag')
    return font, lines, b[3] - b[1], 1


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

    if H >= 96:
        # ---- tall panel (e.g. LCD 256x160): the place becomes a proper header,
        # the magnitude the hero on the left, the distance/age a sized-up right
        # column — the magnitude bar keeps the full bottom edge.
        hf, hlines, hlh, hgap = _cv_wrap_fit(canvas, loc, W - 6, int(H * 0.15), 2)
        y = 1.0
        for ln in hlines:
            canvas.text_top(draw, 3, y, ln, hf, _CV_TEXT)
            y += hlh + hgap
        dy = int(y - hgap + 3)
        draw.line([(3, dy), (W - 4, dy)], fill=_CV_TRACK)
        top = dy + 4
        mid_h = area_h - top
        mf = canvas.fit_font(ms, int(W * 0.55), int(mid_h * 0.86))
        mw, mh = mf.getlength(ms), canvas.ink(mf, ms)
        canvas.text_top(draw, 3, top + (mid_h - mh) / 2.0, ms, mf, col)
        rows = [x for x in (dist, ago) if x]
        rx = 3 + mw + 12
        rw = W - 5 - rx
        if rows and rw >= 40:
            fs = min(canvas.fit_font(s, rw, max(8, int(H * 0.14))).size for s in rows)
            rf = canvas.font(fs)
            hs = [canvas.ink(rf, s) for s in rows]
            rgap = max(4, int(H * 0.06))
            ry = top + (mid_h - sum(hs) - rgap * (len(rows) - 1)) / 2.0
            for s, sh in zip(rows, hs):
                canvas.text_top(draw, rx, ry, s, rf, _CV_DIM)
                ry += sh + rgap
        return img

    if W < 96:
        # Stacked: the place gets the full width for one line it can actually
        # hold — falling back to its last comma segment (the country/state)
        # rather than a smaller alphabet. The magnitude hangs from the top edge.
        mf = canvas.fit_font(ms, int(W * 0.60), int(area_h * 0.60))
        mh = canvas.ink(mf, ms)
        canvas.text_top(draw, 3, 1, ms, mf, col)
        if ago:
            aw = W - 6 - mf.getlength(ms) - 4
            af = canvas.fit_font(ago, aw, max(7, int(mh * 0.55)))
            if af.getlength(ago) > aw and ' ' in ago:
                ago = ago.split()[0]       # '2H' still answers "when?"
                af = canvas.fit_font(ago, aw, max(7, int(mh * 0.55)))
            if af.getlength(ago) <= aw:    # can't fit even at the 8px floor: drop it
                canvas.text_top(draw, W - 3 - af.getlength(ago), 1 + (mh - canvas.ink(af, ago)) / 2.0,
                                ago, af, _CV_DIM)
        line_h = max(7, area_h - 1 - mh - 2)
        lf = canvas.fit_font(loc, W - 6, line_h)
        if lf.getlength(loc) > W - 6 and ',' in loc:
            # The full place can't fit at the 8px floor — fall back to the last
            # comma segment (the country/state), then the town, rather than a
            # smaller alphabet or a clipped line.
            for cand in (loc.rsplit(',', 1)[-1].strip(), loc.split(',', 1)[0].strip()):
                cand_f = canvas.fit_font(cand, W - 6, line_h)
                if cand and cand_f.getlength(cand) <= W - 6:
                    loc, lf = cand, cand_f
                    break
        canvas.text_top(draw, 3, 1 + mh + 2 + max(0, (line_h - canvas.ink(lf, loc)) / 2.0),
                        loc, lf, _CV_TEXT)
        return img

    sub = '  '.join(x for x in (dist, ago) if x)
    mf = canvas.fit_font(ms, int(W * 0.40), area_h)
    mw, mh = mf.getlength(ms), canvas.ink(mf, ms)
    canvas.text_top(draw, 3, max(1.0, (area_h - mh) / 2.0), ms, mf, col)

    rx = 3 + mw + 6
    rw = W - 3 - rx
    show_sub = H >= 44 and sub
    sub_f = canvas.fit_font(sub, rw, max(7, int(H * 0.15))) if show_sub else None
    if sub_f is not None and sub_f.getlength(sub) > rw:
        # The full meta line can't fit at the 8px floor — the age alone still
        # answers "when?", the distance alone "where?"; failing both, drop it.
        for cand in (ago, dist):
            if cand and sub_f.getlength(cand) <= rw:
                sub = cand
                break
        else:
            show_sub, sub_f = False, None
    sub_h = canvas.ink(sub_f, sub) if show_sub else 0
    loc_h = area_h - 1 - ((sub_h + 2) if show_sub else 0)
    lf, lines, lh, gap = _cv_wrap_fit(canvas, loc, rw, loc_h, 2)
    if ' '.join(lines) != ' '.join(str(loc or '').split()) \
            or max((lf.getlength(ln) for ln in lines), default=0) > rw:
        # Even the 8px floor truncates or clips the full place — fall back to
        # its comma segments (country/state, then the town) shown WHOLE.
        for cand in (loc.rsplit(',', 1)[-1].strip(), loc.split(',', 1)[0].strip()):
            cf, cl, clh, cg = _cv_wrap_fit(canvas, cand, rw, loc_h, 2)
            if cand and ' '.join(cl) == ' '.join(cand.split()) \
                    and max(cf.getlength(ln) for ln in cl) <= rw:
                lf, lines, lh, gap = cf, cl, clh, cg
                break
    # The place hangs from the top edge; the dim distance/age line sits just
    # above the bar — the card spends its whole height, no centered slack.
    y = 1.0
    for ln in lines:
        canvas.text_top(draw, rx, y, ln, lf, _CV_TEXT)
        y += lh + gap
    if show_sub:
        canvas.text_top(draw, rx, by0 - 2 - sub_h, sub, sub_f, _CV_DIM)
    return img


def _cv_quake_ops(canvas, mag, loc, dist, ago, W, H):
    """The tall-panel card as on-device DRAW OPS — the gtext-era twin of the H>=96
    branch in _cv_quake_card, for the LCD (manifest ``lcd_ops``): scalable type and
    native-resolution rects drawn by the wall itself instead of a 256x160 pixel
    frame upscaled x5. Same card: the place as a header over a rule, the magnitude
    the hero in its severity color, distance/age dim beside it, the 0-9 magnitude
    bar along the bottom edge."""
    canvas.clear((0, 0, 0))
    col = _cv_mag_color(mag)
    ms = f'M{mag:.1f}' if isinstance(mag, (int, float)) else 'M?'
    marg = max(3, int(W * 0.012))

    bar_h = max(3, H // 10)
    by1 = H - 1                            # the bar sits on the bottom row
    by0 = by1 - bar_h
    span = W - 2 * marg
    canvas.rect(marg, by0, span, bar_h + 1, _CV_TRACK, fill=True)
    if isinstance(mag, (int, float)):
        frac = min(1.0, max(0.0, float(mag) / 9.0))
        fill_w = round(span * frac)
        if fill_w > 0:
            canvas.rect(marg, by0, fill_w, bar_h + 1, col, fill=True)
    tw = max(1, int(W * 0.004))            # scale ticks at 3, 6 and 9 keep the bar honest
    for tick in (3, 6, 9):
        tx = min(marg + round(span * tick / 9.0), W - marg - tw)
        canvas.rect(tx, by0, tw, bar_h + 1, (0, 0, 0), fill=True)

    # The place as a proper header, wrapped to at most two lines, its ink riding
    # the top row (gtext ink starts ~0.18*size below the given y).
    hsz, hlines = canvas.fit_wrap_gtext(loc, W - 2 * marg, int(H * 0.17), max_lines=2)
    y = 1 - int(hsz * 0.18)
    for ln in hlines:
        canvas.gtext(marg, y, ln, color=_CV_TEXT, size=hsz)
        y += int(hsz * 1.18)
    ink_bot = y - int(hsz * 1.18) + int(hsz * 0.94)
    dy = ink_bot + max(3, int(H * 0.02))
    canvas.line(marg, dy, W - 1 - marg, dy, color=_CV_TRACK, t=max(1, int(round(H / 160))))

    # The magnitude hero on the left, the distance/age column dim beside it —
    # both centered in the band between the rule and the bar.
    top = dy + max(4, int(H * 0.03))
    mid_bot = by0 - max(3, int(H * 0.02))
    mid_h = mid_bot - top
    msz = canvas.fit_gtext(ms, int(W * 0.60), int(mid_h * 0.92))
    mc = top + mid_h / 2.0                 # the band's visual center row
    canvas.gtext(marg, int(mc - msz * 0.56), ms, color=col, size=msz)
    rows = [x for x in (dist, ago) if x]
    rx = marg + int(canvas.text_width(ms, msz)) + max(8, int(W * 0.03))
    rw = W - marg - rx
    if rows and rw >= int(W * 0.16):
        fs = min(canvas.fit_gtext(s, rw, max(8, int(H * 0.14))) for s in rows)
        rgap = max(4, int(H * 0.06))
        block = len(rows) * int(fs * 0.72) + (len(rows) - 1) * rgap
        ry = mc - block / 2.0              # ink-top of the dim column
        for s in rows:
            canvas.gtext(rx, int(ry - fs * 0.18), s, color=_CV_DIM, size=fs)
            ry += int(fs * 0.72) + rgap
    canvas.show()


def fetch_canvas(settings, canvas, i18n=None):
    """The same five USGS quakes as the flap pages, in the same order, one card at
    a time — advancing each redraw like the flap page turn. The feed is cached
    for five minutes; the last good list survives an outage."""
    import time
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "quake") if i18n is not None else s

    minmag = str(settings.get('min_magnitude', '4.5') or '4.5')
    st = getattr(fetch_canvas, '_state', None)
    if st is None:
        st = {'feats': None, 'ts': 0.0, 'minmag': None, 'i': 0}
        setattr(fetch_canvas, '_state', st)
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
        canvas.frame(canvas.message('EARTHQUAKES', t('None recent').upper()))
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
    W, H = canvas.width, canvas.height
    if getattr(canvas, 'can_gtext', False) and H >= 96:
        # The big-panel path: the same card as live ops at native resolution
        # (crisp TTF type + a native-width bar), same page-turn cadence.
        _cv_quake_ops(canvas, mag, loc.upper(), dist.upper(), ago, W, H)
        if len(feats) > 1:
            try:
                dwell = float(settings.get('loop_delay', 6) or 6)
            except (TypeError, ValueError):
                dwell = 6.0
            return max(3.0, min(30.0, dwell))
        return 60.0
    canvas.frame(_cv_quake_card(canvas, ImageDraw, mag, loc.upper(), dist.upper(), ago))
    if len(feats) > 1:
        try:
            dwell = float(settings.get('loop_delay', 6) or 6)
        except (TypeError, ValueError):
            dwell = 6.0
        return max(3.0, min(30.0, dwell))
    return 60.0
