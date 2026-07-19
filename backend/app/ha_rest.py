"""ha_rest.py — read Home Assistant entity states for the Dashboard app.

Two ways in, tried in order:
  * the **Supervisor proxy** when we run as a Home Assistant add-on — SUPERVISOR_TOKEN +
    ``http://supervisor/core/api`` (needs ``homeassistant_api: true`` in the add-on manifest);
  * a configured base URL + long-lived token for standalone Docker — ``COMPANION_HA_URL`` /
    ``COMPANION_HA_TOKEN``.

States are cached a few seconds so the dashboard's redraws don't hammer HA. This is separate
from ``homeassistant.py`` (the companion's OUTBOUND MQTT device) — this only READS the core
REST API.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger("companion.ha_rest")

_cache: dict = {"at": 0.0, "states": []}
_TTL = 8.0


def endpoint() -> tuple[str | None, str | None]:
    """``(base_api_url, token)`` — the Supervisor proxy first, else the configured URL/token."""
    tok = os.environ.get("SUPERVISOR_TOKEN")
    if tok:
        return "http://supervisor/core/api", tok
    url = (os.environ.get("COMPANION_HA_URL") or "").strip().rstrip("/")
    tok = (os.environ.get("COMPANION_HA_TOKEN") or "").strip()
    if url and tok:
        return f"{url}/api", tok
    return None, None


def available() -> bool:
    return endpoint()[0] is not None


def fetch_states(force: bool = False) -> list[dict]:
    """Every HA entity state (cached ~8 s). ``[]`` when HA isn't configured/reachable — the
    last good snapshot is kept and returned on a transient failure."""
    now = time.monotonic()
    if not force and _cache["states"] and now - _cache["at"] < _TTL:
        return _cache["states"]
    api, tok = endpoint()
    if not api:
        return []
    try:
        r = httpx.get(f"{api}/states", headers={"Authorization": f"Bearer {tok}"}, timeout=8.0)
        if r.status_code == 200 and isinstance(r.json(), list):
            _cache["states"], _cache["at"] = r.json(), now
    except Exception as e:
        log.debug("HA states fetch failed: %s", e)
    return _cache["states"]


def search(query: str, limit: int = 30) -> list[dict]:
    """Entities whose id or friendly name matches ``query`` — for the settings picker.
    Returns ``[{value: entity_id, label: "Friendly (entity_id)"}]``."""
    q = (query or "").strip().lower()
    out = []
    for s in fetch_states():
        eid = s.get("entity_id") or ""
        name = str((s.get("attributes") or {}).get("friendly_name") or "")
        if q and q not in eid.lower() and q not in name.lower():
            continue
        out.append({"value": eid, "label": f"{name} ({eid})" if name else eid})
        if len(out) >= limit:
            break
    out.sort(key=lambda e: e["label"].lower())
    return out
