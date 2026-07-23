# =============================================================================
# SHARED — the moon itself: where we are in the synodic cycle, the phase name,
# the illuminated fraction and the countdowns to full/new. Both surfaces (and
# the trigger) read the same sky.
# =============================================================================

SYNODIC = 29.53058867
PHASES = [
    'New moon', 'Waxing crescent', 'First quarter', 'Waxing gibbous',
    'Full moon', 'Waning gibbous', 'Last quarter', 'Waning crescent'
]


def _cycle(now=None):
    """Days into the current synodic cycle. The moon's phase is a fact about the
    moon, not about your wall clock — UTC throughout."""
    from datetime import datetime, timezone
    if now is None:
        now = datetime.now(timezone.utc)
    # Known new moon: January 6, 2000 18:14 UTC
    ref = datetime(2000, 1, 6, 18, 14, 0, tzinfo=timezone.utc)
    return ((now - ref).total_seconds() / 86400) % SYNODIC


def _moon(now=None):
    """Everything a view needs: phase name, illuminated fraction, waxing flag,
    and days to the next full and new moon."""
    import math
    days_into_cycle = _cycle(now)
    phase_idx = int(days_into_cycle / (SYNODIC / 8)) % 8
    illumination = (1 - math.cos(2 * math.pi * days_into_cycle / SYNODIC)) / 2
    full_moon_day = SYNODIC / 2
    if days_into_cycle < full_moon_day:
        days_to_full = full_moon_day - days_into_cycle
    else:
        days_to_full = SYNODIC - days_into_cycle + full_moon_day
    return {
        'days': days_into_cycle,
        'phase_name': PHASES[phase_idx],
        'illumination': illumination,
        'waxing': days_into_cycle < full_moon_day,
        'days_to_full': days_to_full,
        'days_to_new': SYNODIC - days_into_cycle,
    }


