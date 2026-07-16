"""The config merge is five layers (see config._recompute):

    defaults <- gateway sync <- options.json <- env <- identity

Two of those relationships had no test pinning them:

* **gateway sync stays BELOW env** — a value synced from the gateway's
  /api/config must never beat an explicit environment override;
* **the display's identity sits ABOVE env** — a display's own gateway_url IS
  which wall the Config is; if GATEWAY_URL outranked it, every display would be
  dragged onto the same gateway and the second wall would quietly drive the first.

Plus the env parser's failure mode: a junk integer must say WHICH variable.
"""
import pytest

from app import config
from app.config import Config


@pytest.fixture(autouse=True)
def _no_addon_options(tmp_path, monkeypatch):
    """Keep /data/options.json (the add-on layer) out of these tests, even on a
    machine where it exists."""
    monkeypatch.setattr(config, "ADDON_OPTIONS", tmp_path / "no-options.json")


def test_gateway_sync_stays_below_env(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_MQTT_BROKER", "broker-from-env")
    cfg = Config(data_dir=tmp_path)
    # the gateway sync path is Config.update (see gateway sync in main)
    cfg.update({"transport": {"mqtt": {"broker": "broker-from-gateway"}}})
    assert cfg.transport["mqtt"]["broker"] == "broker-from-env", \
        "a gateway-synced value beat an explicit env override"
    # …while the sync still lands wherever env is silent
    cfg.update({"transport": {"mqtt": {"port": 2883}}})
    assert cfg.transport["mqtt"]["port"] == 2883


def test_display_identity_outranks_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "http://wall-one")
    cfg = Config(data_dir=tmp_path, gateway_url="http://wall-two")
    assert cfg.transport["gateway_url"] == "http://wall-two", \
        "GATEWAY_URL dragged this display onto another wall's gateway"


def test_blank_identity_falls_through_to_env(tmp_path, monkeypatch):
    """Blank means 'no display registry in play' — the single-display case, where
    the env var is exactly what should win."""
    monkeypatch.setenv("GATEWAY_URL", "http://wall-one")
    cfg = Config(data_dir=tmp_path, gateway_url="")
    assert cfg.transport["gateway_url"] == "http://wall-one"


def test_identity_outlives_a_gateway_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_URL", "http://wall-one")
    cfg = Config(data_dir=tmp_path, gateway_url="http://wall-two")
    cfg.update({"transport": {"gateway_url": "http://from-sync"}})
    assert cfg.transport["gateway_url"] == "http://wall-two"


def test_a_junk_int_env_var_names_itself(tmp_path, monkeypatch):
    """int('eight') used to surface as a bare ValueError from deep inside the
    merge; the error must name the variable the user has to fix."""
    monkeypatch.setenv("COMPANION_PORT", "eight thousand")
    with pytest.raises(ValueError, match="COMPANION_PORT"):
        Config(data_dir=tmp_path)
