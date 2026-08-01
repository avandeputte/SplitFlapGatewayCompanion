"""Tests for the MCP server (app/mcp_server.py + its mount in main.py).

Three things are pinned here:

* **The gate.** Off by default, 404 as a whole when off, 401 without the bearer token.
  An LLM-drivable write surface that quietly defaulted to on would be a nasty surprise.
* **The mount.** A bare ``POST /mcp`` — which is what every MCP client sends — has to
  reach the server. Starlette's Mount only matches ``/mcp/<something>``, so without the
  path fix the request falls through to the SPA's StaticFiles and comes back 405.
  That is a real regression waiting to happen, so it gets a test.
* **The tools.** In particular that ``show_message`` *awaits* the flaps: an agent calls
  get_display right after, and a background send would have it read the OLD board.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"


@pytest.fixture
def mcp_on(monkeypatch):
    """The layer on, with a pinned token, and the display not talking to hardware."""
    from app import main
    monkeypatch.setattr(main.config, "_mcp", True)
    monkeypatch.setattr(main.config, "_sim", True)
    monkeypatch.setitem(main.config._effective["mcp"], "token", TOKEN)
    return main


@pytest.fixture(scope="module")
def live():
    """A client with the LIFESPAN running — the mounted MCP app's session manager starts
    there, and nothing answers on /mcp without it.

    Module-scoped on purpose: a StreamableHTTPSessionManager can only be run() once per
    instance, and `mcp` is a singleton, so a per-test lifespan dies on the second test.
    Pointed at a dead gateway with syncing off, so startup does no I/O that can hang.
    """
    from app import main
    mp = pytest.MonkeyPatch()
    mp.setattr(main.config, "_mcp", True)
    mp.setattr(main.config, "_sim", True)
    mp.setitem(main.config._effective["mcp"], "token", TOKEN)
    mp.setitem(main.config._effective["transport"], "gateway_url", "http://127.0.0.1:9")
    mp.setitem(main.config._effective, "sync_from_gateway", False)
    with TestClient(main.app) as c:
        yield c
    mp.undo()


def auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}


# --- the gate -----------------------------------------------------------------
def test_the_layer_is_off_by_default():
    from app import main
    assert main.config.mcp_enabled is False


def test_the_whole_surface_404s_when_off():
    """Off means gone, not "answering 401s nobody can satisfy"."""
    from app import main
    c = TestClient(main.app)
    assert c.post("/mcp", json={}, headers=auth()).status_code == 404


def test_a_missing_or_wrong_token_is_401(mcp_on):
    c = TestClient(mcp_on.app)
    assert c.post("/mcp", json={}).status_code == 401
    assert c.post("/mcp", json={}, headers=auth("wrong")).status_code == 401
    # A near-miss must not squeak through (compare_digest, not a prefix match).
    assert c.post("/mcp", json={}, headers=auth(TOKEN + "x")).status_code == 401


def test_a_bare_post_to_mcp_completes_a_handshake(live):
    """The regression this exists for: Starlette's Mount("/mcp") only matches
    "/mcp/<something>", so a bare POST /mcp — which is what every MCP client sends —
    fell through to the SPA's StaticFiles and came back 405 (it only serves GET).
    _MCPPathFix is what stops that, and this is a real initialize handshake through it.
    """
    r = live.post("/mcp", headers=auth(), json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1"}},
    })
    assert r.status_code not in (404, 405), \
        f"a bare /mcp did not reach the MCP transport (got {r.status_code})"
    assert "SplitFlap Gateway Companion" in r.text   # the server introduced itself


@pytest.mark.parametrize("host", ["homeassistant.local:8000", "192.168.1.60:8000", "splitflap.lan"])
def test_a_client_may_connect_by_any_hostname(live, host):
    """FastMCP defaults host=127.0.0.1, and on that default it quietly enables DNS-
    rebinding protection allowing ONLY localhost — so every real client (the add-on at
    homeassistant.local:8000, an agent at 192.168.x.x) got 421 Misdirected Request.
    We turn that check off deliberately; the bearer token is the boundary. Pin it: this
    passed against 127.0.0.1 while being completely broken everywhere else.
    """
    r = live.post("/mcp", headers={**auth(), "Host": host}, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1"}},
    })
    assert r.status_code != 421, f"Host {host!r} rejected as a rebinding attempt"
    assert "SplitFlap Gateway Companion" in r.text


# --- the tools ----------------------------------------------------------------
def call(main, name, args=None):
    """Call a tool the way the protocol does, but without standing up a server.

    mcp 2.0 returns a ``CallToolResult``: ``.structured_content`` is the real return value
    (a bare list wrapped under "result"), falling back to the JSON in the text content block
    for a tool that returns no structured output.
    """
    res = asyncio.run(main.mcp.call_tool(name, args or {}))
    out = res.structured_content
    if out is None:
        out = json.loads(res.content[0].text)
    if isinstance(out, dict):
        return out.get("result", out)
    return out


def test_tools_are_all_registered(mcp_on):
    names = sorted(t.name for t in asyncio.run(mcp_on.mcp.list_tools()))
    assert names == ["clear_alarm", "clear_display", "configure_app", "get_app_settings",
                     "get_display", "get_gateway_settings", "get_timer", "list_alarms",
                     "list_apps", "list_displays", "list_playlists", "list_styles", "run_app",
                     "run_playlist", "set_alarm", "set_gateway_settings", "show_message",
                     "start_timer", "stop", "stop_timer"]


def test_every_tool_takes_an_optional_display(mcp_on):
    """Existing prompts and clients were written when there was one wall and send no
    display id. A tool that REQUIRED the argument would regress every one of them, so it
    must be optional everywhere — and default to the default display."""
    tools = asyncio.run(mcp_on.mcp.list_tools())
    for t in tools:
        if t.name in ("list_displays", "list_styles"):
            continue
        props = (t.input_schema or {}).get("properties", {})
        required = (t.input_schema or {}).get("required", [])
        assert "display" in props, f"{t.name} cannot address a second wall"
        assert "display" not in required, f"{t.name} made display mandatory"


def test_show_message_centers_the_text_on_the_board(mcp_on):
    rows, cols = mcp_on.config.grid["rows"], mcp_on.config.grid["cols"]
    call(mcp_on, "show_message", {"text": "HELLO"})

    chars = "".join(mcp_on.state.current_chars)
    lines = [chars[r * cols:(r + 1) * cols] for r in range(rows)]
    assert "HELLO" in "".join(lines)
    # centered, not jammed into the top-left corner
    assert lines[rows // 2].strip() == "HELLO"


def test_show_message_awaits_the_flaps(mcp_on):
    """An agent calls get_display straight after show_message. If the send were
    backgrounded (send_text_bg), the board would still be mid-transition and it would
    read back the OLD contents — so the tool must not return until the flaps have landed.
    """
    call(mcp_on, "show_message", {"text": "DONE"})
    read = call(mcp_on, "get_display")
    assert "DONE" in "".join(read["lines"])


def test_get_display_reports_the_live_board(mcp_on):
    call(mcp_on, "show_message", {"text": "ABC"})
    out = call(mcp_on, "get_display")
    assert out["rows"] == mcp_on.config.grid["rows"]
    assert out["cols"] == mcp_on.config.grid["cols"]
    assert "".join(out["lines"]) == "".join(mcp_on.state.current_chars)


def test_clear_display_blanks_the_board(mcp_on):
    call(mcp_on, "show_message", {"text": "ABC"})
    call(mcp_on, "clear_display")
    assert "".join(mcp_on.state.current_chars).strip() == ""


def test_an_unknown_style_is_refused(mcp_on):
    with pytest.raises(Exception):
        call(mcp_on, "show_message", {"text": "X", "style": "nope"})


def test_an_unknown_app_is_refused(mcp_on):
    with pytest.raises(Exception):
        call(mcp_on, "run_app", {"app_id": "does_not_exist"})


def test_an_unknown_playlist_is_refused(mcp_on):
    with pytest.raises(Exception):
        call(mcp_on, "run_playlist", {"name": "nope"})


def test_list_styles_offers_what_show_message_accepts(mcp_on):
    from app import renderer
    assert call(mcp_on, "list_styles") == list(renderer.ALL_STYLES)


# --- the dev menu -------------------------------------------------------------
def test_dev_toggle_flips_the_layer(monkeypatch):
    from app import main
    monkeypatch.setattr(main.config, "dev_mode", True)
    c = TestClient(main.app)
    try:
        assert c.post("/api/dev/mcp", json={"on": True}).json()["mcp"] is True
        assert c.get("/api/dev").json()["mcp"] is True
        assert c.get("/api/dev/mcp").json()["token"]        # a token exists once on
        assert c.post("/api/dev/mcp", json={"on": False}).json()["mcp"] is False
    finally:
        main.config.set_mcp(False)


def test_the_toggle_works_without_dev_mode():
    """The ⚙ tools menu is permanent — the MCP switch is an ordinary control, not a
    developer one (same reasoning as the Vestaboard switch)."""
    from app import main
    c = TestClient(main.app)                    # dev_mode off (no env var in tests)
    try:
        assert c.post("/api/dev/mcp", json={"on": True}).json()["mcp"] is True
        assert c.get("/api/dev/mcp").json()["enabled"] is True
    finally:
        main.config.set_mcp(False)


def test_the_generated_token_survives_a_restart(tmp_path):
    """The settings store drops any top-level key it doesn't know (see _META_KEYS), which
    is exactly how the Vestaboard key got silently regenerated on every boot. A token that
    changed on restart would quietly break every configured MCP client."""
    from app.plugin_settings import PluginSettings

    s = PluginSettings(tmp_path)
    s.set("mcp_token", "sekrit-token")

    assert PluginSettings(tmp_path).get("mcp_token") == "sekrit-token"


# --- observability: what is on the flaps, and where in a playlist ------------
# The transcript that motivated this had the agent GUESS the on-screen app three times
# ("almost certainly Word Clock"), because get_display could only say a playlist was
# active — not which of its apps was up. current_app closes that.
def test_get_display_names_the_driver(mcp_on):
    out = call(mcp_on, "get_display")
    assert out["driver"] in ("app", "playlist", "message", "idle")


def test_a_standalone_app_shows_up_as_the_driver_and_the_showing_app(mcp_on):
    mcp_on.state.active_app = "weather"
    mcp_on.state.current_app = "weather"
    try:
        out = call(mcp_on, "get_display")
        assert out["driver"] == "app"
        assert out["showing"]["app_id"] == "weather"
    finally:
        mcp_on.state.active_app = mcp_on.state.current_app = None


def test_a_playlist_reports_which_app_is_on_screen_and_where_in_the_rotation(mcp_on):
    """The heart of it: active_app is null while a playlist drives, but one of its apps
    is on screen. get_display must say which, and place it in the running order."""
    st = mcp_on.state
    st.active_playlist = "morning"
    st.current_app = "word-clock"
    st.playlist_entries = ["word-clock", "crypto", "date"]
    st.playlist_index = 0
    try:
        out = call(mcp_on, "get_display")
        assert out["driver"] == "playlist"
        assert out["showing"]["app_id"] == "word-clock"
        assert out["playlist"] == {
            "name": "morning", "index": 0, "count": 3,
            "apps": ["word-clock", "crypto", "date"], "next": "crypto",
        }
    finally:
        st.active_playlist = st.current_app = st.playlist_entries = st.playlist_index = None


def test_next_wraps_at_the_end_of_a_looping_playlist(mcp_on):
    st = mcp_on.state
    st.active_playlist = "morning"
    st.playlist_entries = ["word-clock", "crypto", "date"]
    st.playlist_index = 2
    try:
        assert call(mcp_on, "get_display")["playlist"]["next"] == "word-clock"
    finally:
        st.active_playlist = st.playlist_entries = st.playlist_index = None


def test_list_playlists_shows_the_running_order(mcp_on):
    """So a client can say what a playlist contains without running it — the agent could
    only report a count before."""
    mcp_on.plugin_settings.set("saved_app_playlists", {
        "morning": {"entries": [{"app": "word-clock"}, {"app": "plugin_crypto"},
                                {"type": "compose", "text": "HI"}], "loop": True},
    })
    try:
        pls = call(mcp_on, "list_playlists")
        assert pls[0]["apps"] == ["word-clock", "crypto", "(message)"]
    finally:
        mcp_on.plugin_settings.set("saved_app_playlists", {})


# --- configure_app / get_app_settings ----------------------------------------
def test_get_app_settings_lists_settings_with_short_names(mcp_on):
    """The storage keys are plugin_<id>_<name>; a client shouldn't have to know that."""
    out = call(mcp_on, "get_app_settings", {"app_id": "stocks"})
    names = {s["name"] for s in out["settings"]}
    assert "stocks_list" in names           # not plugin_stocks_stocks_list
    assert all("plugin_" not in s["name"] for s in out["settings"])


