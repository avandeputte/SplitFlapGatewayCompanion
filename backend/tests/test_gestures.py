"""Clap/tap gestures: the engine's playlist skip (what a gesture drives) and the
gestures module's routing, dedupe and debounce. All against fakes — the SSE stream
itself is exercised at the frame level (handle_frame), never over a socket."""

import asyncio

from app import gestures
from app.config import Config
from app.engine import DisplayController
from app.state import DisplayState
from test_engine_interrupts import FakeGateway, _controller, _until


# --- the engine's skip -------------------------------------------------------

def test_skip_advances_a_compose_playlist_immediately(tmp_path):
    async def run():
        ctrl = _controller(tmp_path, FakeGateway())
        entries = [{"type": "compose", "text": "ONE", "duration": 3600},
                   {"type": "compose", "text": "TWO", "duration": 3600}]
        await ctrl.run_playlist(entries, loop=True, name="Loop")
        await _until(lambda: ctrl.state.playlist_index == 0, "first entry never started")
        assert ctrl.skip_playlist_entry() is True
        # an hour-long slot yields within a poll tick, not at its deadline
        await _until(lambda: ctrl.state.playlist_index == 1, "skip did not advance")
        assert ctrl.skip_playlist_entry() is True          # wraps back around
        await _until(lambda: ctrl.state.playlist_index == 0, "second skip did not advance")
        await ctrl.stop_app()
    asyncio.run(run())


def test_skip_cuts_an_app_entry_short(tmp_path):
    async def run():
        ctrl = _controller(tmp_path, FakeGateway(), page="APP PAGE")
        entries = [{"type": "app", "app": "static", "duration": 3600},
                   {"type": "compose", "text": "AFTER", "duration": 3600}]
        await ctrl.run_playlist(entries, loop=True, name="Mix")
        await _until(lambda: ctrl.state.playlist_index == 0, "app entry never started")
        ctrl.skip_playlist_entry()
        await _until(lambda: ctrl.state.playlist_index == 1, "app slot outlived its skip")
        await ctrl.stop_app()
    asyncio.run(run())


def test_skip_without_a_playlist_is_a_no_op(tmp_path):
    ctrl = _controller(tmp_path, FakeGateway())
    assert ctrl.skip_playlist_entry() is False
    assert not ctrl._skip_evt.is_set()                     # nothing armed to leak later


# --- the gestures module -----------------------------------------------------

class _FakeDisplay:
    def __init__(self, ctrl, settings=None):
        self.id = "default"
        self.controller = ctrl
        self.settings = settings or {}


class _StubCtl:
    def __init__(self, playlist=None):
        self.active_playlist = playlist
        self.active_app = None
        self.skipped = 0
        self.stopped = 0

    def skip_playlist_entry(self):
        if not self.active_playlist:
            return False
        self.skipped += 1
        return True

    async def stop_app(self):
        self.stopped += 1
        self.active_playlist = None


def test_action_for_defaults_and_validates():
    assert gestures.action_for({}, "clap") == "playlist_next"
    assert gestures.action_for({"gesture_tap": "stop"}, "tap") == "stop"
    assert gestures.action_for({"gesture_clap": "none"}, "clap") == "none"
    assert gestures.action_for({"gesture_clap": "sing"}, "clap") == "playlist_next"


def test_dispatch_routes_by_setting():
    async def run():
        ctl = _StubCtl(playlist="Morning")
        d = _FakeDisplay(ctl)
        assert await gestures.dispatch(d, "clap") == "playlist_next"
        assert ctl.skipped == 1

        d = _FakeDisplay(_StubCtl(playlist=None))
        assert await gestures.dispatch(d, "clap") == "none"   # nothing to advance

        ctl = _StubCtl(playlist="Morning")
        d = _FakeDisplay(ctl, {"gesture_tap": "stop"})
        assert await gestures.dispatch(d, "tap") == "stop"
        assert ctl.stopped == 1

        d = _FakeDisplay(_StubCtl(playlist="X"), {"gesture_clap": "none"})
        assert await gestures.dispatch(d, "clap") == "none"
    asyncio.run(run())


def test_frames_dedupe_by_seq_and_debounce_by_clock():
    async def run():
        ctl = _StubCtl(playlist="Loop")
        d = _FakeDisplay(ctl)
        st = gestures.GestureState()
        assert await gestures.handle_frame(d, st, "clap", '{"count":1,"seq":7}', 100.0) \
            == "playlist_next"
        # the same seq re-delivered: dropped
        assert await gestures.handle_frame(d, st, "clap", '{"count":1,"seq":7}', 103.0) is None
        # a fresh seq inside the debounce window: dropped
        assert await gestures.handle_frame(d, st, "clap", '{"count":1,"seq":8}', 100.5) is None
        # a fresh seq after the window: dispatched
        assert await gestures.handle_frame(d, st, "clap", '{"count":1,"seq":9}', 102.0) \
            == "playlist_next"
        # taps hold their own clock — a clap does not debounce a tap
        assert await gestures.handle_frame(d, st, "tap", '{"count":1,"seq":1}', 102.1) \
            == "playlist_next"
        # non-gesture frames pass through untouched
        assert await gestures.handle_frame(d, st, "display", '{"x":1}', 200.0) is None
        assert ctl.skipped == 3
    asyncio.run(run())


