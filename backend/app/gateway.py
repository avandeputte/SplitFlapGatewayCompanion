"""
gateway.py — treat the SplitFlapGateway as the source of truth.

The gateway already exposes its display geometry and MQTT settings via
``GET /api/config`` (fields ``gridRows``, ``gridCols``, ``mqHost``, ``mqPort``,
``mqUser``, ``mqPfx``). The companion pulls those so the user configures the
panel and broker in exactly one place — the gateway. The MQTT *password* is not
exposed by the gateway (by design), so it stays a local companion secret; leave
it blank for an anonymous broker.
"""

from __future__ import annotations

import logging

log = logging.getLogger("companion.gateway")

# Gateway /api/config field -> companion config path.
_GRID_FIELDS = {"gridRows": "rows", "gridCols": "cols"}
_MQTT_FIELDS = {"mqHost": "broker", "mqPort": "port", "mqUser": "username", "mqPfx": "prefix"}


async def fetch_gateway_config(url: str, timeout: float = 5.0) -> dict:
    """GET ``{url}/api/config`` and return the parsed JSON (raises on failure)."""
    import httpx

    base = url.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{base}/api/config")
        r.raise_for_status()
        return r.json()


async def register_companion(gateway_url: str, companion_url: str, timeout: float = 5.0) -> bool:
    """Tell the gateway (v3.0) where this companion lives, so it can show a
    "Companion" tab that links back here. Best-effort; older gateways 404."""
    import httpx

    if not gateway_url or not companion_url:
        return False
    base = gateway_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}/api/companion", json={"url": companion_url})
            return r.status_code < 400
    except Exception as e:
        log.info("companion registration skipped: %s", e)
        return False


def build_sync_patch(gw: dict) -> dict:
    """Map a gateway /api/config document to a companion config patch.

    Only present, well-typed fields are included; the MQTT password, transport
    type and gateway URL are intentionally left untouched (companion-owned).
    """
    grid: dict = {}
    if isinstance(gw.get("gridRows"), int):
        grid["rows"] = max(1, gw["gridRows"])
    if isinstance(gw.get("gridCols"), int):
        grid["cols"] = max(1, gw["gridCols"])

    mqtt: dict = {}
    if isinstance(gw.get("mqHost"), str) and gw["mqHost"]:
        mqtt["broker"] = gw["mqHost"]
    if isinstance(gw.get("mqPort"), int):
        mqtt["port"] = gw["mqPort"]
    if isinstance(gw.get("mqUser"), str):
        mqtt["username"] = gw["mqUser"]
    if isinstance(gw.get("mqPfx"), str) and gw["mqPfx"]:
        mqtt["prefix"] = gw["mqPfx"]

    patch: dict = {}
    if grid:
        patch["grid"] = grid
    if mqtt:
        patch["transport"] = {"mqtt": mqtt}
    return patch
