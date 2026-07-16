"""A classic BCD binary clock, drawn in colour flaps.

Six columns of bits — tens and ones of hours, minutes and seconds — each read
top to bottom as 8-4-2-1. A lit bit is one colour tile, an unlit bit another
(or a blank flap). Reading it is the fun part.
"""

TILES = {
    'red': '\U0001f7e5', 'orange': '\U0001f7e7', 'yellow': '\U0001f7e8',
    'green': '\U0001f7e9', 'blue': '\U0001f7e6', 'purple': '\U0001f7ea',
    'white': '⬜', 'off': '⬛',
}
WEIGHTS = (8, 4, 2, 1)


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
    per-line centring in format_lines keeps the labels under their columns."""
    return gap.join(['H ', 'M ', 'S '][:n_groups])


def _truthy(v):
    return str(v).strip().lower() not in ('false', '0', 'no', 'off', '')


def fetch(settings, format_lines, get_rows, get_cols, caps=None):
    from datetime import datetime

    import pytz

    try:
        tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone('US/Eastern')
    now = datetime.now(tz)

    rows, cols = get_rows(), get_cols()
    one = TILES.get(str(settings.get('one_color', 'green')), TILES['green'])
    zero = TILES.get(str(settings.get('zero_color', 'red')), TILES['red'])
    # Seconds only where the wall says sub-second updates are honest
    # (caps.instant — its own motion statement): mechanical flaps take seconds
    # per flip and would never catch up with a ticking seconds column.
    seconds = (_truthy(settings.get('show_seconds', True)) and cols >= 8
               and bool(getattr(caps, 'instant', False)))

    digits = [now.hour // 10, now.hour % 10, now.minute // 10, now.minute % 10]
    if seconds:
        digits += [now.second // 10, now.second % 10]

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
        lines.append(now.strftime('%H:%M:%S' if seconds else '%H:%M'))
    return [format_lines(*lines)]
