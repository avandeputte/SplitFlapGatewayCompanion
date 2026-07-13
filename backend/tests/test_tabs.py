"""Tests for the tab advertisement exchanged with the gateway (see app/tabs.py).

The contract has to survive every old/new pairing, so the cases below pin both
directions: what we send to a gateway, and what we do with (or without) the tab
list it sends back.
"""

import asyncio
import re
from pathlib import Path

import pytest

import app.gateway as gw
from app.tabs import COMPANION_TABS, clean_tabs

INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "static" / "index.html"

# This suite is synchronous (the venv has no pytest-asyncio), so drive the coroutines.
run = asyncio.run


class FakeDisplay:
    """Stands in for a Display: post_companion only needs somewhere to put the tabs
    the gateway advertised. They used to live in a module global, which with two
    gateways was last-writer-wins — see display.py."""

    def __init__(self):
        self.gateway_tabs = []


@pytest.fixture
def disp():
    return FakeDisplay()


def test_companion_tabs_match_the_ui():
    """COMPANION_TABS is what we tell the gateway we have — so it must be what the
    nav actually has, or the gateway renders links to tabs we don't own."""
    nav = re.search(r'<nav class="tabs".*?</nav>', INDEX_HTML.read_text("utf-8"), re.S).group(0)
    in_ui = re.findall(r'data-tab="([^"]+)"[^>]*>([^<]+)<', nav)
    assert [{"id": i, "label": l} for i, l in in_ui] == COMPANION_TABS


# --- clean_tabs: a peer's list is untrusted (the ids land in hrefs) -------------
def test_clean_tabs_accepts_a_well_formed_list():
    raw = [{"id": "modules", "label": "Modules"}, {"id": "settings", "label": "Settings"}]
    assert clean_tabs(raw) == raw


def test_clean_tabs_keeps_only_id_and_label():
    assert clean_tabs([{"id": "apps", "label": "Apps", "evil": "x"}]) == [{"id": "apps", "label": "Apps"}]


@pytest.mark.parametrize("raw", [
    None, {}, "modules", [], [{"id": "apps"}], [{"label": "Apps"}],
    [{"id": "", "label": "Apps"}],
    [{"id": "a b", "label": "Apps"}],                    # space — not href-safe
    [{"id": "../../evil", "label": "Apps"}],             # path traversal in the hash
    [{"id": "apps", "label": ""}],
    [{"id": "apps", "label": "x" * 25}],                 # over the label cap
    [{"id": "x" * 25, "label": "Apps"}],                 # over the id cap
    [{"id": "apps", "label": "a\nb"}],                   # control character
    [{"id": "a%d" % i, "label": "T"} for i in range(13)],  # over the count cap
    [{"id": "ok", "label": "Ok"}, "junk"],               # one bad entry poisons the list
])
def test_clean_tabs_rejects_junk(raw):
    assert clean_tabs(raw) == []


# --- registration: we advertise ours, and read theirs back ---------------------
class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    """Stands in for httpx.AsyncClient: records the body, replies with `payload`."""

    def __init__(self, payload, status=200):
        self.payload, self.status, self.body = payload, status, None

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.body = json
        return _FakeResponse(self.payload, self.status)


@pytest.fixture
def fake_gateway(monkeypatch):
    import httpx

    def _install(payload, status=200):
        client = _FakeClient(payload, status)
        monkeypatch.setattr(httpx, "AsyncClient", client)
        return client
    return _install


def test_registration_advertises_our_tabs_and_stores_theirs(fake_gateway, disp):
    theirs = [{"id": "modules", "label": "Modules"}, {"id": "settings", "label": "Settings"}]
    client = fake_gateway({"url": "http://c", "status": "", "gwTabs": theirs})

    assert run(gw.post_companion("http://gw", url="http://c", status="idle",
                                 display=disp)) is True
    assert client.body["tabs"] == COMPANION_TABS
    assert disp.gateway_tabs == theirs


def test_old_gateway_without_gwtabs_leaves_us_with_no_advertisement(fake_gateway, disp):
    """A pre-3.4 gateway just echoes url/status. We keep nothing, and the UI falls
    back to its built-in list — including the Backup tab such a gateway still has."""
    client = fake_gateway({"url": "http://c", "status": ""})

    assert run(gw.post_companion("http://gw", url="http://c", display=disp)) is True
    assert client.body["tabs"] == COMPANION_TABS   # harmless: it ignores the field
    assert disp.gateway_tabs == []


def test_junk_gwtabs_is_ignored(fake_gateway, disp):
    fake_gateway({"url": "http://c", "gwTabs": [{"id": "../evil", "label": "X"}]})
    run(gw.post_companion("http://gw", url="http://c", display=disp))
    assert disp.gateway_tabs == []


def test_non_json_reply_is_ignored(fake_gateway, disp):
    fake_gateway(ValueError("not json"))
    assert run(gw.post_companion("http://gw", url="http://c", display=disp)) is True
    assert disp.gateway_tabs == []


def test_advertisement_survives_a_status_only_heartbeat(fake_gateway, disp):
    """A heartbeat with no url carries no tabs — it must not wipe what we know."""
    fake_gateway({"url": "http://c", "gwTabs": [{"id": "modules", "label": "Modules"}]})
    run(gw.post_companion("http://gw", url="http://c", display=disp))
    assert disp.gateway_tabs == [{"id": "modules", "label": "Modules"}]

    client = fake_gateway({"url": "http://c", "status": "Running"})
    run(gw.post_companion("http://gw", status="Running: Weather", display=disp))
    assert "tabs" not in client.body           # nothing to say about tabs
    assert disp.gateway_tabs == [{"id": "modules", "label": "Modules"}]


def test_deregister_does_not_advertise(fake_gateway):
    client = fake_gateway({"url": "", "status": ""})
    run(gw.post_companion("http://gw", url=""))
    assert "tabs" not in client.body
