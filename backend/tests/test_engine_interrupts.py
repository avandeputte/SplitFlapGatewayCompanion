"""The engine's unchanged-page suppression must track what is REALLY on the wall.

A non-anim app skips re-sending a page it already sent — right, until something else
paints over that page. Two somethings used to break it, both leaving the wall wrong
indefinitely:

  * a FAILED send still recorded the page as sent, so a gateway that rebooted
    mid-rotation never got the page again (a static app retried nothing);
  * a trigger interrupt painted its text over a static app's page, and when the
    interrupt ended the app's next page equalled its last one — suppressed — so the
    trigger text stayed up forever. The old comment PROMISED the app would redraw;
    the code never delivered.

The fix: _emit_page invalidates the record on every paint, and only a send that
actually reached the wall re-validates it (under the same lock).

Also pinned here, from the same audit row: a manual message sent during a timed
show_temporary() must not be blanked when the timer ends; a second show_temporary()
replaces the first rather than queueing behind its full timer; stop() cancels the
temp task.

Everything runs against fakes — no gateway is ever contacted.
"""
import asyncio

from app import device
from app.config import Config
from app.engine import DisplayController
from app.state import DisplayState

# A minimal canvas wall, enough for _caps() to report the overlay-ticker surface.
CANVAS_DOC = {
    "product": "Matrix Portal Gateway", "fw": "3.13.0",
    "features": ["cells", "colors", "canvas", "ticker"],
    "charset": {"uniform": True, "common": "ABC"},
    "canvas": {"formats": ["rgb888"], "width": 128, "height": 32,
               "rect": True, "ticker": True, "readback": True,
               "ops": ["clear", "text", "show"]},
}


class FakePlugins:
    """A one-app runtime: a STATIC app whose single page never changes."""

    def __init__(self, page="HELLO", loop_delay=0.02):
        self.page = page
        self.loop_delay = loop_delay
        self.settings = {}

    def manifest(self, app_id):
        return {}

    def get_pages(self, app_id, overrides=None):
        return [self.page]

    def page_timing(self, app_id, overrides=None):
        return {"is_anim": False, "style": "ltr", "speed": 0,
                "loop_delay": self.loop_delay, "skip_rotation": False}


class FakeGateway:
    """Records every batch; can be told to fail the next N sends."""

    type_name = "fake"
    batch_capable = True

    def __init__(self, fail_next=0):
        self.fail_next = fail_next
        self.batches: list[list[tuple[int, str]]] = []

    @property
    def connected(self):
        return True

    @property
    def last_error(self):
        return None

    async def send_frame(self, mid, ch):
        raise AssertionError("batch transport must never be driven per-frame")

    async def send_batch(self, frames, step_ms):
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("gateway went away")
        self.batches.append(list(frames))

    async def close(self):
        pass


def _controller(tmp_path, transport, page="HELLO"):
    cfg = Config(data_dir=tmp_path)
    ctrl = DisplayController(cfg, DisplayState(cfg.module_count()))
    ctrl.transport = transport
    ctrl.attach_plugins(FakePlugins(page))
    return ctrl


def _shown(ctrl):
    return "".join(ctrl.state.current_chars).strip()


async def _until(pred, what, timeout=5.0):
    """Poll rather than sleep-and-hope — same rationale as test_stop_blanks."""
    for _ in range(int(timeout / 0.02)):
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(what)


# ---------------------------------------------------------------------------
# a failed send is not a sent page
# ---------------------------------------------------------------------------
def test_a_failed_send_is_retried_not_suppressed(tmp_path):
    """The gateway drops the first send. The app's page hasn't changed — but the wall
    never got it, so 'unchanged' is a lie and the loop must send it again."""
    async def go():
        tr = FakeGateway(fail_next=1)
        c = _controller(tmp_path, tr)
        await c.run_app("static")
        await _until(lambda: tr.batches, "the page was never re-sent after the failure")
        assert _shown(c).startswith("HELLO"), "the wall never recovered from the failed send"
        await c.stop()
    asyncio.run(go())


# ---------------------------------------------------------------------------
# the wall does not keep a trigger's text forever
# ---------------------------------------------------------------------------
def test_a_static_app_repaints_after_an_interrupt(tmp_path):
    """The audit's headline: a trigger over a static one-page app. The interrupt paints
    ALERT; when it ends, the app's next page equals its last page — and the old code
    suppressed the resend, leaving ALERT up indefinitely."""
    async def go():
        tr = FakeGateway()
        c = _controller(tmp_path, tr)
        await c.run_app("static")
        await _until(lambda: _shown(c).startswith("HELLO"), "the app never drew")

        await c.fire_interrupt("ALERT", 0.05)
        assert not (c.active_app is None), "the interrupt must not stop the app"
        await _until(lambda: _shown(c).startswith("HELLO"),
                     "the trigger's text is still on the wall — the app never repainted")
        # …and it reached the WALL, not just the preview: the last batch is the page.
        assert any(("H" in dict(b).values()) for b in tr.batches[-2:])
        await c.stop()
    asyncio.run(go())


