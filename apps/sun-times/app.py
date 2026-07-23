"""Sunrise / sunset / day length for the configured location (keyless: Open-Meteo).

Times track the location: Open-Meteo returns them in the place's own local time
(timezone=auto), just like the weather app — no separate timezone setting needed."""


# =============================================================================
# SHARED — the sun DATA: the location ladder and the one Open-Meteo daily call
# both surfaces read (sunrise, sunset, daylight seconds, the place's UTC offset).
# =============================================================================

def _latlon(settings, requests):
    """Global precise location, else geocode of the ZIP, else a Boston fallback."""
    lat = str(settings.get('location_lat', '') or '').strip()
    lon = str(settings.get('location_lon', '') or '').strip()
    if lat and lon:
        try:
            return float(lat), float(lon)
        except ValueError:
            pass
    zip_code = str(settings.get('zip_code', '02118') or '02118').strip()
    try:
        import re
        params = {'q': zip_code, 'format': 'json', 'limit': 1}
        if re.fullmatch(r'\d{5}', zip_code):     # a US ZIP — disambiguate (02118 also exists abroad)
            params['countrycodes'] = 'us'
        geo = requests.get('https://nominatim.openstreetmap.org/search', params=params,
                           headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'},
                           timeout=6).json()
        if geo:
            return float(geo[0]['lat']), float(geo[0]['lon'])
    except Exception:
        pass
    return 42.3601, -71.0589


