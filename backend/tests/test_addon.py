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
ADDON = yaml.safe_load((ROOT / "addon" / "config.yaml").read_text("utf-8"))
STATIC = ROOT / "backend" / "app" / "static"


# --- the manifest -------------------------------------------------------------
def test_the_addon_version_matches_the_release():
    """`image` has no build, so Supervisor pulls ghcr...:<version>. If this drifts from
    VERSION, the tag it asks for was never published and the install just fails."""
    assert ADDON["version"] == (ROOT / "VERSION").read_text().strip()


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
