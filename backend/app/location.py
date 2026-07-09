"""Resolve the configured global location to a country + currency (keyless: Nominatim).

A shared helper injected into apps that declare a ``get_location`` parameter (the
same pattern as ``get_weather``). It lets currency/holiday apps key off *where you
are* rather than your language — the language can't tell France (EUR) from Canada
(CAD) or Switzerland (CHF). The reverse-geocode is cached per location, so it costs
one lookup, not one per render.
"""

import re

# Catalog globals this helper draws on (named in the app dialog's "also uses" hint).
# It reads the location_precise composite's coordinates and the ZIP fallback.
GLOBAL_KEYS = ["location_precise", "zip_code"]

# Country (ISO 3166-1 alpha-2) -> currency (ISO 4217): the eurozone plus the common
# non-euro countries. Unknown countries fall through to None (caller decides).
_CURRENCY = {
    "US": "USD", "GB": "GBP", "AU": "AUD", "CA": "CAD", "CH": "CHF", "JP": "JPY",
    "CN": "CNY", "IN": "INR", "BR": "BRL", "MX": "MXN", "NZ": "NZD", "ZA": "ZAR",
    "SE": "SEK", "NO": "NOK", "DK": "DKK", "PL": "PLN", "CZ": "CZK", "HU": "HUF",
    "RU": "RUB", "TR": "TRY", "KR": "KRW", "SG": "SGD", "HK": "HKD", "AE": "AED",
    "SA": "SAR", "IL": "ILS", "TH": "THB", "ID": "IDR", "MY": "MYR", "PH": "PHP",
    # eurozone
    "AT": "EUR", "BE": "EUR", "CY": "EUR", "EE": "EUR", "FI": "EUR", "FR": "EUR",
    "DE": "EUR", "GR": "EUR", "IE": "EUR", "IT": "EUR", "LV": "EUR", "LT": "EUR",
    "LU": "EUR", "MT": "EUR", "NL": "EUR", "PT": "EUR", "SK": "EUR", "SI": "EUR",
    "ES": "EUR", "HR": "EUR",
}

_geo_cache: dict = {}   # rounded (lat, lon) -> {"country": "CA", "subdivision": "CA-QC"}


def _latlon(settings, requests):
    """The configured location's lat/lon: precise coords, else a geocoded ZIP."""
    lat = str(settings.get("location_lat", "") or "").strip()
    lon = str(settings.get("location_lon", "") or "").strip()
    if lat and lon:
        try:
            return float(lat), float(lon)
        except ValueError:
            pass
    zip_code = str(settings.get("zip_code", "") or "").strip()
    if not zip_code:
        return None
    try:
        params = {"q": zip_code, "format": "json", "limit": 1}
        if re.fullmatch(r"\d{5}", zip_code):      # a US ZIP — 02118 also exists abroad
            params["countrycodes"] = "us"
        geo = requests.get("https://nominatim.openstreetmap.org/search", params=params,
                           headers={"User-Agent": "SplitFlapGatewayCompanion/1.0"}, timeout=6).json()
        if geo:
            return float(geo[0]["lat"]), float(geo[0]["lon"])
    except Exception:
        pass
    return None


def _geo(settings):
    """Reverse-geocode the configured location to {country, subdivision}, cached.
    subdivision is the ISO 3166-2 code (e.g. 'CA-QC' for Quebec) or None."""
    import requests
    ll = _latlon(settings, requests)
    if not ll:
        return {"country": None, "subdivision": None}
    key = (round(ll[0], 2), round(ll[1], 2))
    if key in _geo_cache:
        return _geo_cache[key]
    out = {"country": None, "subdivision": None}
    try:
        r = requests.get("https://nominatim.openstreetmap.org/reverse",
                         params={"lat": ll[0], "lon": ll[1], "format": "json", "zoom": 5},
                         headers={"User-Agent": "SplitFlapGatewayCompanion/1.0"}, timeout=6).json()
        addr = r.get("address") or {}
        out["country"] = str(addr.get("country_code") or "").upper()[:2] or None
        sub = str(addr.get("ISO3166-2-lvl4") or addr.get("ISO3166-2-lvl6") or "").upper()
        out["subdivision"] = sub or None
        if out["country"]:
            _geo_cache[key] = out
    except Exception:
        pass
    return out


def country(settings):
    """ISO country code for the configured location (reverse-geocoded, cached)."""
    return _geo(settings).get("country")


def resolve(settings) -> dict:
    """{ok, country, subdivision, currency} for the configured location. ok is False
    (values None) when there's no location set or the lookup failed."""
    g = _geo(settings)
    cc = g.get("country")
    return {"ok": bool(cc), "country": cc, "subdivision": g.get("subdivision"),
            "currency": _CURRENCY.get(cc) if cc else None}
