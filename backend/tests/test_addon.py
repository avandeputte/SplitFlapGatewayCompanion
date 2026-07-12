"""Tests for the Home Assistant add-on packaging (addon/) and the two things it needs
from the app: reading /data/options.json, and serving the SPA under an ingress prefix.

The add-on runs the SAME image the project publishes — there is no add-on build — so the
failure modes are not compile errors, they are silent ones:

* ``version`` drifting from VERSION, so Supervisor pulls a tag that was never pushed.
* An option in config.yaml that nothing reads, so the toggle does nothing.
* An absolute ``/api/...`` URL in the SPA, which under ingress resolves against the Home
  Assistant root and 404s — the page loads and then quietly does nothing.

None of those show up in a unit test of the app, so they get one here.
"""

import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "app" / "static"

# Every add-on this repository publishes: any directory with a config.yaml. Today that
# is the beta channel alone; the stable one (addon/) joins it at v1.5.0, and these tests
# then cover it too without being edited.
CHANNELS = {p.parent.name: yaml.safe_load(p.read_text("utf-8"))
            for p in sorted(ROOT.glob("*/config.yaml"))}
ADDON = CHANNELS["addon-beta"]


# --- the manifest -------------------------------------------------------------
def test_the_beta_addon_version_matches_the_release():
    """`image` has no build, so Supervisor pulls ghcr...:<version>. If this drifts from
    VERSION, the tag it asks for was never published and the install just fails."""
    assert ADDON["version"] == (ROOT / "VERSION").read_text().strip()


def test_each_channel_has_its_own_slug():
    """A slug is the add-on's identity. If the stable channel ever shared the beta's, it
    would collide with an installed beta instead of sitting beside it."""
    slugs = [c["slug"] for c in CHANNELS.values()]
    assert len(slugs) == len(set(slugs)), f"duplicate slug across channels: {slugs}"


def test_the_beta_channel_is_flagged_as_such():
    assert ADDON["slug"].endswith("_beta")
    assert ADDON["stage"] == "experimental"
    assert "beta" in ADDON["name"].lower()


def test_the_manifest_has_what_supervisor_requires():
    for key in ("name", "version", "slug", "description", "arch"):
        assert ADDON.get(key), f"missing required key: {key}"
    assert set(ADDON["arch"]) <= {"aarch64", "amd64", "armv7", "armhf", "i386"}


def test_ingress_is_on_and_points_at_the_app_port():
    assert ADDON["ingress"] is True
    assert ADDON["ingress_port"] == 8000


def test_the_port_is_published_for_the_non_browser_clients():
    """Ingress serves the UI, but a Vestaboard rest_command and an MCP client are not the
    HA frontend — they need a real port or they cannot reach us at all."""
    assert ADDON["ports"]["8000/tcp"] == 8000


def test_every_option_is_actually_read_by_the_app():
    """An option nothing reads is a switch that does nothing — worse than no switch."""
    from app import config as cfg

    known = set(cfg.DEFAULTS) | {"gateway_url", "mqtt_password", "companion_public_url",
                                 "home_assistant", "vestaboard_key", "mcp_token", "log_level"}
    for opt in set(ADDON["options"]) | set(ADDON["schema"]):
        assert opt in known, f"config.yaml offers {opt!r}, but nothing reads it"


def test_the_defaults_are_safe():
    """The two write surfaces stay off until asked for, and the add-on ships HA-themed."""
    assert ADDON["options"]["vestaboard"] is False
    assert ADDON["options"]["mcp"] is False
    assert ADDON["options"]["theme"] == "ha"


# --- /data/options.json -------------------------------------------------------
@pytest.fixture
def options(tmp_path, monkeypatch):
    """Stand in for Supervisor writing the user's Configuration tab to /data."""
    from app import config as cfg

    def write(doc):
        p = tmp_path / "options.json"
        p.write_text(json.dumps(doc))
        monkeypatch.setattr(cfg, "ADDON_OPTIONS", p)
        return cfg
    return write


