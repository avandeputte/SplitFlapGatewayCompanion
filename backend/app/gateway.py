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
import threading

log = logging.getLogger("companion.gateway")

# Set while a settings blob is being uploaded to / downloaded from the gateway. The
# engine yields to it so display frames don't compete for the gateway's attention
# mid-transfer. Thread-safe (the transfer runs in a background thread).
_settings_transfer = threading.Event()


def settings_active() -> bool:
    """True while a settings upload/download is in flight (engine pauses sending)."""
    return _settings_transfer.is_set()


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


def gateway_version(gw: dict) -> tuple[int, int] | None:
    """(major, minor) parsed from a gateway /api/config document, or None."""
    import re

    raw = gw.get("version") or gw.get("firmwareVersion") or gw.get("fw") or ""
    m = re.search(r"(\d+)\.(\d+)", str(raw))
    return (int(m.group(1)), int(m.group(2))) if m else None


def supports_settings(gw: dict) -> bool:
    """Whether the gateway can store the companion's settings (Gateway 3.1+)."""
    v = gateway_version(gw)
    return v is not None and v >= (3, 1)


# --- companion settings blob, stored on the gateway (3.1+), gzipped -----------
# These are synchronous (they run in a background debounce thread and at startup),
# unlike the async helpers above. Contract with the gateway firmware:
#   GET  /api/companion/settings -> 200 + gzipped JSON body, or 404/204 when none
#   PUT  /api/companion/settings <- gzipped JSON body (store atomically), 2xx on ok

def fetch_gateway_settings(url: str, timeout: float = 8.0) -> dict | None:
    """Fetch + decompress the stored settings doc, or None (nothing stored / error)."""
    import gzip
    import json

    import httpx

    base = url.rstrip("/")
    _settings_transfer.set()      # pause the display send loop for the transfer
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{base}/api/companion/settings")
        if r.status_code != 200 or not r.content:
            return None
        raw = r.content
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError):
            pass   # tolerate an uncompressed body
        doc = json.loads(raw.decode("utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception as e:
        log.warning("could not fetch settings from gateway: %s", e)
        return None
    finally:
        _settings_transfer.clear()


def push_gateway_settings(url: str, doc: dict, timeout: float = 8.0) -> bool:
    """Compress + PUT the settings doc to the gateway. Best-effort (returns success)."""
    import gzip
    import json

    import httpx

    base = url.rstrip("/")
    _settings_transfer.set()      # pause the display send loop for the transfer
    try:
        body = gzip.compress(json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
        with httpx.Client(timeout=timeout) as client:
            r = client.put(f"{base}/api/companion/settings", content=body,
                           headers={"Content-Type": "application/gzip"})
        return r.status_code < 400
    except Exception as e:
        log.warning("could not push settings to gateway: %s", e)
        return False
    finally:
        _settings_transfer.clear()


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
