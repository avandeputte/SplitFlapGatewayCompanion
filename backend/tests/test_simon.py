"""Simon — the sequence-echo memory game: playback beats, echo input, the one-wrong-
press loss, the idle hint replay, and the framework contract (attract self-play,
quiet-in-attract, binary ops streaming)."""

from conftest import canvas_surface, load_app, make_runtime
from test_canvas_ops35 import OPS35


def _cv(w=128, h=64):
    return canvas_surface("http://gw", w, h, ("rgb888",), (), ops=OPS35, ops_bin=True)


class _Ctl:
    def __init__(self, dir_=None, events=(), taps=(), presses=0, engaged=True):
        self.dir = dir_
        self.events = list(events)
        self.taps = list(taps)
        self.presses = presses
        self._engaged = engaged

    def active(self, within=6.0):
        return self._engaged


_PAD = ('left', 'up', 'down', 'right')


def _play_through_show(app, cv, ctl=None, limit=40):
    """Fetch until the machine finishes playing the melody and opens input."""
    st = app._state._st
    for _ in range(limit):
        if st['phase'] == 'input':
            return
        app.fetch_canvas({"speed": "10"}, cv, controls=ctl)
    raise AssertionError('show phase never finished')


def test_catalog_and_binary_stream(gw_calls):
    rt = make_runtime(installed=["canvas-simon"])
    card = {a["id"]: a for a in rt.app_list()}["canvas-simon"]
    assert card["interactive"] is True
    assert card["icon_svg"].startswith("data:image/svg+xml,")
    gw_calls.clear()
    load_app("canvas-simon").fetch_canvas({"speed": "5"}, _cv())
    assert "/api/canvas/opsb" in [c[1] for c in gw_calls]


def test_show_walks_the_melody_then_opens_input(quiet_gateway):
    app = load_app("canvas-simon")
    cv = _cv()
    ctl = _Ctl(events=["start"], presses=1)
    app.fetch_canvas({"speed": "10"}, cv, controls=ctl)
    st = app._state._st
    assert st['phase'] == 'show' and len(st['seq']) == 1
    _play_through_show(app, cv, _Ctl(presses=1))
    assert st['phase'] == 'input' and st['cursor'] == 0


def test_correct_echo_grows_the_melody_and_replays(quiet_gateway):
    app = load_app("canvas-simon")
    cv = _cv()
    app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    _play_through_show(app, cv, _Ctl(presses=1))
    right = _PAD[st['seq'][0]]
    app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(taps=[right], presses=2))
    assert st['score'] == 10 and len(st['seq']) == 2     # grew...
    assert st['phase'] == 'show' and st['sp'] == 0       # ...and replays from the top


def test_one_wrong_press_ends_the_game_and_any_key_redeals(quiet_gateway):
    app = load_app("canvas-simon")
    cv = _cv()
    app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    _play_through_show(app, cv, _Ctl(presses=1))
    wrong = _PAD[(st['seq'][0] + 1) % 4]
    app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(taps=[wrong], presses=2))
    assert st['phase'] == 'gameover'
    app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(presses=2))   # arms the freeze
    f0 = st['fade']
    app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(presses=2))
    assert st['fade'] > f0
    app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(presses=3))   # any key
    assert st['phase'] == 'show' and st['score'] == 0 and len(st['seq']) == 1


def test_sound_rides_the_player_notes_but_never_attract(quiet_gateway):
    app = load_app("canvas-simon")
    cv = _cv()
    sounds = []
    for _ in range(50):                                  # attract: melody + self-echo
        app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(engaged=False),
                         play_sound=lambda **kw: sounds.append(kw))
    assert not sounds
    app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(events=["start"], presses=1),
                     play_sound=lambda **kw: sounds.append(kw))
    st = app._state._st
    for _ in range(6):
        if st['phase'] == 'input':
            break
        app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(presses=1),
                         play_sound=lambda **kw: sounds.append(kw))
    assert sounds and all('notes' in kw for kw in sounds)   # the melody is audible now


def test_attract_demos_forever_and_caps_the_melody(quiet_gateway):
    app = load_app("canvas-simon")
    cv = _cv()
    longest = 0
    for _ in range(400):
        app.fetch_canvas({"speed": "10"}, cv)
        st = app._state._st
        assert st['phase'] in ('show', 'input')          # never sticks in gameover
        longest = max(longest, len(st['seq']))
    assert 2 <= longest <= 9                             # it grows, and it restarts


def test_idle_player_gets_the_hint_replay(quiet_gateway):
    app = load_app("canvas-simon")
    cv = _cv()
    app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(events=["start"], presses=1))
    st = app._state._st
    _play_through_show(app, cv, _Ctl(presses=1))
    for _ in range(80):                                  # stare blankly at the wall
        app.fetch_canvas({"speed": "10"}, cv, controls=_Ctl(presses=1))
        if st['phase'] == 'show':
            break
    assert st['phase'] == 'show' and st['cursor'] == 0   # the melody replays as a hint
