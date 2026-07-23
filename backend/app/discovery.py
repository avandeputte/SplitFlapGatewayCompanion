"""Find SplitFlap gateways on the LAN — for the Displays dialog, on demand only.

Two mechanisms run together; nothing here ever runs in the background:

* **An HTTP sweep.** Every SplitFlap-family gateway — ESP32 and Matrix Portal
  alike — answers ``GET /api/config`` with its grid (``gridRows``/``gridCols``),
  and that is the fingerprint. We probe the /24s the companion can honestly
  claim to be near: the subnets of gateways it already drives, the host's real
  LAN address (from Supervisor when we are the HA add-on — the container's own
  bridge address is the wrong subnet, see ``gateway.addon_public_url``), and
  the interface ``detect_local_ip()`` picks. Plain unicast HTTP, so it works
  from inside a bridged container, where multicast cannot.

* **mDNS, where it works.** Gateways advertise ``_http._tcp`` with a hostname
  starting with ``splitflap`` (the firmware uses ``splitflap-gw``). Multicast
  only reaches us on bare metal or host networking — in the HA add-on or a
  default Docker bridge the browse hears silence, which is why mDNS is the
  accelerator and not the mechanism. ``zeroconf`` is optional: not installed
  means the sweep runs alone.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from urllib.parse import urlparse

import httpx

from .gateway import HA_INTERNAL_NET, _primary_ipv4, detect_local_ip, gateway_version

log = logging.getLogger("companion.discovery")

PROBE_TIMEOUT = 0.6      # per host; a gateway on the LAN answers in tens of ms
PROBE_CONCURRENCY = 96
MDNS_WINDOW = 1.6        # seconds to listen for mDNS answers
MAX_SUBNETS = 3


def looks_like_gateway(doc) -> bool:
    """The /api/config fingerprint: a JSON object with an integer grid."""
    if not isinstance(doc, dict):
        return False
    try:
        return int(doc["gridRows"]) > 0 and int(doc["gridCols"]) > 0
    except (KeyError, TypeError, ValueError):
        return False


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def candidate_subnets(known_urls: list[str]) -> list[str]:
    """The /24s worth sweeping, best signal first: where the gateways we already
    drive live, then where we live. Capped, deduped, and never the HA internal
    bridge — sweeping our own container's subnet would probe other add-ons."""
    internal = ipaddress.ip_network(HA_INTERNAL_NET)
    nets: list[str] = []

    def add(ip: str):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return
        if addr.is_loopback or addr in internal:
            return
        net = str(ipaddress.ip_network(f"{ip}/24", strict=False))
        if net not in nets:
            nets.append(net)

    for u in known_urls:
        add(urlparse(u).hostname or "")
    add(detect_local_ip(known_urls[0] if known_urls else "") or "")
    return nets[:MAX_SUBNETS]


