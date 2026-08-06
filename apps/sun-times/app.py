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
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
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


def _cv_sky_ops(canvas, data, rise, sett, now_loc, f, i18n, W, H):
    """The sky as on-device DRAW OPS — the gtext twin of the PIL path below, for the
    LCD (manifest ``lcd_ops``): AA circles, lines and scalable type drawn by the wall
    at native resolution instead of a 256x160 pixel frame upscaled x5. Same scene:
    dotted arc over the horizon, the sun (or stars and a sunk disc) where the day
    actually stands, day length / rise countdown mid-sky, times at the arc's feet."""
    import math
    from datetime import timedelta

    def t(s):
        return i18n.t(s, "sun") if i18n is not None else s

    def u(k):
        return i18n.unit(k) if i18n is not None else k

    def fmt_time(dt):
        if i18n is not None:
            return i18n.time(dt, ampm_space=False)
        return dt.strftime('%I:%M%p').lstrip('0')

    aa = bool(getattr(canvas, 'aa_ok', False))
    canvas.clear((0, 0, 0))

    time_h = max(8, int(H * 0.22))
    horizon_y = H - time_h - max(3, int(H * 0.02))
    hz_t = max(1, int(round(H / 160)))              # stroke weight, 1px per 160 rows
    rd = max(1, int(H * 0.006))                     # an arc dot's radius
    peak_y = rd + 1                                 # the crown dot rides the top row
    x0 = max(5, int(W * 0.02))
    x1 = W - 1 - x0
    span = x1 - x0

    # The horizon, with the day arc dotted over it.
    canvas.line(1, horizon_y, W - 2, horizon_y, color=_HORIZON, t=hz_t)
    steps = max(16, span // max(4, int(W * 0.016)))
    for i in range(steps + 1):
        if i % 2:
            continue                                # dotted
        a = i / steps
        x = x0 + a * span
        y = horizon_y - math.sin(a * math.pi) * (horizon_y - peak_y)
        canvas.circle(int(round(x)), int(round(y)), rd, color=_ARC_COL, fill=True, aa=aa)

    # Mid-sky label: day length by day, the countdown to sunrise by night.
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
        msz = canvas.fit_gtext(mid, int(W * 0.62), max(8, int(H * 0.20)))
        canvas.gtext(W // 2, horizon_y - max(4, int(H * 0.025)), mid, color=col,
                     size=msz, align='center', valign='ink-bottom')

    # The sun where it actually is — on the arc by day, sunk into the horizon at
    # the side it set (or will rise) on by night, under the star field.
    r = max(2, H // 14)
    if not night:
        ray_in = r + max(2, int(r * 0.2))
        ray_out = r + max(3, int(r * 0.45))
        sx = x0 + f * span
        sy = horizon_y - math.sin(f * math.pi) * (horizon_y - peak_y)
        sy = max(sy, ray_out + 2.0)                 # rays kiss the top row near noon
        canvas.circle(sx, sy, r, color=_SUN_COL, fill=True, aa=aa)
        for ang in range(0, 360, 45):               # rays only when it's up
            dx, dy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
            canvas.line(sx + dx * ray_in, sy + dy * ray_in,
                        sx + dx * ray_out, sy + dy * ray_out,
                        color=_SUN_COL, t=max(1, int(r * 0.12)))
    else:
        # The same fixed pseudo-random star field (deterministic — no flicker).
        rs = max(1, int(H * 0.003))
        for i in range(max(8, W // 12)):
            h = (i * 2654435761) & 0xFFFFFFFF
            sx_ = 2 + (h % (W - 4))
            sy_ = 1 + ((h >> 11) % max(1, horizon_y - 5))
            bright = (230, 230, 240) if (h >> 22) % 5 == 0 else (120, 122, 132)
            canvas.circle(sx_, sy_, rs, color=bright, fill=True, aa=aa)
        sx = x0 if f < 0 else x1
        canvas.circle(sx, horizon_y, r, color=_SUN_DOWN, fill=True, aa=aa)
        canvas.rect(sx - r - 1, horizon_y + 1, 2 * r + 3, r + 2,
                    (0, 0, 0), fill=True)           # sunk below the line
        canvas.line(1, horizon_y, W - 2, horizon_y, color=_HORIZON, t=hz_t)

    # Rise and set times at the arc's feet, digits sitting on the bottom row.
    rtxt, stxt = fmt_time(rise), fmt_time(sett)
    mt = max(2, int(W * 0.008))
    tsz = canvas.fit_gtext(rtxt if len(rtxt) >= len(stxt) else stxt,
                           int(W * 0.44), int(time_h * 1.25))
    canvas.gtext(mt, H - 1, rtxt, color=_RISE_COL, size=tsz, valign='ink-bottom')
    canvas.gtext(W - mt, H - 1, stxt, color=_SET_COL, size=tsz, align='right',
                 valign='ink-bottom')
    canvas.show()


def fetch_canvas(settings, canvas, i18n=None, get_location=None):
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
        canvas.frame(canvas.message(t('Sun times').upper(), t('Offline').upper(),
                                    color=_TXT_COL, dim=_SUB_COL))
        return 60.0

    # "Now" in the location's own clock — the API talks local time throughout.
    now_loc = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=data['utc_offset'])
    day_len = max(1.0, (sett - rise).total_seconds())
    f = (now_loc - rise).total_seconds() / day_len          # <0 before rise, >1 after set

    W, H = canvas.width, canvas.height
    if getattr(canvas, 'can_gtext', False) and H >= 96:
        # The big-panel path: live ops at native resolution (crisp AA sky + TTF times).
        _cv_sky_ops(canvas, data, rise, sett, now_loc, f, i18n, W, H)
        return 120.0
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
        mf = canvas.fit_font(mid, int(W * 0.62), max(8, int(H * 0.20)))
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
    tf = canvas.fit_font(rtxt if len(rtxt) >= len(stxt) else stxt, int(W * 0.44), time_h)
    rb, sb = tf.getbbox(rtxt), tf.getbbox(stxt)
    ty = H - 1 - max(rb[3] - rb[1], sb[3] - sb[1])   # digits sit on the bottom row
    draw.text((2, ty - rb[1]), rtxt, font=tf, fill=_RISE_COL)
    draw.text((W - 2 - tf.getlength(stxt), ty - sb[1]), stxt, font=tf, fill=_SET_COL)

    canvas.frame(img)
    return 120.0                    # the sun crawls along the arc a pixel or two a minute
