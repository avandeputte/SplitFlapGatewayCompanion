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

from app.config import Config
from app.engine import DisplayController
from app.state import DisplayState


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
        await asyncio.sleep(0.4)          # let the note's timer expire
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
# the loops still resume normally
# ---------------------------------------------------------------------------
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
