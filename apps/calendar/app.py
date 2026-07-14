"""Calendar — the next thing you have to be at, from an iCal feed.

The point of this app is the glance: you walk past the wall and it tells you what is next, and
when. So the WHEN comes first and is written the way a person says it — "Today 3:30PM",
"Tomorrow", "Tue 9:00AM" — and the title gets whatever room is left. On a wall with the rows
to spare it shows the event after that one too.

Three things about iCal are worth knowing, because they are where the bugs live:

* **A line can be folded.** RFC 5545 wraps long lines and continues them with a leading space,
  so `SUMMARY:Dinner with Al` and `SUMMARY:Dinner with A` + `\\n ex` are the same event. Unfold
  before parsing anything, or you truncate every long title in the feed.

* **VEVENTs nest.** A VEVENT usually contains a VALARM, and the VALARM has its own `UID` —
  and a `VTIMEZONE` has its own `DTSTART`. A parser that just scans for `DTSTART:` reads the
  timezone definition's and puts your meeting in 1970. So this one tracks the component it is
  actually inside.

* **A recurring event is not one event.** `RRULE:FREQ=WEEKLY;BYDAY=TU` is the standup you go to
  every week, and "the next event" usually IS one of those. Expanding an RRULE properly is a
  nastier job than it looks (BYSETPOS, BYDAY=-1SU, an UNTIL in UTC against a local DTSTART,
  DST), so it is left to dateutil, which is a correct implementation of it.
"""

# The three kinds of DTSTART, and what each one means:
#   DTSTART;VALUE=DATE:20260714              an ALL-DAY event — a date, with no time at all
#   DTSTART:20260714T130000Z                 an instant, in UTC
#   DTSTART;TZID=America/New_York:20260714T090000    a wall-clock time in a named zone
#
# The middle one is the only one that is unambiguous on its own. The last needs the zone; the
# first needs to be kept a DATE, because turning it into midnight-local makes an all-day event
# on Saturday start to look like a thing at 00:00, and then sort before Friday evening.


