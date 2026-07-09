def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    from datetime import datetime
    import pytz
    try:
        tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone('US/Eastern')
    now = datetime.now(tz)
    time_str = now.strftime('%I:%M %p')
    # Localized day/month names when the companion injects i18n; else English.
    if i18n is not None:
        weekday, month = i18n.weekday(now), i18n.month(now)
    else:
        weekday, month = now.strftime('%A').upper(), now.strftime('%B').upper()
    month_day = f'{month} {now.day}'
    rows = get_rows()
    if rows == 2:
        return [format_lines(month_day, weekday)]
    return [format_lines(time_str, month_day, weekday)]
