"""ha_rest.py — reading Home Assistant entity states for the dashboard apps.

Covers the two ways in (Supervisor proxy vs configured URL/token), the ~8s cache with a
last-good-snapshot on transient failure, and the picker search — plus that the runtime
actually injects ``get_ha_states`` into an app that asks for it.
"""

import pytest

from app import ha_rest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Clean env + module cache each test — both are process-global."""
    for k in ("SUPERVISOR_TOKEN", "COMPANION_HA_URL", "COMPANION_HA_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    ha_rest._cache.update(at=0.0, states=[])
    yield
    ha_rest._cache.update(at=0.0, states=[])


class _Resp:
    def __init__(self, code, payload):
        self.status_code, self._payload = code, payload

    def json(self):
        return self._payload


# -- endpoint precedence ----------------------------------------------------

def test_endpoint_prefers_supervisor(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supertok")
    monkeypatch.setenv("COMPANION_HA_URL", "http://ha.local")
    monkeypatch.setenv("COMPANION_HA_TOKEN", "usertok")
    assert ha_rest.endpoint() == ("http://supervisor/core/api", "supertok")


def test_endpoint_falls_back_to_configured_url(monkeypatch):
    monkeypatch.setenv("COMPANION_HA_URL", "http://ha.local/")   # trailing slash trimmed
    monkeypatch.setenv("COMPANION_HA_TOKEN", "usertok")
    assert ha_rest.endpoint() == ("http://ha.local/api", "usertok")
    assert ha_rest.available() is True


def test_endpoint_none_when_unconfigured():
    assert ha_rest.endpoint() == (None, None)
    assert ha_rest.available() is False
    assert ha_rest.fetch_states() == []                          # no HTTP attempted


# -- fetch_states caching + resilience --------------------------------------

def test_fetch_states_caches_then_keeps_last_good(monkeypatch):
    monkeypatch.setenv("COMPANION_HA_URL", "http://ha.local")
    monkeypatch.setenv("COMPANION_HA_TOKEN", "tok")
    good = [{"entity_id": "light.k", "state": "on", "attributes": {}}]
    calls = {"n": 0}

    def ok(url, **kw):
        calls["n"] += 1
        return _Resp(200, good)

    monkeypatch.setattr(ha_rest.httpx, "get", ok)
    assert ha_rest.fetch_states() == good and calls["n"] == 1
    assert ha_rest.fetch_states() == good and calls["n"] == 1    # within TTL -> cache, no refetch

    def boom(url, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(ha_rest.httpx, "get", boom)
    assert ha_rest.fetch_states(force=True) == good              # transient failure -> last good, not []


def test_fetch_states_ignores_non_list_body(monkeypatch):
    monkeypatch.setenv("COMPANION_HA_URL", "http://ha.local")
    monkeypatch.setenv("COMPANION_HA_TOKEN", "tok")
    monkeypatch.setattr(ha_rest.httpx, "get", lambda url, **kw: _Resp(200, {"error": "nope"}))
    assert ha_rest.fetch_states() == []                          # a dict body is not a state list


def test_fetch_states_ignores_error_status(monkeypatch):
    monkeypatch.setenv("COMPANION_HA_URL", "http://ha.local")
    monkeypatch.setenv("COMPANION_HA_TOKEN", "tok")
    monkeypatch.setattr(ha_rest.httpx, "get", lambda url, **kw: _Resp(401, [{"entity_id": "x"}]))
    assert ha_rest.fetch_states() == []                          # non-200 body never trusted


# -- search -----------------------------------------------------------------

def test_search_filters_and_sorts(monkeypatch):
    states = [
        {"entity_id": "light.kitchen", "attributes": {"friendly_name": "Kitchen Lamp"}},
        {"entity_id": "sensor.co2", "attributes": {"friendly_name": "CO2"}},
        {"entity_id": "light.garage", "attributes": {}},
    ]
    monkeypatch.setattr(ha_rest, "fetch_states", lambda: states)
    res = ha_rest.search("light")
    # sorted by LABEL: "Kitchen Lamp…" < "light.garage"; sensor.co2 filtered out by the query.
    assert [r["value"] for r in res] == ["light.kitchen", "light.garage"]
    assert res[0]["label"] == "Kitchen Lamp (light.kitchen)"
    assert res[1]["label"] == "light.garage"                    # no friendly name -> bare id


def test_search_matches_friendly_name_and_limits(monkeypatch):
    states = [{"entity_id": f"sensor.s{i}", "attributes": {"friendly_name": "Living Room"}} for i in range(50)]
    monkeypatch.setattr(ha_rest, "fetch_states", lambda: states)
    assert len(ha_rest.search("living", limit=10)) == 10        # query matches friendly name, capped


# -- injection into an app --------------------------------------------------

def test_get_ha_states_is_injected_into_a_requesting_app(monkeypatch, tmp_path):
    from conftest import make_runtime
    from app import device
    rt = make_runtime(tmp_path=tmp_path, installed=["entity-board"], caps=device.SPLIT_FLAP)
    wants = rt._wants["entity-board"]
    assert "get_ha_states" in wants                             # the app's fetch() opts in by param name
    sample = [{"entity_id": "light.k", "state": "on", "attributes": {}}]
    monkeypatch.setattr(ha_rest, "fetch_states", lambda: sample)
    kw = rt._helper_kwargs("entity-board", wants, {})
    assert kw["get_ha_states"]() == sample
