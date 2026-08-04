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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
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

    if H >= 96:
        # Tall LCD panel: a balanced top-to-bottom composition instead of a hero
        # up top and a thin gauge stranded at the floor with dead air between.
        # Title, a big number + classification hero filling the middle, and a
        # thick red->green gauge (lit to the value, a scaled needle on the spot,
        # 0/100 end labels) anchored along the bottom.
        pad = 4
        title = 'BTC FEAR & GREED'
        tf = canvas.fit_font(title, W - 2 * pad, max(11, int(H * 0.12)))
        canvas.text_top(draw, (W - tf.getlength(title)) / 2.0, 3, title, tf, _CV_DIM)
        ttop = 3 + canvas.ink(tf, title)

        bar_h = max(16, int(H * 0.17))
        lab_h = max(9, int(H * 0.09))
        by1 = H - 1 - lab_h - 2
        by0 = by1 - bar_h
        x0, x1 = pad, W - 1 - pad
        for x in range(x0, x1 + 1):
            v = (x - x0) / max(1, x1 - x0) * 100.0
            c = _cv_zone_color(v)
            f = 1.0 if v <= value else 0.22            # lit to the value, dim beyond it
            draw.line([(x, by0), (x, by1)], fill=tuple(int(ch * f) for ch in c))
        mx = x0 + round(value / 100.0 * (x1 - x0))
        draw.rectangle([mx - 2, by0 - 1, mx + 2, by1], fill=(255, 255, 255))
        draw.polygon([(mx - 5, by0 - 9), (mx + 5, by0 - 9), (mx, by0 - 1)],
                     fill=(255, 255, 255))         # needle head
        ef = canvas.font(lab_h)
        canvas.text_top(draw, x0, by1 + 3, '0', ef, _CV_DIM)
        canvas.text_top(draw, x1 - ef.getlength('100'), by1 + 3, '100', ef, _CV_DIM)

        vs = str(value)
        lab_lines = label.upper().split() or [label.upper()]
        hero_top = ttop + max(4, int(H * 0.03))
        hero_h = by0 - 9 - hero_top
        vf = canvas.fit_font(vs, int(W * 0.44), hero_h)
        vw, vh = vf.getlength(vs), canvas.ink(vf, vs)
        gap = max(6, int(W * 0.03))
        lw_max = W - 2 * pad - vw - gap
        line_h = max(9, int(hero_h * (0.58 if len(lab_lines) == 1 else 0.40)))
        lf = min((canvas.fit_font(ln, lw_max, line_h) for ln in lab_lines), key=lambda f: f.size)
        lh = canvas.ink(lf, 'AG')
        lgap = max(1, lh // 5)
        lblock = len(lab_lines) * lh + (len(lab_lines) - 1) * lgap
        lw = max(lf.getlength(ln) for ln in lab_lines)
        gx = (W - (vw + gap + lw)) / 2.0
        vy = hero_top + (hero_h - vh) / 2.0
        canvas.text_top(draw, gx, vy, vs, vf, col)
        ly = vy + (vh - lblock) / 2.0
        for ln in lab_lines:
            canvas.text_top(draw, gx + vw + gap, ly, ln, lf, _CV_TEXT)
            ly += lh + lgap
        return img

    top = 1
    if H >= 48:
        title = 'BTC FEAR & GREED'
        tf = canvas.fit_font(title, W - 6, 8)
        canvas.text_top(draw, (W - tf.getlength(title)) / 2.0, 1, title, tf, _CV_DIM)
        top = 1 + canvas.ink(tf, title) + 2

    bar_h = max(4, H // 8)
    by1 = H - 1                                    # the gauge sits on the bottom row
    by0 = by1 - bar_h
    x0, x1 = 3, W - 4
    for x in range(x0, x1 + 1):
        v = (x - x0) / max(1, x1 - x0) * 100.0
        c = _cv_zone_color(v)
        f = 1.0 if v <= value else 0.25            # lit to the value, dim beyond it
        draw.line([(x, by0), (x, by1)], fill=tuple(int(ch * f) for ch in c))
    mx = x0 + round(value / 100.0 * (x1 - x0))
    draw.rectangle([mx - 1, by0 - 2, mx + 1, by1], fill=(255, 255, 255))

    mid_h = by0 - 3 - top
    vs = str(value)
    lab = label.upper()
    lab_lines = lab.split(None, 1) if (W < 110 and ' ' in lab) else [lab]
    vf = canvas.fit_font(vs, int(W * 0.40), mid_h)
    vw, vh = vf.getlength(vs), canvas.ink(vf, vs)
    gap = 5
    lw_max = W - 8 - vw - gap
    lf = min((canvas.fit_font(ln, lw_max, max(7, int(mid_h * (0.42 if len(lab_lines) > 1 else 0.55))))
              for ln in lab_lines), key=lambda f: f.size)
    if lf.size < 7 and len(lab.split()) > 1:
        # Too tight even wrapped: keep the classification's noun ("FEAR") legible —
        # the number and the color already carry the "extreme".
        lab_lines = [lab.split()[-1]]
        lf = canvas.fit_font(lab_lines[0], lw_max, max(7, int(mid_h * 0.55)))
    lh = canvas.ink(lf, 'AG')
    lgap = max(1, lh // 5)
    lblock = len(lab_lines) * lh + (len(lab_lines) - 1) * lgap
    lw = max(lf.getlength(ln) for ln in lab_lines)
    x = (W - (vw + gap + lw)) / 2.0
    # With a title strip the block centers between it and the gauge; without one
    # (short panels) the number hangs from the top edge so no rows go dark.
    vy0 = top + (mid_h - vh) / 2.0 if top > 1 else float(top)
    canvas.text_top(draw, x, vy0, vs, vf, col)
    ly = vy0 + (vh - lblock) / 2.0
    for ln in lab_lines:
        canvas.text_top(draw, x + vw + gap, ly, ln, lf, _CV_TEXT)
        ly += lh + lgap
    return img


def fetch_canvas(settings, canvas, i18n=None):
    """Draw the index as a color gauge; the last good reading survives an outage.
    The index updates daily — five minutes between redraws is already generous."""
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "sentiment") if i18n is not None else s

    st = getattr(fetch_canvas, '_state', None)
    if st is None:
        st = {'last': None}
        setattr(fetch_canvas, '_state', st)
    try:
        st['last'] = _index()
    except Exception:
        pass
    if st['last'] is None:
        canvas.frame(canvas.message('FEAR & GREED', t('Offline').upper()))
        return 120.0
    value, classification = st['last']
    canvas.frame(_cv_gauge(canvas, ImageDraw, value, t(classification)))
    return 300.0
