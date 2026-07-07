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


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def detect_local_ip(gateway_url: str = "") -> str | None:
    """Best-effort: the LAN IP of the interface that reaches the gateway.

    Opening a UDP socket toward an address (no packets sent) makes the OS pick
    the outbound interface; its local address is the IP the gateway would see.
    We only aim at IP targets (the gateway's IP if it's one, else a public IP)
    so we never do a slow/blocking hostname lookup on the event loop.
    """
    import ipaddress
    import socket

    targets = []
    host = _host_of(gateway_url)
    if host:
        try:
            ipaddress.ip_address(host)
            targets.append(host)   # only when the gateway URL is already an IP
        except ValueError:
            pass
    targets.append("8.8.8.8")      # public IP → gives the primary interface

    for target in targets:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            continue
        finally:
            s.close()
    return None


async def post_companion(gateway_url: str, *, url: str | None = None,
                         status: str | None = None, timeout: float = 5.0) -> bool:
    """Register / heartbeat / deregister with the gateway (v3.0+).

    ``url`` set → (re)register that URL; ``url=""`` → deregister; ``status`` →
    update the running-status the gateway shows on its status page. Best-effort:
    a transiently-unreachable gateway is simply retried on the next heartbeat.
    """
    import httpx

    if not gateway_url:
        return False
    body: dict = {}
    if url is not None:
        body["url"] = url
    if status is not None:
        body["status"] = status
    if not body:
        return False
    base = gateway_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}/api/companion", json=body)
            return r.status_code < 400
    except Exception as e:
        log.debug("companion post skipped: %s", e)
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
