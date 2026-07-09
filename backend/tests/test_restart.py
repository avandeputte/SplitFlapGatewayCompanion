"""Saving settings restarts the app if it's the one currently on the display."""

from fastapi.testclient import TestClient


def test_saving_active_app_settings_restarts_it(monkeypatch):
    from app import main
    calls = []

    async def fake_run_app(app_id):
        calls.append(app_id)

    monkeypatch.setattr(main.plugins, "save_settings", lambda a, v: None)
    monkeypatch.setattr(main.controller, "run_app", fake_run_app)
    client = TestClient(main.app)

    # the edited app IS the active one -> it restarts
    monkeypatch.setattr(main.controller, "active_app", "weather")
    r = client.post("/api/apps/weather/settings", json={"values": {}})
    assert r.status_code == 200 and calls == ["weather"]

    # a different app is active -> no restart
    calls.clear()
    monkeypatch.setattr(main.controller, "active_app", "time")
    client.post("/api/apps/weather/settings", json={"values": {}})
    assert calls == []


def test_saving_globals_restarts_the_active_app(monkeypatch):
    from app import main
    calls = []

    async def fake_run_app(app_id):
        calls.append(app_id)

    monkeypatch.setattr(main.plugins, "save_global_settings", lambda v: None)
    monkeypatch.setattr(main.controller, "run_app", fake_run_app)
    client = TestClient(main.app)

    monkeypatch.setattr(main.controller, "active_app", "dashboard")
    client.post("/api/global-settings", json={"values": {"weather_api_key": "K"}})
    assert calls == ["dashboard"]

    # nothing running -> nothing to restart
    calls.clear()
    monkeypatch.setattr(main.controller, "active_app", None)
    client.post("/api/global-settings", json={"values": {"weather_api_key": "K"}})
    assert calls == []
