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


# =============================================================================
# SHARED — the iCal machinery: fetch the feeds, parse the events, expand the
# recurrences, build the merged upcoming timeline. Both surfaces read the same
# timeline, so a wall and a panel always agree on what is next.
# =============================================================================

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
                       'rrule': '', 'exdates': [], 'canceled': False}
            continue
        if name == 'END':
            done = stack.pop() if stack else ''
            if done == 'VEVENT':
                if cur and cur['start'] is not None and not cur['canceled']:
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
                cur['canceled'] = True
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


def _tz(settings, pytz):
    """The configured timezone, UTC when the setting is unusable."""
    try:
        return pytz.timezone(str(settings.get('timezone', 'US/Eastern') or 'US/Eastern'))
    except Exception:
        return pytz.UTC


def _fetch_feeds(urls):
    """Each reachable feed's text. Several calendars — work, family, birthdays — merge into
    one timeline. One of them being unreachable must not hide the others: a dead feed is a
    reason to show less, not nothing. Only when EVERY feed fails is the app actually offline."""
    import requests
    feeds = []
    for url in urls:
        try:
            r = requests.get(url, timeout=15,
                             headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'})
            r.raise_for_status()
            feeds.append(r.text)
        except Exception:
            continue
    return feeds


def _upcoming(settings, feeds, tz, pytz, now):
    """The merged, time-sorted upcoming events as ``[(when, all_day, summary), ...]``."""
    from datetime import timedelta
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
    return upcoming


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


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

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
    import pytz
    from datetime import datetime

    rows, cols = get_rows(), get_cols()

    def t(s, ctx='calendar'):
        return i18n.t(s, ctx) if i18n is not None else s

    urls = _urls(settings.get('ical_url', ''))
    if not urls:
        return [format_lines(t('Calendar'), t('Configure', 'common'), 'iCal URL')]

    tz = _tz(settings, pytz)
    feeds = _fetch_feeds(urls)
    if not feeds:
        return [format_lines(t('Calendar'), t('Offline', 'common'))]

    try:
        now = datetime.now(tz)
        upcoming = _upcoming(settings, feeds, tz, pytz, now)

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


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# An agenda: the next events as rows — a color-coded time chip (amber today,
# cyan tomorrow, steel later) over each title in real type. The panel's height
# decides how many events; the SAME timeline the flap pages read. Black
# background, no gradient.
# =============================================================================

_WHITE = (240, 240, 244)
_GRAY = (150, 150, 158)
_TODAY = (255, 180, 60)                     # today's chip
_TOMORROW = (90, 200, 250)                  # tomorrow's chip
_LATER = (135, 150, 185)                    # further out
_INK = (12, 12, 14)                         # chip text — near-black on the chip color


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 5px)."""
    size = max(5, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 5 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_ellipsis(font, text, max_w):
    """``text`` cut with an ellipsis to fit ``max_w`` at this font (full text if it fits)."""
    if font.getlength(text) <= max_w:
        return text
    while text and font.getlength(text + '…') > max_w:
        text = text[:-1].rstrip()
    return (text + '…') if text else ''


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (no URL / offline / no events)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    b1 = f1.getbbox(line1)
    h1 = b1[3] - b1[1]
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = (f2.getbbox(line2)[3] - f2.getbbox(line2)[1]) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    draw.text(((W - f1.getlength(line1)) / 2.0, y - b1[1]), line1, font=f1, fill=_WHITE)
    if line2:
        y += h1 + gap
        draw.text(((W - f2.getlength(line2)) / 2.0, y - f2.getbbox(line2)[1]), line2, font=f2, fill=_GRAY)
    return img


def _cv_chip_color(dt, now):
    days = (dt.date() - now.date()).days
    if days <= 0:
        return _TODAY
    if days == 1:
        return _TOMORROW
    return _LATER


def _cv_chip_label(when, all_day, now, i18n, cf_probe):
    """The chip's text, degrading with the room: the full "TOMORROW 2:49AM", else the day
    alone ("TMRW", "FRI") — except today, where the clock is the useful part."""
    full = _when(when, all_day, now, i18n).upper()
    if cf_probe(full):
        return full
    days = (when.date() - now.date()).days
    if days == 0 and not all_day:
        return full.rsplit(' ', 1)[-1]                       # the clock
    day = _when(when, True, now, i18n).upper()               # the day alone
    if cf_probe(day):
        return day
    return (i18n.weekday(when, short=True) if i18n is not None
            else when.strftime('%a')).upper()                # last resort: "THU"


def _cv_chip(canvas, draw, x, y, h, label, color, max_w):
    """A rounded time chip; returns its width. The label's ink top is clamped to
    y+2 — a fitted font's real ink can overshoot its bbox by a couple of rows, and
    a chip riding the panel's top edge must never clip its glyph tops."""
    cf = _cv_fit(canvas, label, max_w, h - 3)
    cb = cf.getbbox(label)
    cw = int(cf.getlength(label)) + 7
    draw.rounded_rectangle([x, y, x + cw, y + h - 1], radius=2, fill=color)
    ly = max(y + 2 - cb[1], y + (h - 1 - (cb[3] - cb[1])) / 2.0 - cb[1])
    draw.text((x + (cw - cf.getlength(label)) / 2.0, ly), label, font=cf, fill=_INK)
    return cw


