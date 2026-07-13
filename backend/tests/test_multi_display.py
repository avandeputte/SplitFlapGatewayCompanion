"""Phases 2-4: the surfaces that used to assume one wall.

Each of these is a place where a naive implementation is silently wrong rather than
visibly broken — it drives *a* display, just not the one you meant:

  * MQTT/Home Assistant — node_id and topic_prefix come from config, which is the SAME
    object shape for every display. Two walls would publish to one set of topics under
    one device identifier, and the second wall's discovery would overwrite the first's.
  * the gateway proxy — it rewrites the proxied page's own links to a base, so the wall
    has to be in the PATH; a ?display= would survive exactly one request.
  * the Vestaboard API — a Vestaboard *is* one board and its clients post to a fixed
    path with no way to name a wall.
  * MCP — every existing prompt was written when there was one display and passes no id.

The through-line: whatever addresses no display must land on the DEFAULT one, and must
keep working untouched.
"""
from pathlib import Path

import pytest

from app.config import Config
from app.display import Display, DisplayManager
from app.homeassistant import HomeAssistant
from app.registry import DisplayRegistry

APPS_DIR = Path(__file__).resolve().parents[2] / "apps"


def _display(tmp_path, id="default", name="SplitFlap", gateway_url="http://gw"):
    return Display.build(apps_dir=APPS_DIR, id=id, name=name, data_dir=tmp_path,
                         gateway_url=gateway_url, own_settings=True)


# ---------------------------------------------------------------------------
# MQTT / Home Assistant: one device per wall
# ---------------------------------------------------------------------------
def test_the_default_display_keeps_its_historic_ha_ids(tmp_path):
    """Suffixing the default would orphan every existing entity and silently break any
    automation pointing at select.splitflap_companion_app. Existing installs are the
    overwhelming majority — they must not notice this release."""
    d = _display(tmp_path)
    assert d.ha.node == "splitflap-companion"
    assert d.ha.tp == "splitflap-companion"
    assert d.ha._device()["identifiers"] == ["splitflap-companion"]
    assert d.ha._device()["name"] == "SplitFlap Companion"


def test_a_second_display_gets_its_own_device_and_topics(tmp_path):
    a = _display(tmp_path / "a", id="default")
    b = _display(tmp_path / "b", id="office", name="Office wall")

    assert b.ha.node != a.ha.node, "two walls, one MQTT node id — they would fight"
    assert b.ha.tp != a.ha.tp
    assert b.ha._device()["identifiers"] != a.ha._device()["identifiers"]
    assert "Office wall" in b.ha._device()["name"]

    # every topic a display publishes on must be its own
    assert b.ha._avail() != a.ha._avail()
    assert b.ha._state("app") != a.ha._state("app")
    assert b.ha._cmd("app") != a.ha._cmd("app")
    assert b.ha._disc_topic("select", "app") != a.ha._disc_topic("select", "app")


def test_entity_unique_ids_do_not_collide(tmp_path):
    a = _display(tmp_path / "a", id="default")
    b = _display(tmp_path / "b", id="office")
    ids_a = {e[2]["unique_id"] for e in a.ha._discovery()}
    ids_b = {e[2]["unique_id"] for e in b.ha._discovery()}
    assert not (ids_a & ids_b), "Home Assistant would merge these into one entity"


# ---------------------------------------------------------------------------
# the gateway proxy: the wall is in the path
# ---------------------------------------------------------------------------
class _D:
    def __init__(self, id, gateway_url):
        self.id, self.gateway_url = id, gateway_url


class _Displays:
    def __init__(self, *d):
        self._d = {x.id: x for x in d}

    def get(self, i):
        return self._d.get(i) if i else None

    @property
    def default(self):
        return next(iter(self._d.values()))


def test_the_proxy_routes_by_display_id(monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app import gwproxy

    seen = {}

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, target, **kw):
            seen["target"] = target

            class R:
                status_code = 200
                headers = {"content-type": "text/plain"}
                content = b"ok"
                text = "ok"
            return R()

    monkeypatch.setattr(gwproxy.httpx, "AsyncClient", FakeClient)
    app = FastAPI()
    app.include_router(gwproxy.build(_Displays(_D("default", "http://kitchen"),
                                               _D("office", "http://office"))))
    c = TestClient(app)

    # a bare /gw/ path is the DEFAULT wall — every existing bookmark and every link the
    # gateway itself emits relies on that
    c.get("/gw/status")
    assert seen["target"] == "http://kitchen/status"

    # …and a display id in the path picks the other one
    c.get("/gw/office/status")
    assert seen["target"] == "http://office/status"

    # a gateway path that merely LOOKS like an id is still a path on the default gateway
    c.get("/gw/settings")
    assert seen["target"] == "http://kitchen/settings"


