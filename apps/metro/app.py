# =============================================================================
# SHARED — the MBTA data: next arrival per direction and where each direction
# goes. Both surfaces read these, so a wall and a panel always show the same
# trains in the same order.
# =============================================================================

def _next_arrivals(stop, route):
    """The first predicted arrival per direction, as ``{direction_id: minutes}``.
    Raises on a network failure — the caller decides what an error looks like."""
    import requests
    from datetime import datetime, timezone
    r = requests.get(
        'https://api-v3.mbta.com/predictions',
        params={'filter[stop]': stop, 'filter[route]': route, 'sort': 'arrival_time'},
        timeout=10
    ).json()
    preds = {}
    now = datetime.now(timezone.utc)
    for p in r.get('data', []):
        arr = p['attributes'].get('arrival_time')
        d_id = p['attributes'].get('direction_id', 0)
        if arr and d_id not in preds:
            dt = datetime.fromisoformat(arr)
            preds[d_id] = max(0, int((dt - now).total_seconds() // 60))
    return preds


def _destinations(route):
    """Where each direction actually GOES — "Forest Hills", not "Dir0". The route
    carries a destination per direction_id; it never changes, so look it up once
    and cache it. An empty mapping is the fallback if the lookup ever fails."""
    import requests
    cache = getattr(_destinations, '_cache', None)
    if cache is None:
        cache = {}
        setattr(_destinations, '_cache', cache)
    if route not in cache:
        try:
            rt = requests.get(f'https://api-v3.mbta.com/routes/{route}', timeout=8).json()
            dd = (rt.get('data') or {}).get('attributes', {}).get('direction_destinations') or []
            cache[route] = {i: name for i, name in enumerate(dd) if name}
        except Exception:
            cache[route] = {}
    return cache[route]


# =============================================================================
# SPLIT-FLAP — fetch(), its column layout, and the arrival/alert trigger.
# =============================================================================

def _columns(pairs, cols, gap=3):
    """Two aligned columns — destination flush left, time flush right — kept together
    as one CENTERED block rather than pinned to the wall's edges.

    format_lines centers each line, so the block is only as wide as its content plus a
    small gap: on a wide wall the destination and its time sit together in the middle
    instead of stranded at opposite ends. The times still line up in a column (every
    line the same width). A narrow wall falls back to the full width, trimming the
    destination, never the time."""
    pairs = [(str(left), str(right)) for left, right in pairs]
    rw = max((len(r) for _, r in pairs), default=0)
    lw = max((len(l) for l, _ in pairs), default=0)
    inner = min(cols, lw + gap + rw)
    lspace = max(1, inner - rw)                       # destination column, incl. the gap
    out = []
    for left, right in pairs:
        if len(left) > lspace - 1:
            left = left[:max(0, lspace - 1)]
        out.append((left.ljust(lspace) + right.rjust(rw))[:cols])
    return out


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    def t(s):
        return i18n.t(s, "transit") if i18n is not None else s

    # Defaults match the manifest (place-NSTAT = North Station), so a blank
    # setting rides the same platform the settings dialog shows.
    stop = settings.get('mbta_stop', 'place-NSTAT')
    route = settings.get('mbta_route', 'Orange')
    rows, cols = get_rows(), get_cols()
    try:
        mins = _next_arrivals(stop, route)
        preds = {d_id: f'{m} {t("min")}' for d_id, m in mins.items()}
        dests = _destinations(route)

        def dname(d_id):
            return dests.get(d_id) or f'Dir{d_id}'

        no_color = settings.get('disable_colors', 'no') == 'yes'
        header = f'{route} {t("Line")}' if no_color else f'🟧 {route} {t("Line")} 🟧'
        line0 = preds.get(0, t('No data'))
        line1 = preds.get(1, t('No data'))
        if rows == 1:
            return [format_lines(f'{dname(0)} {line0}  {dname(1)} {line1}'[:cols])]
        pairs = [(dname(0), line0), (dname(1), line1)]
        if rows == 2:
            return [format_lines(*_columns(pairs, cols))]
        return [format_lines(header, *_columns(pairs, cols))]
    except Exception:
        return [format_lines('Metro', t('Error'), t('Check config'))]


def trigger(settings, conditions):
    """Fire when the next train is arriving within the configured window, or on service alerts."""
    import requests
    from datetime import datetime, timezone

    condition_type = conditions.get('condition_type', 'arriving')
    minutes = int(conditions.get('minutes', 5))
    direction = conditions.get('direction', 'either')
    stop = settings.get('mbta_stop', 'place-NSTAT')
    route = settings.get('mbta_route', 'Orange')

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'seen_alert_ids': set()}
        setattr(trigger, '_state', state)

    try:
        if condition_type == 'arriving':
            r = requests.get(
                'https://api-v3.mbta.com/predictions',
                params={'filter[stop]': stop, 'filter[route]': route, 'sort': 'arrival_time'},
                timeout=10
            ).json()
            now = datetime.now(timezone.utc)
            for p in r.get('data', []):
                arr = p['attributes'].get('arrival_time')
                d_id = p['attributes'].get('direction_id', 0)
                if not arr:
                    continue
                if direction == '0' and d_id != 0:
                    continue
                if direction == '1' and d_id != 1:
                    continue
                dt = datetime.fromisoformat(arr)
                mins_away = (dt - now).total_seconds() / 60
                if 0 <= mins_away <= minutes:
                    return True

        elif condition_type == 'alert':
            r = requests.get(
                'https://api-v3.mbta.com/alerts',
                params={'filter[route]': route, 'filter[stop]': stop},
                timeout=10
            ).json()
            for alert in r.get('data', []):
                aid = alert.get('id', '')
                effect = alert.get('attributes', {}).get('effect', '')
                # Only fire for service-affecting alerts
                if effect in ('DELAY', 'SUSPENSION', 'SHUTTLE', 'STOP_CLOSURE', 'DETOUR'):
                    if aid not in state['seen_alert_ids']:
                        state['seen_alert_ids'].add(aid)
                        return True
            # Prune old alert IDs
            if len(state['seen_alert_ids']) > 200:
                state['seen_alert_ids'] = set(list(state['seen_alert_ids'])[-100:])

    except Exception:
        raise
    return False


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A departure board: the line's own color as a header bar, then one row per
# direction — destination left, minutes right, the minutes color-coded by
# urgency (due now, soon, later). Black background, no gradient.
# =============================================================================

# The MBTA's own line colors, keyed by the route id's first word.
_LINE_COLORS = {
    'orange': (237, 139, 0),
    'red': (218, 41, 28),
    'blue': (0, 61, 165),
    'green': (0, 132, 61),
    'mattapan': (218, 41, 28),
    'silver': (124, 135, 142),
    'cr': (128, 39, 108),                   # commuter rail purple
}
_STEEL = (100, 110, 125)                    # an unknown route
_WHITE = (240, 240, 244)
_GRAY = (150, 150, 158)
_DUE = (240, 70, 60)                        # <= 2 min: run
_SOON = (255, 180, 60)                      # <= 5 min: go now
_LATER = (100, 220, 120)                    # time to spare


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 8px)."""
    size = max(8, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 8 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
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
    """A quiet two-line message (API unreachable / bad config)."""
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


def _cv_line_color(route):
    return _LINE_COLORS.get(str(route).lower().split('-')[0], _STEEL)


def _cv_minutes(mins):
    """(text, color) for a minutes-away value — 'DUE' red under two minutes."""
    if mins is None:
        return '--', _GRAY
    if mins <= 1:
        return 'DUE', _DUE
    if mins <= 2:
        return f'{mins}', _DUE
    if mins <= 5:
        return f'{mins}', _SOON
    return f'{mins}', _LATER


def fetch_matrix(settings, canvas):
    """Draw the stop as a departure board: line-color header, a row per direction with the
    destination and color-coded minutes. Predictions move by the minute — redraw every 30s."""
    from PIL import ImageDraw

    stop = settings.get('mbta_stop', 'place-NSTAT')
    route = settings.get('mbta_route', 'Orange')
    try:
        mins = _next_arrivals(stop, route)
        dests = _destinations(route)
    except Exception:
        canvas.frame(_cv_message(canvas, ImageDraw, 'METRO', 'CHECK CONFIG'))
        return 60.0

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    color = _cv_line_color(route)

    # Header: the line's color as a full-width bar with the route's name on it.
    title = f'{route} LINE'.replace('-', ' ').upper() if W >= 96 else str(route).upper()
    bar_h = max(9, int(H * 0.24))
    draw.rectangle([0, 0, W - 1, bar_h - 1], fill=color)
    tf = _cv_fit(canvas, title, W - 8, bar_h - 3)
    tb = tf.getbbox(title)
    draw.text(((W - tf.getlength(title)) / 2.0, (bar_h - 1 - (tb[3] - tb[1])) / 2.0 - tb[1]),
              title, font=tf, fill=_WHITE)

    # One row per direction: destination left, minutes right, urgency-colored.
    rows = []
    for d_id in (0, 1):
        dest = str(dests.get(d_id) or f'Dir {d_id}').upper()
        rows.append((dest, *_cv_minutes(mins.get(d_id))))

    # Two full-height bands under the bar: the first starts right beneath it, the
    # second runs to the panel's last row — no leftover strip of dark LEDs.
    area_top = bar_h + 1
    mid = area_top + (H - area_top) // 2
    unit = ' MIN' if W >= 128 else ''
    row_ink = []                                # (ink top, ink bottom) per row
    for i, (dest, mtxt, mcol) in enumerate(rows):
        ry, rb_ = (area_top, mid - 1) if i == 0 else (mid + 1, H - 1)
        row_h = rb_ - ry + 1
        mm = mtxt if (mtxt in ('DUE', '--') or not unit) else f'{mtxt}{unit}'
        mf = _cv_fit(canvas, mm, int(W * 0.4), row_h - 2)
        mb = mf.getbbox(mm)
        mw = mf.getlength(mm)
        # Centered in the band, but the last band's ink is pulled down to the
        # panel's edge (the bbox habitually over-reports a pixel at the bottom).
        my = ry + (row_h - (mb[3] - mb[1])) / 2.0
        if i == 1:
            my = max(my, rb_ - (mb[3] - mb[1]))
        draw.text((W - 3 - mw, my - mb[1]), mm, font=mf, fill=mcol)
        # The whole destination at the largest size that fits the row; only when even
        # that would drop below readable, hold a readable size and ellipsise instead.
        avail = W - 8 - mw
        df = _cv_fit(canvas, dest, avail, row_h - 2)
        dtext = dest
        if (df.getbbox(dest)[3] - df.getbbox(dest)[1]) < 6 or df.getlength(dest) > avail:
            df = _cv_fit(canvas, '0', avail, 7)              # readable floor; ellipsise, never overflow
            dtext = _cv_ellipsis(df, dest, avail)
        db = df.getbbox(dtext or '0')
        dy = ry + (row_h - (db[3] - db[1])) / 2.0
        if i == 1:
            dy = max(dy, rb_ - (db[3] - db[1]))
        draw.text((3, dy - db[1]), dtext, font=df, fill=_WHITE)
        row_ink.append((min(my, dy),
                        max(my + (mb[3] - mb[1]), dy + (db[3] - db[1])) - 1))
    # The divider sits midway between the first row's ink and the second's —
    # centered on the air between them, not on the band arithmetic.
    rule_y = int(round((row_ink[0][1] + row_ink[1][0]) / 2.0))
    draw.line([(2, rule_y), (W - 3, rule_y)], fill=(45, 50, 60))

    canvas.frame(img)
    return 30.0