def _cv_title(canvas, draw, summary, x, y, w, h, cap_h=None, bottom=False):
    """The event title: whole at the largest size that fits, else held at a readable
    size and ellipsised. (Point size, not ink height, is the readability test — a
    mixed-case bbox includes descenders and lies about how small the type is.)
    ``cap_h`` caps the type below the box height; ``bottom`` sinks the ink to the
    box's last row instead of centering (the panel-edge row)."""
    fit_h = min(h, cap_h) if cap_h else h
    tf = _cv_fit(canvas, summary, w, fit_h)
    text = summary
    if tf.size < 9:
        cap = 8 if w < 80 else int(fit_h * 0.75)             # narrow panels: chars over size
        tf = _cv_fit(canvas, '0', w, max(8, cap))
        text = _cv_ellipsis(tf, summary, w)
    tb = tf.getbbox(text or '0')
    ty = y + h - (tb[3] - tb[1]) if bottom else y + (h - (tb[3] - tb[1])) / 2.0
    draw.text((x, ty - tb[1]), text, font=tf, fill=_WHITE)


def fetch_matrix(settings, canvas, i18n=None):
    """Draw the next events as chip-and-title rows. A calendar shifts slowly — redraw every
    two minutes keeps "Today 3:30PM" honest without hammering anyone's feed."""
    import pytz
    from datetime import datetime
    from PIL import ImageDraw

    urls = _urls(settings.get('ical_url', ''))
    if not urls:
        canvas.frame(_cv_message(canvas, ImageDraw, 'CALENDAR', 'SET AN ICAL URL'))
        return 300.0
    tz = _tz(settings, pytz)
    feeds = _fetch_feeds(urls)
    if not feeds:
        canvas.frame(_cv_message(canvas, ImageDraw, 'CALENDAR', 'OFFLINE'))
        return 120.0
    try:
        now = datetime.now(tz)
        upcoming = _upcoming(settings, feeds, tz, pytz, now)
    except Exception:
        canvas.frame(_cv_message(canvas, ImageDraw, 'CALENDAR', 'ERROR'))
        return 120.0
    if not upcoming:
        canvas.frame(_cv_message(canvas, ImageDraw, 'CALENDAR', 'NO EVENTS'))
        return 300.0

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    def probe(chip_h, max_w):
        # can this label hold a readable size in this chip?
        return lambda label: _cv_fit(canvas, label, max_w, chip_h - 3).size >= 8

    if H >= 48:
        # Stacked agenda: each event is a time chip over its title, the events cut
        # into full-height bands — the first chip rides row 0, the last title's ink
        # sinks to the panel's last row. Three events only on a panel tall enough
        # to keep the titles readable.
        want = 3 if H >= 96 else 2
        shown = upcoming[:want]
        n = len(shown)
        for i, (when, all_day, summary) in enumerate(shown):
            ry = round(i * H / n)
            band_h = round((i + 1) * H / n) - ry
            chip_h = max(11, int(band_h * 0.42))
            label = _cv_chip_label(when, all_day, now, i18n, probe(chip_h, W - 10))
            _cv_chip(canvas, draw, 2, ry, chip_h, label, _cv_chip_color(when, now), W - 10)
            title_h = band_h - chip_h - (1 if i == n - 1 else 2)
            _cv_title(canvas, draw, summary, 4, ry + chip_h + 1, W - 8, title_h,
                      cap_h=chip_h + 5, bottom=(i == n - 1))   # consistent rows, no ballooning
    else:
        # Short panel: one event — the chip on the top edge, the title's ink on the
        # bottom one, both full width.
        when, all_day, summary = upcoming[0]
        chip_h = max(11, int(H * 0.42))
        label = _cv_chip_label(when, all_day, now, i18n, probe(chip_h, W - 10))
        _cv_chip(canvas, draw, 1, 0, chip_h, label, _cv_chip_color(when, now), W - 10)
        _cv_title(canvas, draw, summary, 2, chip_h + 1, W - 4, H - chip_h - 1, bottom=True)

    canvas.frame(img)
    return 120.0