def test_configure_app_writes_a_setting(mcp_on):
    call(mcp_on, "configure_app", {"app_id": "stocks", "settings": {"stocks_list": "AAPL,MSFT"}})
    assert mcp_on.plugin_settings.get("plugin_stocks_stocks_list") == "AAPL,MSFT"


def test_configure_app_rejects_an_unknown_setting(mcp_on):
    with pytest.raises(Exception):
        call(mcp_on, "configure_app", {"app_id": "stocks", "settings": {"nope": 1}})


def test_configure_app_rejects_an_unknown_app(mcp_on):
    with pytest.raises(Exception):
        call(mcp_on, "configure_app", {"app_id": "does_not_exist", "settings": {"x": 1}})


def test_list_apps_flags_configurable_apps(mcp_on):
    apps = call(mcp_on, "list_apps")
    assert apps, "no apps installed to check"
    assert all(isinstance(a["configurable"], bool) for a in apps)
    # stocks has settings; confirm the flag matches what get_app_settings reports for it
    # (via the registry, which sees it whether or not it's in the installed list).
    has = bool(call(mcp_on, "get_app_settings", {"app_id": "stocks"})["settings"])
    assert has is True
    entry = next((a for a in apps if a["id"] == "stocks"), None)
    if entry:                                    # only asserted when stocks is installed
        assert entry["configurable"] is True


