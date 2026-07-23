# =============================================================================
# SHARED — the flight data both surfaces render. The provider stack, its
# polling cadence and its cache all live inside fetch() (below); the shared
# path runs that poll with throwaway formatting and reads the cached state, so
# a wall and a panel always see the same aircraft from the same poll.
# =============================================================================

def _center(settings, get_location):
    """Where to look — the same order fetch() uses: the global precise location,
    then the per-app "lat,lon" override, then the default."""
    import re
    if get_location is not None:
        loc = get_location() or {}
        if loc.get("lat") is not None and loc.get("lon") is not None:
            try:
                return float(loc["lat"]), float(loc["lon"])
            except (TypeError, ValueError):
                pass
    raw = str(settings.get("location", "") or "").strip()
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", raw)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    return 42.3601, -71.0589


def _radius_km(settings):
    """The configured search radius in km, clamped the way fetch() clamps it."""
    try:
        value = float(settings.get("radius", 100))
    except Exception:
        value = 100.0
    value = max(1.0, value)
    unit = str(settings.get("radius_unit", "mi")).lower()
    km = value * 1.609344 if unit != "km" else value
    return max(10.0, min(250.0, km))


def _shared_flights(settings, get_location):
    """The current flight list for any surface: run fetch()'s own poll (it respects its
    polling_rate cache, so this does NOT hit the provider on every call) and read the
    state it maintains. Returns (flights, (lat, lon), radius_km, error-or-None)."""
    fetch(settings, lambda *a: "", lambda: 3, lambda: 22, get_location=get_location)
    state = getattr(fetch, "_state", None) or {}
    flights = state.get("flights") or []
    err = state.get("last_error") if not flights else None
    return flights, _center(settings, get_location), _radius_km(settings), err


# =============================================================================
# SPLIT-FLAP — fetch() (the provider stack + column pages) and the trigger.
# =============================================================================

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
        # centers them identically and the columns line up down the page (a right-stripped
        # short row would be re-centered a column over).
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

# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A radar card, one aircraft per hold: the callsign big, its route beside it,
# a heading-true bearing arrow with distance + compass, the altitude in the
# configured units — rotating through the same nearby list the flap pages
# tabulate (same poll, same radius, same ordering). Black background.
# =============================================================================

