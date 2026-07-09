"""Resolve the configured global location to a country + currency (keyless: Nominatim).

A shared helper injected into apps that declare a ``get_location`` parameter (the
same pattern as ``get_weather``). It lets currency/holiday apps key off *where you
are* rather than your language — the language can't tell France (EUR) from Canada
(CAD) or Switzerland (CHF). The reverse-geocode is cached per location, so it costs
one lookup, not one per render.
"""

import re

# Global settings this reads (surfaced as a hint on apps that use the helper).
GLOBAL_KEYS = ["location_lat", "location_lon", "location_name", "zip_code"]

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

_country_cache: dict = {}   # rounded (lat, lon) -> ISO country code


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


def country(settings):
    """ISO country code for the configured location (reverse-geocoded, cached)."""
    import requests
    ll = _latlon(settings, requests)
    if not ll:
        return None
    key = (round(ll[0], 2), round(ll[1], 2))
    if key in _country_cache:
        return _country_cache[key]
    try:
        r = requests.get("https://nominatim.openstreetmap.org/reverse",
                         params={"lat": ll[0], "lon": ll[1], "format": "json", "zoom": 3},
                         headers={"User-Agent": "SplitFlapGatewayCompanion/1.0"}, timeout=6).json()
        cc = str((r.get("address") or {}).get("country_code") or "").upper()[:2] or None
        if cc:
            _country_cache[key] = cc
        return cc
    except Exception:
        return None


def resolve(settings) -> dict:
    """{ok, country, currency} for the configured location. ok is False (country/
    currency None) when there's no location set or the lookup failed."""
    cc = country(settings)
    return {"ok": bool(cc), "country": cc, "currency": _CURRENCY.get(cc) if cc else None}
