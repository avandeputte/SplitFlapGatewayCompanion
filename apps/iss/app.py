def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests

    def t(s):
        return i18n.t(s, "space") if i18n is not None else s

    try:
        pos = requests.get('http://api.open-notify.org/iss-now.json', timeout=10).json()
        ppl = requests.get('http://api.open-notify.org/astros.json', timeout=10).json()
        lat = pos['iss_position']['latitude']
        lon = pos['iss_position']['longitude']
        num = ppl['number']
        rows = get_rows()

        # A hemisphere letter instead of a sign: "41.00S 123.45W" is 14 wide where
        # the API's raw "LAT -41.0050 LON 123.4506" was 25 — which no small wall
        # showed; it was silently cut mid-longitude.
        def coord(v, hemis, dec):
            f = float(v)
            return f'{abs(f):.{dec}f}{hemis[0] if f >= 0 else hemis[1]}'

        if rows == 1:
            return [format_lines(f'ISS {coord(lat, "NS", 0)} {coord(lon, "EW", 0)}')]
        pos_line = f'{coord(lat, "NS", 2)} {coord(lon, "EW", 2)}'
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
                # Off a companion host (splitflap-os injects nothing): geocode the
                # ZIP ourselves.
                zip_code = settings.get('zip_code', '02118')
                geo = requests.get(
                    f'https://nominatim.openstreetmap.org/search?q={zip_code}&format=json&limit=1',
                    timeout=5, headers={'User-Agent': 'SplitFlapOS/1.0'}
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
