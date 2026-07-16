"""Scheduler (app triggers): backoff arithmetic and concurrent dispatch.

The file had zero coverage, and the one line that most needed a test was the failure
backoff: ``min(interval·2ᶠ, 600)`` CAPPED the effective interval at 10 minutes, so a
failing trigger with a 3600 s interval was polled every 600 s — six times MORE often
than a healthy one. Backing off must go UP from the healthy interval, never below it.

Everything here drives ``check_triggers()`` directly with a fake clock and fake
plugins — no display, no network, no real gateway.
"""
import asyncio
import threading

import app.scheduler as scheduler_module
from app.scheduler import Scheduler


class FakeTime:
    """Stands in for the ``time`` module inside app.scheduler."""

    def __init__(self, t=1_000_000.0):
        # Far from epoch 0: _last_check defaults to 0, so a small "now" would sit
        # inside the very first interval window and nothing would ever be due.
        self.t = float(t)

    def time(self):
        return self.t


class FakeController:
    def __init__(self):
        self.interrupts = []

    async def fire_interrupt(self, text, seconds, *, frame=False, **kw):
        self.interrupts.append((text, seconds, frame))


class FakePlugins:
    """One trigger-capable app. ``behave`` decides what call_trigger does."""

    def __init__(self, interval=60.0, behave=None, cooldown=0.0):
        self.settings = {
            "triggers_enabled": True,
            "triggers": [{"id": "t1", "app": "alerts", "enabled": True,
                          "cooldown": cooldown, "conditions": {}}],
        }
        self.interval = interval
        self.behave = behave or (lambda app_id: False)
        self.calls = []

    def has_trigger(self, app_id):
        return True

    def manifest(self, app_id):
        return {"trigger_interval": self.interval, "trigger_display_seconds": 5}

    def call_trigger(self, app_id, conditions):
        self.calls.append(app_id)
        return self.behave(app_id)

    def get_pages(self, app_id):
        return ["Something happened"]

    def is_anim(self, app_id):
        return False


def _scheduler(plugins):
    return Scheduler(FakeController(), plugins)


def _check(s):
    asyncio.run(s.check_triggers())


# ---------------------------------------------------------------------------
# failure backoff
# ---------------------------------------------------------------------------
def test_a_failing_trigger_is_never_polled_more_often_than_a_healthy_one(monkeypatch):
    """The audit bug: interval 3600, one failure, and min(interval·2, 600) said "poll
    again in 600 s" — six times the healthy rate, as a REWARD for failing."""
    clock = FakeTime()
    monkeypatch.setattr(scheduler_module, "time", clock)

    def boom(app_id):
        raise RuntimeError("upstream down")

    p = FakePlugins(interval=3600, behave=boom)
    s = _scheduler(p)

    _check(s)
    assert len(p.calls) == 1
    assert s._failures["t1"] == 1

    clock.t += 700           # past the broken 600 s backoff, well short of 3600
    _check(s)
    assert len(p.calls) == 1, "a failing 3600s trigger was polled after only 700s"

    clock.t += 2900          # now a full healthy interval has passed
    _check(s)
    assert len(p.calls) == 2


def test_backoff_still_backs_off_small_intervals_capped_at_ten_minutes(monkeypatch):
    clock = FakeTime()
    monkeypatch.setattr(scheduler_module, "time", clock)

    def boom(app_id):
        raise RuntimeError("no")

    p = FakePlugins(interval=10, behave=boom)
    s = _scheduler(p)

    _check(s)                # t0: first check fails -> backoff 20
    clock.t += 10
    _check(s)
    assert len(p.calls) == 1, "a failing trigger was polled at the healthy interval"
    clock.t += 10            # 20s since the failure
    _check(s)
    assert len(p.calls) == 2

    # Pile on failures: the backoff caps at 600 s, not at 2^n eternity.
    s._failures["t1"] = 30
    base = clock.t
    clock.t = base + 599
    _check(s)
    assert len(p.calls) == 2
    clock.t = base + 600
    _check(s)
    assert len(p.calls) == 3


def test_a_healthy_trigger_polls_at_its_interval_and_recovers_after_failure(monkeypatch):
    clock = FakeTime()
    monkeypatch.setattr(scheduler_module, "time", clock)
    p = FakePlugins(interval=60)
    s = _scheduler(p)

    _check(s)
    clock.t += 59
    _check(s)
    assert len(p.calls) == 1
    clock.t += 1
    _check(s)
    assert len(p.calls) == 2
    assert s._failures.get("t1", 0) == 0     # success resets the count


# ---------------------------------------------------------------------------
# a fired trigger interrupts the display
# ---------------------------------------------------------------------------
def test_a_fired_trigger_interrupts_with_its_first_page(monkeypatch):
    clock = FakeTime()
    monkeypatch.setattr(scheduler_module, "time", clock)
    p = FakePlugins(interval=60, behave=lambda app_id: True)
    s = _scheduler(p)

    _check(s)
    assert s.c.interrupts == [("Something happened", 5.0, False)]
    assert s.last_fired("t1") == clock.t     # cooldown recorded


# ---------------------------------------------------------------------------
# triggers are checked concurrently
# ---------------------------------------------------------------------------
def test_one_slow_trigger_does_not_delay_the_others():
    """Both triggers' checks wait on a barrier that only opens when BOTH are running.
    Dispatched serially — as the old loop did — the first check times out at the
    barrier and fails; dispatched concurrently they meet and both succeed."""
    barrier = threading.Barrier(2)

    class TwoTriggers(FakePlugins):
        def __init__(self):
            super().__init__(interval=60)
            self.settings["triggers"] = [
                {"id": "a", "app": "one", "enabled": True, "cooldown": 0},
                {"id": "b", "app": "two", "enabled": True, "cooldown": 0},
            ]

        def call_trigger(self, app_id, conditions):
            self.calls.append(app_id)
            barrier.wait(timeout=3)          # only passable if BOTH run at once
            return False

    p = TwoTriggers()
    s = _scheduler(p)
    _check(s)
    assert sorted(p.calls) == ["one", "two"]
    assert not any(s._failures.values()), \
        "a check timed out at the barrier — dispatch is serial"
