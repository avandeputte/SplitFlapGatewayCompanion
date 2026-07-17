"""
catalog.py — the built-in catalog of well-known, reusable GLOBAL settings.

These are the *reusable* infrastructure keys — the ones many apps legitimately
share (a location, a timezone, an API key, the default page dwell). They are the
only settings shown in the "Global settings" editor, and they render richly from
these definitions regardless of which apps are installed.

Deliberately NOT here: settings that belong to a single app even though they are
technically shared (a stock list, a YouTube channel id). Those live in that
app's own settings dialog.

Some catalog entries are *composite*: one control that reads/writes several
stored keys. ``location`` is a place search that fills ``location_lat`` /
``location_lon`` / ``location_name`` (the keys apps actually read).
"""

from __future__ import annotations

from . import i18n

CATALOG: list[dict] = [
    {"key": "weather_provider", "label": "Weather Provider", "type": "toggle",
     "default": "openmeteo",
     "options": [{"value": "openmeteo", "label": "Open-Meteo"},
                 {"value": "weatherapi", "label": "WeatherAPI.com"},
                 {"value": "qweather", "label": "QWeather"},
                 {"value": "openweather", "label": "OpenWeather"}],
     "note": "Open-Meteo is keyless; the others use the Weather API key below."},
    {"key": "weather_api_key", "label": "Weather Provider API Key", "type": "password",
     "ph": "Optional for Open-Meteo",
     "note": "Required for OpenWeather, WeatherAPI.com and QWeather; Open-Meteo needs none."},
    {"key": "zip_code", "label": "Location — ZIP, postcode, or city", "type": "text",
     "ph": "02118",
     "note": "Geocoded to place you — used unless a precise location is set below."},
    {"key": "location_precise", "label": "Location — precise (optional)", "type": "search_chips",
     "searchUrl": "/location_search", "resultKey": "results", "maxItems": 1, "geolocate": True,
     "note": "Search a place for exact coordinates; overrides the ZIP/city above.",
     "_composite": ["location_lat", "location_lon", "location_name"]},
    {"key": "timezone", "label": "Timezone", "type": "search_chips",
     "searchUrl": "/timezones", "resultKey": "zones", "maxItems": 1,
     "note": "Default timezone for clocks and time-based apps."},
    {"key": "language", "label": "Language", "type": "select", "default": "en-US",
     # The selectable languages live in i18n_data.json (only Windows-1252 /
     # Western-Latin-1 — no Greek, Cyrillic, CJK, since the modules can't show them).
     # Includes non-European regional variants (pt-BR, es-MX, fr-CA, …).
     "options": i18n.LANGUAGE_OPTIONS,
     "note": "The display language for apps that support it (look for the 🌐 badge): it "
             "translates their words and sets date order, number format and 12h/24h. "
             "Currency and holidays follow your Location, not this. Any app can override "
             "it in its own settings. Only Windows-1252 (Western-European) languages are "
             "listed — the modules can't show others."},
    {"key": "global_loop_delay", "label": "Default page dwell (seconds)", "type": "number",
     "default": 8, "min": "1", "max": "60", "step": "1", "stepper": True,
     "note": "How long each app page shows before advancing, unless an app overrides it."},
    {"key": "disable_colors", "label": "Disable colors", "type": "toggle", "default": "no",
     "options": [{"value": "no", "label": "No"}, {"value": "yes", "label": "Yes"}],
     "note": "Show up/down and status as text only, without the colored tiles."},
    {"key": "force_uppercase", "label": "Always uppercase", "type": "toggle", "default": "no",
     "options": [{"value": "no", "label": "No"}, {"value": "yes", "label": "Yes"}],
     "note": "Show everything in capitals, even on a display that can render lowercase "
             "(a Matrix Portal). A real split-flap has no lowercase flaps and is always in "
             "capitals regardless — this is for when you prefer that look."},
    {"key": "yt_api_key", "label": "YouTube Data API Key", "type": "password",
     "note": "Shared by the YouTube subscriber, comments and livestream apps."},
]

CATALOG_KEYS: frozenset[str] = frozenset(c["key"] for c in CATALOG)
CATALOG_BY_KEY: dict[str, dict] = {c["key"]: c for c in CATALOG}

# The keys actually STORED for the globals: a composite control (location_precise)
# is not stored itself — its component keys (location_lat/lon/name) are. Used by
# the settings store to decide what belongs in the on-disk "global" section.
_COMPOSITE_KEYS: frozenset[str] = frozenset(
    k for c in CATALOG for k in c.get("_composite", []))
GLOBAL_STORAGE_KEYS: frozenset[str] = frozenset(
    {c["key"] for c in CATALOG if not c.get("_composite")} | _COMPOSITE_KEYS)