def test_addon_options_become_config(options):
    cfg = options({"gateway_url": "http://gw.local", "mcp": True, "vestaboard": True,
                   "theme": "ha", "mqtt_password": "sekrit"})
    ov = cfg._addon_overrides()
    assert ov["transport"]["gateway_url"] == "http://gw.local"
    assert ov["transport"]["mqtt"]["password"] == "sekrit"
    assert ov["mcp"]["enabled"] is True
    assert ov["vestaboard"]["enabled"] is True
    assert ov["theme"] == "ha"


def test_blank_optional_fields_do_not_clobber_defaults(options):
    """HA writes "" for an untouched optional field. That must mean "not set", not
    "set to empty" — an empty gateway_url would override a real one from the env."""
    cfg = options({"gateway_url": "", "mqtt_password": "", "companion_public_url": ""})
    assert cfg._addon_overrides() == {}


def test_no_options_file_means_we_are_not_an_addon(options, tmp_path, monkeypatch):
    from app import config as cfg
    monkeypatch.setattr(cfg, "ADDON_OPTIONS", tmp_path / "nope.json")
    assert cfg._addon_overrides() == {}
    assert cfg.addon_options() == {}


def test_env_still_beats_the_addon_options(options, monkeypatch):
    """A hand-run container has to be able to override anything, add-on or not."""
    cfg = options({"gateway_url": "http://from-addon"})
    monkeypatch.setenv("GATEWAY_URL", "http://from-env")
    c = cfg.Config()
    assert c.transport["gateway_url"] == "http://from-env"


def test_the_log_level_option_is_honoured(options):
    """It is read at import, before Config exists, so it bypasses the merge entirely —
    which is exactly how it would end up silently ignored."""
    cfg = options({"log_level": "DEBUG"})
    assert cfg.addon_option("log_level", "INFO") == "DEBUG"


# --- the startup preflight ----------------------------------------------------
# The container's CMD is `python -m app`, and its guard ran on os.environ alone. An
# add-on has NO env vars — the URL is typed into the Configuration tab and lands in
# /data/options.json — so a correctly-configured add-on died at startup with
# "GATEWAY_URL is not set", before Config (which does read that file) was ever built.
def test_the_preflight_accepts_a_gateway_url_from_the_addon_options(options):
    from app.__main__ import gateway_url
    options({"gateway_url": "http://gw.local"})
    assert gateway_url() == "http://gw.local"


def test_the_preflight_still_prefers_the_environment(options, monkeypatch):
    from app.__main__ import gateway_url
    options({"gateway_url": "http://from-addon"})
    monkeypatch.setenv("GATEWAY_URL", "http://from-env")
    assert gateway_url() == "http://from-env"


def test_the_preflight_still_refuses_when_nothing_is_set(options, monkeypatch):
    from app.__main__ import gateway_url
    options({})
    monkeypatch.delenv("GATEWAY_URL", raising=False)
    assert gateway_url() == ""


# --- ingress ------------------------------------------------------------------
def test_the_spa_makes_no_absolute_api_calls():
    """Under ingress the page lives at /api/hassio_ingress/<token>/. A fetch to
    "/api/apps" resolves against the HA root, not ours — the UI would load and then do
    nothing. Every request must go through url(), which prefixes window.__BASE__."""
    js = (STATIC / "app.js").read_text("utf-8")
    bare = re.findall(r'fetch\(\s*["`]/', js)
    assert not bare, f"{len(bare)} fetch() call(s) bypass url() and would break under ingress"


