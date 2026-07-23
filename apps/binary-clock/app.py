"""A classic BCD binary clock, drawn in color flaps.

Six columns of bits — tens and ones of hours, minutes and seconds — each read
top to bottom as 8-4-2-1. A lit bit is one color tile, an unlit bit another
(or a blank flap). Reading it is the fun part.
"""

# =============================================================================
# SHARED — the BCD time itself: timezone resolution and the digit columns that
# both surfaces light up (color flaps on a reel, LED dots on a panel).
# =============================================================================

WEIGHTS = (8, 4, 2, 1)


def _tz(settings):
    import pytz
    try:
        return pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        return pytz.timezone('US/Eastern')


def _truthy(v):
    return str(v).strip().lower() not in ('false', '0', 'no', 'off', '')


def _bcd_digits(now, seconds):
    """The decimal digits both views encode: H-tens, H-ones, M-tens, M-ones
    (+ S-tens, S-ones when seconds are on)."""
    digits = [now.hour // 10, now.hour % 10, now.minute // 10, now.minute % 10]
    if seconds:
        digits += [now.second // 10, now.second % 10]
    return digits


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

TILES = {
    'red': '\U0001f7e5', 'orange': '\U0001f7e7', 'yellow': '\U0001f7e8',
    'green': '\U0001f7e9', 'blue': '\U0001f7e6', 'purple': '\U0001f7ea',
    'white': '⬜', 'off': '⬛',
}


def _bits_rows(digits, one, zero, gap):
    """One line per bit weight; each digit pair is a group, groups joined by gap."""
    lines = []
    for w in WEIGHTS:
        cells = [one if d & w else zero for d in digits]
        groups = [''.join(cells[i:i + 2]) for i in range(0, len(cells), 2)]
        lines.append(gap.join(groups))
    return lines


def _units_row(n_groups, gap):
    """H / M / S initials under the groups — same width as the bit rows, so the
    per-line centering in format_lines keeps the labels under their columns."""
    return gap.join(['H ', 'M ', 'S '][:n_groups])


def fetch(settings, format_lines, get_rows, get_cols, caps=None):
    from datetime import datetime

    now = datetime.now(_tz(settings))

    rows, cols = get_rows(), get_cols()
    one = TILES.get(str(settings.get('one_color', 'green')), TILES['green'])
    zero = TILES.get(str(settings.get('zero_color', 'red')), TILES['red'])
    # Seconds only where the wall says sub-second updates are honest
    # (caps.instant — its own motion statement): mechanical flaps take seconds
    # per flip and would never catch up with a ticking seconds column.
    seconds = (_truthy(settings.get('show_seconds', True)) and cols >= 8
               and bool(getattr(caps, 'instant', False)))

    digits = _bcd_digits(now, seconds)

    n_groups = len(digits) // 2
    # Groups are 2 tiles wide; take 2-flap gaps when the wall has room for them.
    gap = '  ' if cols >= n_groups * 2 + (n_groups - 1) * 2 else ' '

    lines = _bits_rows(digits, one, zero, gap)
    # With rows to spare, put the H/M/S labels above and the plain-language time
    # on the very bottom row — the answer key to the puzzle above it. The clock
    # is BCD of a 24-hour time, so the decimal is 24h too, and it shows seconds
    # exactly when the bit columns do.
    if rows >= 6:
        lines.append(_units_row(n_groups, gap))
    if rows >= 5:
        # The decimal answer, built with the SAME group-and-gap geometry as the
        # bit rows: two-digit zero-padded H/M/S units, the colon living in the gap
        # between them — so each digit lands directly under its binary column.
        units = [f'{now.hour:02d}', f'{now.minute:02d}']
        if seconds:
            units.append(f'{now.second:02d}')
        lines.append((':'.center(len(gap))).join(units))
    return [format_lines(*lines)]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The same BCD columns as a wall of LED dots: a lit bit is a bright dot in the
# configured "1" color, an unlit bit a faint socket in the "0" color, groups
# spaced apart like the flap view. A panel with vertical room adds the decimal
# answer underneath, exactly like the tall flap wall does. Black background.
# =============================================================================

_RGB = {
    'red': (235, 68, 58), 'orange': (255, 152, 42), 'yellow': (250, 208, 60),
    'green': (58, 222, 108), 'blue': (72, 142, 250), 'purple': (170, 92, 238),
    'white': (238, 238, 244),
}
_SOCKET = (26, 27, 32)              # the "off" 0-color: a barely-there socket


def _dim(color, k=0.24):
    return tuple(max(0, min(255, int(round(v * k)))) for v in color)


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


def _dot_geometry(W, H, n, label_h):
    """Dot diameter + gaps so 4 rows fit the height and the n columns (in pairs,
    with wider group gaps) fit the width. Returns (d, pair_gap, group_gap, vgap)."""
    groups = n // 2
    avail_h = H - label_h - 2
    for d in range(avail_h, 1, -1):
        vgap = max(1, d // 3)
        if 4 * d + 3 * vgap > avail_h:
            continue
        pair_gap = max(1, d // 3)
        group_gap = max(2, d)
        total_w = n * d + groups * pair_gap + (groups - 1) * group_gap
        if total_w <= W - 4:
            return d, pair_gap, group_gap, vgap
    return 2, 1, 2, 1


def fetch_matrix(settings, canvas, caps=None):
    """The BCD clock as LED dots. Redraws in step with what it shows: on the
    second when the seconds columns are up, on the minute otherwise."""
    from datetime import datetime
    from PIL import ImageDraw

    now = datetime.now(_tz(settings))
    seconds = _truthy(settings.get('show_seconds', True))    # a panel repaints instantly
    digits = _bcd_digits(now, seconds)
    n = len(digits)

    one_key = str(settings.get('one_color', 'green'))
    zero_key = str(settings.get('zero_color', 'red'))
    one = _RGB.get(one_key, _RGB['green'])
    # The 0 color reads as a dim socket, not a second bright grid — 'off' is
    # near-black, anything else a faint wash of the chosen color.
    zero = _SOCKET if zero_key == 'off' else _dim(_RGB.get(zero_key, _RGB['red']))

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    # Decimal answer under the dots when there is vertical room for both.
    label_h = max(9, H // 5) if H >= 48 else 0
    d, pair_gap, group_gap, vgap = _dot_geometry(W, H, n, label_h)

    groups = n // 2
    grid_w = n * d + groups * pair_gap + (groups - 1) * group_gap
    grid_h = 4 * d + 3 * vgap
    x0 = (W - grid_w) // 2
    y0 = (H - label_h - grid_h) // 2

    x = x0
    for i, digit in enumerate(digits):
        for r, w in enumerate(WEIGHTS):
            y = y0 + r * (d + vgap)
            draw.ellipse([x, y, x + d - 1, y + d - 1], fill=(one if digit & w else zero))
        x += d + (pair_gap if i % 2 == 0 else group_gap)

    if label_h:
        units = [f'{now.hour:02d}', f'{now.minute:02d}']
        if seconds:
            units.append(f'{now.second:02d}')
        text = ':'.join(units)
        f = _cv_fit(canvas, text, W - 6, label_h - 2)
        b = f.getbbox(text)
        ly = H - (b[3] - b[1]) - b[1]           # digits sit on the bottom row
        draw.text(((W - f.getlength(text)) / 2.0, ly), text, font=f, fill=(238, 238, 244))

    canvas.frame(img)
    if seconds:
        return max(0.05, 1.0 - now.microsecond / 1e6)       # land on the next second
    return max(1.0, 60.0 - now.second - now.microsecond / 1e6)