# --- timed-revert show_message ------------------------------------------------
def test_a_timed_message_does_not_stop_a_running_app(mcp_on):
    """The point of `seconds`: a heads-up that leaves the rotation running underneath.
    show_temporary interrupts rather than taking over, so active_app must be untouched."""
    mcp_on.state.active_app = "weather"
    mcp_on.controller.active_app = "weather"
    try:
        out = call(mcp_on, "show_message", {"text": "DINNER", "seconds": 2})
        assert out["seconds"] == 2
        assert out["reverts_to"] == "the running app/playlist"
        # the app is still the driver — a permanent show_message would have cleared it
        assert mcp_on.controller.active_app == "weather"
    finally:
        mcp_on.state.active_app = mcp_on.controller.active_app = None
        t = getattr(mcp_on.controller, "_temp_task", None)
        if t:
            t.cancel()


def test_mcp_holds_the_live_manager_not_a_snapshot():
    """The MCP server is built ONCE, at import. If it captured the displays as a list rather
    than holding the DisplayManager, a wall added later in the UI would be invisible to an
    agent forever — and invisibly so, which is the worst kind."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "mcp_server.py").read_text("utf-8")
    assert "def build(displays)" in src
    # every lookup goes through the manager, live
    assert "displays.default" in src and "displays.get(display)" in src
    assert "displays.all()" in src        # list_displays enumerates it at CALL time


def test_an_unknown_display_names_the_ones_that_exist():
    """An agent that guessed wrong should be told what it could have said, not just "no"."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "mcp_server.py").read_text("utf-8")
    assert 'known = ", ".join(displays.ids())' in src
    assert "no such display" in src


