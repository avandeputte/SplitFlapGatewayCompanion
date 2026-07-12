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

    FastMCP returns ``(content_blocks, structured)``; the structured half is the real
    return value, with a bare list wrapped under "result".
    """
    out = asyncio.run(main.mcp.call_tool(name, args or {}))
    if isinstance(out, tuple):
        out = out[1]
    if isinstance(out, dict):
        return out.get("result", out)
    return json.loads(out[0].text)


def test_tools_are_all_registered(mcp_on):
    names = sorted(t.name for t in asyncio.run(mcp_on.mcp.list_tools()))
    assert names == ["clear_display", "get_display", "list_apps", "list_playlists",
                     "list_styles", "run_app", "run_playlist", "show_message", "stop"]


def test_show_message_centres_the_text_on_the_board(mcp_on):
    rows, cols = mcp_on.config.grid["rows"], mcp_on.config.grid["cols"]
    call(mcp_on, "show_message", {"text": "HELLO"})

    chars = "".join(mcp_on.state.current_chars)
    lines = [chars[r * cols:(r + 1) * cols] for r in range(rows)]
    assert "HELLO" in "".join(lines)
    # centred, not jammed into the top-left corner
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


def test_the_dev_toggle_is_dev_gated():
    from app import main
    c = TestClient(main.app)                    # dev_mode off (no env var in tests)
    assert c.post("/api/dev/mcp", json={"on": True}).status_code == 404
    assert c.get("/api/dev/mcp").status_code == 404


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
