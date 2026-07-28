"""Interactive matrix games: the low-latency control channel (web UI → /api/game/input →
the app's ``controls`` helper) and the gateway speaker (``play_sound``). Chomper is the
proof of concept — attract-mode auto-play until a player grabs the pad, then live steering
with tones, all at streaming-binary-ops latency.
"""

import pytest
from fastapi.testclient import TestClient

from app import device, gameinput
from conftest import canvas_surface, load_app
from test_canvas_ops35 import OPS35


# --- the input buffer -------------------------------------------------------

def test_gameinput_holds_direction_and_drains_events():
    gameinput.reset("http://w")
    assert gameinput.push("http://w", "left", now=1.0)
    assert gameinput.push("http://w", "start", now=1.1)
    snap = gameinput.snapshot("http://w", now=1.2)
    assert snap.dir == "left" and snap.events == ["start"] and snap.active()
    # events drain; the held direction persists
    snap2 = gameinput.snapshot("http://w", now=1.3)
    assert snap2.dir == "left" and snap2.events == []
    # a newer direction wins; release clears it
    gameinput.push("http://w", "up", now=1.4)
    assert gameinput.snapshot("http://w", now=1.4).dir == "up"
    gameinput.push("http://w", "release", now=1.5)
    assert gameinput.snapshot("http://w", now=1.5).dir is None


def test_gameinput_active_times_out_to_attract():
    gameinput.reset("http://w")
    gameinput.push("http://w", "right", now=100.0)
    assert gameinput.snapshot("http://w", now=101.0).active()          # engaged
    assert not gameinput.snapshot("http://w", now=110.0).active()      # idle → attract
    assert not gameinput.push("http://w", "nonsense", now=111.0)       # unknown action


def test_walls_are_isolated():
    gameinput.reset("http://a")
    gameinput.reset("http://b")
    gameinput.push("http://a", "up", now=1.0)
    assert gameinput.snapshot("http://a", now=1.0).dir == "up"
    assert gameinput.snapshot("http://b", now=1.0).dir is None


# --- the /api/game/input route ----------------------------------------------

@pytest.fixture
def game_client(monkeypatch):
    from app import main
    main.config.update({"transport": {"gateway_url": "http://gw"}})
    gameinput.reset("http://gw")
    return TestClient(main.app)


def test_input_route_records_for_the_wall(game_client, monkeypatch):
    pushed = []
    monkeypatch.setattr(gameinput, "push",
                        lambda url, action, *, now: (pushed.append((url, action)) or True))
    r = game_client.post("/api/game/input", json={"action": "down"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert pushed and pushed[-1][1] == "down" and pushed[-1][0]        # wall url resolved
    # an unknown action is rejected at the door
    monkeypatch.setattr(gameinput, "push", lambda url, action, *, now: False)
    assert game_client.post("/api/game/input", json={"action": "zzz"}).status_code == 400


# --- the speaker helper -----------------------------------------------------

def test_play_sound_posts_a_tone_and_is_gated(monkeypatch):
    from app import canvas, gateway
    calls = []

    class _R:
        status_code = 200

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(gateway, "_request",
                        lambda m, u, p, *, timeout, **kw: (calls.append((p, kw.get("json"))) or _R()))
    assert canvas.play_sound("http://gw", notes=[[523, 90], [0, 40], [784, 120]], vol=70)
    import time as _t
    _t.sleep(0.05)                                    # the POST fires on a daemon thread
    assert calls and calls[-1][0] == "/api/sound"
    assert calls[-1][1] == {"vol": 70, "notes": [[523, 90], [0, 40], [784, 120]]}
    # a single freq/ms works too, and an empty call is a no-op
    calls.clear()
    canvas.play_sound("http://gw", freq=440, ms=100)
    _t.sleep(0.05)
    assert calls[-1][1] == {"vol": 70, "freq": 440, "ms": 100}
    assert not canvas.play_sound("http://gw")         # nothing to play


def test_can_sound_capability_parses():
    doc = {"features": ["canvas", "sound"], "charset": {"common": "A"}}
    assert device.from_capabilities(doc).can_sound
    assert not device.from_capabilities({"features": ["canvas"], "charset": {"common": "A"}}).can_sound


# --- Chomper: attract vs play, sound, pause ---------------------------------

class _Controls:
    def __init__(self, dir_=None, events=(), presses=0, engaged=True):
        self.dir = dir_
        self.events = list(events)
        self.presses = presses          # the press-edge counter (see gameinput)
        self._engaged = engaged

    def active(self, within=6.0):
        return self._engaged


def _cv():
    return canvas_surface("http://gw", 128, 64, ("rgb888",), (), ops=OPS35, ops_bin=1, sprite=True)


def test_chomper_attract_mode_auto_plays_without_controls():
    app = load_app("canvas-chomper")
    app._new_game(app._state._st) if hasattr(app._state, "_st") else None
    cv = _cv()
    cells = set()
    for _ in range(12):
        app.fetch_matrix({"speed": "5"}, cv)          # no controls → BFS attract
        cells.add(app._state._st["pac"]["cell"])
    assert len(cells) > 3                              # it moves itself


def test_chomper_player_steers_and_sounds():
    app = load_app("canvas-chomper")
    cv = _cv()
    sounds = []
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls(events=["start"]),
                     play_sound=lambda **kw: sounds.append(kw))
    st = app._state._st
    moved = set()
    for i in range(24):                               # an actively-pressing player: presses
        app.fetch_matrix({"speed": "8"}, cv,          # climb, so any between-lives freeze resumes
                         controls=_Controls("left", presses=i + 1),
                         play_sound=lambda **kw: sounds.append(kw))
        moved.add(st["pac"]["cell"])
    assert len(moved) > 3 and st["pac"]["dir"] == "l"
    assert st["score"] > 0 and sounds                 # ate pellets, played waka


def test_chomper_pause_freezes_and_start_resets():
    app = load_app("canvas-chomper")
    cv = _cv()
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls("right"))
    st = app._state._st
    st["score"] = 500
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls("right", ["pause"]))
    assert st["paused"]
    frozen = st["pac"]["cell"]
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls("right"))
    assert st["pac"]["cell"] == frozen                # paused → no movement
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls(events=["start"]))
    assert st["score"] <= 10 and st["lives"] == 3 and not st["paused"]   # fresh game (then one tick)


