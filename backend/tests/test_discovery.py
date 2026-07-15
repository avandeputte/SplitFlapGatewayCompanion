"""Gateway discovery (app/discovery.py): the fingerprint, the sweep, the marks.

Everything runs against httpx.MockTransport — a discovery test that probed the
real LAN (let alone live hardware) would be doing exactly what this project's
tests must never do. mDNS is switched off the same way: mdns=False.
"""
import asyncio

import httpx

from app import discovery


# ---------------------------------------------------------------------------
# the /api/config fingerprint
# ---------------------------------------------------------------------------
def test_fingerprint():
    assert discovery.looks_like_gateway({"gridRows": 3, "gridCols": 15})
    # the Matrix Portal serializes numbers as numbers, but be tolerant of strings
    assert discovery.looks_like_gateway({"gridRows": "3", "gridCols": "15"})
    assert not discovery.looks_like_gateway({"gridRows": 0, "gridCols": 15})
    assert not discovery.looks_like_gateway({"rows": 3, "cols": 15})
    assert not discovery.looks_like_gateway({"gridRows": "x", "gridCols": 15})
    assert not discovery.looks_like_gateway([1, 2])
    assert not discovery.looks_like_gateway(None)


# ---------------------------------------------------------------------------
# which subnets get swept
# ---------------------------------------------------------------------------
def test_subnets_come_from_known_gateways_then_our_own_interface(monkeypatch):
    monkeypatch.setattr(discovery, "detect_local_ip", lambda *_: "10.0.0.7")
    nets = discovery.candidate_subnets(
        ["http://192.168.1.229", "http://192.168.1.12:8080"])
    assert nets == ["192.168.1.0/24", "10.0.0.0/24"]


def test_subnets_never_include_the_ha_bridge(monkeypatch):
    # Inside the add-on the socket trick truthfully reports the container's own
    # 172.30.x.x address; sweeping that subnet would probe other add-ons.
    monkeypatch.setattr(discovery, "detect_local_ip", lambda *_: "172.30.33.4")
    assert discovery.candidate_subnets([]) == []


def test_subnets_skip_hostnames_without_choking(monkeypatch):
    monkeypatch.setattr(discovery, "detect_local_ip", lambda *_: None)
    assert discovery.candidate_subnets(["http://splitflap-gw.local"]) == []


# ---------------------------------------------------------------------------
# the sweep itself (a fake neighbourhood behind MockTransport)
# ---------------------------------------------------------------------------
def _quiet_env(monkeypatch):
    monkeypatch.setattr(discovery, "detect_local_ip", lambda *_: None)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)


def test_sweep_finds_a_gateway_and_ignores_the_neighbours(monkeypatch):
    _quiet_env(monkeypatch)

    def lan(request):
        if request.url.host == "192.168.1.229" and request.url.path == "/api/config":
            return httpx.Response(200, json={"gridRows": 3, "gridCols": 15,
                                             "version": "3.1", "name": "hall wall"})
        if request.url.host == "192.168.1.50":
            return httpx.Response(200, text="<html>a printer</html>")
        raise httpx.ConnectError("unreachable")

    found = asyncio.run(discovery.discover(
        ["http://192.168.1.10"], transport=httpx.MockTransport(lan), mdns=False))
    assert [f["url"] for f in found] == ["http://192.168.1.229"]
    f = found[0]
    assert (f["rows"], f["cols"], f["version"], f["name"]) == (3, 15, "3.1", "hall wall")
    assert f["known"] is False


def test_already_registered_gateways_are_marked_and_sorted_last(monkeypatch):
    _quiet_env(monkeypatch)

    def lan(request):
        if request.url.host in ("192.168.1.10", "192.168.1.229"):
            return httpx.Response(200, json={"gridRows": 2, "gridCols": 10})
        raise httpx.ConnectError("unreachable")

    found = asyncio.run(discovery.discover(
        ["http://192.168.1.10"], transport=httpx.MockTransport(lan), mdns=False))
    assert [(f["url"], f["known"]) for f in found] == [
        ("http://192.168.1.229", False),
        ("http://192.168.1.10", True),
    ]


def test_a_registered_custom_port_is_swept_too(monkeypatch):
    _quiet_env(monkeypatch)

    def lan(request):
        if request.url.host == "192.168.1.60" and request.url.port == 8080:
            return httpx.Response(200, json={"gridRows": 5, "gridCols": 15})
        raise httpx.ConnectError("unreachable")

    found = asyncio.run(discovery.discover(
        ["http://192.168.1.10:8080"], transport=httpx.MockTransport(lan), mdns=False))
    assert [f["url"] for f in found] == ["http://192.168.1.60:8080"]
