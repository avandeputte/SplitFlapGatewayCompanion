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
def _clean(s):
    # No character filtering: the renderer degrades wall-aware at the last moment
    # (accents survive on reels that carry them). Filtering to ASCII here was
    # punching holes in names like "Dvořák" on walls that could have shown them.
    return s.strip()


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
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
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

    # Lead with the year (no wasted 'ON THIS DAY' header row) and keep each event
    # whole on its own page — up to three that fit, so the rotation shows more
    # than one thing that happened today.
    events = [(y, f'{y} {d}') for y, d in events]     # (year, "YEAR DESC")
    if rows == 1:
        picks = sorted(events, key=lambda e: len(e[1]))[:3]
        return [t[:cols].center(cols) for _y, t in picks]
    random.shuffle(events)
    pages = []
    for _y, t in events:
        if len(_split(t, cols)) <= rows:
            pages.append(format_lines(*_split(t, cols)))
        if len(pages) == 3:
            break
    if not pages:
        text = min(events, key=lambda e: len(e[1]))[1]
        # format_lines centres it; doing it here as well lands it below the middle.
        pages = [format_lines(*_split(text, cols)[:rows])]
    return pages