# --- resilience: a broken/absent MCP dependency must not brick the whole app ---
def test_a_broken_mcp_layer_503s_when_enabled_never_crashes(monkeypatch):
    """If the mcp library is missing/incompatible the app still starts; /mcp then 404s
    while off and 503s while on, instead of the companion failing to boot (the mcp 2.0.0
    regression). Exercises the guard with inner=None — the built app being absent."""
    import asyncio
    from app import main

    guard = main._MCPGuard(None)                       # as if build() had failed

    async def _status(enabled):
        monkeypatch.setattr(main.config, "_mcp", enabled)
        out = {}

        async def send(msg):
            if msg["type"] == "http.response.start":
                out["status"] = msg["status"]

        await guard({"type": "http", "headers": []}, None, send)
        return out["status"]

    assert asyncio.run(_status(False)) == 404          # off → gone
    assert asyncio.run(_status(True)) == 503           # on but unbuildable → unavailable, not a crash


# --- the Matrix timer/alarm tools --------------------------------------------

def _raw(main, name, args=None):
    """call_tool normalized for error asserts: the in-process call_tool RAISES ToolError
    for a tool that errored (the wire transport would wrap it as isError), so both
    shapes come back as an object with .is_error/.content."""
    from types import SimpleNamespace
    try:
        return asyncio.run(main.mcp.call_tool(name, args or {}))
    except Exception as e:
        return SimpleNamespace(is_error=True, content=[SimpleNamespace(text=str(e))])