def test_chomper_is_marked_interactive_in_the_catalog():
    from conftest import make_runtime
    rt = make_runtime(installed=["canvas-chomper"])
    card = next(a for a in rt.app_list() if a["id"] == "canvas-chomper")
    assert card["interactive"] is True


def _force_death(st):
    """Put a ghost on the chomper so the next step is a fatal collision."""
    st["ghost_list"][0]["cell"] = st["pac"]["cell"]


def test_chomper_pauses_ready_between_lives_until_a_key():
    app = load_app("canvas-chomper")
    cv = _cv()
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls(events=["start"], presses=1))
    st = app._state._st
    st["lives"] = 3
    _force_death(st)
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls("left", presses=1))
    assert st["phase"] == "ready" and st["lives"] == 2      # a life lost -> frozen
    frozen = st["pac"]["cell"]
    for _ in range(4):                                       # a HELD key must not resume
        app.fetch_matrix({"speed": "8"}, cv, controls=_Controls("left", presses=1))
    assert st["phase"] == "ready" and st["pac"]["cell"] == frozen
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls("left", presses=2))   # a fresh press
    assert st["phase"] == "play"


def test_chomper_game_over_fades_then_restarts_on_a_key():
    app = load_app("canvas-chomper")
    cv = _cv()
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls(events=["start"], presses=1))
    st = app._state._st
    st["lives"], st["score"] = 1, 999
    _force_death(st)
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls("left", presses=1))
    assert st["phase"] == "gameover"
    f0 = st["fade"]
    for _ in range(3):                                       # the board fades while it waits
        app.fetch_matrix({"speed": "8"}, cv, controls=_Controls("left", presses=1))
    assert st["fade"] > f0 and st["phase"] == "gameover"
    app.fetch_matrix({"speed": "8"}, cv, controls=_Controls("left", presses=2))   # any key
    assert st["phase"] == "play" and st["lives"] == 3 and st["score"] <= 10


def test_chomper_attract_mode_never_freezes_on_a_death():
    app = load_app("canvas-chomper")
    cv = _cv()
    for _ in range(6):
        app.fetch_matrix({"speed": "5"}, cv)                # no controls -> attract
    st = app._state._st
    st["lives"] = 1
    _force_death(st)
    app.fetch_matrix({"speed": "5"}, cv)                    # fatal, but attract resets instantly
    assert st.get("phase", "play") == "play"
