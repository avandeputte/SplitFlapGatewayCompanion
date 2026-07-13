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

from .tabs import COMPANION_TABS, clean_tabs

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


# Home Assistant's internal bridge, the one the add-on container lives on. An address
# here is precisely the wrong answer — it is what we were already reporting.
HA_INTERNAL_NET = "172.30.32.0/23"


def _primary_ipv4(interfaces: list) -> str:
    """The host's LAN address: the primary interface's, else any other real one.

    Supervisor gives it in CIDR ("192.168.1.50/24"), and older versions used an `address`
    list where newer ones use `ip_address`. Addresses on the internal bridge are skipped
    in both passes — falling back to one would just re-report the container's own address
    under a different name.
    """
    import ipaddress

    internal = ipaddress.ip_network(HA_INTERNAL_NET)

    def addr_of(iface: dict) -> str:
        v4 = iface.get("ipv4") or {}
        a = v4.get("ip_address") or ""
        if not a:
            lst = v4.get("address") or []
            a = lst[0] if lst else ""
        a = a.split("/")[0]
        try:
            return "" if ipaddress.ip_address(a) in internal else a
        except ValueError:
            return ""

    for primary_only in (True, False):
        for iface in interfaces:
            if primary_only and not iface.get("primary"):
                continue
            if (a := addr_of(iface)):
                return a
    return ""


async def addon_public_url(container_port: int = 8000) -> str:
    """The URL a device on the LAN can actually reach us at, when we run as a Home
    Assistant add-on. Empty when we don't.

    Inside an add-on the container sits on Home Assistant's internal bridge, so
    ``detect_local_ip()`` — which asks the OS which interface reaches the gateway —
    truthfully answers with something like ``172.30.33.4``. That is right for the
    container and useless to an ESP32 on the LAN, and it is what we were handing the
    gateway to register and link back to.

    Neither half of the real answer is visible from in here: the host's own address, and
    the port our container is published on (the user can remap it). Supervisor knows both,
    so ask it. Requires ``hassio_api: true`` in the add-on manifest.
    """
    import os

    import httpx

    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return ""                       # not an add-on; the socket trick is right
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("http://supervisor/addons/self/info", headers=headers)
            info = r.json()
            info = info.get("data", info)
            r = await c.get("http://supervisor/network/info", headers=headers)
            net = r.json()
            net = net.get("data", net)
    except Exception as e:
        log.warning("add-on: could not ask Supervisor for the host address (%s) — "
                    "set the Companion URL option if the gateway can't reach us", e)
        return ""

    port = (info.get("network") or {}).get(f"{container_port}/tcp")
    if not port:
        log.warning("add-on: port %d/tcp is not published, so nothing on the LAN can "
                    "reach the companion. Publish it in the add-on's Network settings, "
                    "or set the Companion URL option.", container_port)
        return ""
    ip = _primary_ipv4(net.get("interfaces") or [])
    if not ip:
        log.warning("add-on: Supervisor reported no host IPv4 address")
        return ""
    url = f"http://{ip}:{port}"
    log.info("add-on: registering with the gateway as %s (the host's address, not the "
             "container's)", url)
    return url


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


# The tabs a gateway advertises about itself (Gateway 3.4+) belong to THAT gateway,
# so they live on its Display (display.gateway_tabs) — not here. As a module global
# this was last-writer-wins the moment a second gateway registered: the nav would
# show whichever one had most recently answered. post_companion() hands the tabs it
# learned to the display it was called for.


async def post_companion(gateway_url: str, *, url: str | None = None,
                         status: str | None = None, timeout: float = 5.0,
                         display=None) -> bool:
    """Register / heartbeat / deregister with the gateway (v3.0+).

    ``url`` set → (re)register that URL; ``url=""`` → deregister; ``status`` →
    update the running-status the gateway shows on its status page. Best-effort:
    a transiently-unreachable gateway is simply retried on the next heartbeat.

    Registering also advertises our tabs, and reads the gateway's own tabs back out
    of the reply (Gateway 3.4+) so each side's nav links exactly what the other has
    — see tabs.py. A gateway that answers without ``gwTabs`` is an older one: we
    keep whatever we had (i.e. nothing), and the UI falls back to its built-in list.

    ``display`` is the Display this gateway belongs to; the tabs it advertises are
    stored there. Omit it and the reply's tabs are simply not recorded (which is what
    a caller that only wants to heartbeat, or a test, wants).
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
    # Only advertise alongside a registration — a deregister (url="") is us going
    # away, and a status-only heartbeat has nothing to say about tabs.
    if url:
        body["tabs"] = COMPANION_TABS
    base = gateway_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base}/api/companion", json=body)
            if r.status_code < 400:
                try:
                    tabs = clean_tabs(r.json().get("gwTabs"))
                except Exception:
                    tabs = []          # not JSON, or no gwTabs: a pre-3.4 gateway
                if tabs and display is not None and tabs != display.gateway_tabs:
                    log.info("gateway advertises %d tabs: %s", len(tabs),
                             ", ".join(x["id"] for x in tabs))
                if tabs and display is not None:
                    display.gateway_tabs = tabs
            return r.status_code < 400
    except Exception as e:
        log.debug("companion post skipped: %s", e)
        return False


async def home_all(gateway_url: str, timeout: float = 10.0) -> bool:
    """Broadcast a Home to every module via ``POST /api/flap/home {"id": -1}``.

    The gateway sends the RS-485 broadcast (``m*h``) and returns immediately
    (fire-and-forget), so this resolves quickly. Raises on an unreachable gateway
    or a non-2xx response; returns True when the gateway accepts the command.
    """
    import httpx

    base = gateway_url.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base}/api/flap/home", json={"id": -1})
        r.raise_for_status()
        return r.status_code < 400


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
        log.debug("gateway settings GET -> %s (%d bytes)", r.status_code, len(r.content or b""))
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
        log.debug("gateway settings PUT %d gzip bytes -> %s", len(body), r.status_code)
        return r.status_code < 400
    except Exception as e:
        log.warning("could not push settings to gateway: %s", e)
        return False
    finally:
        _settings_transfer.clear()


def _as_int(v):
    """Coerce a gateway field to int — tolerating a numeric string — else None, so a
    grid resync isn't silently dropped if the firmware serializes numbers as strings."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def build_sync_patch(gw: dict) -> dict:
    """Map a gateway /api/config document to a companion config patch.

    Only present, well-typed fields are included; the MQTT password, transport
    type and gateway URL are intentionally left untouched (companion-owned).
    """
    grid: dict = {}
    rows, cols = _as_int(gw.get("gridRows")), _as_int(gw.get("gridCols"))
    if rows is not None:
        grid["rows"] = max(1, rows)
    if cols is not None:
        grid["cols"] = max(1, cols)

    mqtt: dict = {}
    if isinstance(gw.get("mqHost"), str) and gw["mqHost"]:
        mqtt["broker"] = gw["mqHost"]
    port = _as_int(gw.get("mqPort"))
    if port is not None:
        mqtt["port"] = port
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