def _matrix_caps(monkeypatch, main):
    """Make every display's wall advertise the timer/alarms capabilities. Class-level:
    the module-scoped `live` fixture's lifespan can swap the display instances under a
    later test, so an instance patch would silently miss the one the server resolves."""
    from types import SimpleNamespace
    from app.engine import DisplayController
    monkeypatch.setattr(DisplayController, "_caps",
                        lambda self: SimpleNamespace(can_timer=True, can_alarms=True))


def test_timer_tools_refuse_a_wall_without_the_capability(mcp_on):
    """The physical split-flap gateway advertises neither feature token, so the tools
    say so instead of poking endpoints that do not exist."""
    res = _raw(mcp_on, "get_timer")
    assert res.is_error and "capability" in res.content[0].text
    res = _raw(mcp_on, "set_alarm", {"slot": 0, "time": "07:30"})
    assert res.is_error and "capability" in res.content[0].text


def test_get_and_start_and_stop_timer(mcp_on, monkeypatch):
    from app import gateway
    _matrix_caps(monkeypatch, mcp_on)
    monkeypatch.setattr(gateway, "timer_get",
                        lambda url: {"active": True, "remaining": 95, "alarmFiring": False})
    out = call(mcp_on, "get_timer")
    assert out == {"active": True, "remaining_seconds": 95, "alarm_firing": False}

    started = []
    monkeypatch.setattr(gateway, "timer_start",
                        lambda url, sec: started.append(sec) or {"ok": True, "remaining": sec})
    out = call(mcp_on, "start_timer", {"seconds": 600})
    assert started == [600] and out["remaining_seconds"] == 600
    assert _raw(mcp_on, "start_timer", {"seconds": 0}).is_error          # 1 s .. 24 h

    stopped = []
    monkeypatch.setattr(gateway, "timer_stop", lambda url: stopped.append(url) or True)
    assert call(mcp_on, "stop_timer")["ok"] is True and stopped


def test_set_alarm_read_modify_writes_one_slot(mcp_on, monkeypatch):
    from app import gateway
    _matrix_caps(monkeypatch, mcp_on)
    slots = [{"time": "06:30", "days": 0x7F, "enabled": True},
             {"time": "07:00", "days": 0x7F, "enabled": False},
             {"time": "07:00", "days": 0x7F, "enabled": False},
             {"time": "07:00", "days": 0x7F, "enabled": False}]
    written = []
    monkeypatch.setattr(gateway, "alarms_get", lambda url: [dict(s) for s in slots])
    monkeypatch.setattr(gateway, "alarms_set", lambda url, sl: written.append(sl) or True)

    out = call(mcp_on, "set_alarm", {"slot": 2, "time": "6:45", "days": "weekdays"})
    assert out["days"] == "weekdays"
    assert written[-1][2] == {"time": "6:45", "days": 0x3E, "enabled": True}
    assert written[-1][0] == slots[0]                                    # untouched neighbors

    out = call(mcp_on, "clear_alarm", {"slot": 0})
    assert written[-1][0]["enabled"] is False
    assert written[-1][0]["time"] == "06:30"                             # keeps its schedule

    assert _raw(mcp_on, "set_alarm", {"slot": 0, "time": "25:00"}).is_error
    assert _raw(mcp_on, "set_alarm", {"slot": 9, "time": "07:00"}).is_error
    assert _raw(mcp_on, "set_alarm", {"slot": 0, "time": "07:00",
                                      "days": "someday"}).is_error


