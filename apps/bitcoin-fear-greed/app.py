"""Bitcoin Fear & Greed Index plugin for Split-Flap Display."""


# =============================================================================
# SHARED — the index itself (_index), read by every surface — plus the trigger
# (surface-independent by contract).
# =============================================================================


def _index():
    """The current index from alternative.me (keyless) — (value 0-100, the API's
    English classification). Raises on outage; each surface has its own quiet
    fallback."""
    import json
    import urllib.request
    url = "https://api.alternative.me/fng/?limit=1"
    req = urllib.request.Request(url, headers={"User-Agent": "SplitFlap/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode())
    entry = data["data"][0]
    return int(entry["value"]), str(entry["value_classification"])


def trigger(settings, conditions):
    """Fire when the Fear & Greed index crosses into extreme territory."""

    zone = conditions.get('zone', 'extreme_fear')
    threshold = int(conditions.get('threshold', 20))

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'last_zone': None}
        setattr(trigger, '_state', state)

    try:
        value = _index()[0]

        if zone == 'extreme_fear':
            in_zone = value <= threshold
        elif zone == 'extreme_greed':
            in_zone = value >= (100 - threshold)
        else:  # either
            in_zone = value <= threshold or value >= (100 - threshold)

        current_zone = zone if in_zone else None
        if in_zone and state['last_zone'] != current_zone:
            state['last_zone'] = current_zone
            return True
        if not in_zone:
            state['last_zone'] = None
    except Exception:
        raise
    return False


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):

    def t(s):
        return i18n.t(s, "sentiment") if i18n is not None else s

    try:
        n, classification = _index()
        value = str(n)
        # "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed" -> localized.
        # The API already writes the classification as a person would, and the catalog
        # folds its keys, so it needs no uppercasing to be found — shouting it here
        # would only take the case away from the wall before the wall could decide.
        label = t(classification)
        # A color square renders everywhere: a colored pixel block on a matrix
        # wall, the matching color FLAP on a physical one (every reel carries 7).
        tile = "🟥" if n <= 24 else "🟧" if n <= 44 else "🟨" if n <= 55 else "🟩"
        rows, cols = get_rows(), get_cols()
        if rows == 1:
            # The index value is the payload — it must never be the line that drops.
            return [format_lines(f"{tile} F&G {value} {label}"[:cols])]
        if rows == 2:
            return [format_lines("BTC Fear&Greed", f"{tile} {value}/100 {label}"[:cols])]
        # A wide wall gets a full-width gauge: the bar fills to the index (0-100) across
        # the whole wall, in the zone's color — a red sliver at Extreme Fear, a long
        # green bar at Greed — so the mood reads at a glance from across the room. Color
        # tiles render everywhere (matrix pixels / the matching color FLAP on a reel),
        # like moon-phase. A narrow wall keeps the concise three-line text.
        if cols >= 24:
            filled = max(0, min(cols, round(n / 100 * cols)))
            bar = tile * filled + '⬛' * (cols - filled)
            return [format_lines("BTC Fear & Greed", bar, f"{value}/100  {label}")]
        return [format_lines("BTC Fear&Greed", f"Index: {value}/100", f"{tile} {label}")]
    except Exception:
        return [format_lines("BTC Fear&Greed", t("Offline"), "")]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The index as a color gauge: a red->green zone scale lit up to today's value,
# a white marker on the spot, the number in its zone's color with the
# classification beside it. Solid black background; adaptive down to 64x32.
# =============================================================================


_CV_TEXT = (238, 238, 244)                 # primary text
_CV_DIM = (150, 150, 158)                  # secondary text
# The index's own zones, red fear -> green greed (same cut points as the flap tiles).
_CV_ZONES = ((24, (236, 62, 48)), (44, (255, 142, 40)),
             (55, (250, 210, 60)), (100, (76, 212, 112)))


def _cv_zone_color(n):
    for limit, color in _CV_ZONES:
        if n <= limit:
            return color
    return _CV_ZONES[-1][1]


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


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message on black (offline) — never a crash, never blank."""
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


def _cv_gauge(canvas, ImageDraw, value, label):
    """The index as a red->green gauge: the zone scale runs dim across the width,
    lit bright up to the value, a white marker on the spot; the number sits above
    it in the zone's color with the classification beside (or under, on a
    narrow panel). Title strip only where the height affords it."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    col = _cv_zone_color(value)

    top = 1
    if H >= 48:
        title = 'BTC FEAR & GREED'
        tf = _cv_fit(canvas, title, W - 6, 8)
        _cv_text(draw, (W - tf.getlength(title)) / 2.0, 2, title, tf, _CV_DIM)
        top = 2 + _cv_ink(tf, title) + 2

    bar_h = max(4, H // 8)
    by1 = H - max(2, H // 16)
    by0 = by1 - bar_h
    x0, x1 = 3, W - 4
    for x in range(x0, x1 + 1):
        v = (x - x0) / max(1, x1 - x0) * 100.0
        c = _cv_zone_color(v)
        f = 1.0 if v <= value else 0.25            # lit to the value, dim beyond it
        draw.line([(x, by0), (x, by1)], fill=tuple(int(ch * f) for ch in c))
    mx = x0 + round(value / 100.0 * (x1 - x0))
    draw.rectangle([mx - 1, by0 - 2, mx + 1, by1 + 2], fill=(255, 255, 255))

    mid_h = by0 - 3 - top
    vs = str(value)
    lab = label.upper()
    lab_lines = lab.split(None, 1) if (W < 110 and ' ' in lab) else [lab]
    vf = _cv_fit(canvas, vs, int(W * 0.34), mid_h)
    vw, vh = vf.getlength(vs), _cv_ink(vf, vs)
    gap = 5
    lw_max = W - 8 - vw - gap
    lf = min((_cv_fit(canvas, ln, lw_max, max(7, int(mid_h * (0.42 if len(lab_lines) > 1 else 0.55))))
              for ln in lab_lines), key=lambda f: f.size)
    if lf.size < 7 and len(lab.split()) > 1:
        # Too tight even wrapped: keep the classification's noun ("FEAR") legible —
        # the number and the color already carry the "extreme".
        lab_lines = [lab.split()[-1]]
        lf = _cv_fit(canvas, lab_lines[0], lw_max, max(7, int(mid_h * 0.55)))
    lh = _cv_ink(lf, 'AG')
    lgap = max(1, lh // 5)
    lblock = len(lab_lines) * lh + (len(lab_lines) - 1) * lgap
    lw = max(lf.getlength(ln) for ln in lab_lines)
    x = (W - (vw + gap + lw)) / 2.0
    _cv_text(draw, x, top + (mid_h - vh) / 2.0, vs, vf, col)
    ly = top + (mid_h - lblock) / 2.0
    for ln in lab_lines:
        _cv_text(draw, x + vw + gap, ly, ln, lf, _CV_TEXT)
        ly += lh + lgap
    return img


def fetch_matrix(settings, canvas, i18n=None):
    """Draw the index as a color gauge; the last good reading survives an outage.
    The index updates daily — five minutes between redraws is already generous."""
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "sentiment") if i18n is not None else s

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'last': None}
        setattr(fetch_matrix, '_state', st)
    try:
        st['last'] = _index()
    except Exception:
        pass
    if st['last'] is None:
        canvas.frame(_cv_message(canvas, ImageDraw, 'FEAR & GREED', t('Offline').upper()))
        return 120.0
    value, classification = st['last']
    canvas.frame(_cv_gauge(canvas, ImageDraw, value, t(classification)))
    return 300.0
