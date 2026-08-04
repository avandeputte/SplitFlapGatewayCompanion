"""The three arcade games (Snake, Flappy, Breakout) on the interactive framework:
attract-mode self-play, live steering from the pad, the game-over fade, quiet-in-attract
sound, and binary-ops streaming — the same contract Chomper and Tetris pinned.
"""

from conftest import canvas_surface, load_app, make_runtime
from test_canvas_ops35 import OPS35


def _cv(w=128, h=64):
    return canvas_surface("http://gw", w, h, ("rgb888",), (), ops=OPS35, ops_bin=True,
                          sprite=True)


class _Ctl:
    def __init__(self, dir_=None, events=(), taps=(), presses=0, engaged=True):
        self.dir = dir_
        self.events = list(events)
        self.taps = list(taps)
        self.presses = presses
        self._engaged = engaged

    def active(self, within=6.0):
        return self._engaged


# --- shared contract ---------------------------------------------------------

def test_all_three_are_interactive_and_stream_binary(gw_calls):
    rt = make_runtime(installed=["canvas-snake", "canvas-flappy", "canvas-breakout"])
    cards = {a["id"]: a for a in rt.app_list()}
    for app_id in ("canvas-snake", "canvas-flappy", "canvas-breakout"):
        assert cards[app_id]["interactive"] is True
        assert cards[app_id]["icon_svg"].startswith("data:image/svg+xml,")
        gw_calls.clear()
        app = load_app(app_id)
        app.fetch_canvas({"speed": "5"}, _cv())
        assert "/api/canvas/opsb" in [c[1] for c in gw_calls], app_id


def test_all_three_are_silent_in_attract():
    for app_id in ("canvas-snake", "canvas-flappy", "canvas-breakout"):
        app = load_app(app_id)
        cv = _cv()
        sounds = []
        for _ in range(60):
            app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl(engaged=False),
                             play_sound=lambda **kw: sounds.append(kw))
        assert not sounds, app_id


# --- snake -------------------------------------------------------------------

def test_snake_attract_plays_and_eats():
    app = load_app("canvas-snake")
    cv = _cv()
    st = None
    for _ in range(200):
        app.fetch_canvas({"speed": "8"}, cv)
        st = app._state._st
    assert st["score"] > 0 or len(st["snake"]) > 3       # the demo found food


def test_snake_steers_and_never_reverses():
    app = load_app("canvas-snake")
    cv = _cv()
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    st["dir"] = "r"
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl("down", presses=2))
    assert st["dir"] == "d"
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl("up", presses=3))
    assert st["dir"] == "u" or st["dir"] == "d"          # up is a reverse only mid-run
    st["dir"] = "r"
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl("left", presses=4))
    assert st["dir"] == "r"                              # straight reverse is refused


def test_snake_dies_into_gameover_and_restarts(gw_calls):
    app = load_app("canvas-snake")
    cv = _cv()
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    st["snake"] = [(1, 1), (1, 2)]                       # head at the wall corner
    st["dir"] = "u"                                      # next step hits the border
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl(presses=1))
    assert st["phase"] == "gameover"
    f0 = st["fade"]
    for _ in range(3):
        app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl(presses=1))
    assert st["fade"] > f0                               # fading while it waits
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl(presses=2))   # any key
    assert st["phase"] == "play" and st["score"] == 0


def test_snake_grows_on_food():
    app = load_app("canvas-snake")
    cv = _cv()
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    hx, hy = st["snake"][0]
    st["dir"] = "r"
    st["food"] = (hx + 1, hy)                            # right in front of the head
    before = len(st["snake"])
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl(presses=1))
    app.fetch_canvas({"speed": "8"}, cv, controls=_Ctl(presses=1))
    assert st["score"] >= 10 and len(st["snake"]) > before


# --- flappy ------------------------------------------------------------------

def test_flappy_attract_flaps_and_survives_a_while():
    app = load_app("canvas-flappy")
    cv = _cv()
    hearts = 0
    for _ in range(120):
        app.fetch_canvas({"speed": "5"}, cv)
        st = app._state._st
        hearts = max(hearts, st["score"])
    assert st["phase"] == "play"                         # attract never sticks in gameover


def test_flappy_press_edge_flaps_once():
    app = load_app("canvas-flappy")
    cv = _cv()
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    st["y"], st["vy"] = 30.0, 0.0
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=2))    # fresh press: flap
    assert st["vy"] < 0
    vy_after_flap = st["vy"]
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=2))    # held: no new flap
    assert st["vy"] > vy_after_flap                       # gravity pulled it back


def test_flappy_hits_the_ground_and_restarts():
    app = load_app("canvas-flappy")
    cv = _cv()
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    st["y"], st["vy"] = cv.height - 4.0, 2.0             # about to hit the ground
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert st["phase"] == "gameover"
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=1))    # frozen
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=2))    # any key
    assert st["phase"] == "play" and st["score"] == 0


# --- breakout ----------------------------------------------------------------

def test_breakout_attract_breaks_bricks():
    app = load_app("canvas-breakout")
    cv = _cv()
    total = None
    for _ in range(300):
        app.fetch_canvas({"speed": "8"}, cv)
        st = app._state._st
        if total is None:
            total = len(st["bricks"])
    assert st["score"] > 0 or len(st["bricks"]) < total


def test_breakout_paddle_steers_and_up_launches():
    app = load_app("canvas-breakout")
    cv = _cv()
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    assert st["held"]                                    # a fresh game waits on the paddle
    x0 = st["px"]
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl("right", presses=2))
    assert st["px"] > x0
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(taps=["up"], presses=3))
    assert not st["held"] and st["ball"] is not None      # launched


def test_breakout_brick_hit_scores_and_life_loss_holds_ready():
    app = load_app("canvas-breakout")
    cv = _cv()
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    br = st["bricks"][0]
    st.update(held=False, ball={"x": br["x"] + 1.0, "y": br["y"] + 1.0, "vx": 0.0, "vy": -1.0})
    n0, s0 = len(st["bricks"]), st["score"]
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert len(st["bricks"]) == n0 - 1 and st["score"] > s0

    st.update(held=False, ball={"x": 5.0, "y": cv.height - 2.0, "vx": 0.0, "vy": 2.0})
    st["px"] = cv.width - 10                             # paddle far away: the ball drops
    lives = st["lives"]
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert st["lives"] == lives - 1 and st["phase"] == "ready"
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=1))    # arms the freeze, draws READY
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=2))    # a fresh key resumes
    assert st["phase"] == "play"


def test_breakout_clearing_the_wall_levels_up():
    app = load_app("canvas-breakout")
    cv = _cv()
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    br = st["bricks"][0]
    st["bricks"] = [br]                                  # one brick left
    st.update(held=False, ball={"x": br["x"] + 1.0, "y": br["y"] + 1.0, "vx": 0.0, "vy": -1.0})
    app.fetch_canvas({"speed": "5"}, cv, controls=_Ctl(presses=1))
    assert st["level"] == 2 and len(st["bricks"]) > 1 and st["held"]
