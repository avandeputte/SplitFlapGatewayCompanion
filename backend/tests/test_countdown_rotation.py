"""Multiple countdowns rotate on a configurable timer, and the seconds keep
ticking. The app shows ONE countdown per fetch (chosen by wall-clock time via
`_rotation_index`) and is re-fetched every second — so the shown countdown
re-renders each second (its seconds advance) while the switch to the next
happens only every `transition_seconds`. Returning every countdown as its own
page instead coupled the rotation to the 1-second page dwell, flipping them
past once a second."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from conftest import load_app

DRAWN = SimpleNamespace(instant=True)


def test_rotation_index_holds_each_block_then_advances():
    idx = load_app("countdown")._rotation_index
    # span=6, 3 countdowns: same index for 6 consecutive seconds, then +1 (mod 3).
    base = 18_000_000  # multiple of span*count, so the first block is index 0
    assert [idx(base + s, 6, 3) for s in range(6)] == [0] * 6
    assert idx(base + 6, 6, 3) == 1
    assert idx(base + 12, 6, 3) == 2
    assert idx(base + 18, 6, 3) == 0        # wraps
    assert idx(base, 1, 3) == 0             # span clamped to >= 2 (never sub-second)
    assert idx(base, 6, 1) == 0 and idx(base, 6, 0) == 0


def _one_countdown_target(hours):
    return (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")


def test_only_one_countdown_shows_per_fetch():
    app = load_app("countdown")
    s = {"countdown_enabled": "on", "countdown_event": "ALPHA",
         "countdown_target": _one_countdown_target(2),
         "countdown_2_enabled": "on", "countdown_2_event": "BETA",
         "countdown_2_target": _one_countdown_target(200 * 24),
         "transition_seconds": "6"}
    text = " ".join(app.fetch(s, lambda *l, **k: " | ".join(l),
                              lambda: 3, lambda: 15, caps=DRAWN))
    assert not ("ALPHA" in text and "BETA" in text), text   # never both at once


def test_single_countdown_is_unaffected():
    app = load_app("countdown")
    s = {"countdown_enabled": "on", "countdown_event": "SOLO",
         "countdown_target": _one_countdown_target(50), "transition_seconds": "6"}
    text = " ".join(app.fetch(s, lambda *l, **k: " | ".join(l),
                              lambda: 3, lambda: 15, caps=DRAWN))
    assert "SOLO" in text
