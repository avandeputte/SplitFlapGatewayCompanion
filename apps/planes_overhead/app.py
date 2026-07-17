def fetch(settings, format_lines, get_rows, get_cols, get_location=None):
    import math
    import re
    import time
    from datetime import datetime, timezone

    import requests

    # Keep provider polling state inside this plugin so settings changes
    # can force an immediate refresh without server-side cache hooks.
    state = getattr(fetch, "_state", None)
    if state is None:
        state = {
            "last_sig": None,
            "last_polled_at": 0.0,
            "flights": [],
            "last_error_provider": None,
            "last_error": None,
            "opensky_token": None,
            "opensky_token_exp": 0.0,
        }
        setattr(fetch, "_state", state)

    def _to_float(value, default):
        try:
            return float(value)
        except Exception:
            return default

    def _to_int(value, default):
        try:
            return int(value)
        except Exception:
            return default

    def _parse_lat_lon(raw):
        if not raw:
            return None
        text = str(raw).strip()
        match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", text)
        if not match:
            return None
        lat = float(match.group(1))
        lon = float(match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
        return None

    def _resolve_location(location):
        return _parse_lat_lon(location)

    def _haversine_km(lat1, lon1, lat2, lon2):
        radius = 6371.0
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return radius * c

    def _bearing_deg(lat1, lon1, lat2, lon2):
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        dl = math.radians(lon2 - lon1)
        y = math.sin(dl) * math.cos(p2)
        x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
        return (math.degrees(math.atan2(y, x)) + 360) % 360

    def _cardinal(deg):
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        index = int((deg + 22.5) // 45) % 8
        return directions[index]

    def _sanitize_callsign(value):
        if not value:
            return "Unknown"
        # A callsign IS a code — this .upper() normalizes one, it does not shout.
        clean = str(value).strip().upper()
        return clean if clean else "Unknown"

    def _parse_timestamp(value):
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(float(text))
        except Exception:
            pass
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return int(datetime.fromisoformat(text).astimezone(timezone.utc).timestamp())
        except Exception:
            return None

    def _format_altitude(altitude_m, unit):
        if altitude_m is None:
            return "A?"
        if unit == "m":
            altitude = int(round(altitude_m))
            if altitude >= 10000:
                return f"A{int(round(altitude / 1000.0))}KM"
            return f"A{altitude}M"
        altitude_ft = int(round(altitude_m * 3.28084))
        if unit == "fl":
            return f"FL{int(round(altitude_ft / 100.0))}"
        if altitude_ft >= 10000:
            return f"A{int(round(altitude_ft / 1000.0))}K"
        return f"A{altitude_ft}"

    def _format_speed(speed_ms, unit):
        if speed_ms is None:
            return "?"
        if unit == "mph":
            return f"{int(round(speed_ms * 2.23694))}MPH"
        if unit == "kmh":
            return f"{int(round(speed_ms * 3.6))}KPH"
        return f"{int(round(speed_ms * 1.94384))}KT"

    def _clean_code(value):
        """An airport code (IATA/ICAO) uppercased and trimmed, or '' — the route feed's
        codes; only the keyed providers carry these (OpenSky's free feed has no route)."""
        code = str(value or "").strip().upper()
        return code if code and code.isalnum() and len(code) <= 4 else ""

    def _normalize_flight(callsign, latitude, longitude, *, altitude_m=None, speed_ms=None,
                          heading=None, on_ground=False, last_seen=None,
                          origin=None, destination=None):
        if latitude is None or longitude is None:
            return None
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except Exception:
            return None
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None
        normalized = {
            "callsign": _sanitize_callsign(callsign),
            "lat": latitude,
            "lon": longitude,
            "altitude_m": None,
            "speed_ms": None,
            "heading": None,
            "on_ground": bool(on_ground),
            "last_seen": _parse_timestamp(last_seen),
            "origin": _clean_code(origin),
            "destination": _clean_code(destination),
        }
        try:
            if altitude_m is not None:
                normalized["altitude_m"] = float(altitude_m)
        except Exception:
            pass
        try:
            if speed_ms is not None:
                normalized["speed_ms"] = float(speed_ms)
        except Exception:
            pass
        try:
            if heading is not None:
                normalized["heading"] = float(heading)
        except Exception:
            pass
        return normalized

    def _get_opensky_token(client_id, client_secret):
        now = time.time()
        if state.get("opensky_token") and now < float(state.get("opensky_token_exp", 0.0)):
            return state.get("opensky_token")

        response = requests.post(
            "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise ValueError("OpenSky token response missing access_token")
        expires_in = int(payload.get("expires_in", 1800))
        state["opensky_token"] = token
        state["opensky_token_exp"] = now + max(60, expires_in - 30)
        return token

    def _fetch_opensky(lamin, lomin, lamax, lomax, client_id, client_secret):
        headers = None
        if client_id and client_secret:
            token = _get_opensky_token(client_id, client_secret)
            headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            "https://opensky-network.org/api/states/all",
            params={
                "lamin": round(lamin, 5),
                "lomin": round(lomin, 5),
                "lamax": round(lamax, 5),
                "lomax": round(lomax, 5),
            },
            headers=headers,
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        flights = []
        for state in payload.get("states", []) or []:
            if len(state) < 17:
                continue
            altitude_m = state[13] if len(state) > 13 and state[13] is not None else None
            if altitude_m is None:
                altitude_m = state[7] if len(state) > 7 and state[7] is not None else None
            flight = _normalize_flight(
                state[1],
                state[6],
                state[5],
                altitude_m=altitude_m,
                speed_ms=state[9] if len(state) > 9 else None,
                heading=state[10] if len(state) > 10 else None,
                on_ground=state[8] if len(state) > 8 else False,
                last_seen=state[4] if len(state) > 4 else None,
            )
            if flight:
                flights.append(flight)
        return flights

    def _fetch_flightaware(lamin, lomin, lamax, lomax, api_key):
        query = f'-latlong "{lamin:.5f} {lomin:.5f} {lamax:.5f} {lomax:.5f}"'
        response = requests.get(
            "https://aeroapi.flightaware.com/aeroapi/flights/search",
            params={"query": query, "max_pages": 1},
            headers={"x-apikey": api_key},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        flights = []
        for item in payload.get("flights", []) or []:
            pos = item.get("last_position") or {}
            altitude_ft = pos.get("altitude")
            speed_kt = pos.get("groundspeed")
            org, dst = item.get("origin") or {}, item.get("destination") or {}
            flight = _normalize_flight(
                item.get("ident_icao") or item.get("ident_iata") or item.get("ident") or item.get("registration"),
                pos.get("latitude"),
                pos.get("longitude"),
                altitude_m=(altitude_ft * 0.3048) if altitude_ft is not None else None,
                speed_ms=(speed_kt / 1.94384) if speed_kt is not None else None,
                heading=pos.get("heading"),
                on_ground=False,
                last_seen=pos.get("timestamp") or item.get("last_position_time"),
                origin=org.get("code_iata") or org.get("code"),
                destination=dst.get("code_iata") or dst.get("code"),
            )
            if flight:
                flights.append(flight)
        return flights

    def _fetch_airlabs(lamin, lomin, lamax, lomax, api_key):
        response = requests.get(
            "https://airlabs.co/api/v9/flights",
            params={
                "api_key": api_key,
                "bbox": f"{lamin:.5f},{lomin:.5f},{lamax:.5f},{lomax:.5f}",
                "_fields": "lat,lng,alt,dir,speed,updated,flight_iata,flight_icao,flight_number,reg_number,status,dep_iata,arr_iata",
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("response") or payload.get("data") or []
        flights = []
        for item in items:
            speed_kmh = item.get("speed")
            flight = _normalize_flight(
                item.get("flight_iata") or item.get("flight_icao") or item.get("flight_number") or item.get("reg_number"),
                item.get("lat"),
                item.get("lng"),
                altitude_m=item.get("alt"),
                speed_ms=(speed_kmh / 3.6) if speed_kmh is not None else None,
                heading=item.get("dir"),
                on_ground=str(item.get("status", "")).lower() in ("landed", "scheduled", "ground"),
                last_seen=item.get("updated"),
                origin=item.get("dep_iata"),
                destination=item.get("arr_iata"),
            )
            if flight:
                flights.append(flight)
        return flights

    def _fetch_aviationstack(api_key):
        response = requests.get(
            "https://api.aviationstack.com/v1/flights",
            params={
                "access_key": api_key,
                "flight_status": "active",
                "limit": 100,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        flights = []
        for item in payload.get("data", []) or []:
            live = item.get("live") or {}
            flight_info = item.get("flight") or {}
            aircraft = item.get("aircraft") or {}
            dep = item.get("departure") or {}
            arr = item.get("arrival") or {}
            speed_kmh = live.get("speed_horizontal")
            flight = _normalize_flight(
                flight_info.get("iata") or flight_info.get("icao") or flight_info.get("number") or aircraft.get("registration"),
                live.get("latitude"),
                live.get("longitude"),
                altitude_m=live.get("altitude"),
                speed_ms=(speed_kmh / 3.6) if speed_kmh is not None else None,
                heading=live.get("direction"),
                on_ground=live.get("is_ground", False),
                last_seen=live.get("updated"),
                origin=dep.get("iata"),
                destination=arr.get("iata"),
            )
            if flight:
                flights.append(flight)
        return flights

    def _extract_fr24_items(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("data", "aircraft", "flights", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return [v for v in value.values() if isinstance(v, dict)]
        return [
            value
            for value in payload.values()
            if isinstance(value, dict) and any(k in value for k in ("lat", "lng", "lon", "latitude", "longitude"))
        ]

    def _fetch_flightradar24(lamin, lomin, lamax, lomax, api_key, api_host):
        host = (api_host or "flightradar24-com.p.rapidapi.com").strip()
        response = requests.get(
            f"https://{host}/flights/list-in-boundary",
            params={
                "bl_lat": f"{lamin:.5f}",
                "bl_lng": f"{lomin:.5f}",
                "tr_lat": f"{lamax:.5f}",
                "tr_lng": f"{lomax:.5f}",
            },
            headers={
                "X-RapidAPI-Key": api_key,
                "X-RapidAPI-Host": host,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        flights = []
        for item in _extract_fr24_items(payload):
            speed_kt = item.get("speed") or item.get("ground_speed") or item.get("groundSpeed")
            altitude_ft = item.get("alt") or item.get("altitude")
            flight = _normalize_flight(
                item.get("callsign") or item.get("flight") or item.get("flight_number") or item.get("icao") or item.get("iata") or item.get("registration"),
                item.get("lat") or item.get("latitude"),
                item.get("lng") or item.get("lon") or item.get("longitude"),
                altitude_m=(altitude_ft * 0.3048) if altitude_ft is not None else None,
                speed_ms=(speed_kt / 1.94384) if speed_kt is not None else None,
                heading=item.get("track") or item.get("heading"),
                on_ground=str(item.get("status", "")).lower() in ("landed", "scheduled", "ground"),
                last_seen=item.get("timestamp") or item.get("last_seen") or item.get("time"),
                origin=item.get("origin") or item.get("orig") or item.get("from"),
                destination=item.get("destination") or item.get("dest") or item.get("to"),
            )
            if flight:
                flights.append(flight)
        return flights

    def _provider_requirements(provider):
        return {
            "opensky": [],
            "flightaware": [("flightaware_api_key", "FLIGHTAWARE")],
            "flightradar24": [("flightradar24_api_key", "FR24 key")],
            "airlabs": [("airlabs_api_key", "AIRLABS key")],
            "aviationstack": [("aviationstack_api_key", "AVSTACK key")],
        }.get(provider, [])

    def _provider_tag(provider):
        return {
            "opensky": "OPENSKY",
            "flightaware": "FLTAWARE",
            "flightradar24": "FR24",
            "airlabs": "AIRLABS",
            "aviationstack": "AVSTACK",
        }.get(provider, "API")

    def _extract_error_text(response):
        if not response:
            return ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                for key in ("error", "message", "reason", "detail"):
                    if key in payload and payload[key]:
                        return str(payload[key])
            return str(payload)
        except Exception:
            return (response.text or "").strip()

    def _error_pages(provider, err, dwell_repeat):
        tag = _provider_tag(provider)
        if isinstance(err, requests.Timeout):
            return [format_lines("Planes", "API timeout", tag)] * dwell_repeat
        if isinstance(err, requests.ConnectionError):
            return [format_lines("Planes", "Connection err", tag)] * dwell_repeat

        status = None
        body_text = ""
        if isinstance(err, requests.HTTPError):
            response = getattr(err, "response", None)
            if response is not None:
                status = response.status_code
                body_text = _extract_error_text(response).lower()

        if status in (401, 403):
            return [format_lines("Planes", "Auth error", f"{tag} key")] * dwell_repeat

        if status == 429 or any(token in body_text for token in ("rate", "limit", "quota", "usage", "too many")):
            return [format_lines("Planes", "Rate limited", tag)] * dwell_repeat

        if status == 402:
            return [format_lines("Planes", "Plan limit", tag)] * dwell_repeat

        if status in (500, 502, 503, 504):
            return [format_lines("Planes", "API offline", tag)] * dwell_repeat

        if status is not None:
            return [format_lines("Planes", f"API err {status}", tag)] * dwell_repeat

        return [format_lines("Planes", "Data error", "Try again")] * dwell_repeat

    def _fetch_provider_flights(provider, lamin, lomin, lamax, lomax):
        if provider == "opensky":
            return _fetch_opensky(
                lamin,
                lomin,
                lamax,
                lomax,
                str(settings.get("opensky_client_id", "")).strip(),
                str(settings.get("opensky_client_secret", "")).strip(),
            )
        if provider == "flightaware":
            return _fetch_flightaware(lamin, lomin, lamax, lomax, str(settings.get("flightaware_api_key", "")).strip())
        if provider == "flightradar24":
            return _fetch_flightradar24(
                lamin,
                lomin,
                lamax,
                lomax,
                str(settings.get("flightradar24_api_key", "")).strip(),
                str(settings.get("flightradar24_api_host", "")).strip(),
            )
        if provider == "airlabs":
            return _fetch_airlabs(lamin, lomin, lamax, lomax, str(settings.get("airlabs_api_key", "")).strip())
        if provider == "aviationstack":
            return _fetch_aviationstack(str(settings.get("aviationstack_api_key", "")).strip())
        raise ValueError(f"Unsupported source: {provider}")

    base_loop_seconds = 4.0

    location_raw = str(settings.get("location", "") or "")
    radius_value = max(
        1.0,
        _to_float(settings.get("radius", 100), 100.0),
    )
    radius_unit = str(settings.get("radius_unit", "mi")).lower()
    max_results = max(1, min(10, _to_int(settings.get("max_results", "9"), 9)))
    units_preset = str(settings.get("units_preset", "aviation")).lower()
    distance_unit = str(settings.get("distance_unit", "nm")).lower()
    altitude_unit = str(settings.get("altitude_unit", "fl")).lower()
    speed_unit = str(settings.get("speed_unit", "kt")).lower()
    dwell_seconds = max(1.0, min(30.0, _to_float(settings.get("dwell_seconds", "15"), 15.0)))
    polling_seconds = max(15.0, min(3600.0, _to_float(settings.get("polling_rate", "240"), 240.0)))
    data_source = str(settings.get("data_source", "opensky")).strip().lower()
    dwell_repeat = max(1, int(round(dwell_seconds / base_loop_seconds)))

    if units_preset == "aviation":
        distance_unit = "nm"
        altitude_unit = "fl"
        speed_unit = "kt"
    elif units_preset == "metric":
        distance_unit = "km"
        altitude_unit = "m"
        speed_unit = "kmh"
    elif units_preset == "imperial":
        distance_unit = "mi"
        altitude_unit = "ft"
        speed_unit = "mph"

    if distance_unit not in ("km", "mi", "nm"):
        distance_unit = "km"
    if radius_unit not in ("mi", "km"):
        radius_unit = "mi"
    if altitude_unit not in ("ft", "fl", "m"):
        altitude_unit = "ft"
    if speed_unit not in ("kt", "mph", "kmh"):
        speed_unit = "kt"
    if data_source not in ("opensky", "flightaware", "flightradar24", "airlabs", "aviationstack"):
        data_source = "opensky"

    radius_km = radius_value * 1.609344 if radius_unit == "mi" else radius_value
    radius_km = max(10.0, min(250.0, radius_km))

    # Where to look. Prefer the GLOBAL precise location (the Location set in the main
    # settings, shared with weather / tides / sun-times); the per-app field below is only
    # an override, used when someone types one in or no global location is set at all.
    center = None
    if get_location is not None:
        loc = get_location() or {}
        if loc.get("lat") is not None and loc.get("lon") is not None:
            try:
                center = (float(loc["lat"]), float(loc["lon"]))
            except (TypeError, ValueError):
                center = None
    if center is None and location_raw.strip():
        center = _resolve_location(location_raw)
        if not center:
            return [format_lines("Planes", "Bad location", "Use lat,lon")] * dwell_repeat
    if center is None:
        center = (42.3601, -71.0589)                 # last resort: nothing set anywhere

    for key, label in _provider_requirements(data_source):
        if not str(settings.get(key, "")).strip():
            return [format_lines("Planes", "Add API key", label)] * dwell_repeat

    lat, lon = center
    delta_lat = radius_km / 111.0
    cos_lat = math.cos(math.radians(lat))
    delta_lon = radius_km / max(111.0 * max(cos_lat, 0.01), 1.0)

    lamin = max(-90.0, lat - delta_lat)
    lamax = min(90.0, lat + delta_lat)
    lomin = max(-180.0, lon - delta_lon)
    lomax = min(180.0, lon + delta_lon)

    settings_sig = (
        location_raw,
        center,                                      # global location changes -> refetch
        radius_value,
        radius_unit,
        max_results,
        units_preset,
        distance_unit,
        altitude_unit,
        speed_unit,
        dwell_seconds,
        polling_seconds,
        data_source,
        str(settings.get("opensky_client_id", "")).strip(),
        str(settings.get("opensky_client_secret", "")).strip(),
        str(settings.get("flightaware_api_key", "")).strip(),
        str(settings.get("flightradar24_api_key", "")).strip(),
        str(settings.get("flightradar24_api_host", "")).strip(),
        str(settings.get("airlabs_api_key", "")).strip(),
        str(settings.get("aviationstack_api_key", "")).strip(),
    )

    now_ts = time.time()
    sig_changed = settings_sig != state["last_sig"]
    due_for_poll = (now_ts - state["last_polled_at"]) >= polling_seconds
    need_poll = sig_changed or due_for_poll or (not state["flights"] and state["last_error"] is None)

    if need_poll:
        try:
            state["flights"] = _fetch_provider_flights(data_source, lamin, lomin, lamax, lomax)
            state["last_error_provider"] = None
            state["last_error"] = None
            state["last_polled_at"] = now_ts
            state["last_sig"] = settings_sig
        except Exception as err:
            state["last_error_provider"] = data_source
            state["last_error"] = err
            state["last_polled_at"] = now_ts
            state["last_sig"] = settings_sig

    if state["last_error"] and not state["flights"]:
        return _error_pages(state["last_error_provider"] or data_source, state["last_error"], dwell_repeat)

    flights = state["flights"]

    now = int(time.time())
    nearby = []
    for flight in flights:
        if flight["on_ground"]:
            continue
        if flight["last_seen"] and (now - int(flight["last_seen"]) > 300):
            continue

        dist_km = _haversine_km(lat, lon, flight["lat"], flight["lon"])
        if dist_km > radius_km:
            continue

        bearing = _bearing_deg(lat, lon, flight["lat"], flight["lon"])
        nearby.append({
            "flight": flight,
            "distance": dist_km,
            "direction": _cardinal(bearing),
        })

    if not nearby:
        radius_text = f"{radius_value:.0f}{radius_unit.upper()}"
        return [format_lines("Planes", "None nearby", f"Rad {radius_text}")] * dwell_repeat

    nearby.sort(key=lambda item: item["distance"])

    # --- each aircraft's field values ------------------------------------------
    def _route(f):
        o, d = f.get("origin", ""), f.get("destination", "")
        if o and d:
            return f"{o}→{d}"                       # PIT->SFO
        return (o and f"{o}→") or (d and f"→{d}") or ""

    def _dist_val(km):
        if distance_unit == "mi":
            return f"{km * 0.621371:.1f}MI"
        if distance_unit == "nm":
            return f"{km * 0.539957:.1f}NM"
        return f"{km:.1f}KM"

    rows_data = []
    for item in nearby[:max_results]:
        f = item["flight"]
        rows_data.append({
            "callsign": f["callsign"],
            "route": _route(f),
            "_dval": _dist_val(item["distance"]),
            "_ddir": item["direction"],
            "altitude": _format_altitude(f["altitude_m"], altitude_unit),
            "speed": _format_speed(f["speed_ms"], speed_unit),
        })
    # Distance aligned INSIDE its column: the number flush right (so the decimals line up)
    # and the compass letters flush left (so they line up too) — one plain "3.0MI SE"
    # string per row would put the MI and the direction at ragged columns.
    dvw = max(len(r["_dval"]) for r in rows_data)
    ddw = max(len(r["_ddir"]) for r in rows_data)
    for r in rows_data:
        r["distance"] = f"{r['_dval']:>{dvw}} {r['_ddir']:<{ddw}}"

    # --- which fields to show (the user picks; callsign always) ----------------
    def _yes(key):
        return str(settings.get(key, "yes")).strip().lower() != "no"
    ORDER = ["callsign", "route", "distance", "altitude", "speed"]     # display + priority
    picked = {"callsign", *(k for k in ("route", "distance", "altitude", "speed")
                            if _yes(f"show_{k}"))}
    # a column shows only if it's picked AND some aircraft actually has a value for it
    cols_shown = [k for k in ORDER if k in picked and any(r[k] for r in rows_data)]

    rows, cols = get_rows(), get_cols()
    gap = 2
    g = " " * gap
    RIGHT = {"speed"}                                    # numbers read better flush right
    width = {k: max(len(r[k]) for r in rows_data) for k in cols_shown}

    def cell(r, k):
        return r[k].rjust(width[k]) if k in RIGHT else r[k].ljust(width[k])

    def line_w(keys):
        return sum(width[k] for k in keys) + gap * max(0, len(keys) - 1)

    # Prefer DROPPING a field to WRAPPING: keep the longest priority-prefix that fits one
    # line. Only when that would strip everything but the callsign do we wrap instead.
    kept = cols_shown[:]
    while len(kept) > 1 and line_w(kept) > cols:
        kept.pop()

    pages = []
    if line_w(kept) <= cols and (len(kept) >= 2 or len(cols_shown) <= 1):
        # ONE LINE per aircraft, columns aligned, packed `rows` aircraft to a page. The
        # lines are NOT stripped: every row is padded to the same width so format_lines
        # centres them identically and the columns line up down the page (a right-stripped
        # short row would be re-centred a column over).
        lines = [g.join(cell(r, k) for k in kept) for r in rows_data]
        step = max(1, rows)
        for i in range(0, len(lines), step):
            pages.extend([format_lines(*lines[i:i + step])] * dwell_repeat)
    else:
        # The picked fields don't fit one line: wrap each aircraft onto TWO lines —
        # identity (callsign + route) then the metrics — and still pack rows//2 aircraft
        # per page. (Turn fields off to keep it to one line.)
        top = [k for k in ("callsign", "route") if k in cols_shown]
        bot = [k for k in ("distance", "altitude", "speed") if k in cols_shown]

        def grp(r, keys):
            return g.join(cell(r, k) for k in keys)

        lpp = 2 if bot else 1
        per = max(1, rows // lpp)
        for i in range(0, len(rows_data), per):
            lines = []
            for r in rows_data[i:i + per]:
                lines.append(grp(r, top))
                if bot:
                    lines.append(grp(r, bot))
            pages.extend([format_lines(*lines)] * dwell_repeat)

    return pages


def trigger(settings, conditions):
    """Fire when aircraft matching the configured filter appear overhead."""
    import requests

    filter_type = conditions.get('filter', 'any')
    keyword = conditions.get('keyword', '').upper().strip()

    # Common US military callsign prefixes
    MILITARY_PREFIXES = (
        'RCH', 'REACH', 'SPAR', 'SAM', 'VENUS', 'EVAC', 'JAKE',
        'TOPGUN', 'VIPER', 'MAGMA', 'IRON', 'DOOM', 'SKULL', 'GHOST',
        'ARMY', 'NAVY', 'USMC', 'USCG', 'AFSOC', 'DUKE', 'BOXER',
    )

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'seen_callsigns': set()}
        setattr(trigger, '_state', state)

    # Reuse fetch state's cached flights if available (avoids extra API calls)
    fetch_state = getattr(fetch, '_state', None)
    flights = fetch_state['flights'] if fetch_state and fetch_state.get('flights') else []

    if not flights:
        # No cached data — do a quick OpenSky poll. Look where the fetch looks: the
        # GLOBAL precise location first (location_lat/lon in the main settings), then the
        # per-app override field, then a default.
        try:
            lat_s = str(settings.get('location_lat', '') or '').strip()
            lon_s = str(settings.get('location_lon', '') or '').strip()
            loc = str(settings.get('location', '') or '').strip()
            if lat_s and lon_s:
                lat, lon = float(lat_s), float(lon_s)
            elif loc:
                lat, lon = [float(x.strip()) for x in loc.split(',')]
            else:
                lat, lon = 42.3601, -71.0589
            radius_km = 50
            d = radius_km / 111.0
            r = requests.get(
                'https://opensky-network.org/api/states/all',
                params={'lamin': lat-d, 'lomin': lon-d, 'lamax': lat+d, 'lomax': lon+d},
                timeout=8
            ).json()
            flights = [{'callsign': (s[1] or '').strip().upper(),
                        'altitude_m': s[7]} for s in (r.get('states') or [])]
        except Exception:
            return False

    alt_threshold_ft = float(conditions.get('altitude_ft', 0))
    new_found = False
    for f in flights:
        cs = f.get('callsign', '')
        if not cs:
            continue
        if filter_type == 'keyword' and keyword and keyword not in cs:
            continue
        if filter_type == 'military' and not any(cs.startswith(p) for p in MILITARY_PREFIXES):
            continue
        if filter_type == 'altitude' and alt_threshold_ft > 0:
            alt_m = f.get('altitude_m')
            if alt_m is None:
                continue
            alt_ft = float(alt_m) * 3.28084
            if alt_ft < alt_threshold_ft:
                continue
        if cs not in state['seen_callsigns']:
            state['seen_callsigns'].add(cs)
            new_found = True

    # Prune seen set to avoid unbounded growth
    if len(state['seen_callsigns']) > 500:
        state['seen_callsigns'] = set(list(state['seen_callsigns'])[-200:])

    return new_found