def test_the_shell_carries_the_ingress_prefix():
    from app import main
    c = TestClient(main.app)
    html = c.get("/", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"}).text

    assert 'window.__BASE__="/api/hassio_ingress/abc"' in html
    assert 'href="/api/hassio_ingress/abc/styles.css' in html
    assert 'src="/api/hassio_ingress/abc/app.js' in html


def test_without_ingress_the_urls_are_untouched():
    from app import main
    c = TestClient(main.app)
    html = c.get("/").text
    assert 'window.__BASE__=""' in html
    assert 'href="/styles.css' in html


# --- the theme ----------------------------------------------------------------
def test_the_ha_theme_is_layered_over_the_base_stylesheet(monkeypatch):
    from app import main
    monkeypatch.setitem(main.config._effective, "theme", "ha")
    html = TestClient(main.app).get("/").text
    # both, and in this order — the theme only overrides, it does not replace
    assert html.index("styles.css") < html.index("theme-ha.css")


def test_the_default_theme_does_not_load_it():
    from app import main
    assert main.config.theme == "default"
    assert "theme-ha.css" not in TestClient(main.app).get("/").text


def test_the_theme_leaves_the_flaps_alone():
    """.board/.flap are hardcoded darks because they depict physical flaps. Re-skinning
    them in HA's palette would be a lie about the hardware."""
    css = (STATIC / "theme-ha.css").read_text("utf-8")
    assert not re.search(r"^\.flap\s*\{", css, re.M)
    assert not re.search(r"^\.board\s*\{", css, re.M)


# --- the URL we register with the gateway -------------------------------------
# An add-on container sits on Home Assistant's internal bridge, so asking the OS which
# interface reaches the gateway truthfully answers 172.30.33.x. That is the container's
# address; no ESP32 on the LAN can reach it, and it is what we were registering.
import asyncio


class _FakeSupervisor:
    """Answers /addons/self/info and /network/info like Supervisor does."""

    def __init__(self, port=8000, ip="192.168.1.50/24", primary=True, fail=False):
        self.port, self.ip, self.primary, self.fail = port, ip, primary, fail
        self.seen = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        if self.fail:
            raise OSError("supervisor unreachable")
        self.seen.append((url, (headers or {}).get("Authorization")))
        if url.endswith("/addons/self/info"):
            body = {"data": {"network": {"8000/tcp": self.port} if self.port else {}}}
        else:
            body = {"data": {"interfaces": [
                {"interface": "hassio", "primary": False, "ipv4": {"ip_address": "172.30.33.4/23"}},
                {"interface": "eth0", "primary": self.primary, "ipv4": {"ip_address": self.ip}},
            ]}}
        return _Resp(body)


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def supervisor(monkeypatch):
    import httpx
    from app import gateway

    def install(**kw):
        fake = _FakeSupervisor(**kw)
        monkeypatch.setattr(httpx, "AsyncClient", fake)
        monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
        return fake, gateway
    return install


def test_we_register_the_hosts_address_not_the_containers(supervisor):
    fake, gateway = supervisor()
    assert asyncio.run(gateway.addon_public_url(8000)) == "http://192.168.1.50:8000"
    # ...and it authenticated with the Supervisor token.
    assert all(auth == "Bearer tok" for _, auth in fake.seen)


def test_the_published_host_port_is_used_not_our_own(supervisor):
    """The user can remap the add-on's port; the gateway must be told the one the LAN
    can actually connect to, not the 8000 we listen on inside the container."""
    _, gateway = supervisor(port=8123)
    assert asyncio.run(gateway.addon_public_url(8000)) == "http://192.168.1.50:8123"


def test_an_unpublished_port_registers_nothing(supervisor):
    """With no host port there is no way in, and a URL would be a lie."""
    _, gateway = supervisor(port=None)
    assert asyncio.run(gateway.addon_public_url(8000)) == ""


def test_a_non_primary_interface_still_beats_the_bridge(supervisor):
    _, gateway = supervisor(primary=False)
    assert asyncio.run(gateway.addon_public_url(8000)) == "http://192.168.1.50:8000"


def test_an_unreachable_supervisor_is_not_fatal(supervisor):
    _, gateway = supervisor(fail=True)
    assert asyncio.run(gateway.addon_public_url(8000)) == ""


def test_outside_an_addon_we_do_not_ask_supervisor(monkeypatch):
    from app import gateway
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert asyncio.run(gateway.addon_public_url(8000)) == ""


def test_the_addon_may_call_the_supervisor_api():
    """/network/info is refused without this, and we would silently fall back to the
    container's own address again."""
    assert ADDON["hassio_api"] is True
