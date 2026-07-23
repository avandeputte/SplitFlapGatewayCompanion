# =============================================================================
# SHARED — the ISS data: where it is and who is aboard (open-notify, keyless).
# Both surfaces read the same two endpoints, so a wall and a panel always put
# the station at the same coordinates with the same crew.
# =============================================================================

def _iss_position():
    """The station's position document, raw. Raises on a network failure."""
    import requests
    return requests.get('http://api.open-notify.org/iss-now.json', timeout=10).json()


def _astros():
    """The who-is-in-space document, raw. Raises on a network failure."""
    import requests
    return requests.get('http://api.open-notify.org/astros.json', timeout=10).json()


def _coord(v, hemis, dec):
    """A hemisphere letter instead of a sign: "41.00S 123.45W" is 14 wide where
    the API's raw "LAT -41.0050 LON 123.4506" was 25 — which no small wall
    showed; it was silently cut mid-longitude."""
    f = float(v)
    return f'{abs(f):.{dec}f}{hemis[0] if f >= 0 else hemis[1]}'


# =============================================================================
# SPLIT-FLAP — fetch() and the overhead/crew trigger, unique to the flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    def t(s):
        return i18n.t(s, "space") if i18n is not None else s

    try:
        pos = _iss_position()
        ppl = _astros()
        lat = pos['iss_position']['latitude']
        lon = pos['iss_position']['longitude']
        num = ppl['number']
        rows = get_rows()

        if rows == 1:
            return [format_lines(f'ISS {_coord(lat, "NS", 0)} {_coord(lon, "EW", 0)}')]
        pos_line = f'{_coord(lat, "NS", 2)} {_coord(lon, "EW", 2)}'
        if rows == 2:
            return [format_lines('ISS tracker', pos_line)]
        if rows >= 4:
            # astros.json is already fetched above and carries who is aboard — the
            # position API has nothing else to give (no altitude, no velocity).
            cols = get_cols()
            crew = [str(pp.get('name', ''))[:cols]
                    for pp in (ppl.get('people') or []) if pp.get('craft') == 'ISS']
            body = [pos_line, f'{num} ' + t('In space')]
            return [format_lines('ISS tracker', *body, *crew[:max(0, rows - 3)])]
        return [format_lines('ISS tracker', pos_line, f'{num} ' + t('In space'))]
    except Exception:
        return [format_lines('ISS tracker', t('Error'), t('API fail'))]


def trigger(settings, conditions, get_location=None):
    """Fire when ISS is overhead, or on crew milestone."""
    import requests, math
    from datetime import datetime
    import pytz

    condition_type = conditions.get('condition_type', 'overhead')

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'last_crew_count': None, 'last_crew_names': None}
        setattr(trigger, '_state', state)

    try:
        if condition_type in ('overhead', 'visible_pass'):
            loc_lat = settings.get('location_lat', '')
            loc_lon = settings.get('location_lon', '')
            if loc_lat and loc_lon:
                user_lat, user_lon = float(loc_lat), float(loc_lon)
            elif get_location is not None and get_location().get('lat') is not None:
                # The platform's cached geocode of the configured location — no
                # need to run our own Nominatim query on every trigger poll.
                loc = get_location()
                user_lat, user_lon = float(loc['lat']), float(loc['lon'])
            else:
                # No injected get_location (a bare host): geocode the
                # ZIP ourselves.
                zip_code = settings.get('zip_code', '02118')
                geo = requests.get(
                    f'https://nominatim.openstreetmap.org/search?q={zip_code}&format=json&limit=1',
                    timeout=5, headers={'User-Agent': 'SplitFlapGatewayCompanion/1.0'}
                ).json()
                if not geo:
                    return False
                user_lat = float(geo[0]['lat'])
                user_lon = float(geo[0]['lon'])

            pos = requests.get('http://api.open-notify.org/iss-now.json', timeout=5).json()
            iss_lat = float(pos['iss_position']['latitude'])
            iss_lon = float(pos['iss_position']['longitude'])

            R = 6371
            dlat = math.radians(iss_lat - user_lat)
            dlon = math.radians(iss_lon - user_lon)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(user_lat)) * math.cos(math.radians(iss_lat)) * math.sin(dlon/2)**2
            dist = R * 2 * math.asin(math.sqrt(a))

            if dist >= 500:
                return False

            if condition_type == 'visible_pass':
                # Check nighttime and clear sky
                try:
                    tz = pytz.timezone(settings.get('timezone') or 'UTC')
                except Exception:
                    tz = pytz.utc
                hour = datetime.now(tz).hour
                is_night = hour >= 20 or hour <= 5

                weather = requests.get(
                    f'https://api.open-meteo.com/v1/forecast?latitude={user_lat}&longitude={user_lon}'
                    '&current=cloud_cover',
                    timeout=5
                ).json()
                cloud_cover = weather.get('current', {}).get('cloud_cover', 100)
                is_clear = cloud_cover <= 30

                return is_night and is_clear

            return True  # overhead, no visibility check

        elif condition_type == 'crew_change':
            ppl = requests.get('http://api.open-notify.org/astros.json', timeout=5).json()
            iss_crew = [p['name'] for p in ppl.get('people', []) if p.get('craft') == 'ISS']
            crew_set = frozenset(iss_crew)

            if state['last_crew_names'] is None:
                state['last_crew_names'] = crew_set
                return False
            if crew_set != state['last_crew_names']:
                state['last_crew_names'] = crew_set
                return True

    except Exception:
        raise
    return False


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A tracker view: a lat/lon world grid with the station's sinusoidal ground
# track (51.6° inclination) threaded through its live position, the ISS as an
# amber crosshair marker, coordinates and crew count beside or beneath it.
# Black background, no gradient.
# =============================================================================

