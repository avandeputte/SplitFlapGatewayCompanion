"""Countdown Bars — the remaining time as full-width draining bars.

A canvas app (surface: canvas), and the pixel-native cousin of the flap
Countdown. Where the flap grid spells the countdown with colour-flap bars that
leave gutters between the value and the wall's edge, this draws each time unit
as a bar that spans the WHOLE panel width and stacks the units with no gaps —
the panel becomes one instrument, bottom-lit red at the seconds, cooling to
blue (or purple) at the top.

Each unit — optional Years, then Days, Hours, Minutes and optional Seconds —
gets one full-width bar:
  * a dark track (a dim tint of the unit's colour) across the full width,
  * a fill from the LEFT sized to that unit's own cycle fraction (years of a
    decade, days of the year, hours of the day, minutes of the hour, seconds
    of the minute), so every bar drains as its unit ticks down,
  * the value and label drawn INSIDE the bar, left-aligned in white with a 1px
    dark shadow so it stays legible over both the lit and unlit halves.

Past a year out the Years bar leads and the Days bar counts days-within-the-
year, exactly like the flap app. The event name rides a slim header on a tall
panel, or overlays the top bar (right-aligned) where there is no room. When the
target passes the panel turns into one celebratory ARRIVED bar; with no target
it asks, plainly, to set one. Colours run cool→urgent and never touch pink.
The bundled font and the panel-sized helpers come from the injected `canvas`.
"""

# One colour per unit, cool (far off) to urgent (imminent). No pink anywhere.
# (rgb, plural label, singular label); the short label is the key letter.
_UNITS = {
    'Y': ((150, 70, 230), 'YEARS', 'YEAR'),
    'D': ((60, 130, 245), 'DAYS', 'DAY'),
    'H': ((32, 200, 150), 'HOURS', 'HOUR'),
    'M': ((255, 175, 45), 'MIN', 'MIN'),
    'S': ((240, 68, 55), 'SEC', 'SEC'),
}


def _dim(color, f=0.13):
    """A dark 'track' tint of a unit's colour — the unlit part of its bar. Kept
    deep so the lit fill and the white value read strongly against it."""
    return tuple(int(c * f) for c in color)


def _valstr(key, value):
    """Hours/minutes/seconds are zero-padded to two digits ('07 MIN'); years and
    days show their natural width (a day count runs past two digits)."""
    return f'{value:02d}' if key in ('H', 'M', 'S') else str(value)


def _bar_font(canvas, avail_h):
    """The largest bundled font whose digit ink fits ``avail_h`` px tall.
    Returns (font, ink_top, ink_height) so callers can vertically centre by ink."""
    size = max(5, int(avail_h * 1.5))
    font = canvas.font(size)
    for _ in range(64):
        _, t, _, b = font.getbbox('80')
        if (b - t) <= avail_h or size <= 5:
            return font, t, b - t
        size -= 1
        font = canvas.font(size)
    _, t, _, b = font.getbbox('80')
    return font, t, b - t


def _fit_width(canvas, text, max_w, start):
    """The largest bundled font (from ``start`` px down) whose ``text`` fits ``max_w``."""
    size = max(5, int(start))
    font = canvas.font(size)
    while size > 5 and font.getlength(text) > max_w:
        size -= 1
        font = canvas.font(size)
    return font


def _label(key, value, font, max_w):
    """'23 DAYS' if it fits ``max_w``, else the compact '23 D', else just '23'."""
    _, plural, singular = _UNITS[key]
    vs = _valstr(key, value)
    word = singular if value == 1 else plural
    cand = f'{vs} {word}'
    if font.getlength(cand) <= max_w:
        return cand
    short = f'{vs} {key}'
    if font.getlength(short) <= max_w:
        return short
    return vs


def _truncate(font, text, max_w):
    """Drop trailing characters until ``text`` fits ``max_w``."""
    while text and font.getlength(text) > max_w:
        text = text[:-1]
    return text


def _shadow_text(draw, x, y, text, font, fill=(255, 255, 255)):
    """White (or ``fill``) text with a 1px dark OUTLINE on all sides — so it stays
    legible over a bright bar fill and a dark track alike. Anchored top-left."""
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0), anchor='la')
    draw.text((x, y), text, font=font, fill=fill, anchor='la')


