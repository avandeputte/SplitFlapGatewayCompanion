"""The out-of-the-box "All apps" playlist.

Computed fresh from the Apps screen on every read — never stored — so it always
mirrors what is installed. It rides first in every playlist listing (the API, Home
Assistant's playlist select, MCP), runs like any saved playlist, and its name is
reserved: it cannot be saved over or deleted.
"""

from fastapi.testclient import TestClient

from conftest import make_runtime


def test_builtin_all_apps_mirrors_the_apps_screen(tmp_path):
    rt = make_runtime(tmp_path=tmp_path, installed=["time", "date"])
    pls = rt.builtin_playlists()
    assert list(pls) == ["All apps"]
    pl = pls["All apps"]
    assert pl["loop"] is True and pl["builtin"] is True
    # entries = the Apps screen, in its own (name-sorted) order
    assert pl["entries"] == [{"app": a["id"]} for a in rt.app_list()]
    assert {e["app"] for e in pl["entries"]} == {"time", "date"}
    # nothing installed -> no builtin (an empty playlist would just blank the wall)
    empty = make_runtime(tmp_path=tmp_path / "none", installed=[])
    assert empty.builtin_playlists() == {}


def test_playlists_api_lists_builtin_first_and_reserves_the_name(monkeypatch):
    from app import main
    builtin = {"All apps": {"entries": [{"app": "time"}], "loop": True, "builtin": True}}
    monkeypatch.setattr(main.plugins, "builtin_playlists", lambda: dict(builtin))
    client = TestClient(main.app)

    doc = client.get("/api/playlists").json()["playlists"]
    assert list(doc)[0] == "All apps"                     # out of the box, top of the tab
    assert doc["All apps"]["builtin"] is True

    # the name is reserved in both directions
    r = client.post("/api/playlists", json={"name": "All apps", "entries": []})
    assert r.status_code == 400
    r = client.delete("/api/playlists/All%20apps")
    assert r.status_code == 400


def test_ha_playlist_select_offers_and_runs_the_builtin(monkeypatch):
    from app import main
    d = main.displays.default
    builtin = {"All apps": {"entries": [{"app": "time"}, {"app": "date"}], "loop": True,
                            "builtin": True}}
    monkeypatch.setattr(d.plugins, "builtin_playlists", lambda: dict(builtin))

    # the select's option list carries it (first, after Off)
    disco = {uid: cfg for _t, uid, cfg in d.ha._discovery()}
    assert disco["playlist"]["options"][:2] == ["Off", "All apps"]

    # selecting it runs the computed entries under the builtin name
    ran = []

    async def fake_run(entries, loop=True, name=None):
        ran.append((entries, loop, name))

    monkeypatch.setattr(d.controller, "run_playlist", fake_run)
    coro = d.ha._command_coro(d.ha._cmd("playlist"), "All apps")
    import asyncio
    asyncio.run(coro)
    assert ran == [([{"app": "time"}, {"app": "date"}], True, "All apps")]
