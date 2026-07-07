"""Home Assistant integration — discovery payloads + command→action mapping."""

import asyncio

from app.config import Config
from app.homeassistant import HomeAssistant


class FakeController:
    def __init__(self):
        self.active_app = None
        self.active_playlist = None
        self.calls = []

    async def run_app(self, aid):
        self.calls.append(("run_app", aid)); self.active_app = aid

    async def stop_app(self):
        self.calls.append(("stop",)); self.active_app = None; self.active_playlist = None

    async def run_playlist(self, entries, loop, name):
        self.calls.append(("run_pl", name)); self.active_playlist = name

    def send_text_bg(self, text):
        self.calls.append(("msg", text))


class FakeSettings:
    def __init__(self, pl):
        self._pl = pl

    def get(self, k, d=None):
        return {"saved_app_playlists": self._pl}.get(k, d)


class FakePlugins:
    def __init__(self, apps, pl):
        self._apps = apps
        self.settings = FakeSettings(pl)

    def app_list(self):
        return self._apps

    def manifest(self, aid):
        return next((a for a in self._apps if a["id"] == aid), None)


def _ha(tmp_path):
    cfg = Config(data_dir=tmp_path)
    plugins = FakePlugins(
        [{"id": "weather", "name": "Weather"}, {"id": "date", "name": "Date"}],
        {"Morning": {"entries": [], "loop": True}})
    ctrl = FakeController()
    return HomeAssistant(cfg, plugins, ctrl), ctrl


def test_discovery_entities(tmp_path):
    ha, _ = _ha(tmp_path)
    comps = {obj: (comp, cfg) for comp, obj, cfg in ha._discovery()}
    # Only companion-unique controls — no redundant message/now-playing.
    assert set(comps) == {"app", "playlist", "stop"}
    assert comps["app"][0] == "select" and comps["playlist"][0] == "select"
    assert comps["stop"][0] == "button"
    assert comps["app"][1]["options"] == ["Off", "Weather", "Date"]
    assert comps["playlist"][1]["options"] == ["Off", "Morning"]
    assert comps["app"][1]["command_topic"] == "splitflap-companion/cmd/app"
    assert comps["app"][1]["unique_id"] == "splitflap-companion_app"
    # every entity shares one device + availability
    for _, _, cfg in ha._discovery():
        assert cfg["device"]["identifiers"] == ["splitflap-companion"]
        assert cfg["availability_topic"] == "splitflap-companion/availability"


def test_cmd_run_app_and_off(tmp_path):
    ha, ctrl = _ha(tmp_path)
    asyncio.run(ha._command_coro(ha._cmd("app"), "Weather"))
    assert ("run_app", "weather") in ctrl.calls
    asyncio.run(ha._command_coro(ha._cmd("app"), "Off"))
    assert ("stop",) in ctrl.calls


def test_cmd_playlist(tmp_path):
    ha, ctrl = _ha(tmp_path)
    asyncio.run(ha._command_coro(ha._cmd("playlist"), "Morning"))
    assert ("run_pl", "Morning") in ctrl.calls


def test_cmd_stop_button(tmp_path):
    ha, ctrl = _ha(tmp_path)
    asyncio.run(ha._command_coro(ha._cmd("stop"), "PRESS"))
    assert ("stop",) in ctrl.calls


def test_cmd_unknown_app_is_none(tmp_path):
    ha, _ = _ha(tmp_path)
    assert ha._command_coro(ha._cmd("app"), "Nonexistent") is None
    assert ha._command_coro("splitflap-companion/cmd/other", "x") is None


class FakeClient:
    def __init__(self):
        self.pubs = []
        self.subs = []

    def subscribe(self, topic, qos=0):
        self.subs.append(topic)

    def publish(self, topic, payload=None, retain=False):
        self.pubs.append((topic, payload, retain))


def test_on_connect_subscribes_and_publishes(tmp_path):
    """The MQTT plumbing: on connect we subscribe to commands and publish
    availability + retained discovery + initial state."""
    import json
    ha, _ = _ha(tmp_path)
    fc = FakeClient()
    ha._client = fc
    ha._on_connect(fc, None, None, 0)

    assert set(fc.subs) == {ha._cmd(k) for k in ("app", "playlist", "stop")}
    assert (ha._avail(), "online", True) in fc.pubs

    dt = ha._disc_topic("select", "app")
    disc = [p for p in fc.pubs if p[0] == dt]
    assert disc and disc[0][2] is True  # retained
    cfg = json.loads(disc[0][1])
    assert cfg["options"] == ["Off", "Weather", "Date"]
    assert cfg["command_topic"] == ha._cmd("app")

    # initial select state published (retained) — shows the active app
    app_state = [p for p in fc.pubs if p[0] == ha._state("app")]
    assert app_state and app_state[0][1] == "Off" and app_state[0][2] is True


def test_bad_reason_code_no_publish(tmp_path):
    ha, _ = _ha(tmp_path)
    fc = FakeClient()
    ha._client = fc
    ha._on_connect(fc, None, None, 5)  # connection refused
    assert not fc.pubs and not fc.subs