def _sun_data(settings, requests, get_location=None):
    """Today's sun facts for the configured place. The platform's cached geocode
    first (one Nominatim query shared with weather and every other location app);
    our own ladder only off-host. Exceptions propagate — each view has its own
    offline face."""
    loc = get_location() if get_location is not None else None
    if isinstance(loc, dict) and loc.get('lat') is not None:
        lat, lon = float(loc['lat']), float(loc['lon'])
    else:
        lat, lon = _latlon(settings, requests)
    data = requests.get('https://api.open-meteo.com/v1/forecast',
                        params={'latitude': lat, 'longitude': lon,
                                'daily': 'sunrise,sunset,daylight_duration',
                                'timezone': 'auto', 'forecast_days': 1},
                        timeout=8).json()
    daily = data.get('daily', {})
    return {
        'sunrise': (daily.get('sunrise') or [None])[0],
        'sunset': (daily.get('sunset') or [None])[0],
        'daylight': int((daily.get('daylight_duration') or [0])[0] or 0),
        'utc_offset': int(data.get('utc_offset_seconds') or 0),
    }


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def _columns(pairs, cols, gap=3):
    """Two aligned columns — label flush left, value flush right — kept together as
    one CENTERED block rather than pinned to the wall's edges.

    format_lines centers each line, so the block is only as wide as its content plus
    a small gap: on a wide wall the label and its time sit together in the middle
    instead of stranded at opposite ends with a lake of empty space between them. The
    value column still lines up down the page (every line the same width). A narrow
    wall falls back to the full width, trimming the label, never the time.
    """
    pairs = [(str(left), str(right)) for left, right in pairs]
    rw = max((len(r) for _, r in pairs), default=0)
    lw = max((len(l) for l, _ in pairs), default=0)
    inner = min(cols, lw + gap + rw)
    lspace = max(1, inner - rw)                       # label column width, incl. the gap
    out = []
    for left, right in pairs:
        if len(left) > lspace - 1:
            left = left[:max(0, lspace - 1)]
        out.append((left.ljust(lspace) + right.rjust(rw))[:cols])
    return out


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, get_location=None):
    import requests
    from datetime import datetime
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "sun") if i18n is not None else s

    def u(k):                               # localized H/M duration suffix (Dutch U for uur, etc.)
        return i18n.unit(k) if i18n is not None else k

    def fmt_time(iso):                       # ISO is already the location's local time
        if not iso:
            return '--:--'
        dt = datetime.fromisoformat(str(iso))
        # AM/PM is English-only — everyone else gets 24h.
        if i18n is not None:
            return i18n.time(dt, ampm_space=False)
        return dt.strftime('%I:%M%p').lstrip('0')

    try:
        data = _sun_data(settings, requests, get_location)
        rise = fmt_time(data['sunrise'])
        sett = fmt_time(data['sunset'])
        secs = data['daylight']
        length = f'{secs // 3600}{u("H")}{(secs % 3600) // 60:02d}{u("M")}'
        if rows == 1:
            return [format_lines(f'{t("Up")} {rise} {t("Dn")} {sett}')]
        pairs = [(t('Sunrise'), rise), (t('Sunset'), sett)]
        if rows >= 3:
            pairs.append((t('Daylight'), length))
        return [format_lines(*_columns(pairs, cols))]
    except Exception:
        return [format_lines('Sun times', t('Offline'), '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# The day drawn as a sky: a dotted sun arc over the horizon line, the sun
# sitting where it actually is right now at the location (below the line before
# rise / after set), sunrise and sunset times anchored at the arc's feet, and
# the daylight length between them where the width allows. One Open-Meteo call
# per quarter hour, redraws every couple of minutes. Black background.
# =============================================================================

_SUN_COL = (255, 198, 64)        # the sun and its rays
_SUN_DOWN = (110, 96, 70)        # the sun parked below the horizon
_ARC_COL = (88, 94, 108)         # the dotted day arc
_HORIZON = (70, 80, 96)          # the horizon line
_RISE_COL = (255, 178, 44)       # sunrise time
_SET_COL = (255, 122, 62)        # sunset time
_TXT_COL = (245, 245, 248)
_SUB_COL = (132, 136, 148)


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 8px — smaller renders wrong-reading glyphs)."""
    size = max(8, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 8 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (offline / no data)."""
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
    draw.text(((W - f1.getlength(line1)) / 2.0, y - b1[1]), line1, font=f1, fill=_TXT_COL)
    if line2:
        y += h1 + gap
        b2 = f2.getbbox(line2)
        draw.text(((W - f2.getlength(line2)) / 2.0, y - b2[1]), line2, font=f2, fill=_SUB_COL)
    return img


def _cached_sun(settings, get_location):
    """_sun_data with a 15-minute memory, so a 2-minute redraw cadence doesn't
    become a 2-minute API cadence."""
    import time
    import requests
    st = getattr(_cached_sun, '_state', None)
    if st is None:
        st = {'ts': 0.0, 'data': None}
        setattr(_cached_sun, '_state', st)
    now = time.time()
    if st['data'] is None or now - st['ts'] > 900:
        st['data'] = _sun_data(settings, requests, get_location)
        st['ts'] = now
    return st['data']


def fetch_matrix(settings, canvas, i18n=None, get_location=None):
    import math
    from datetime import datetime, timedelta, timezone
    from PIL import ImageDraw

    def t(s):
        return i18n.t(s, "sun") if i18n is not None else s

    def u(k):
        return i18n.unit(k) if i18n is not None else k

    def fmt_time(dt):
        if i18n is not None:
            return i18n.time(dt, ampm_space=False)
        return dt.strftime('%I:%M%p').lstrip('0')

    try:
        data = _cached_sun(settings, get_location)
        rise = datetime.fromisoformat(str(data['sunrise']))
        sett = datetime.fromisoformat(str(data['sunset']))
    except Exception:
        canvas.frame(_cv_message(canvas, ImageDraw, t('Sun times').upper(), t('Offline').upper()))
        return 60.0

    # "Now" in the location's own clock — the API talks local time throughout.
    now_loc = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=data['utc_offset'])
    day_len = max(1.0, (sett - rise).total_seconds())
    f = (now_loc - rise).total_seconds() / day_len          # <0 before rise, >1 after set

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    time_h = max(8, int(H * (0.22 if H >= 48 else 0.30)))
    horizon_y = H - time_h - 3
    peak_y = 1                       # the arc's crown dot rides the top row
    x0, x1 = 5, W - 6
    span = x1 - x0

    # The horizon, with the day arc dotted over it.
    draw.line([(1, horizon_y), (W - 2, horizon_y)], fill=_HORIZON)
    steps = max(16, span // 4)
    for i in range(steps + 1):
        if i % 2:
            continue                                        # dotted
        a = i / steps
        x = x0 + a * span
        y = horizon_y - math.sin(a * math.pi) * (horizon_y - peak_y)
        draw.point((int(round(x)), int(round(y))), fill=_ARC_COL)

    # The sky's centerpiece label: BY DAY the day length; BY NIGHT a countdown to
    # sunrise — the number a night-time glance actually wants. Skipped on the
    # narrowest panels where it would tangle with the arc.
    night = not (0.0 <= f <= 1.0)
    if W >= 96:
        if night:
            nxt = rise if now_loc < rise else rise + timedelta(days=1)
            left = max(0, int((nxt - now_loc).total_seconds()))
            mid = f'{t("Rise in").upper()} {left // 3600}{u("H")}{(left % 3600) // 60:02d}{u("M")}'
            col = _RISE_COL
        else:
            secs = data['daylight']
            mid = f'{secs // 3600}{u("H")}{(secs % 3600) // 60:02d}{u("M")}'
            col = _SUB_COL
        mf = _cv_fit(canvas, mid, int(W * 0.62), max(8, int(H * 0.20)))
        mb = mf.getbbox(mid)
        draw.text(((W - mf.getlength(mid)) / 2.0, horizon_y - 4 - (mb[3] - mb[1]) - mb[1]),
                  mid, font=mf, fill=col)

    # The sun where it actually is: on the arc during the day; at night a dim
    # half-disc sunk into the horizon at the side it set (or will rise) on,
    # under a scatter of faint stars so the sky reads as night, not as empty.
    r = max(2, H // 14)
    if not night:
        sx = x0 + f * span
        sy = horizon_y - math.sin(f * math.pi) * (horizon_y - peak_y)
        # Near noon the track would carry the disc over the top edge — the sun
        # rides just inside the dome instead, rays kissing the top row.
        sy = max(sy, r + 4.0)
        draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=_SUN_COL)
        for ang in range(0, 360, 45):                       # rays only when it's up
            dx, dy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            draw.line([(sx + dx * (r + 2), sy + dy * (r + 2)),
                       (sx + dx * (r + 3), sy + dy * (r + 3))], fill=_SUN_COL)
    else:
        # Fixed pseudo-random star field (deterministic — no flicker between redraws).
        for i in range(max(8, W // 12)):
            h = (i * 2654435761) & 0xFFFFFFFF
            sx_ = 2 + (h % (W - 4))
            sy_ = 1 + ((h >> 11) % max(1, horizon_y - 5))
            bright = (230, 230, 240) if (h >> 22) % 5 == 0 else (120, 122, 132)
            draw.point((sx_, sy_), fill=bright)
        sx = x0 if f < 0 else x1
        draw.ellipse([sx - r, horizon_y - r, sx + r, horizon_y + r], fill=_SUN_DOWN)
        draw.rectangle([sx - r - 1, horizon_y + 1, sx + r + 1, horizon_y + r + 1],
                       fill=(0, 0, 0))                      # sunk below the line
        draw.line([(1, horizon_y), (W - 2, horizon_y)], fill=_HORIZON)

    # Rise and set times at the arc's feet. A narrow panel drops the AM/PM tag —
    # a sunrise is morning and a sunset evening by definition — buying the
    # digits two font sizes.
    rtxt, stxt = fmt_time(rise), fmt_time(sett)
    if W < 96:
        for tag in ('AM', 'PM'):
            rtxt = rtxt[:-2] if rtxt.endswith(tag) else rtxt
            stxt = stxt[:-2] if stxt.endswith(tag) else stxt
    tf = _cv_fit(canvas, rtxt if len(rtxt) >= len(stxt) else stxt, int(W * 0.44), time_h)
    rb, sb = tf.getbbox(rtxt), tf.getbbox(stxt)
    ty = H - 1 - max(rb[3] - rb[1], sb[3] - sb[1])   # digits sit on the bottom row
    draw.text((2, ty - rb[1]), rtxt, font=tf, fill=_RISE_COL)
    draw.text((W - 2 - tf.getlength(stxt), ty - sb[1]), stxt, font=tf, fill=_SET_COL)

    canvas.frame(img)
    return 120.0                    # the sun crawls along the arc a pixel or two a minute