_WHITE = (240, 240, 244)
_GRAY = (150, 150, 158)
_CYAN = (90, 200, 250)                      # the coordinates
_AMBER = (255, 200, 60)                     # the station marker
_GRID = (38, 48, 66)                        # the map graticule
_EQUATOR = (58, 74, 100)
_TRACK = (0, 110, 150)                      # the ground-track sinusoid
_INCL = 51.6                                # ISS orbital inclination, degrees


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


def _cv_shadow(draw, x, y, text, font, fill):
    """Text with a 1px dark outline on all sides, so it stays legible over the map."""
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0), anchor='la')
    draw.text((x, y), text, font=font, fill=fill, anchor='la')


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (API unreachable)."""
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


def _cv_map(draw, x0, y0, mw, mh, lat, lon):
    """The world grid, the ground track through (lat, lon), and the station marker.
    Equirectangular: the whole earth in the box."""
    import math

    def xy(la, lo):
        return (x0 + (lo + 180.0) / 360.0 * (mw - 1),
                y0 + (90.0 - la) / 180.0 * (mh - 1))

    # graticule: meridians every 60°, parallels every 30°, the equator a shade brighter
    for lo in range(-120, 180, 60):
        x = int(round(xy(0, lo)[0]))
        draw.line([(x, y0), (x, y0 + mh - 1)], fill=_GRID)
    for la in (-60, -30, 30, 60):
        y = int(round(xy(la, 0)[1]))
        draw.line([(x0, y), (x0 + mw - 1, y)], fill=_GRID)
    eq = int(round(xy(0, 0)[1]))
    draw.line([(x0, eq), (x0 + mw - 1, eq)], fill=_EQUATOR)
    draw.rectangle([x0, y0, x0 + mw - 1, y0 + mh - 1], outline=_GRID)

    # the ground track: lat = incl * sin(k), threaded through the live fix (dotted)
    k0 = math.asin(max(-1.0, min(1.0, lat / _INCL)))
    for px in range(0, mw, 2):
        dlon = (px / (mw - 1)) * 360.0 - 180.0
        k = k0 + math.radians(dlon)
        tla = _INCL * math.sin(k)
        tlo = lon + dlon
        tlo = ((tlo + 180.0) % 360.0) - 180.0
        tx, ty = xy(tla, tlo)
        draw.point((int(round(tx)), int(round(ty))), fill=_TRACK)

    # the station: an amber crosshair with a white heart
    mx, my = (int(round(v)) for v in xy(lat, lon))
    arm = max(2, mh // 12)
    draw.line([(mx - arm, my), (mx + arm, my)], fill=_AMBER)
    draw.line([(mx, my - arm), (mx, my + arm)], fill=_AMBER)
    draw.point((mx, my), fill=_WHITE)


def fetch_matrix(settings, canvas, i18n=None):
    """Draw the live fix on a world grid with the ground track. The station moves about four
    degrees a minute, so a 15s redraw keeps the marker honest without hammering the API."""
    from PIL import ImageDraw

    try:
        pos = _iss_position()
        lat = float(pos['iss_position']['latitude'])
        lon = float(pos['iss_position']['longitude'])
    except Exception:
        canvas.frame(_cv_message(canvas, ImageDraw, 'ISS TRACKER', 'API FAIL'))
        return 60.0

    crew = ''
    try:
        num = _astros().get('number')
        if num:
            crew = f'CREW {num}'
    except Exception:
        pass

    coords = f'{_coord(lat, "NS", 1)} {_coord(lon, "EW", 1)}'
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    if W >= 112 and H >= 48:
        # Map on the left (2:1, the earth's own shape) running edge to edge — its
        # frame owns the panel's first and last rows — text column on the right.
        mh = H
        mw = min(int(W * 0.62), mh * 2 + 8)
        _cv_map(draw, 0, 0, mw, mh, lat, lon)
        tx = mw + 5
        tw = W - 3 - tx
        title = 'ISS'
        tf = _cv_fit(canvas, title, tw, max(10, int(H * 0.3)))
        tb = tf.getbbox(title)
        draw.text((tx, 1 - tb[1]), title, font=tf, fill=_WHITE)
        y = 1 + (tb[3] - tb[1]) + 4
        cf = _cv_fit(canvas, _coord(lat, "NS", 1), tw, max(7, int(H * 0.17)))
        for ln in (_coord(lat, "NS", 1), _coord(lon, "EW", 1)):
            b = cf.getbbox(ln)
            draw.text((tx, y - b[1]), ln, font=cf, fill=_CYAN)
            y += (b[3] - b[1]) + 3
        if crew:
            bf = _cv_fit(canvas, crew, tw, max(7, int(H * 0.15)))
            bb = bf.getbbox(crew)
            if (bb[3] - bb[1]) >= 6:
                draw.text((tx, H - 1 - (bb[3] - bb[1]) - bb[1]), crew, font=bf, fill=_AMBER)
    else:
        # Compact: the map fills the panel edge to edge, the coordinates ride its
        # lower edge. Degrade the caption rather than the type: full precision, then
        # whole degrees, then the bare coordinates — the first that stays readable wins.
        _cv_map(draw, 0, 0, W, H, lat, lon)
        c0 = f'{_coord(lat, "NS", 0)} {_coord(lon, "EW", 0)}'
        for line in (f'ISS {coords}', f'ISS {c0}', c0):
            lf = _cv_fit(canvas, line, W - 6, max(7, int(H * 0.24)))
            lb = lf.getbbox(line)
            if (lb[3] - lb[1]) >= 6:
                break
        _cv_shadow(draw, (W - lf.getlength(line)) / 2.0, H - 2 - (lb[3] - lb[1]) - lb[1],
                   line, lf, _WHITE)

    canvas.frame(img)
    return 15.0