_MX_WHITE = (240, 240, 244)
_MX_GRAY = (150, 150, 158)
_MX_DIM = (100, 100, 108)
_MX_CYAN = (90, 200, 250)                   # the route
_MX_AMBER = (255, 180, 60)                  # distance + bearing
_MX_GREEN = (110, 220, 130)                 # altitude
_MX_RULE = (48, 52, 62)


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


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (none nearby / provider down)."""
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
    draw.text(((W - f1.getlength(line1)) / 2.0, y - b1[1]), line1, font=f1, fill=_MX_WHITE)
    if line2:
        y += h1 + gap
        draw.text(((W - f2.getlength(line2)) / 2.0, y - f2.getbbox(line2)[1]), line2, font=f2, fill=_MX_GRAY)
    return img


def _mx_haversine(lat1, lon1, lat2, lon2):
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _mx_bearing(lat1, lon1, lat2, lon2):
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _mx_cardinal(deg):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg + 22.5) // 45) % 8]


def _mx_units(settings):
    """(distance_unit, altitude_unit), resolved the way fetch() resolves them."""
    preset = str(settings.get('units_preset', 'aviation')).lower()
    du = str(settings.get('distance_unit', 'nm')).lower()
    au = str(settings.get('altitude_unit', 'fl')).lower()
    if preset == 'aviation':
        du, au = 'nm', 'fl'
    elif preset == 'metric':
        du, au = 'km', 'm'
    elif preset == 'imperial':
        du, au = 'mi', 'ft'
    if du not in ('km', 'mi', 'nm'):
        du = 'km'
    if au not in ('ft', 'fl', 'm'):
        au = 'ft'
    return du, au


def _mx_dist(km, du):
    if du == 'mi':
        return f'{km * 0.621371:.1f} MI'
    if du == 'nm':
        return f'{km * 0.539957:.1f} NM'
    return f'{km:.1f} KM'


def _mx_alt(alt_m, au):
    if alt_m is None:
        return ''
    if au == 'm':
        return f'{int(round(alt_m))} M'
    ft = alt_m * 3.28084
    if au == 'fl':
        return f'FL{int(round(ft / 100.0))}'
    return f'{int(round(ft))} FT'


def _mx_arrow(draw, cx, cy, r, deg, color):
    """A little bearing arrow: which way to look for the aircraft."""
    import math
    rad = math.radians(deg)
    dx, dy = math.sin(rad), -math.cos(rad)
    tip = (cx + dx * r, cy + dy * r)
    tail = (cx - dx * r, cy - dy * r)
    draw.line([tail, tip], fill=color)
    for off in (150, -150):
        hr = math.radians(deg + off)
        draw.line([tip, (tip[0] + math.sin(hr) * (r * 0.7), tip[1] - math.cos(hr) * (r * 0.7))], fill=color)


def fetch_matrix(settings, canvas, get_location=None):
    """Draw one nearby aircraft per hold, rotating through the same list the wall pages. The
    provider poll is throttled inside the shared path (polling_rate), so redraws are cheap."""
    import time
    from PIL import ImageDraw

    try:
        flights, (lat, lon), radius_km, err = _shared_flights(settings, get_location)
    except Exception:
        canvas.frame(_cv_message(canvas, ImageDraw, 'PLANES', 'DATA ERROR'))
        return 60.0
    if err and not flights:
        canvas.frame(_cv_message(canvas, ImageDraw, 'PLANES', 'API ERROR'))
        return 60.0

    now = int(time.time())
    nearby = []
    for f in flights:
        if f.get('on_ground'):
            continue
        if f.get('last_seen') and (now - int(f['last_seen']) > 300):
            continue
        d = _mx_haversine(lat, lon, f['lat'], f['lon'])
        if d > radius_km:
            continue
        nearby.append((d, _mx_bearing(lat, lon, f['lat'], f['lon']), f))
    if not nearby:
        canvas.frame(_cv_message(canvas, ImageDraw, 'PLANES', 'NONE NEARBY'))
        return 60.0
    nearby.sort(key=lambda x: x[0])

    try:
        max_results = max(1, min(10, int(settings.get('max_results', '9'))))
    except Exception:
        max_results = 9
    shown = nearby[:min(5, max_results)]

    st = getattr(fetch_matrix, '_state', None)
    if st is None:
        st = {'i': 0}
        setattr(fetch_matrix, '_state', st)
    idx = st['i'] % len(shown)
    st['i'] = (st['i'] + 1) % len(shown)

    dist_km, bearing, f = shown[idx]
    du, au = _mx_units(settings)
    callsign = str(f.get('callsign') or 'UNKNOWN')
    o, dst = f.get('origin', ''), f.get('destination', '')
    route = f'{o}→{dst}' if (o and dst) else (f'{o}→' if o else (f'→{dst}' if dst else ''))
    dist = f'{_mx_dist(dist_km, du)} {_mx_cardinal(bearing)}'
    alt = _mx_alt(f.get('altitude_m'), au)

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    if H >= 48:
        # Header: a label + pagination dots (this aircraft lit), then the card.
        # Tall enough for a ~10px face: below that, this bold face flattens an O's
        # top curve and "OVERHEAD" reads as "UVERHEAD".
        head_h = max(12, int(H * 0.20))
        lbl = 'OVERHEAD'
        lf = _cv_fit(canvas, lbl, int(W * 0.55), head_h - 2)
        lb = lf.getbbox(lbl)
        if (lb[3] - lb[1]) >= 6:
            # The ink's top row rides y=1 — row 0 is the 1px slack a fitted font's ink
            # can overshoot its bbox by, else "OVERHEAD" clips into "UVERHEAD".
            draw.text((3, 1 - lb[1]), lbl, font=lf, fill=_MX_GRAY)
        step = 4
        dy = 1 + (head_h - 2) // 2
        dx = W - 3 - len(shown) * step
        for j in range(len(shown)):
            if j == idx:                          # the shown aircraft: amber + a hair bigger
                draw.rectangle([dx + j * step - 1, dy - 2, dx + j * step + 2, dy + 1],
                               fill=_MX_AMBER)
            else:
                draw.rectangle([dx + j * step, dy - 1, dx + j * step + 1, dy], fill=_MX_DIM)
        draw.line([(2, head_h + 1), (W - 3, head_h + 1)], fill=_MX_RULE)

        # Hero: callsign big, route beside it in cyan.
        hero_top = head_h + 3
        hero_h = max(12, int(H * 0.34))
        cf = _cv_fit(canvas, callsign, int(W * 0.62), hero_h)
        cb = cf.getbbox(callsign)
        draw.text((3, hero_top + (hero_h - (cb[3] - cb[1])) / 2.0 - cb[1]), callsign, font=cf, fill=_MX_WHITE)
        if route:
            rf = _cv_fit(canvas, route, W - 9 - cf.getlength(callsign), max(7, int(hero_h * 0.55)))
            rb = rf.getbbox(route)
            if (rb[3] - rb[1]) >= 6:
                draw.text((W - 3 - rf.getlength(route),
                           hero_top + (hero_h - (rb[3] - rb[1])) / 2.0 - rb[1]), route, font=rf, fill=_MX_CYAN)

        # Info row: bearing arrow + distance (amber), altitude flush right (green).
        info_top = hero_top + hero_h + 2
        info_h = max(8, int(H * 0.2))

        # Who else is up there earns the panel's bottom edge; with nobody else the
        # info row itself sinks onto it — either way the last LED rows carry ink.
        others = [str(g.get('callsign') or '') for _, _, g in shown if g is not f][:2]
        oline = ('+ ' + '  '.join(o for o in others if o)) if any(others) else ''
        of = ob = None
        if oline and H - (info_top + info_h + 2) >= 8:
            of = _cv_fit(canvas, oline, W - 6, min(H - info_top - info_h - 3, max(7, int(H * 0.14))))
            ob = of.getbbox(oline)
            if (ob[3] - ob[1]) < 5:
                of = None

        df = _cv_fit(canvas, dist, int(W * 0.5), info_h - 1)
        db = df.getbbox(dist)
        dh = db[3] - db[1]
        iy = info_top + (info_h - dh) / 2.0 if of else H - 1 - dh
        ax = 3 + info_h // 2
        ay = int(iy + dh / 2.0)
        _mx_arrow(draw, ax, ay, max(3, info_h // 2 - 1), bearing, _MX_AMBER)
        draw.text((ax + info_h // 2 + 3, iy - db[1]), dist, font=df, fill=_MX_AMBER)
        if alt:
            af = _cv_fit(canvas, alt, int(W * 0.32), info_h - 1)
            ab = af.getbbox(alt)
            if (ab[3] - ab[1]) >= 6:
                draw.text((W - 3 - af.getlength(alt), iy + dh - (ab[3] - ab[1]) - ab[1]),
                          alt, font=af, fill=_MX_GREEN)
        if of:
            draw.text((3, H - 1 - (ob[3] - ob[1]) - ob[1]), oline, font=of, fill=_MX_DIM)
    else:
        # Compact: callsign over the amber distance line, the altitude beside it on a
        # wide panel and on its OWN bottom row on a narrow one — three rows of real
        # data instead of a dark hole. The callsign's ink rides row 1 (row 0 is the
        # bbox-overshoot slack) and the last row's ink sinks to the panel's edge.
        cs_h = max(11, int(H * 0.48))
        cf = _cv_fit(canvas, callsign, W - 6, cs_h)
        cb = cf.getbbox(callsign)
        draw.text((3, 1 - cb[1]), callsign, font=cf, fill=_MX_WHITE)
        info_top = 1 + (cb[3] - cb[1]) + 2

        alt_below = bool(alt) and W < 112
        af = ab = None
        if alt:
            af = _cv_fit(canvas, alt, W - 6 if alt_below else int(W * 0.3),
                         max(7, int(H * 0.28)) if alt_below else H - 1 - info_top - 1)
            ab = af.getbbox(alt)

        info_h = H - 1 - info_top - ((ab[3] - ab[1] + 2) if alt_below else 0)
        box = min(12, info_h)                    # the arrow's box — not the row's height
        ax = 3 + box // 2
        tx = 3 + box + 3
        avail = (W - int(W * 0.3) - 4 if (alt and not alt_below) else W - 2) - tx
        df = _cv_fit(canvas, dist, avail, info_h - 1)
        db = df.getbbox(dist)
        dh = db[3] - db[1]
        iy = info_top + (info_h - dh) / 2.0 if alt_below else H - 1 - dh
        _mx_arrow(draw, ax, int(iy + dh / 2.0), max(3, min(box, dh + 2) // 2), bearing, _MX_AMBER)
        draw.text((tx, iy - db[1]), dist, font=df, fill=_MX_AMBER)
        if alt and not alt_below:
            if (ab[3] - ab[1]) >= 6:
                draw.text((W - 3 - af.getlength(alt), iy + dh - (ab[3] - ab[1]) - ab[1]),
                          alt, font=af, fill=_MX_GREEN)
        elif alt_below:
            draw.text((tx, H - 1 - (ab[3] - ab[1]) - ab[1]), alt, font=af, fill=_MX_GREEN)

    canvas.frame(img)
    return 10.0
