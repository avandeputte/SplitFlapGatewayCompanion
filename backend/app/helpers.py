"""
helpers.py — app-data search/lookup endpoints (the search_chips backends).

These are faithful ports of the app-plugin helper routes and MUST stay at the
same paths and return the same JSON keys, because dropped-in app manifests point
their ``searchUrl`` at them (``/location_search``, ``/timezones``,
``/stocks_search``, ``/crypto_search``) and use ``resultKey`` to read the array.
See COMPATIBILITY.md.

Uses httpx (async) instead of blocking requests so the event loop stays free.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
import pytz

log = logging.getLogger("companion.helpers")

SPORTS_LEAGUES = {
    "nfl": {"path": "football/nfl", "name": "NFL"},
    "nba": {"path": "basketball/nba", "name": "NBA"},
    "mlb": {"path": "baseball/mlb", "name": "MLB"},
    "nhl": {"path": "hockey/nhl", "name": "NHL"},
    "ncaaf": {"path": "football/college-football", "name": "NCAAF"},
    "ncaab": {"path": "basketball/mens-college-basketball", "name": "NCAAB"},
    "mls": {"path": "soccer/usa.1", "name": "MLS"},
    "epl": {"path": "soccer/eng.1", "name": "EPL"},
    "laliga": {"path": "soccer/esp.1", "name": "LALIGA"},
    "ucl": {"path": "soccer/uefa.champions", "name": "UCL"},
    "uel": {"path": "soccer/uefa.europa", "name": "EUROPA"},
    "ger": {"path": "soccer/ger.1", "name": "BUNDESLIGA"},
    "ita": {"path": "soccer/ita.1", "name": "SERIE A"},
    "fra": {"path": "soccer/fra.1", "name": "LIGUE 1"},
    "por": {"path": "soccer/por.1", "name": "PRIMEIRA"},
    "ned": {"path": "soccer/ned.1", "name": "EREDIVISIE"},
    "mex": {"path": "soccer/mex.1", "name": "LIGA MX"},
    "bra": {"path": "soccer/bra.1", "name": "BRASILEIRAO"},
    "efl": {"path": "soccer/eng.2", "name": "CHAMPIONSHIP"},
    "msoc": {"path": "soccer/usa.ncaa.m.1", "name": "MSOC"},
    "wsoc": {"path": "soccer/usa.ncaa.w.1", "name": "WSOC"},
    "wnba": {"path": "basketball/wnba", "name": "WNBA"},
    "ncaaw": {"path": "basketball/womens-college-basketball", "name": "NCAAW"},
    "soft": {"path": "baseball/college-softball", "name": "SOFTBALL"},
    "pga": {"path": "golf/pga", "name": "PGA"},
    "ufc": {"path": "mma/ufc", "name": "UFC"},
}

_teams_cache: dict[str, list] = {}
_crypto_cache: list = []


async def _get_json(url: str, *, params=None, headers=None, timeout=8.0):
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        return r.json()


async def location_search(q: str) -> dict:
    if len(q) < 2:
        return {"results": []}
    try:
        data = await _get_json(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 6, "addressdetails": 1},
            headers={"User-Agent": "SplitFlapGatewayCompanion/1.0"}, timeout=6.0)
        results = []
        for r in data:
            name = r.get("display_name", q)
            addr = r.get("address", {})
            short = (addr.get("city") or addr.get("town") or addr.get("village")
                     or addr.get("municipality") or name.split(",")[0].strip())
            results.append({"lat": r["lat"], "lon": r["lon"], "name": name,
                            "short_name": short, "value": f"{r['lat']},{r['lon']}|{q}",
                            "label": name})
        return {"results": results}
    except Exception as e:
        log.warning("location search error: %s", e)
        return {"results": [], "error": str(e)}


async def location_timezone(lat: str, lon: str) -> dict:
    if not lat or not lon:
        return {"timezone": ""}
    try:
        data = await _get_json("https://api.open-meteo.com/v1/forecast",
                               params={"latitude": lat, "longitude": lon,
                                       "forecast_days": 1, "current": "temperature_2m"},
                               timeout=6.0)
        return {"timezone": data.get("timezone", "")}
    except Exception:
        return {"timezone": ""}


def timezones(q: str) -> dict:
    q = (q or "").strip().lower()
    common = ["US/Eastern", "US/Central", "US/Mountain", "US/Pacific", "US/Hawaii",
              "Europe/London", "Europe/Paris", "Europe/Berlin", "Asia/Tokyo", "Asia/Shanghai",
              "Australia/Sydney", "Pacific/Auckland", "America/Chicago", "America/Denver",
              "America/Los_Angeles", "America/New_York", "America/Toronto", "America/Sao_Paulo"]
    results, seen = [], set()

    def add(tz):
        if tz in seen:
            return
        seen.add(tz)
        try:
            off = datetime.now(pytz.timezone(tz)).strftime("%z")
            label = f"{tz} (UTC{off[:3]}:{off[3:]})"
        except Exception:
            label = tz
        results.append({"value": tz, "label": label})

    if not q:
        for tz in common:
            add(tz)
    else:
        for tz in common:
            if q in tz.lower():
                add(tz)
        for tz in pytz.common_timezones:
            if q in tz.lower():
                add(tz)
    return {"zones": results[:20]}


async def stocks_search(q: str) -> dict:
    if len(q) < 1:
        return {"tickers": []}
    try:
        data = await _get_json(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": q, "quotesCount": 8, "newsCount": 0, "enableFuzzyQuery": "false"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=6.0)
        tickers = []
        for item in data.get("quotes", []):
            sym = item.get("symbol", "")
            name = item.get("shortname") or item.get("longname") or ""
            if sym:
                tickers.append({"value": sym, "label": f"{sym} — {name}" if name else sym})
        return {"tickers": tickers}
    except Exception as e:
        log.warning("stock search error: %s", e)
        return {"tickers": [], "error": str(e)}


async def crypto_search(q: str) -> dict:
    global _crypto_cache
    q = (q or "").strip().lower()
    if len(q) < 1:
        return {"coins": []}
    if not _crypto_cache:
        try:
            data = await _get_json("https://api.coingecko.com/api/v3/coins/list", timeout=10.0)
            _crypto_cache = [{"id": c["id"], "symbol": c["symbol"].upper(), "name": c["name"]}
                             for c in data]
        except Exception as e:
            log.warning("coingecko error: %s", e)
            return {"coins": [], "error": str(e)}
    results = []
    for c in _crypto_cache:
        if q in c["name"].lower() or q in c["symbol"].lower() or q in c["id"]:
            exact = c["id"] == q or c["name"].lower() == q or c["symbol"].lower() == q
            results.append({"value": c["id"], "label": f"{c['name']} ({c['symbol']})", "_exact": exact})
            if len(results) >= 30:
                break
    results.sort(key=lambda r: (not r["_exact"], r["label"].lower()))
    for r in results:
        del r["_exact"]
    return {"coins": results[:12]}


def sports_leagues(settings) -> dict:
    out = []
    for key, info in SPORTS_LEAGUES.items():
        followed = (settings.get(f"sports_{key}", "") or "").strip()
        out.append({"key": key, "name": info["name"], "followed": followed,
                    "follow_all": followed == "*"})
    return {"leagues": out}


async def sports_teams(league_key: str, q: str) -> dict:
    info = SPORTS_LEAGUES.get(league_key)
    if not info:
        return {"teams": [], "error": "Unknown league"}
    if league_key in ("pga", "ufc"):
        return {"teams": [], "no_teams": True}
    q = (q or "").strip().lower()
    if league_key not in _teams_cache:
        try:
            all_teams = []
            for page in range(1, 4):
                data = await _get_json(
                    f"https://site.api.espn.com/apis/site/v2/sports/{info['path']}/teams",
                    params={"limit": 200, "page": page})
                batch = data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
                if not batch:
                    break
                for entry in batch:
                    t = entry.get("team", entry)
                    all_teams.append({"abbr": t.get("abbreviation", "?"),
                                      "name": t.get("displayName", "?"),
                                      "short": t.get("shortDisplayName", t.get("displayName", "?"))})
            all_teams.sort(key=lambda t: t["name"])
            seen = set()
            all_teams = [t for t in all_teams if t["abbr"] not in seen and not seen.add(t["abbr"])]
            _teams_cache[league_key] = all_teams
        except Exception as e:
            return {"teams": [], "error": str(e)}
    teams = _teams_cache[league_key]
    if q:
        teams = [t for t in teams if q in t["name"].lower() or q in t["abbr"].lower()]
    return {"teams": teams}


def sports_follow(settings, league: str, teams: str) -> dict:
    if league not in SPORTS_LEAGUES:
        return {"ok": False, "error": "Unknown league"}
    settings.set(f"sports_{league}", teams)
    return {"ok": True}
