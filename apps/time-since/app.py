def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
    from datetime import datetime
    import pytz

    def t(s):
        return i18n.t(s, "time") if i18n is not None else s

    def u(k):                       # localized Y/D/H/M/S suffix
        return i18n.unit(k) if i18n is not None else k

    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)
    event = settings.get('event_name', 'The start')
    date_str = settings.get('event_date', '2024-01-01')
    try:
        start = datetime.strptime(date_str, '%Y-%m-%d')
        start = tz.localize(start)
    except Exception:
        return [format_lines(event, t('Invalid date'), '')]
    diff = now - start
    if diff.total_seconds() < 0:
        return [format_lines(event, t('Not yet'), t('Started'))]
    days = diff.days
    hrs, rem = divmod(diff.seconds, 3600)
    mins, secs = divmod(rem, 60)
    years = days // 365
    remaining_days = days % 365
    # A live seconds counter means a flip every second, forever. Only a wall that
    # repaints (caps.instant) gets it; a mechanical wall shows minutes and moves
    # once a minute. It must also actually fit: "364D 23H 59M 59S" is 16 wide.
    instant = bool(getattr(caps, 'instant', False))
    if years > 0:
        elapsed = f'{years}{u("Y")} {remaining_days}{u("D")} {hrs}{u("H")}'
    else:
        elapsed = f'{days}{u("D")} {hrs}{u("H")} {mins}{u("M")}'
        with_secs = f'{elapsed} {secs}{u("S")}'
        if instant and len(with_secs) <= get_cols():
            elapsed = with_secs
    lines = [event, elapsed, t('Time since')]
    if get_rows() >= 4 and i18n is not None:
        lines.append(i18n.date(start, year=True))    # the wall has room: since when
    return [format_lines(*lines)]


def trigger(settings, conditions):
    """Fire when the elapsed time hits a round milestone."""
    from datetime import datetime
    import pytz

    milestone = conditions.get('milestone', '1y')
    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)
    date_str = settings.get('event_date', '2024-01-01')

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'fired_milestone': None}
        setattr(trigger, '_state', state)

    try:
        start = tz.localize(datetime.strptime(date_str, '%Y-%m-%d'))
        diff = now - start
        if diff.total_seconds() < 0:
            return False
        days = diff.days

        # Map milestone to day windows
        windows = {
            '100d': (100, 101),
            '365d': (365, 366),
            '1y':   (365, 366),
            '2y':   (730, 731),
            '5y':   (1825, 1826),
            '10y':  (3650, 3651),
        }
        lo, hi = windows.get(milestone, (365, 366))
        in_window = lo <= days < hi
        key = f"{milestone}:{date_str}:{lo}"

        if in_window and state['fired_milestone'] != key:
            state['fired_milestone'] = key
            return True
        if not in_window and state['fired_milestone'] == key:
            state['fired_milestone'] = None
    except Exception:
        raise
    return False
