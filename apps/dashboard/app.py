"""Dashboard plugin: time + current weather (via the shared weather helper)."""

def fetch(settings, format_lines, get_rows, get_cols, get_weather=None):
    from datetime import datetime
    import pytz
    tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    dt = datetime.now(tz)
    time_page = format_lines(dt.strftime("%A").upper(),
                             dt.strftime("%b %d %Y").upper(),
                             dt.strftime("%I:%M %p").upper())

    # Weather comes from the companion's shared helper (global provider + key +
    # location). With no helper (e.g. a bare host), just show the time.
    if get_weather is None:
        return [time_page]
    w = get_weather()
    if not w or not w.get('ok'):
        return [time_page, format_lines("NO WEATHER", "DATA", "TRY LATER")]

    c = get_cols()
    city = str(w.get('city') or 'LOCATION').upper()
    temp = w.get('temp_f')
    feels = w.get('feels_like_f')
    desc = str(w.get('desc') or '').upper()
    high = w.get('hi_f')
    low = w.get('lo_f')
    now_t = dt.strftime("%I:%M%p").lstrip("0")
    mcl = max(1, c - 1 - len(now_t))
    l1 = f"{city[:mcl]} {now_t}".center(c)
    pfx = f"{temp}F ({feels}F) "
    l2 = (pfx + desc[:max(0, c - len(pfx))]).center(c)
    l3 = f"H:{high}F L:{low}F".center(c)
    return [time_page, format_lines(l1, l2, l3)]