def _render_bars(canvas, ImageDraw, keys, val, frac, event, header_h):
    """The countdown itself: one full-width, no-gap bar per unit, and the event
    name on the plain black header above them — never on a bar."""
    W, H = canvas.width, canvas.height
    pad = 2
    img = canvas.blank((0, 0, 0))          # pure black — the title sits on THIS
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"                     # crisp 1-bit text — no anti-aliased fuzz

    # The event name, centred on the black header (no panel/bar behind it), with a
    # thin colour line dividing it from the bars.
    if header_h > 0 and event:
        hf, htop, hh = _bar_font(canvas, max(5, header_h - 2))
        etext = _truncate(hf, event, W - 4)
        ex = (W - hf.getlength(etext)) / 2.0
        ey = (header_h - 2 - hh) / 2.0 - htop
        _shadow_text(draw, ex, max(0, ey), etext, hf, fill=(240, 240, 245))
        draw.rectangle([0, header_h - 1, W - 1, header_h - 1], fill=_UNITS[keys[0]][0])

    # Bar edges: rounded so the units EXACTLY tile [header_h, H) with no gaps.
    n = len(keys)
    top, area = header_h, H - header_h
    edges = [top + round(i * area / n) for i in range(n + 1)]
    min_bh = min(edges[i + 1] - edges[i] for i in range(n))
    font, ink_top, ink_h = _bar_font(canvas, max(5, min_bh - 1))

    for i, key in enumerate(keys):
        color = _UNITS[key][0]
        y0, y1 = edges[i], edges[i + 1]
        bh = y1 - y0
        draw.rectangle([0, y0, W - 1, y1 - 1], fill=_dim(color))     # deep full-width track
        fw = int(round(min(1.0, max(0.0, frac[key])) * W))           # fill from the left
        if fw > 0:
            draw.rectangle([0, y0, fw - 1, y1 - 1], fill=color)
        vtext = _label(key, val[key], font, W - 2 * pad)
        ty = y0 + (bh - ink_h) / 2.0 - ink_top                       # vertically centred
        _shadow_text(draw, pad, ty, vtext, font)
    return img


def _render_arrived(canvas, Image, ImageDraw, event, frame):
    """The target has passed: one celebratory full-panel bar with a moving shine."""
    W, H = canvas.width, canvas.height
    base = canvas.vgrad((255, 196, 70), (28, 168, 92)).convert('RGBA')   # gold -> emerald

    overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cx = int((frame * 4) % (W + 30)) - 15                                # sweeping highlight
    for dx in range(-14, 15):
        a = int(70 * max(0.0, 1 - abs(dx) / 14.0))
        if a:
            od.line([(cx + dx, 0), (cx + dx, H)], fill=(255, 255, 255, a))
    img = Image.alpha_composite(base, overlay).convert('RGB')
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"                     # crisp 1-bit text — no anti-aliased fuzz

    hero = 'ARRIVED!'
    hf = _fit_width(canvas, hero, W - 4, int(H * 0.52))
    hb = hf.getbbox(hero)
    hh = hb[3] - hb[1]
    eh, ef = 0, None
    if event:
        ef = _fit_width(canvas, event, W - 4, int(H * 0.30))
        eb = ef.getbbox(event)
        eh = eb[3] - eb[1]
    gap = 2 if event else 0
    y = (H - (hh + gap + eh)) / 2.0
    if event:
        _shadow_text(draw, (W - ef.getlength(event)) / 2.0, y - ef.getbbox(event)[1],
                     event, ef, fill=(255, 250, 235))
        y += eh + gap
    _shadow_text(draw, (W - hf.getlength(hero)) / 2.0, y - hb[1], hero, hf)
    return img


