"""Tests for serving the gateway's UI through the companion (app/gwproxy.py).

Home Assistant can only put the ADD-ON's own port in the sidebar, and the gateway is a
different device — so the only way its UI appears inside HA is if we serve it. Which means
the page has to be rewritten on the way through, and each rewrite has a way of failing
silently:

* Miss its ``fetch("/api/...")`` calls and they hit the COMPANION's API instead — a
  different API that answers 404, or worse, answers.
* Miss ``target='_top'`` and the first click on the gateway's Companion tab throws the
  user out of Home Assistant, which is the bug this exists to fix.
* Miss the companion URL the gateway hands its own page (the LAN address it registered)
  and the link back leaves HA even without a target.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import gwproxy

GATEWAY_PAGE = (
    '<!DOCTYPE html><html><head><title>Split-Flap Gateway</title>'
    '<link rel="icon" href="/favicon.svg"><style>:root{--bg:#1a1a2e}</style></head>'
    '<body><a href="/ota">OTA</a>'
    '<script>fetch("/api/status").then(r=>r.json());</script></body></html>'
)

INGRESS = {"X-Ingress-Path": "/api/hassio_ingress/tok"}


class _Cfg:
    transport = {"gateway_url": "http://192.168.1.229"}


class _FakeGateway:
    """Stands in for the ESP32: returns the page, or JSON for /api/*."""

    def __init__(self, body=GATEWAY_PAGE, ctype="text/html", status=200):
        self.body, self.ctype, self.status = body, ctype, status
        self.requested = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, params=None, content=None, headers=None):
        self.requested.append((method, url, dict(headers or {})))
        return _Resp(self.body, self.ctype, self.status)


class _Resp:
    def __init__(self, body, ctype, status):
        self.text = body if isinstance(body, str) else body.decode()
        self.content = body.encode() if isinstance(body, str) else body
        self.status_code = status
        self.headers = {"content-type": ctype, "etag": "W/x"}

    def json(self):
        return json.loads(self.text)


@pytest.fixture
def client(monkeypatch):
    def build(**kw):
        fake = _FakeGateway(**kw)
        monkeypatch.setattr(gwproxy.httpx, "AsyncClient", fake)
        app = FastAPI()
        app.include_router(gwproxy.build(_Cfg()))
        return TestClient(app), fake
    return build


def test_the_gateway_page_is_served_through_us(client):
    c, _ = client()
    r = c.get("/gw/")
    assert r.status_code == 200
    assert "Split-Flap Gateway" in r.text


def test_its_api_calls_are_aimed_back_at_the_gateway(client):
    """The page calls fetch("/api/status"). Served under /gw/, an untouched call would hit
    the COMPANION's /api/status — a different API. The shim re-points them."""
    c, _ = client()
    body = c.get("/gw/", headers=INGRESS).text
    assert 'var B = "/api/hassio_ingress/tok/gw"' in body
    assert "window.fetch = function" in body


def test_absolute_links_are_prefixed(client):
    c, _ = client()
    body = c.get("/gw/", headers=INGRESS).text
    assert 'href="/api/hassio_ingress/tok/gw/favicon.svg"' in body
    assert 'href="/api/hassio_ingress/tok/gw/ota"' in body


def test_top_targets_are_defused(client):
    """The gateway's Companion tabs are built with target='_top' — right when it's a
    foreign site, wrong when we are both inside Home Assistant: it breaks out of the
    sidebar, which is exactly the bug this proxy exists to fix."""
    c, _ = client()
    body = c.get("/gw/", headers=INGRESS).text
    assert 'a[target="_top"]' in body       # the click interceptor is installed


def test_the_ha_theme_is_always_injected(client):
    """The HA look is the project's only look. New gateway firmware ships it natively;
    the injection keeps an OLDER gateway matching, and is harmless on a new one."""
    c, _ = client()
    body = c.get("/gw/", headers=INGRESS).text
    assert 'href="/api/hassio_ingress/tok/gateway-theme.css"' in body


def test_the_gateway_is_told_to_link_back_through_ingress(client):
    """The gateway advertises us as the LAN URL we registered (http://192.168.1.220:8000).
    Its page builds the Companion tab from that, and following it would leave Home
    Assistant. Point it at our ingress path instead."""
    c, _ = client(body=json.dumps({"url": "http://192.168.1.220:8000", "status": "idle"}),
                  ctype="application/json")
    doc = c.get("/gw/api/companion", headers=INGRESS).json()
    assert doc["url"] == "/api/hassio_ingress/tok"


def test_outside_ingress_the_paths_stay_bare(client):
    """A plain Docker user browses the companion at its own root — no prefix to add."""
    c, _ = client()
    body = c.get("/gw/").text
    assert 'var B = "/gw"' in body
    assert 'href="/gw/ota"' in body


def test_conditional_headers_are_not_forwarded(client):
    """We rewrite the page, so the gateway's ETag is not an ETag for what we return — a
    304 would hand the browser an un-rewritten copy out of its cache."""
    c, fake = client()
    c.get("/gw/", headers={"If-None-Match": 'W/"x"'})
    sent = fake.requested[0][2]
    assert not any(k.lower() == "if-none-match" for k in sent)


def test_an_unreachable_gateway_is_a_502_not_a_crash(client, monkeypatch):
    class Boom(_FakeGateway):
        async def request(self, *a, **kw):
            raise OSError("no route to host")

    monkeypatch.setattr(gwproxy.httpx, "AsyncClient", Boom())
    app = FastAPI()
    app.include_router(gwproxy.build(_Cfg()))
    assert TestClient(app).get("/gw/").status_code == 502


def test_the_spa_links_gateway_tabs_through_the_proxy():
    """A direct link to the gateway's own address leaves Home Assistant."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text()
    assert 'a.href = `${url("/gw/")}#${t.id}`' in js
    assert 'a.target = "_top"' not in js


def test_setup_gateway_tabs_does_not_shadow_the_url_helper():
    """A regression that hid every gateway tab: setupGatewayTabs called the global url()
    helper for the proxy path, but a local `let url = ""` (the gateway's own URL) shadowed
    it, so `url("/gw/")` threw "url is not a function" and the tab loop died silently — no
    gateway tabs rendered at all. The href check above is a static string match and passed
    right through it; only the shadow matters at runtime."""
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text()
    body = re.search(r"function setupGatewayTabs\(\).*?\n}\n", js, re.S).group(0)
    assert not re.search(r"\b(?:let|const|var)\s+url\b", body), \
        "setupGatewayTabs declares a local `url`, shadowing the global url() helper it calls"
