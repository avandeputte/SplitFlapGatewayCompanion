"""Dashboard plugin: time + current weather (via the shared weather helper).

On a small wall it rotates two pages (time, then weather). On a TALL wall (five
rows or more) it drops both onto one dense page that actually uses the rows and
the width — weekday/date and city/temperature spread to the edges, the time and
condition centred, then high/low and (with a sixth row) feels-like, humidity and
wind — instead of two sparse three-line pages floating in a big grid.
"""


def fetch(settings, format_lines, get_rows, get_cols, get_weather=None, i18n=None):
    from datetime import datetime
    import pytz
    try:
        tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone('US/Eastern')
    dt = datetime.now(tz)
    if i18n is not None:                     # localized names, locale date order, 24h outside English
        weekday = i18n.weekday(dt)
        date_line = i18n.date(dt, short=True, year=True)
        time_str = i18n.time(dt)
    else:
        weekday = dt.strftime("%A")
        date_line = dt.strftime("%b %d %Y")
        time_str = dt.strftime("%I:%M %p").lstrip("0")
    time_page = format_lines(weekday, date_line, time_str)

    # Weather comes from the companion's shared helper (global provider + key +
    # location). With no helper (e.g. a bare host), just show the time.
    if get_weather is None:
        return [time_page]
    w = get_weather()
    rows, c = get_rows(), get_cols()
    if not w or not w.get('ok'):
        return [time_page, format_lines("No weather", "data", "Try later")]

    city = str(w.get('city') or 'Location')
    temp = w.get('temp_f')
    feels = w.get('feels_like_f')
    desc = str(w.get('desc') or '')
    if i18n is not None:                     # translate shared-helper condition text where we can
        desc = i18n.t(desc, "weather")
    high = w.get('hi_f')
    low = w.get('lo_f')
    hum, wind = w.get('humidity'), w.get('wind_mph')

    def row(left, right):
        """One full-width line: `left` flush left, `right` flush right — so a wide
        wall reads edge to edge instead of a short string stranded in the middle."""
        left, right = str(left), str(right)
        if not right:
            return left[:c]
        if len(left) + 1 + len(right) > c:
            return f"{left} {right}"[:c]
        return left + " " * (c - len(left) - len(right)) + right

    # A TALL wall gets one dense page that uses every row and the full width.
    if rows >= 5:
        temp_s = f"{temp}F" if temp is not None else ""
        lines = [row(weekday, date_line), time_str,
                 row(city[:max(1, c - len(temp_s) - 1)], temp_s), desc]
        hl = row(f"H {high}F" if high is not None else "",
                 f"L {low}F" if low is not None else "")
        if hl.strip():
            lines.append(hl)
        if rows >= 6:
            det = []
            if feels is not None:
                det.append(f"Feels {feels}F")
            if hum is not None:
                det.append(f"{int(hum)}% hum")
            if wind is not None:
                det.append(f"{int(wind)}mph")
            if det:
                lines.append("  ".join(det))
        return [format_lines(*lines[:rows])]

    # Compact walls keep the two-page rotation.
    now_t = i18n.time(dt, ampm_space=False) if i18n is not None else dt.strftime("%I:%M%p").lstrip("0")
    mcl = max(1, c - 1 - len(now_t))
    l1 = f"{city[:mcl]} {now_t}".center(c)
    pfx = f"{temp}F ({feels}F) "
    l2 = (pfx + desc[:max(0, c - len(pfx))]).center(c)
    l3 = f"H:{high}F L:{low}F".center(c)
    if rows >= 4:
        bits = []
        if hum is not None:
            bits.append(f"{int(hum)}% hum")
        if wind is not None:
            bits.append(f"{int(wind)}mph")
        l4 = "  ".join(bits).center(c) if bits else ""
        return [time_page, format_lines(l1, l2, l3, l4)]
    return [time_page, format_lines(l1, l2, l3)]