async def _supervisor_lan_ip() -> str:
    """The host's LAN address, by asking Supervisor — only meaningful (and only
    possible) when we run as the HA add-on."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return ""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("http://supervisor/network/info",
                            headers={"Authorization": f"Bearer {token}"})
            net = r.json()
            net = net.get("data", net)
        return _primary_ipv4(net.get("interfaces") or [])
    except Exception as e:
        log.debug("discovery: Supervisor network/info unavailable (%s)", e)
        return ""


async def _probe(client: httpx.AsyncClient, base: str, sem: asyncio.Semaphore) -> dict | None:
    """GET {base}/api/config; a dict entry when it fingerprints as a gateway."""
    async with sem:
        try:
            r = await client.get(f"{base}/api/config")
            doc = r.json()
        except Exception:
            return None
    if r.status_code != 200 or not looks_like_gateway(doc):
        return None
    v = gateway_version(doc)
    return {
        "url": base,
        "rows": int(doc["gridRows"]),
        "cols": int(doc["gridCols"]),
        "version": f"{v[0]}.{v[1]}" if v else "",
        "name": str(doc.get("name") or doc.get("hostname") or ""),
    }


async def _mdns_candidates(window: float = MDNS_WINDOW) -> list[str]:
    """Base URLs of _http._tcp services whose hostname says splitflap. Empty on
    any failure — including the common one, a network where multicast never
    reaches us."""
    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    except ImportError:
        return []

    found: list[str] = []
    resolves: list[asyncio.Task] = []
    try:
        azc = AsyncZeroconf()
    except OSError as e:
        log.debug("discovery: mDNS socket unavailable (%s)", e)
        return []

    def on_change(zeroconf, service_type, name, state_change):
        if state_change is not ServiceStateChange.Added:
            return
        if "splitflap" not in name.lower():
            return

        async def resolve():
            # The full window, in ms — an answer is legitimate right up to the
            # end of the browse.
            info = await azc.async_get_service_info(service_type, name, timeout=int(window * 1000))
            if not info:
                return
            if "splitflap" not in (info.server or "").lower() and "splitflap" not in name.lower():
                return
            for addr in info.parsed_scoped_addresses():
                if ":" in addr:
                    continue                      # v4 only, like the sweep
                port = info.port or 80
                found.append(f"http://{addr}" + (f":{port}" if port != 80 else ""))

        resolves.append(asyncio.ensure_future(resolve()))

    try:
        browser = AsyncServiceBrowser(azc.zeroconf, "_http._tcp.local.", handlers=[on_change])
        await asyncio.sleep(window)
        # Let in-flight resolves land BEFORE the browser is canceled and
        # zeroconf closed — fire-and-forget meant late answers were silently
        # dropped on the floor. A resolve during the gather can add more.
        while resolves:
            pending, resolves = resolves[:], []
            await asyncio.gather(*pending, return_exceptions=True)
        await browser.async_cancel()
    except Exception as e:
        log.debug("discovery: mDNS browse failed (%s)", e)
    finally:
        await azc.async_close()
    return found


async def discover(known_urls: list[str], *, transport: httpx.AsyncBaseTransport | None = None,
                   mdns: bool = True) -> list[dict]:
    """Scan for gateways. Returns entries ``{url, rows, cols, version, name,
    known}`` — ``known`` when a registered display already points there.

    ``transport`` exists for tests: probing real neighbours (let alone live
    hardware) from a test run is exactly what this project does not do.
    """
    mdns_task = asyncio.ensure_future(_mdns_candidates()) if mdns else None

    bases: list[str] = []
    subnets = candidate_subnets(known_urls)
    sup_ip = await _supervisor_lan_ip()
    if sup_ip:
        extra = candidate_subnets([f"http://{sup_ip}"])
        subnets = (subnets + [n for n in extra if n not in subnets])[:MAX_SUBNETS]

    # Gateways serve on :80; a display registered on a custom port means more of
    # the same could be out there, so sweep that port too.
    ports = {80} | {p for u in known_urls if (p := urlparse(u).port)}
    for net in subnets:
        for host in ipaddress.ip_network(net).hosts():
            for port in sorted(ports):
                bases.append(f"http://{host}" + (f":{port}" if port != 80 else ""))
    if mdns_task is not None:
        bases.extend(await mdns_task)

    seen: set[str] = set()
    bases = [b for b in bases if not (_norm(b) in seen or seen.add(_norm(b)))]
    log.info("discovery: probing %d addresses across %s", len(bases), subnets or "no subnets")

    sem = asyncio.Semaphore(PROBE_CONCURRENCY)
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, transport=transport) as client:
        results = await asyncio.gather(*(_probe(client, b, sem) for b in bases))

    known = {_norm(u) for u in known_urls}
    found: list[dict] = []
    for entry in results:
        if entry is None or _norm(entry["url"]) in {_norm(f["url"]) for f in found}:
            continue
        entry["known"] = _norm(entry["url"]) in known
        found.append(entry)
    found.sort(key=lambda e: (e["known"], e["url"]))
    log.info("discovery: found %d gateway(s)", len(found))
    return found