def _render_message(canvas, ImageDraw, line1, line2):
    """A friendly two-line prompt on a quiet dark gradient (no target set)."""
    W, H = canvas.width, canvas.height
    img = canvas.vgrad((34, 40, 52), (12, 14, 20))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"                     # crisp 1-bit text — no anti-aliased fuzz
    f1 = _fit_width(canvas, line1, W - 4, int(H * 0.40))
    b1 = f1.getbbox(line1)
    h1 = b1[3] - b1[1]
    f2, h2 = None, 0
    if line2:
        f2 = _fit_width(canvas, line2, W - 4, int(H * 0.30))
        b2 = f2.getbbox(line2)
        h2 = b2[3] - b2[1]
    gap = 2 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    _shadow_text(draw, (W - f1.getlength(line1)) / 2.0, y - b1[1], line1, f1, fill=(235, 238, 245))
    if line2:
        y += h1 + gap
        _shadow_text(draw, (W - f2.getlength(line2)) / 2.0, y - b2[1], line2, f2, fill=(150, 170, 210))
    return img


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None
    from datetime import datetime
    from PIL import Image, ImageDraw

    st = getattr(fetch, '_state', None)
    if st is None:
        st = {'frame': 0}
        setattr(fetch, '_state', st)
    st['frame'] += 1
    frame = st['frame']

    W, H = canvas.width, canvas.height

    event = (str(settings.get('countdown_event') or '').strip() or 'New Year').upper()
    target_str = str(settings.get('countdown_target') or '').strip()
    show_seconds = (str(settings.get('show_seconds', 'yes') or 'yes').strip().lower()
                    in ('yes', 'on', '1', 'true'))

    # Timezone-aware where pytz is present (matching the flap Countdown, which
    # defaults to UTC); naive-but-consistent if it is not, so the diff still holds.
    tzname = str(settings.get('timezone') or '').strip()
    now, target, valid = None, None, False
    try:
        import pytz
        try:
            tz = pytz.timezone(tzname) if tzname else pytz.utc
        except Exception:
            tz = pytz.utc
        now = datetime.now(tz)
        if target_str:
            try:
                target = datetime.fromisoformat(target_str)
                if target.tzinfo is None:
                    target = tz.localize(target)
                valid = True
            except (TypeError, ValueError):
                valid = False
    except Exception:
        now = datetime.now()
        if target_str:
            try:
                target = datetime.fromisoformat(target_str)
                if target.tzinfo is not None:
                    target = target.replace(tzinfo=None)
                valid = True
            except (TypeError, ValueError):
                valid = False

    if not valid or target is None:
        canvas.frame(_render_message(canvas, ImageDraw, 'SET A TARGET', 'DATE'))
        return 1.0

    total = (target - now).total_seconds()
    if total <= 0:
        canvas.frame(_render_arrived(canvas, Image, ImageDraw, event, frame))
        return 0.2

    # Break the remaining time into units, with each unit's own-cycle fraction
    # (drives the fill). Past a year out the Years unit leads and Days becomes
    # days-within-the-year — which also keeps the day value inside a year.
    total_i = int(total)
    days_i, rem = divmod(total_i, 86400)
    hrs_i, rem = divmod(rem, 3600)
    mins_i, secs_i = divmod(rem, 60)
    years_i, remdays_i = divmod(days_i, 365)
    days_f = total / 86400.0

    val = {'Y': years_i, 'D': (remdays_i if years_i > 0 else days_i),
           'H': hrs_i, 'M': mins_i, 'S': secs_i}
    frac = {
        'S': (total % 60.0) / 60.0,
        'M': (total % 3600.0) / 3600.0,
        'H': (total % 86400.0) / 86400.0,
        'D': ((days_f % 365.0) / 365.0) if years_i > 0 else min(1.0, days_f / 365.0),
        'Y': min(1.0, days_f / 3650.0),
    }

    # The event ALWAYS gets its own line on the plain black header — never a bar
    # behind it. A little taller on a tall panel, slim on a short one.
    header_h = max(9, min(18, int(H * 0.24))) if event else 0

    # As many bars as fit legibly, trimmed least-significant-first (seconds go
    # first on a small wall — they are for launch day, not a decade away).
    max_bars = max(1, (H - header_h) // 7)
    if W < 96:
        max_bars = min(max_bars, 3)
    max_bars = min(max_bars, 5)

    keys = (['Y', 'D'] if years_i > 0 else ['D']) + ['H', 'M']
    if show_seconds:
        keys.append('S')
    keys = keys[:max_bars]

    canvas.frame(_render_bars(canvas, ImageDraw, keys, val, frac, event, header_h))
    return 0.2                                      # ~5 fps: a smooth seconds sweep