# ---------------------------------------------------------------------------
# show_temporary vs a manual message
# ---------------------------------------------------------------------------
def test_a_manual_send_during_a_temporary_message_is_not_blanked(tmp_path):
    """Nothing is running; show_temporary() puts a timed note up (blank_if_idle). While
    it is up, somebody composes a real message. When the note's timer ends it must NOT
    blank the board — the manual message is the newest intent."""
    async def go():
        tr = FakeGateway()
        c = _controller(tmp_path, tr)
        c.show_temporary("BACK IN 5", 0.15)
        await _until(lambda: "BACK IN 5" in _shown(c), "the temporary note never drew")

        await c.send_text("HI THERE")
        assert _shown(c).startswith("HI THERE")
        await _until(lambda: c._temp_task is None or c._temp_task.done(),
                     "the temporary note's timer never expired")
        await asyncio.sleep(0.05)          # a beat for any (wrong) blanking to land
        assert _shown(c).startswith("HI THERE"), \
            "the expiring temporary message blanked a newer manual message"
        await c.stop()
    asyncio.run(go())


def test_a_second_temporary_message_replaces_the_first(tmp_path):
    """"Dinner!" superseded by "Dinner NOW" must not wait out the first timer queued
    on the interrupt lock."""
    async def go():
        tr = FakeGateway()
        c = _controller(tmp_path, tr)
        c.show_temporary("FIRST", 60)     # a long timer nobody wants to wait out
        await _until(lambda: "FIRST" in _shown(c), "the first note never drew")

        c.show_temporary("SECOND", 60)
        await _until(lambda: "SECOND" in _shown(c),
                     "the second message queued behind the first's 60s timer", timeout=2.0)
        await c.stop()
    asyncio.run(go())


def test_stop_cancels_a_live_temporary_message(tmp_path):
    """stop() used to leave the temp task alive to repaint/blank a closed display."""
    async def go():
        tr = FakeGateway()
        c = _controller(tmp_path, tr)
        c.show_temporary("NOTE", 60)
        await _until(lambda: "NOTE" in _shown(c), "the note never drew")
        task = c._temp_task
        await c.stop()
        assert c._temp_task is None
        assert task.done(), "stop() left the temporary-message task running"
    asyncio.run(go())


# ---------------------------------------------------------------------------
# a trigger's overlay ticker (fire_overlay_ticker)
# ---------------------------------------------------------------------------
def test_overlay_ticker_falls_back_to_the_interrupt_off_canvas(tmp_path):
    """No canvas endpoints here — the trigger must still notify, through the ordinary
    interrupt, with the same (clamped) duration and frame flag."""
    async def go():
        c = _controller(tmp_path, FakeGateway())        # default caps = plain split-flap
        seen = []

        async def fake_fi(text, seconds, *, frame=False, **k):
            seen.append((text, seconds, frame))

        c.fire_interrupt = fake_fi
        await c.fire_overlay_ticker("PING", 5, frame=True)
        assert seen == [("PING", 5, True)]
    asyncio.run(go())


def test_overlay_ticker_on_canvas_composites_without_taking_over(tmp_path, monkeypatch):
    """A canvas wall gets a NON-intrusive overlay ticker — put_ticker(overlay=True) carrying
    the TTL — never a fire_interrupt takeover, and a background self-clear is scheduled."""
    import app.engine as engine_mod
    calls = []
    monkeypatch.setattr(engine_mod.canvas, "put_ticker",
                        lambda url, text, *a, **k: (calls.append((text, a)) or True))

    async def go():
        c = _controller(tmp_path, FakeGateway())
        c.config.update({"transport": {"gateway_url": "http://gw"}})
        c.plugins._caps = lambda: device.from_capabilities(CANVAS_DOC)
        took_over = []

        async def fake_fi(*a, **k):        # must NOT be called on the canvas path
            took_over.append(a)

        c.fire_interrupt = fake_fi

        await c.fire_overlay_ticker("BREAKING", 30, color=(9, 8, 7))
        assert not took_over, "a canvas overlay ticker must not take the panel over"
        assert calls and calls[0][0] == "BREAKING"
        _color, _speed, overlay, _band, _font, secs = calls[0][1]
        assert overlay is True and secs == 30
        assert c._ticker_tasks, "no self-clear was scheduled"
        c.abort()                                # cancels the pending clear
        assert not c._ticker_tasks
    asyncio.run(go())


