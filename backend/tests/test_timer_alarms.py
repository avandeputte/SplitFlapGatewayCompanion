"""The Matrix Gateway kitchen-timer/alarm plumbing shared by the HA MQTT device and
the MCP tools: capability parsing, the REST client's wire shapes, and the day-mask
grammar (bit0=Sunday … bit6=Saturday, matching the firmware's tm_wday check)."""

import pytest

from app import device, gateway
from conftest import json_response


def test_capabilities_parse_the_timer_and_alarm_tokens():
    doc = {"features": ["canvas", "timer", "alarms"], "charset": {"common": "A"}}
    caps = device.from_capabilities(doc)
    assert caps.can_timer and caps.can_alarms
    caps = device.from_capabilities({"features": ["canvas"], "charset": {"common": "A"}})
    assert not caps.can_timer and not caps.can_alarms


def test_day_mask_round_trips():
    assert gateway.days_to_mask("daily") == 0x7F
    assert gateway.days_to_mask("weekdays") == 0x3E
    assert gateway.days_to_mask("weekends") == 0x41
    assert gateway.days_to_mask("mon,fri") == 0x22
    assert gateway.days_to_mask("Sunday Saturday") == 0x41   # full names, any case, spaces
    assert gateway.days_to_mask(0x15) == 0x15                # an int mask passes through
    assert gateway.mask_to_days(0x7F) == "daily"
    assert gateway.mask_to_days(0x3E) == "weekdays"
    assert gateway.mask_to_days(0x22) == "mon,fri"
    with pytest.raises(ValueError):
        gateway.days_to_mask("someday")


def test_timer_client_wire_shapes(monkeypatch):
    calls = []

    def fake(method, url, path, *, timeout, **kw):
        calls.append((method, path, kw.get("json")))
        return json_response({"ok": True, "active": True, "remaining": 60,
                              "alarmFiring": False})

    monkeypatch.setattr(gateway, "_request", fake)
    assert gateway.timer_get("http://gw")["remaining"] == 60
    gateway.timer_start("http://gw", 300)
    assert ("POST", "/api/timer", {"sec": 300}) in calls
    gateway.timer_stop("http://gw")
    assert ("POST", "/api/timer", {"stop": True}) in calls


def test_alarm_client_wire_shapes(monkeypatch):
    calls = []
    slots = [{"time": "06:30", "days": 62, "enabled": True}]

    def fake(method, url, path, *, timeout, **kw):
        calls.append((method, path, kw.get("json")))
        return json_response(slots if method == "GET" else {"ok": True})

    monkeypatch.setattr(gateway, "_request", fake)
    assert gateway.alarms_get("http://gw") == slots
    gateway.alarms_set("http://gw", slots * 6)               # the firmware takes 4 at most
    posted = next(j for m, p, j in calls if m == "POST")
    assert len(posted) == 4


def test_client_failures_degrade_quietly(monkeypatch):
    monkeypatch.setattr(gateway, "_request",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("wall down")))
    assert gateway.timer_get("http://gw") == {}
    assert gateway.timer_stop("http://gw") is False
    assert gateway.alarms_get("http://gw") == []
    assert gateway.alarms_set("http://gw", []) is False
