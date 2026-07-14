"""On This Day in History — one concise event, on a single page (byabbe.se)."""

_FALLBACK = [
    (1776, "Declaration of Independence signed"),
    (1969, "First moon landing by Apollo 11"),
    (1989, "Berlin Wall falls in Germany"),
    (1903, "Wright brothers first flight"),
    (1865, "Civil War ends in America"),
    (1945, "World War 2 ends"),
    (1963, "I Have a Dream speech by MLK"),
    (1912, "Titanic sinks on maiden voyage"),
    (1929, "Stock market crash Black Tuesday"),
    (1955, "Rosa Parks refuses to give up seat"),
]
_ALLOWED = set(" ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;:%'.,/?*")


def _clean(s):
    # Validate on the uppercased form (the allow-set is uppercase) but emit the
    # character as written: a wall that can show lowercase should get it, and one
    # that cannot has the companion fold it on the way out.
    return ''.join(c if c.upper() in _ALLOWED else ' ' for c in s).strip()


def _split(text, width):
    words, lines, cur = text.split(), [], ''
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w[:width]
        elif not cur:
            cur = w[:width]
        else:
            cur += ' ' + w
    if cur:
        lines.append(cur)
    return lines


def fetch(settings, format_lines, get_rows, get_cols):
    import urllib.request
    import json
    import random
    from datetime import datetime
    import pytz

    cols, rows = get_cols(), get_rows()
    try:
        tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        tz = pytz.timezone('US/Eastern')
    now = datetime.now(tz)

    try:
        url = f"https://byabbe.se/on-this-day/{now.month}/{now.day}/events.json"
        req = urllib.request.Request(url, headers={"User-Agent": "SplitFlapGatewayCompanion/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        events = [(str(e.get("year", "")), _clean(e.get("description", "")))
                  for e in data.get("events", []) if e.get("description")]
        if not events:
            raise ValueError("no events")
    except Exception:
        events = [(str(y), _clean(d)) for y, d in _FALLBACK]

    # Lead with the year (no wasted 'ON THIS DAY' header row) and keep the whole
    # thing on one page — prefer a short event that fits, else the shortest.
    events = [(y, f'{y} {d}') for y, d in events]     # (year, "YEAR DESC")
    if rows == 1:
        return [min(events, key=lambda e: len(e[1]))[1][:cols].center(cols)]
    random.shuffle(events)
    text = min(events, key=lambda e: len(e[1]))[1]
    for _y, t in events:
        if len(_split(t, cols)) <= rows:
            text = t
            break
    lines = _split(text, cols)[:rows]
    # format_lines centres it; doing it here as well lands it below the middle.
    return [format_lines(*lines)]