def _unfold(text):
    """RFC 5545 line folding: a continuation line begins with a space or a tab."""
    out = []
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line[:1] in (' ', '\t') and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(value):
    """The text escapes iCal actually uses: a literal comma, semicolon, newline, backslash."""
    out, i = [], 0
    while i < len(value):
        c = value[i]
        if c == '\\' and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({'n': ' ', 'N': ' ', ',': ',', ';': ';', '\\': '\\'}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _parse_line(line):
    """`NAME;PARAM=X;PARAM=Y:value` -> ('NAME', {'PARAM': 'X', ...}, 'value')."""
    head, sep, value = line.partition(':')
    if not sep:
        return None, {}, ''
    parts = head.split(';')
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition('=')
        params[k.upper()] = v.strip('"')
    return name, params, value


def _to_dt(value, params, tz, pytz):
    """One DTSTART/DTEND/EXDATE value -> (datetime, all_day).

    An all-day event stays a DATE: it is given midnight in the LOCAL zone only so that it can
    be compared with the timed events, and `all_day` is what stops it being printed as 12:00AM.
    """
    from datetime import datetime

    value = value.split(',')[0].strip()          # EXDATE can carry a list; the first will do
    if params.get('VALUE') == 'DATE' or (len(value) == 8 and 'T' not in value):
        d = datetime.strptime(value[:8], '%Y%m%d')
        return tz.localize(d) if hasattr(tz, 'localize') else d.replace(tzinfo=tz), True
    if value.endswith('Z'):
        naive = datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
        return naive.replace(tzinfo=pytz.UTC).astimezone(tz), False
    naive = datetime.strptime(value[:15], '%Y%m%dT%H%M%S')
    zone = params.get('TZID')
    if zone:
        try:
            src = pytz.timezone(zone)
            return src.localize(naive).astimezone(tz), False
        except Exception:
            pass
    # A floating time has no zone at all, and means "wherever you are".
    return (tz.localize(naive) if hasattr(tz, 'localize') else naive.replace(tzinfo=tz)), False


def _events(text, tz, pytz):
    """Every VEVENT in the feed, as {start, all_day, summary, rrule, exdates}.

    Only properties whose innermost component is VEVENT are read — see the module docstring on
    VALARM and VTIMEZONE, both of which carry properties with the same names.
    """
    stack, cur, out = [], None, []
    for line in _unfold(text):
        name, params, value = _parse_line(line)
        if name == 'BEGIN':
            stack.append(value.upper())
            if value.upper() == 'VEVENT':
                cur = {'summary': '', 'start': None, 'all_day': False,
                       'rrule': '', 'exdates': [], 'cancelled': False}
            continue
        if name == 'END':
            done = stack.pop() if stack else ''
            if done == 'VEVENT':
                if cur and cur['start'] is not None and not cur['cancelled']:
                    out.append(cur)
                cur = None
            continue
        if cur is None or (stack and stack[-1] != 'VEVENT'):
            continue                              # inside a VALARM, or outside any VEVENT
        try:
            if name == 'DTSTART':
                cur['start'], cur['all_day'] = _to_dt(value, params, tz, pytz)
            elif name == 'SUMMARY':
                cur['summary'] = _unescape(value).strip()
            elif name == 'RRULE':
                cur['rrule'] = value
            elif name == 'EXDATE':
                for one in value.split(','):
                    cur['exdates'].append(_to_dt(one, params, tz, pytz)[0])
            elif name == 'STATUS' and value.strip().upper() == 'CANCELLED':
                cur['cancelled'] = True
        except Exception:
            continue                              # one malformed property must not kill the feed
    return out


def _next_occurrence(ev, now, horizon):
    """When this event next happens, at or after `now` — expanding an RRULE if it has one."""
    start = ev['start']
    if not ev['rrule']:
        return start if now <= start <= horizon else None
    try:
        from dateutil.rrule import rrulestr
        rule = rrulestr(ev['rrule'], dtstart=start)
        for when in rule.between(now, horizon, inc=True):
            if not any(abs((when - x).total_seconds()) < 60 for x in ev['exdates']):
                return when
        return None
    except Exception:
        # An RRULE we cannot expand is better ignored than allowed to take the app down; the
        # event's own start still counts if it happens to be ahead of us.
        return start if now <= start <= horizon else None


def _urls(raw):
    """The iCal URLs, from one setting. Commas separate them — the same convention the
    companion already uses for several gateways in GATEWAY_URL — and newlines are accepted too,
    because a field you paste several long URLs into is a field people paste newlines into."""
    out = []
    for chunk in str(raw or '').replace('\n', ',').replace('\r', ',').split(','):
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
    return out


def _wrap(text, cols, rows):
    """Break a title across at most `rows` lines, on spaces where it can."""
    words, lines, line = str(text).split(), [], ''
    for w in words:
        if len(w) > cols:                          # a single word longer than the wall
            if line:
                lines.append(line)
                line = ''
            while len(w) > cols and len(lines) < rows:
                lines.append(w[:cols])
                w = w[cols:]
        if not line:
            line = w
        elif len(line) + 1 + len(w) <= cols:
            line += ' ' + w
        else:
            lines.append(line)
            line = w
        if len(lines) >= rows:
            break
    if line and len(lines) < rows:
        lines.append(line)
    return lines[:rows] or ['']


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    import pytz
    from datetime import datetime, timedelta

    rows, cols = get_rows(), get_cols()

    def t(s, ctx='calendar'):
        return i18n.t(s, ctx) if i18n is not None else s

    urls = _urls(settings.get('ical_url', ''))
    if not urls:
        return [format_lines(t('Calendar'), t('Configure', 'common'), 'iCal URL')]

    try:
        tz = pytz.timezone(str(settings.get('timezone', 'US/Eastern') or 'US/Eastern'))
    except Exception:
        tz = pytz.UTC

    # Several calendars — work, family, birthdays — merge into one timeline. One of them being
    # unreachable must not hide the others: a dead feed is a reason to show less, not nothing.
    # Only when EVERY feed fails is the app actually offline.
    feeds = []
    for url in urls:
        try:
            r = requests.get(url, timeout=15,
                             headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'})
            r.raise_for_status()
            feeds.append(r.text)
        except Exception:
            continue
    if not feeds:
        return [format_lines(t('Calendar'), t('Offline', 'common'))]

    try:
        now = datetime.now(tz)
        try:
            days = max(1, min(365, int(settings.get('days_ahead', 60) or 60)))
        except (TypeError, ValueError):
            days = 60
        horizon = now + timedelta(days=days)
        skip_all_day = str(settings.get('skip_all_day', 'no')).lower() == 'yes'

        # An all-day event counts for the whole of its day, so "now" for it is midnight —
        # otherwise today's birthday disappears at 00:01 and the wall says the next one is
        # in a year.
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

        upcoming = []
        for text in feeds:                       # every calendar, into one timeline
            for ev in _events(text, tz, pytz):
                if ev['all_day'] and skip_all_day:
                    continue
                when = _next_occurrence(ev, midnight if ev['all_day'] else now, horizon)
                if when is not None and ev['summary']:
                    upcoming.append((when, ev['all_day'], ev['summary']))
        upcoming.sort(key=lambda e: (e[0], e[2]))   # by time; the title only breaks a tie

        if not upcoming:
            return [format_lines(t('Calendar'), t('No events'))]

        # Two events need four lines to say anything (a when and a title each); below that,
        # one event gets the whole wall and its title gets the spare rows.
        want = 2 if rows >= 4 and len(upcoming) > 1 else 1
        shown = upcoming[:want]

        if rows == 1:
            when, all_day, summary = shown[0]
            return [f'{_when(when, all_day, now, i18n)} {summary}'[:cols].center(cols)]

        lines = []
        spare = rows - 2 * len(shown)
        for i, (when, all_day, summary) in enumerate(shown):
            title_rows = 1 + (spare if i == 0 and spare > 0 else 0)
            lines.append(_when(when, all_day, now, i18n))
            lines.extend(_wrap(summary, cols, title_rows))
        return [format_lines(*lines[:rows])]
    except Exception:
        return [format_lines(t('Calendar'), t('Error', 'common'))]


def _when(dt, all_day, now, i18n):
    """The line a person would say: "Today 3:30PM", "Tomorrow", "Tue 9:00AM", "Jul 21"."""
    def t(s, ctx='calendar'):
        return i18n.t(s, ctx) if i18n is not None else s

    days = (dt.date() - now.date()).days
    if days == 0:
        day = t('Today', 'time')
    elif days == 1:
        day = t('Tomorrow', 'time')
    elif 0 < days < 7:
        day = i18n.weekday(dt, short=True) if i18n is not None else dt.strftime('%a')
    else:
        day = i18n.date(dt, short=True) if i18n is not None else f"{dt.strftime('%b')} {dt.day}"

    if all_day:
        return day
    clock = (i18n.time(dt, ampm_space=False) if i18n is not None
             else dt.strftime('%I:%M%p').lstrip('0'))
    return f'{day} {clock}'