# ---------------------------------------------------------------------------
# a clock page re-ticks mid-hold on the minute boundary (refresh_align: minute)
# ---------------------------------------------------------------------------
def test_clock_page_reticks_at_the_minute_boundary_during_a_long_hold(tmp_path, monkeypatch):
    """A Time screen held for a long dwell must still tick over: _hold_page wakes at each
    wall-clock minute boundary, re-fetches, and re-emits the page with the new time."""
    import app.engine as eng

    async def go():
        c = _controller(tmp_path, FakeGateway())
        c.plugins.manifest = lambda a: {"refresh_align": "minute"}
        fake = {"t": 100000 * 60 + 30.0}                       # 30s into a minute
        monkeypatch.setattr(eng.time, "monotonic", lambda: fake["t"])
        monkeypatch.setattr(eng.time, "time", lambda: fake["t"])
        # get_pages renders the CURRENT (fake) minute, so the text changes each rollover.
        c.plugins.get_pages = lambda app_id, ov=None: [f"TIME {int(fake['t'] // 60)}"]

        async def fake_sleep(delay):                           # advance the clock, never really wait
            fake["t"] += max(0.0, delay)
        monkeypatch.setattr(c, "_entry_sleep", fake_sleep)

        emits = []

        async def spy_emit(clean, *, style, speed, record_as=None):
            emits.append(record_as)
            c._app_last_sent = record_as                       # mirror the real emit path
            return True
        monkeypatch.setattr(c, "_emit_page_from_loop", spy_emit)

        c._app_last_sent = "TIME 100000"                       # what's on the wall now
        t = {"is_anim": False, "style": "ltr", "speed": 0}
        await c._hold_page("dashboard", None, 0, 200.0, t, lambda: True)   # ~3 rollovers
        assert emits == ["TIME 100001", "TIME 100002", "TIME 100003"]
    asyncio.run(go())


def test_hold_page_without_refresh_align_just_sleeps(tmp_path, monkeypatch):
    """A normal app holds its page for the whole dwell — one sleep, no re-emits."""
    async def go():
        c = _controller(tmp_path, FakeGateway())
        c.plugins.manifest = lambda a: {}                      # not a clock app
        naps = []
        async def fake_sleep(delay):
            naps.append(delay)
        monkeypatch.setattr(c, "_entry_sleep", fake_sleep)
        monkeypatch.setattr(c, "_emit_page_from_loop",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-emit")))
        await c._hold_page("news", None, 0, 30.0, {"is_anim": False, "style": "ltr", "speed": 0},
                           lambda: True)
        assert naps == [30.0]
    asyncio.run(go())


# ---------------------------------------------------------------------------
# the loops still resume normally
# ---------------------------------------------------------------------------
def test_per_page_seconds_set_the_dwell_else_loop_delay(tmp_path):
    """An app with predefined screens can hold each a different length by returning page dicts
    with their own `seconds`; a page without one falls back to the app's loop_delay."""
    async def go():
        c = _controller(tmp_path, FakeGateway())
        c.plugins.get_pages = lambda app_id, ov=None: [
            {"text": "A", "seconds": 3}, {"text": "B", "seconds": 12}, "C"]
        c.plugins.page_timing = lambda app_id, ov=None: {
            "is_anim": False, "style": "ltr", "speed": 0, "loop_delay": 7}
        slept = []

        async def fake_sleep(d):
            slept.append(d)

        async def fake_emit(*a, **k):
            return True

        c._entry_sleep = fake_sleep            # capture dwell instead of waiting
        c._emit_page_from_loop = fake_emit     # isolate the dwell logic from the transport
        c._normalize = lambda text, frame=False: text
        await c._play_app_pages("x", None, lambda: True)
        assert slept == [3.0, 12.0, 7.0]       # per-page, per-page, then the loop_delay fallback
    asyncio.run(go())


def test_an_app_whose_page_changes_still_updates(tmp_path):
    """Suppression must only skip pages the wall REALLY shows — a changed page sends."""
    async def go():
        tr = FakeGateway()
        c = _controller(tmp_path, tr)
        await c.run_app("static")
        await _until(lambda: _shown(c).startswith("HELLO"), "the app never drew")
        n = len(tr.batches)
        await asyncio.sleep(0.1)
        assert len(tr.batches) == n, "an unchanged page was re-sent — suppression is gone"

        c.plugins.page = "WORLD"
        await _until(lambda: _shown(c).startswith("WORLD"), "the new page never drew")
        await c.stop()
    asyncio.run(go())
