"""The companion's REST proxy for the Matrix Gateway's timer, alarms and device
settings — what the HACS integration (no MQTT) drives. Capability-gated per section;
POST /api/alarms is a one-slot patch the companion read-modify-writes."""

import pytest
from fastapi.testclient import TestClient

from app import gateway


@pytest.fixture
def client(monkeypatch):
    from types import SimpleNamespace
    from app import main
    from app.engine import DisplayController
    monkeypatch.setattr(main.config, "_sim", True)
    monkeypatch.setitem(main.config._effective["transport"], "gateway_url", "http://gw")
    monkeypatch.setattr(DisplayController, "_caps",
                        lambda self: SimpleNamespace(can_timer=True, can_alarms=True,
                                                     can_quiet=True, can_sound=True,
                                                     can_brightness=True))
    return TestClient(main.app)


@pytest.fixture
def physical(monkeypatch):
    from types import SimpleNamespace
    from app import main
    from app.engine import DisplayController
    monkeypatch.setattr(main.config, "_sim", True)
    monkeypatch.setattr(DisplayController, "_caps",
                        lambda self: SimpleNamespace())
    return TestClient(main.app)


def test_get_timer_combines_state_and_alarms(client, monkeypatch):
    monkeypatch.setattr(gateway, "timer_get",
                        lambda url: {"active": True, "remaining": 120, "alarmFiring": False})
    monkeypatch.setattr(gateway, "alarms_get",
                        lambda url: [{"time": "06:30", "days": 0x3E, "enabled": True}])
    doc = client.get("/api/timer").json()
    assert doc["supported"] == {"timer": True, "alarms": True}
    assert doc["timer"]["active"] and doc["timer"]["remaining"] == 120
    assert doc["timer"]["ends_at"]                              # stable ISO end for pollers
    assert doc["alarms"][0] == {"slot": 0, "time": "06:30", "days": "weekdays", "enabled": True}


def test_get_timer_on_a_physical_wall_reports_unsupported(physical):
    doc = physical.get("/api/timer").json()
    assert doc == {"supported": {"timer": False, "alarms": False}}


def test_post_timer_start_and_stop(client, monkeypatch):
    seen = []
    monkeypatch.setattr(gateway, "timer_start",
                        lambda url, sec: seen.append(("start", sec)) or {"ok": True, "remaining": sec})
    monkeypatch.setattr(gateway, "timer_stop", lambda url: seen.append(("stop",)) or True)
    assert client.post("/api/timer", json={"seconds": 300}).json()["remaining"] == 300
    assert client.post("/api/timer", json={"stop": True}).json()["stopped"] is True
    assert seen == [("start", 300), ("stop",)]
    assert client.post("/api/timer", json={"seconds": 0}).status_code == 400
    assert client.post("/api/timer", json={"seconds": 90000}).status_code == 400


def test_post_alarms_patches_one_slot(client, monkeypatch):
    slots = [{"time": "06:30", "days": 0x7F, "enabled": True},
             {"time": "07:00", "days": 0x7F, "enabled": False}]
    written = []
    monkeypatch.setattr(gateway, "alarms_get", lambda url: [dict(s) for s in slots])
    monkeypatch.setattr(gateway, "alarms_set", lambda url, sl: written.append(sl) or True)
    doc = client.post("/api/alarms", json={"slot": 1, "time": "21:15",
                                           "days": "weekends", "enabled": True}).json()
    assert doc == {"ok": True, "slot": 1, "time": "21:15", "days": "weekends", "enabled": True}
    assert written[-1][1] == {"time": "21:15", "days": 0x41, "enabled": True}
    assert written[-1][0] == slots[0]                            # neighbor untouched
    assert client.post("/api/alarms", json={"slot": 9}).status_code == 400
    assert client.post("/api/alarms", json={"slot": 0, "time": "25:00"}).status_code == 400
    assert client.post("/api/alarms", json={"slot": 0, "days": "someday"}).status_code == 400


def test_gateway_settings_get_and_patch(client, monkeypatch):
    monkeypatch.setattr(gateway, "quiet_get", lambda url: {"on": False})
    monkeypatch.setattr(gateway, "quiet_schedule_get",
                        lambda url: {"enabled": True, "start": "22:00", "end": "07:00", "days": 0x7F})
    monkeypatch.setattr(gateway, "config_get",
                        lambda url: {"soundEnabled": True, "soundVolume": 70, "panelBright": 200,
                                     "dimEnabled": False, "dimStart": "22:00", "dimEnd": "07:00",
                                     "dimLevel": 30})
    doc = client.get("/api/gateway/settings").json()
    assert doc["quiet"]["schedule"]["days"] == "daily"
    assert doc["sound"] == {"enabled": True, "volume": 70}
    assert doc["brightness"] == 200 and doc["dim"]["enabled"] is False

    sent = {}

    def quiet_set(url, on):
        sent["quiet"] = on
        return {"ok": True, "on": on}

    monkeypatch.setattr(gateway, "quiet_set", quiet_set)
    monkeypatch.setattr(gateway, "config_settings_set",
                        lambda url, p: sent.__setitem__("cfg", p) or True)
    doc = client.post("/api/gateway/settings",
                      json={"quiet": {"on": True}, "brightness": 128}).json()
    assert doc["applied"]["quiet_on"] is True and sent["cfg"] == {"panelBright": 128}
    assert client.post("/api/gateway/settings", json={"bogus": 1}).status_code == 400


def test_gateway_settings_respect_missing_capabilities(physical):
    doc = physical.get("/api/gateway/settings").json()
    assert doc == {"supported": {"quiet": False, "sound": False, "brightness": False}}
    assert physical.post("/api/gateway/settings",
                         json={"quiet": {"on": True}}).status_code == 409
    assert physical.post("/api/timer", json={"seconds": 60}).status_code == 409
    assert physical.post("/api/alarms", json={"slot": 0, "enabled": True}).status_code == 409
