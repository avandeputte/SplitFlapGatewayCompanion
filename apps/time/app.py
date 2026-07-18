def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
    from datetime import datetime
    import pytz
    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)
    # Seconds are opt-in, and only where the wall says sub-second updates are
    # honest (caps.instant — its own motion statement): a mechanical module takes
    # seconds per flip, so a ticking seconds field would keep the wall permanently
    # mid-clatter. They also have to fit the row. getattr, so the app still runs
    # on a stock splitflap-os whose caps has no such attribute.
    want_secs = (str(settings.get('show_seconds', '')).strip().lower()
                 in ('1', 'true', 'yes', 'on')
                 and bool(getattr(caps, 'instant', False)))

    def _drop1zero(s):
        # A single leading zero looks cleaner (9:30, not 09:30) — but drop only ONE, or
        # midnight in 24h (00:30) loses its hour entirely and reads ":30".
        return s[1:] if s[:1] == "0" and s[1:2].isdigit() else s

    def clock(seconds):
        # An explicit Time Format wins; otherwise the Language decides (12h AM/PM
        # for English, 24h elsewhere) via the injected i18n helper.
        tf = settings.get('time_format')
        if tf in ('12hr', '24hr'):
            f24, f12 = ("%H:%M:%S", "%I:%M:%S%p") if seconds else ("%H:%M", "%I:%M%p")
            return _drop1zero(now.strftime(f24 if tf == '24hr' else f12))
        if i18n is not None:
            return i18n.time(now, seconds=seconds, ampm_space=False)
        return _drop1zero(now.strftime("%I:%M:%S%p" if seconds else "%I:%M%p"))

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
