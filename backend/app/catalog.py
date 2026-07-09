"""
catalog.py — the built-in catalog of well-known, reusable GLOBAL settings.

These are the shared infrastructure keys many apps read (API keys, location,
timezone, watch-lists). They are the ONLY settings shown in the "Global settings"
editor, and they render richly from these definitions regardless of which apps
happen to be installed. Everything else — even a setting shared between a few
apps — is configured in each app's own settings dialog.

Keeping this list here (rather than deriving it from whichever app declares a
key) means the field renders correctly even when the declaring app isn't
installed (e.g. the Dashboard app needs the weather API key without the Weather
app installed).
"""

from __future__ import annotations

CATALOG: list[dict] = [
    {"key": "weather_api_key", "label": "Weather Provider API Key", "type": "password",
     "ph": "Optional for Open-Meteo",
     "note": "Required for OpenWeather, WeatherAPI.com and QWeather; Open-Meteo needs none."},
    {"key": "zip_code", "label": "Location — ZIP, postcode, or city", "type": "text",
     "ph": "02118",
     "note": "Where weather, ISS and similar apps place you when they have no precise coordinates."},
    {"key": "timezone", "label": "Timezone", "type": "search_chips",
     "searchUrl": "/timezones", "resultKey": "zones", "maxItems": 1,
     "note": "Default timezone for clocks and time-based apps."},
    {"key": "stocks_list", "label": "Stock Tickers", "type": "search_chips",
     "searchUrl": "/stocks_search", "resultKey": "tickers",
     "note": "Symbols the Stocks app cycles through."},
    {"key": "crypto_list", "label": "Cryptocurrencies", "type": "search_chips",
     "searchUrl": "/crypto_search", "resultKey": "coins",
     "note": "Coins the Crypto app cycles through."},
    {"key": "world_clock_zones", "label": "World Clock Timezones", "type": "search_chips",
     "searchUrl": "/timezones", "resultKey": "zones", "maxItems": 3,
     "note": "Up to three zones shown by the World Clock app."},
    {"key": "yt_api_key", "label": "YouTube Data API Key", "type": "password",
     "note": "Needed by the YouTube subscriber and livestream apps."},
    {"key": "yt_channel_id", "label": "YouTube Channel ID", "type": "text",
     "note": "Channel whose subscriber count is shown."},
    {"key": "yt_video_id", "label": "YouTube Video ID", "type": "text",
     "note": "Live video used for viewer count / comments."},
]

CATALOG_KEYS: frozenset[str] = frozenset(c["key"] for c in CATALOG)
CATALOG_BY_KEY: dict[str, dict] = {c["key"]: c for c in CATALOG}
