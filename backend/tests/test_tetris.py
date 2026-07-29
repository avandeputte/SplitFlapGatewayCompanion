"""Horizontal Tetris (canvas-tetris): gravity pulls left, pieces enter from the right, a
full column clears. Attract-mode auto-play, live steering from the control pad (discrete
taps), and the game-over fade — all drawn with ops and streamed as binary ops.
"""

from conftest import canvas_surface, load_app
from test_canvas_ops35 import OPS35


def _cv(w=256, h=64):
    return canvas_surface("http://gw", w, h, ("rgb888",), (), ops=OPS35, ops_bin=1, sprite=True)


class _Ctl:
    def __init__(self, dir_=None, events=(), taps=(), presses=0, engaged=True):
        self.dir = dir_
        self.events = list(events)
        self.taps = list(taps)
        self.presses = presses
        self._engaged = engaged

    def active(self, within=6.0):
        return self._engaged


def test_every_tetromino_has_four_valid_rotations():
    app = load_app("canvas-tetris")
    for kind in app._KINDS:
        rots = app._ROTS[kind]
        assert len(rots) == 4
        for cells in rots:
            assert len(cells) == 4                     # four cells per piece
            assert all(r >= 0 and c >= 0 for r, c in cells)   # normalized to the box


def test_attract_mode_plays_and_clears_columns():
    app = load_app("canvas-tetris")
    cv = _cv()
    peak = 0
    for _ in range(500):
        app.fetch_matrix({"speed": "6"}, cv)          # no controls → attract AI
        peak = max(peak, app._state._st["lines"])
    assert peak > 0                                    # it completes columns on its own


def test_taps_move_and_rotate_the_piece():
    app = load_app("canvas-tetris")
    cv = _cv()
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    st["piece"] = {"kind": "T", "rot": 0, "r": 3, "c": 15}   # centered, room to move
    st["plan"] = None
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(taps=["up"], presses=2))
    assert st["piece"]["r"] == 2                        # up moved it one row toward 0
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(taps=["down", "down"], presses=4))
    assert st["piece"]["r"] == 4
    r_before = st["piece"]["rot"]
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(taps=["right"], presses=5))
    assert st["piece"]["rot"] == (r_before + 1) % 4     # right rotates


def test_left_tap_soft_drops_toward_the_wall():
    app = load_app("canvas-tetris")
    cv = _cv()
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    c0 = st["piece"]["c"]
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(taps=["left"], presses=2))
    assert st["piece"]["c"] < c0                        # moved left (toward gravity)


def test_full_column_clears_and_shifts_left():
    app = load_app("canvas-tetris")
    cv = _cv()
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    rows, cols = st["rows"], st["cols"]
    # a solid column at col 1, a marker at col 2 — clearing col 1 pulls col 2 to col 1
    for r in range(rows):
        st["grid"][r][1] = (200, 200, 200)
    st["grid"][0][2] = (10, 20, 30)
    st["grid"][0][1] = None                            # leave one gap so col 1 isn't full yet
    st["grid"][0][1] = (200, 200, 200)                 # ...then fill it: col 1 is now complete
    n = app._clear(st)
    assert n == 1 and st["grid"][0][1] == (10, 20, 30)   # col 2 shifted into col 1


def test_game_over_fades_then_restarts(gw_calls):
    app = load_app("canvas-tetris")
    cv = _cv()
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    for r in range(1, st["rows"]):                     # fill all but row 0 → no full column,
        for c in range(st["cols"]):                    # but the next spawn has no room
            st["grid"][r][c] = (70, 70, 80)
    st["score"] = 4200
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(taps=["left"], presses=1))
    assert st["phase"] == "gameover"
    f0 = st["fade"]
    for _ in range(3):
        app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(presses=1))
    assert st["fade"] > f0                              # the well fades while it waits
    app.fetch_matrix({"speed": "6"}, cv, controls=_Ctl(presses=2))   # any key
    assert st["phase"] == "play" and st["score"] == 0


def test_a_frame_streams_as_binary_ops(gw_calls):
    app = load_app("canvas-tetris")
    cv = _cv()
    app.fetch_matrix({"speed": "6"}, cv)
    assert "/api/canvas/opsb" in [c[1] for c in gw_calls]


def test_tetris_sounds_only_when_a_human_plays():
    app = load_app("canvas-tetris")
    cv = _cv()
    # Unattended attract play locks pieces (and clears columns) but stays silent.
    quiet = []
    for _ in range(200):
        app.fetch_matrix({"speed": "8"}, cv, controls=_Ctl(engaged=False),
                         play_sound=lambda **kw: quiet.append(kw))
    assert not quiet
    # A live player who drives a piece into a lock hears it.
    app.fetch_matrix({"speed": "8"}, cv, controls=_Ctl(events=["start"], presses=1))
    loud = []
    app.fetch_matrix({"speed": "8"}, cv, controls=_Ctl(taps=["left"] * 40, presses=2),
                     play_sound=lambda **kw: loud.append(kw))
    assert loud                                        # the lock tone played for the player


def test_tetris_takeover_setting_extends_the_idle_window():
    app = load_app("canvas-tetris")
    cv = _cv()
    seen = []

    class _Rec(_Ctl):
        def active(self, within=6.0):
            seen.append(within)
            return self._engaged

    app.fetch_matrix({"speed": "8", "takeover": "75"}, cv, controls=_Rec())
    assert seen[-1] == 75
    seen.clear()
    app.fetch_matrix({"speed": "8"}, cv, controls=_Rec())
    assert seen[-1] == 30                               # default (was a hardcoded 6s)
    for raw, want in (("99999", 120), ("1", 5), ("abc", 30), ("", 30), (None, 30)):
        seen.clear()
        app.fetch_matrix({"speed": "8", "takeover": raw}, cv, controls=_Rec())
        assert seen[-1] == want                         # clamped to [5, 120]; junk → default 30


def test_tetris_is_marked_interactive():
    from conftest import make_runtime
    rt = make_runtime(installed=["canvas-tetris"])
    card = next(a for a in rt.app_list() if a["id"] == "canvas-tetris")
    assert card["interactive"] is True
