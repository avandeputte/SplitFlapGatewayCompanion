"""gameinput.py — the low-latency control channel for interactive matrix apps.

A running matrix game (Chomper) reads player input every frame; the web UI POSTs each
button/keypress to ``/api/game/input``, which lands here. State is a tiny in-memory
buffer per wall (keyed by gateway url, the same identity ``canvas`` uses), so an input is
available to the very next tick with no round trip of its own.

Two kinds of input: a HELD DIRECTION (the freshest one wins — classic buffered-turn
steering) and discrete EVENTS (start / pause / coin — drained once). ``last_ts`` lets an
app run an attract-mode demo and hand control to the player the instant a human touches
the pad, then fall back after a few idle seconds.
"""

from __future__ import annotations

import threading
import time

_DIRS = ("up", "down", "left", "right")
_EVENTS = ("start", "pause", "coin")
_lock = threading.Lock()
_walls: dict[str, "_Pad"] = {}


class _Pad:
    __slots__ = ("dir", "events", "last_ts")

    def __init__(self):
        self.dir: str | None = None      # the latest held direction
        self.events: list[str] = []      # discrete presses awaiting a drain
        self.last_ts: float = 0.0        # monotonic time of the last input


def _pad(url: str) -> _Pad:
    key = (url or "").rstrip("/")
    with _lock:
        p = _walls.get(key)
        if p is None:
            p = _walls[key] = _Pad()
        return p


def push(url: str, action: str, *, now: float) -> bool:
    """Record one input for the wall. ``now`` is a monotonic timestamp (passed in so the
    module stays free of the wall-clock calls the render harness forbids). Returns whether
    the action was understood."""
    action = str(action or "").strip().lower()
    p = _pad(url)
    with _lock:
        if action in _DIRS:
            p.dir = action
        elif action in _EVENTS:
            p.events.append(action)
        elif action == "release":        # the pad was let go — stop steering, coast
            p.dir = None
        else:
            return False
        p.last_ts = now
        return True


class Controls:
    """The per-frame view an app reads: the held direction, the events since the last
    frame (drained), and whether a human has touched the pad recently (attract vs play)."""

    __slots__ = ("dir", "events", "_idle")

    def __init__(self, dir_, events, idle):
        self.dir = dir_
        self.events = events
        self._idle = idle

    def active(self, within: float = 6.0) -> bool:
        """True while the player is engaged — the last input was under ``within`` seconds
        ago. An app runs its attract-mode demo whenever this is False."""
        return self._idle < within


def snapshot(url: str, *, now: float) -> Controls:
    """The current controls for the wall, draining the discrete events. ``now`` is a
    monotonic timestamp (see ``push``)."""
    p = _pad(url)
    with _lock:
        events, p.events = p.events, []
        idle = now - p.last_ts if p.last_ts else 1e9
        return Controls(p.dir, events, idle)


def reset(url: str) -> None:
    """Forget a wall's input state (a fresh game, or a display teardown)."""
    with _lock:
        _walls.pop((url or "").rstrip("/"), None)
