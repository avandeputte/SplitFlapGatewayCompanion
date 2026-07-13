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


# --- resuming what was playing ------------------------------------------------
# A container that updates itself (the HA add-on does) came back to a dead display: the
# playlist that had been running just stopped. Nothing else knows to restart it — the
# gateway holds the hardware config, not what the companion was doing.
import asyncio

import pytest


class _Recorder:
    """Stands in for the settings store."""

    def __init__(self, initial=None):
        self.doc = initial if initial is not None else {}
        self.writes = 0

    def get(self, key, default=None):
        return self.doc if key == "last_run" else default

    def set(self, key, value):
        assert key == "last_run"
        self.doc = value
        self.writes += 1


def test_the_engine_records_the_app_it_starts(monkeypatch):
    from app import main
    rec = _Recorder()
    monkeypatch.setattr(main.displays.default, "settings", rec)
    main.controller.attach_persist(main._remember_driver)
    try:
        main.controller._remember({"kind": "app", "app": "weather"})
        assert rec.doc == {"kind": "app", "app": "weather"}
    finally:
        main.controller.attach_persist(None)


def test_a_playlist_keeps_its_entries_not_just_its_name(monkeypatch):
    """A playlist can be run unsaved, and that one has no name to look up on the way
    back — so the entries have to travel with it."""
    from app import main
    rec = _Recorder()
    monkeypatch.setattr(main.displays.default, "settings", rec)
    main.controller.attach_persist(main._remember_driver)
    try:
        entries = [{"app": "weather"}, {"app": "time"}]
        main.controller._remember({"kind": "playlist", "name": "", "entries": entries, "loop": True})
        assert rec.doc["entries"] == entries
        assert rec.doc["loop"] is True
    finally:
        main.controller.attach_persist(None)


def test_an_unchanged_driver_is_not_rewritten(monkeypatch):
    """This fires on every manual message too, and each write hits the disk and mirrors
    to the gateway."""
    from app import main
    rec = _Recorder()
    monkeypatch.setattr(main.displays.default, "settings", rec)
    main._remember_driver({"kind": "app", "app": "weather"})
    main._remember_driver({"kind": "app", "app": "weather"})
    main._remember_driver({"kind": "app", "app": "weather"})
    assert rec.writes == 1


def test_resume_restarts_the_playlist_that_was_running(monkeypatch):
    from app import main
    entries = [{"app": "weather"}]
    rec = _Recorder({"kind": "playlist", "name": "morning", "entries": entries, "loop": True})
    monkeypatch.setattr(main.displays.default, "settings", rec)

    started = {}

    async def fake_run_playlist(e, loop=True, name=None):
        started.update(entries=e, loop=loop, name=name)

    monkeypatch.setattr(main.controller, "run_playlist", fake_run_playlist)
    monkeypatch.setattr(main.ha, "publish_state", lambda: None)

    asyncio.run(main.resume_last_run())
    assert started == {"entries": entries, "loop": True, "name": "morning"}


def test_resume_restarts_the_app_that_was_running(monkeypatch):
    from app import main
    rec = _Recorder({"kind": "app", "app": "weather"})
    monkeypatch.setattr(main.displays.default, "settings", rec)

    started = {}

    async def fake_run_app(app_id):
        started["app"] = app_id

    monkeypatch.setattr(main.controller, "run_app", fake_run_app)
    monkeypatch.setattr(main.ha, "publish_state", lambda: None)

    asyncio.run(main.resume_last_run())
    assert started == {"app": "weather"}


def test_nothing_running_resumes_nothing(monkeypatch):
    """A manual message clears the record, so a restart leaves the board alone rather
    than resurrecting the app it replaced."""
    from app import main
    monkeypatch.setattr(main.displays.default, "settings", _Recorder({}))

    async def boom(*a, **kw):
        raise AssertionError("should not have started anything")

    monkeypatch.setattr(main.controller, "run_app", boom)
    monkeypatch.setattr(main.controller, "run_playlist", boom)
    asyncio.run(main.resume_last_run())


def test_an_app_uninstalled_since_the_last_run_is_forgotten(monkeypatch):
    """Otherwise it would fail on every boot forever."""
    from app import main
    rec = _Recorder({"kind": "app", "app": "gone"})
    monkeypatch.setattr(main.displays.default, "settings", rec)

    async def missing(app_id):
        raise KeyError(app_id)

    monkeypatch.setattr(main.controller, "run_app", missing)
    asyncio.run(main.resume_last_run())
    assert rec.doc == {}          # forgotten, not retried forever


def test_last_run_survives_the_settings_store(tmp_path):
    """The store drops any top-level key it doesn't know (_META_KEYS) — the same trap
    that silently regenerated the Vestaboard key on every boot."""
    from app.plugin_settings import PluginSettings

    s = PluginSettings(tmp_path)
    s.set("last_run", {"kind": "app", "app": "weather"})
    assert PluginSettings(tmp_path).get("last_run") == {"kind": "app", "app": "weather"}