def test_list_alarms_decodes_days(mcp_on, monkeypatch):
    from app import gateway
    _matrix_caps(monkeypatch, mcp_on)
    monkeypatch.setattr(gateway, "alarms_get", lambda url: [
        {"time": "06:30", "days": 0x3E, "enabled": True},
        {"time": "09:00", "days": 0x41, "enabled": False},
        {"time": "07:00", "days": 0x7F, "enabled": False},
        {"time": "07:00", "days": 0x22, "enabled": False}])
    out = call(mcp_on, "list_alarms")
    assert [a["days"] for a in out] == ["weekdays", "weekends", "daily", "mon,fri"]
    assert out[0] == {"slot": 0, "time": "06:30", "days": "weekdays", "enabled": True}


# --- the gateway-settings tools ----------------------------------------------

def _settings_caps(monkeypatch, main, quiet=True, sound=True, brightness=True):
    from types import SimpleNamespace
    from app.engine import DisplayController
    monkeypatch.setattr(DisplayController, "_caps",
                        lambda self: SimpleNamespace(can_quiet=quiet, can_sound=sound,
                                                     can_brightness=brightness))


def test_get_gateway_settings_reads_by_capability(mcp_on, monkeypatch):
    from app import gateway
    _settings_caps(monkeypatch, mcp_on)
    monkeypatch.setattr(gateway, "quiet_get", lambda url: {"on": True})
    monkeypatch.setattr(gateway, "quiet_schedule_get",
                        lambda url: {"enabled": True, "start": "22:00", "end": "07:00",
                                     "days": 0x3E, "offset": -240})
    monkeypatch.setattr(gateway, "config_get",
                        lambda url: {"soundEnabled": True, "soundVolume": 60,
                                     "panelBright": 180, "dimEnabled": True,
                                     "dimStart": "23:00", "dimEnd": "06:30", "dimLevel": 40})
    out = call(mcp_on, "get_gateway_settings")
    assert out["quiet"] == {"on": True, "schedule": {"enabled": True, "start": "22:00",
                                                     "end": "07:00", "days": "weekdays"}}
    assert out["sound"] == {"enabled": True, "volume": 60}
    assert out["brightness"] == 180 and out["dim"]["level"] == 40


def test_set_gateway_settings_fans_out_by_section(mcp_on, monkeypatch):
    from app import gateway
    _settings_caps(monkeypatch, mcp_on)
    sent = {}

    def quiet_set(url, on):
        sent["quiet"] = on
        return {"ok": True, "on": on}

    monkeypatch.setattr(gateway, "quiet_set", quiet_set)
    monkeypatch.setattr(gateway, "quiet_schedule_set",
                        lambda url, p: sent.__setitem__("sched", p) or True)
    monkeypatch.setattr(gateway, "config_settings_set",
                        lambda url, p: sent.__setitem__("cfg", p) or True)
    out = call(mcp_on, "set_gateway_settings", {"patch": {
        "quiet": {"on": True, "schedule": {"enabled": True, "days": "weekends"}},
        "sound": {"volume": 45}, "brightness": 200,
        "dim": {"enabled": True, "start": "21:30", "level": 30}}})
    assert sent["quiet"] is True
    assert sent["sched"] == {"enabled": True, "days": 0x41}
    assert sent["cfg"] == {"soundVolume": 45, "panelBright": 200, "dimEnabled": True,
                           "dimStart": "21:30", "dimLevel": 30}
    assert out["applied"]["quiet_on"] is True


def test_set_gateway_settings_respects_capabilities_and_validates(mcp_on, monkeypatch):
    _settings_caps(monkeypatch, mcp_on, quiet=False)
    res = _raw(mcp_on, "set_gateway_settings", {"patch": {"quiet": {"on": True}}})
    assert res.is_error and "no Quiet Time" in res.content[0].text
    _settings_caps(monkeypatch, mcp_on)
    res = _raw(mcp_on, "set_gateway_settings",
               {"patch": {"quiet": {"schedule": {"start": "25:00"}}}})
    assert res.is_error and "HH:MM" in res.content[0].text
    res = _raw(mcp_on, "set_gateway_settings", {"patch": {"nonsense": 1}})
    assert res.is_error and "unknown sections" in res.content[0].text