def test_bad_gesture_payloads_do_not_crash():
    async def run():
        d = _FakeDisplay(_StubCtl(playlist="L"))
        st = gestures.GestureState()
        assert await gestures.handle_frame(d, st, "clap", "not json", 1000.0) == "playlist_next"
        assert await gestures.handle_frame(d, st, "clap", "", 1002.0) == "playlist_next"
    asyncio.run(run())


# --- the acknowledgment chirp ------------------------------------------------

def test_a_landed_gesture_chirps_and_an_idle_one_stays_silent(monkeypatch):
    from types import SimpleNamespace
    chirps = []
    monkeypatch.setattr(gestures.canvas, "play_sound",
                        lambda url, **kw: chirps.append(kw))

    async def run():
        ctl = _StubCtl(playlist="Loop")
        ctl.caps = SimpleNamespace(can_sound=True)
        d = _FakeDisplay(ctl)
        d.gateway_url = "http://gw"
        st = gestures.GestureState()
        await gestures.handle_frame(d, st, "clap", '{"seq":1}', 100.0)
        assert len(chirps) == 1 and chirps[0]["notes"][0][0] < chirps[0]["notes"][1][0]

        # nothing to skip -> no action -> no chirp
        idle = _FakeDisplay(_StubCtl(playlist=None))
        idle.controller.caps = SimpleNamespace(can_sound=True)
        idle.gateway_url = "http://gw"
        await gestures.handle_frame(idle, gestures.GestureState(), "clap", '{"seq":1}', 200.0)
        assert len(chirps) == 1

        # a wall without a speaker skips silently, but still skips
        mute = _StubCtl(playlist="Loop")
        mute.caps = SimpleNamespace(can_sound=False)
        dm = _FakeDisplay(mute)
        dm.gateway_url = "http://gw"
        await gestures.handle_frame(dm, gestures.GestureState(), "tap", '{"seq":2}', 300.0)
        assert mute.skipped == 1 and len(chirps) == 1

        # stop gets the falling blip
        stopper = _StubCtl(playlist="Loop")
        stopper.caps = SimpleNamespace(can_sound=True)
        ds = _FakeDisplay(stopper, {"gesture_clap": "stop"})
        ds.gateway_url = "http://gw"
        await gestures.handle_frame(ds, gestures.GestureState(), "clap", '{"seq":3}', 400.0)
        assert len(chirps) == 2 and chirps[1]["notes"][0][0] > chirps[1]["notes"][1][0]
    asyncio.run(run())


# --- the watcher's capability re-check (the "flashed the wall at boot" hole) --

def test_watch_waits_for_caps_and_connects_when_they_appear(monkeypatch):
    """The deployed regression: the wall was down/mid-flash when the companion booted,
    the startup caps snapshot said no 'events', and gestures were silently dead for
    the process life. The watcher must idle WITHOUT touching the network while caps
    are absent, then connect on its own once the wall advertises them."""
    from types import SimpleNamespace
    attempts = []

    class _NoNet:
        def __init__(self, *a, **kw):
            attempts.append(1)
            raise AssertionError("must not open a client while caps lack 'events'")

    async def run():
        monkeypatch.setattr(gestures, "_BACKOFF_MIN", 0.01)
        monkeypatch.setattr(gestures.httpx, "AsyncClient", _NoNet)
        sleeps = []
        real_sleep = asyncio.sleep

        async def fast_sleep(s):
            sleeps.append(s)
            await real_sleep(0)

        monkeypatch.setattr(gestures.asyncio, "sleep", fast_sleep)
        ctl = SimpleNamespace(caps=SimpleNamespace(events=False))
        d = SimpleNamespace(id="default", controller=ctl, settings={},
                            gateway_url="http://gw")
        task = asyncio.create_task(gestures.watch(d))
        for _ in range(20):
            await real_sleep(0)
        assert not attempts and sleeps and all(s >= 60 for s in sleeps)

        # the wall comes back with events: the next cycle tries to connect
        class _Probe:
            def __init__(self, *a, **kw):
                attempts.append(1)
                raise OSError("probe reached the network layer")

        monkeypatch.setattr(gestures.httpx, "AsyncClient", _Probe)
        ctl.caps = SimpleNamespace(events=True)
        for _ in range(30):
            await real_sleep(0)
        assert attempts, "caps appeared but the watcher never tried to connect"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(run())
