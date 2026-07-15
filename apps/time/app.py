def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
    from datetime import datetime
    import pytz
    tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    now = datetime.now(tz)
    # Seconds are opt-in, and only on a drawn wall (caps.indexed): a physical
    # module takes seconds per flip, so a ticking seconds field would keep the
    # wall permanently mid-clatter. They also have to fit the row.
    want_secs = (str(settings.get('show_seconds', '')).strip().lower()
                 in ('1', 'true', 'yes', 'on')
                 and caps is not None and caps.indexed)

    def clock(seconds):
        # An explicit Time Format wins; otherwise the Language decides (12h AM/PM
        # for English, 24h elsewhere) via the injected i18n helper.
        tf = settings.get('time_format')
        if tf in ('12hr', '24hr'):
            f24, f12 = ("%H:%M:%S", "%I:%M:%S%p") if seconds else ("%H:%M", "%I:%M%p")
            return now.strftime(f24 if tf == '24hr' else f12).lstrip("0")
        if i18n is not None:
            return i18n.time(now, seconds=seconds, ampm_space=False)
        return now.strftime("%I:%M:%S%p" if seconds else "%I:%M%p").lstrip("0")

    time_str = clock(want_secs)
    if want_secs and len(time_str) > get_cols():
        time_str = clock(False)         # the geometry doesn't support it
    rows = get_rows()
    if rows == 1:
        return [format_lines(time_str)]
    if rows >= 4:
        # Room to spare: a wall clock that also says what day it is.
        if i18n is not None:
            weekday, date_line = i18n.weekday(now), i18n.date(now)
        else:
            weekday = now.strftime('%A')
            date_line = f"{now.strftime('%B')} {now.day}"
        return [format_lines(time_str, '', weekday, date_line)]
    return [format_lines('', time_str, '')]