def test_the_proxy_base_carries_the_display(monkeypatch):
    """The rewritten <base> is what the proxied page's own links resolve against. If it
    dropped the display, the first click inside the office gateway's page would land you
    on the kitchen gateway."""
    from starlette.datastructures import Headers

    from app import gwproxy

    class Req:
        headers = Headers({})
    base, companion = gwproxy._bases(Req(), "office")
    assert base.endswith("/gw/office")
    base, _ = gwproxy._bases(Req(), "")
    assert base.endswith("/gw")


# ---------------------------------------------------------------------------
# what addresses no display lands on the default
# ---------------------------------------------------------------------------
def test_the_vestaboard_api_is_addressable_both_ways():
    """/local-api/message must keep driving the default wall — a Vestaboard IS one board
    and ha-vestaboard posts to that fixed path. The path form reaches the others."""
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/local-api/message" in paths
    assert "/local-api/{display_id}/message" in paths


def test_the_registry_api_exists():
    from app.main import app
    paths = {getattr(r, "path", "") for r in app.routes}
    for p in ("/api/displays", "/api/displays/{display_id}",
              "/api/displays/{display_id}/default"):
        assert p in paths, p


# ---------------------------------------------------------------------------
# GATEWAY_URL takes a list
# ---------------------------------------------------------------------------
def test_gateway_url_accepts_a_comma_delimited_list(tmp_path):
    """One add-on option, two walls: a Home Assistant user configures both from the
    Configuration tab they already have."""
    reg = DisplayRegistry(tmp_path).ensure(gateway_url="http://kitchen")
    added = reg.adopt_env_gateways(["http://kitchen", "http://office", "http://hall"])

    assert [r.id for r in added] == ["display-2", "display-3"]
    assert reg.get("display-2").gateway_url == "http://office"
    assert reg.default_id == "default", "the first entry stays the default wall"


def test_re_reading_the_list_does_not_duplicate(tmp_path):
    reg = DisplayRegistry(tmp_path).ensure(gateway_url="http://kitchen")
    urls = ["http://kitchen", "http://office"]
    reg.adopt_env_gateways(urls)
    reg.adopt_env_gateways(urls)          # every boot
    assert len(reg.all()) == 2


def test_dropping_a_url_from_the_list_never_removes_a_display(tmp_path):
    """Someone who adds a wall in the UI and later edits the env for an unrelated reason
    must not silently lose it — along with its playlists and triggers."""
    reg = DisplayRegistry(tmp_path).ensure(gateway_url="http://kitchen")
    reg.add(name="Office", gateway_url="http://office")
    reg.adopt_env_gateways(["http://kitchen"])     # the env no longer mentions the office
    assert "office" in reg.ids()


# ---------------------------------------------------------------------------
# the SPA follows the active wall
# ---------------------------------------------------------------------------
APP_JS = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text("utf-8")


def test_every_api_call_carries_the_display():
    """url() is the single funnel every fetch goes through — the client-side twin of the
    server's display_for(). Switching walls has to be ONE variable, not a change at each
    of the ~40 call sites."""
    assert 'let DISPLAY = ""' in APP_JS
    assert 'display=" + encodeURIComponent(DISPLAY)' in APP_JS


def test_switching_rereads_everything_the_old_wall_owned():
    """Geometry, apps, playlists, triggers and the gateway's tabs all belong to a wall.
    Patching part of the screen is how you show one wall's apps on another's grid."""
    body = APP_JS[APP_JS.index("async function switchDisplay("):]
    body = body[:body.index("\n}")]
    for fn in ("bootGrid", "loadApps", "loadPlaylists", "loadTriggers", "setupGatewayTabs"):
        assert fn in body, f"switching displays does not re-read {fn}"


def test_the_switcher_is_invisible_with_one_display():
    """Nearly everyone has one wall, and their UI must look exactly as it did."""
    assert 'sel.classList.toggle("hidden", DISPLAYS.length < 2)' in APP_JS


def test_the_chosen_wall_is_remembered():
    assert 'localStorage.setItem("splitflap.display"' in APP_JS
