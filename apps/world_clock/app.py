def _columns(pairs, cols, gap=3):
    """Two aligned columns — `left` flush, `right` flush — kept CLOSE together
    rather than spread to the wall's edges.

    format_lines centres each line, so the block is only as wide as its content
    plus a small gap: on a wide wall the pair sits in the middle instead of the
    city and the time stranded at opposite edges. The right column still lines up
    down the page (every line is the same width, so centring keeps them aligned).
    A narrow wall falls back to the full width, trimming the left, never the right.
    """
    pairs = [(str(left), str(right)) for left, right in pairs]
    rw = max((len(r) for _, r in pairs), default=0)
    lw = max((len(l) for l, _ in pairs), default=0)
    inner = min(cols, lw + gap + rw)
    lspace = max(1, inner - rw)                       # left column width, incl. the gap
    out = []
    for left, right in pairs:
        if len(left) > lspace - 1:
            left = left[:max(0, lspace - 1)]
        out.append((left.ljust(lspace) + right.rjust(rw))[:cols])
    return out


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    from datetime import datetime
    import pytz
    cols = get_cols()
    zones = [s.strip() for s in settings.get('world_clock_zones', 'US/Eastern,US/Pacific,Europe/London').split(',') if s.strip()]
    pairs = []
    for z in zones[:get_rows()]:
        try:
            tz = pytz.timezone(z)
        except pytz.UnknownTimeZoneError:
            continue
        now = datetime.now(tz)
        # Compact time leaves room for the city; a long city is trimmed so the time
        # is never cut off. AM/PM is an English convention — everyone else is 24h.
        if i18n is not None:
            t = i18n.time(now, ampm_space=False)
        else:
            t = now.strftime('%I:%M%p').lstrip('0')
        city = z.split('/')[-1].replace('_', ' ')
        pairs.append((city, t))
    if not pairs:
        return [format_lines('No valid', 'timezones')]
    # No bottom padding: format_lines centres what it is given. Filling the page here
    # would pin three zones to the top of a five-row wall.
    return [format_lines(*_columns(pairs, cols))]


def trigger(settings, conditions):
    """Fire when business hours start or end in a followed timezone."""
    from datetime import datetime
    import pytz

    event = conditions.get('event', 'open')
    zones = [s.strip() for s in settings.get('world_clock_zones', 'US/Eastern,US/Pacific,Europe/London').split(',')]

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'fired_today': set()}
        setattr(trigger, '_state', state)

    # Reset daily
    today = datetime.utcnow().strftime('%Y-%m-%d')
    if state.get('date') != today:
        state['fired_today'] = set()
        state['date'] = today

    for z in zones:
        try:
            tz = pytz.timezone(z)
            now = datetime.now(tz)
            hour, minute = now.hour, now.minute
            city = z.split('/')[-1].replace('_', ' ')
            key = f"{event}:{z}:{today}"

            if event == 'open' and hour == 9 and minute < 5:
                if key not in state['fired_today']:
                    state['fired_today'].add(key)
                    return True
            elif event == 'close' and hour == 17 and minute < 5:
                if key not in state['fired_today']:
                    state['fired_today'].add(key)
                    return True
        except Exception:
            continue
    return False