def trigger(settings, conditions):
    """Fire on full moon or new moon."""
    phase_type = conditions.get('phase', 'full')
    days_into_cycle = _cycle()

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'fired_phase': None}
        setattr(trigger, '_state', state)

    # Full moon: days 13.5–15.5 into cycle; new moon: days 0–1 or 28.5–29.5
    if phase_type == 'full':
        in_phase = 13.5 <= days_into_cycle <= 15.5
    else:  # new
        in_phase = days_into_cycle <= 1.0 or days_into_cycle >= 28.5

    phase_key = f"{phase_type}:{int(days_into_cycle)}"
    if in_phase and state['fired_phase'] != phase_key:
        state['fired_phase'] = phase_key
        return True
    if not in_phase:
        state['fired_phase'] = None  # reset so next occurrence fires
    return False


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    def t(s):
        return i18n.t(s, "moon") if i18n is not None else s

    def u(k):                       # localized D/H/M/S suffix (French J for jour, etc.)
        return i18n.unit(k) if i18n is not None else k

    moon = _moon()
    phase_name = moon['phase_name']
    illum_pct = int(moon['illumination'] * 100)
    days_to_full, days_to_new = moon['days_to_full'], moon['days_to_new']

    cols = get_cols()

    # "Full in 5 Days" where there's room, "Full in 5D" only where there isn't — a wide
    # Matrix wall has no reason to abbreviate. Both the full word and the compact suffix
    # are localized (the time domain: Days->Jours/Tage, D->J/T).
    def days_line(label, n):
        n = int(n)
        full = f'{t(label)} {n} {u("Days")}'
        return full if len(full) <= cols else f'{t(label)} {n}{u("D")}'

    # Visual bar: color tiles render everywhere — yellow pixels on a matrix wall,
    # the yellow color FLAP on a physical one, where a literal 'w' was just the
    # letter W repeated across the row.
    filled = int(moon['illumination'] * cols)
    bar = '🟨' * filled + '⬛' * (cols - filled)

    name = t(phase_name)
    if get_rows() >= 4:
        # Everything is already computed; a 3-row wall just couldn't show it at once.
        return [
            format_lines(name, bar, f'{illum_pct}% {t("Lit")}',
                         days_line("Full in", days_to_full),
                         days_line("New in", days_to_new)),
        ]
    pages = [
        format_lines(name, f'{illum_pct}% {t("Lit")}', days_line("Full in", days_to_full)),
        format_lines(name, bar, days_line("New in", days_to_new)),
    ]
    return pages


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A drawn moon: the disc with its true illuminated fraction (terminator curve
# and all, waxing lit from the right like the northern-hemisphere sky), phase
# name and % lit beside it, next full/new countdown where there is room.
# Black background — it is the night sky, after all.
# =============================================================================

_LIT = (233, 231, 219)          # moonlight
_DARKSIDE = (25, 27, 36)        # the shadowed disc, just visible
_CRATER = (196, 193, 178)       # shading on the lit side
_NAME_COL = (245, 245, 248)
_PCT_COL = (255, 208, 74)       # a moonlit amber for the % lit
_SUB_COL = (132, 136, 148)


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


def _cv_wrap_fit(canvas, text, max_w, max_h, max_lines):
    """Largest font at which ``text`` word-wraps into <= ``max_lines`` lines fitting the box.
    Returns (font, lines, line_height, gap)."""
    def wrap(font):
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

    size = max(5, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = wrap(font)
        b = font.getbbox('Ag')
        lh = b[3] - b[1]
        gap = max(1, lh // 6)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((font.getlength(ln) for ln in lines), default=0)
        if size <= 5 or (total <= max_h and widest <= max_w):
            return font, lines, lh, gap
        size -= 1
    font = canvas.font(5)
    lines = wrap(font)
    b = font.getbbox('Ag')
    return font, lines, b[3] - b[1], 1


def _draw_moon(draw, cx, cy, r, illumination, waxing):
    """The disc row by row: dark chord + lit chord split at the terminator — an
    ellipse whose half-width runs cos through the cycle, so the lit edge is a
    crescent's curve, not a straight line. Waxing lights the right limb."""
    import math
    illumination = min(1.0, max(0.0, illumination))
    k = 1.0 - 2.0 * illumination                            # terminator half-width, +1..-1
    for dy in range(-r, r + 1):
        half = math.sqrt(max(0.0, r * r - dy * dy))
        x0, x1 = cx - half, cx + half
        xt = k * half
        if waxing:
            lit0, lit1 = cx + xt, x1
        else:
            lit0, lit1 = x0, cx - xt
        y = cy + dy
        draw.line([(int(round(x0)), y), (int(round(x1)), y)], fill=_DARKSIDE)
        if lit1 - lit0 >= 1.0:
            draw.line([(int(round(lit0)), y), (int(round(lit1)), y)], fill=_LIT)
    # A little relief on the lit side — craters at fixed fractions of the radius,
    # drawn only where their whole circle falls in moonlight.
    if r >= 9:
        for fx, fy, fr in ((-0.30, -0.35, 0.16), (0.25, 0.05, 0.22), (-0.15, 0.45, 0.13)):
            cxx, cyy, cr = cx + fx * r, cy + fy * r, max(1, int(fr * r * 0.9))
            half = math.sqrt(max(0.0, r * r - (cyy - cy) ** 2))
            lit_edge = cx + k * half if waxing else cx - k * half
            inside = (cxx - cr >= lit_edge) if waxing else (cxx + cr <= lit_edge)
            if inside:
                draw.ellipse([cxx - cr, cyy - cr, cxx + cr, cyy + cr], fill=_CRATER)


def fetch_matrix(settings, canvas, i18n=None):
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "moon") if i18n is not None else s

    def u(k):
        return i18n.unit(k) if i18n is not None else k

    moon = _moon()
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    # A smaller disc on a narrow panel buys the name the width it needs.
    r = min(H // 2 - 3, W // 5) if W >= 96 else min(H // 2 - 4, W // 6)
    cx, cy = 3 + r, H // 2
    _draw_moon(draw, cx, cy, r, moon['illumination'], moon['waxing'])

    # Beside the disc: the phase name, % lit, and (with room) the next event.
    lx = cx + r + 5
    lw = W - lx - 3
    name = t(moon['phase_name']).upper()
    pct = f'{int(moon["illumination"] * 100)}% {t("Lit")}'.upper()
    nxt = ''
    if H >= 48:
        # The flap pages show both countdowns; the panel keeps whichever is nearer.
        if moon['days_to_full'] <= moon['days_to_new']:
            nxt = f'{t("Full in")} {int(moon["days_to_full"])}{u("D")}'.upper()
        else:
            nxt = f'{t("New in")} {int(moon["days_to_new"])}{u("D")}'.upper()

    nf, lines, lh, gap = _cv_wrap_fit(canvas, name, lw, int(H * (0.44 if nxt else 0.52)), 2)
    pf = _cv_fit(canvas, pct, lw, max(7, int(H * 0.18)))
    pb = pf.getbbox(pct)
    ph = pb[3] - pb[1]
    xf = _cv_fit(canvas, nxt, lw, max(7, int(H * 0.15))) if nxt else None
    xh = (xf.getbbox(nxt)[3] - xf.getbbox(nxt)[1]) if nxt else 0

    block = len(lines) * lh + (len(lines) - 1) * gap
    vgap = max(2, H // 14)
    total = block + vgap + ph + ((vgap + xh) if nxt else 0)
    y = (H - total) / 2.0
    for ln in lines:
        draw.text((lx, y - nf.getbbox(ln)[1]), ln, font=nf, fill=_NAME_COL)
        y += lh + gap
    y += vgap - gap
    draw.text((lx, y - pb[1]), pct, font=pf, fill=_PCT_COL)
    if nxt:
        y += ph + vgap
        xb = xf.getbbox(nxt)
        draw.text((lx, y - xb[1]), nxt, font=xf, fill=_SUB_COL)

    canvas.frame(img)
    # The terminator creeps a few pixels a day — five minutes between redraws
    # is generous.
    return 300.0
