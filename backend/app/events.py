"""
events.py — Server-Sent Events for the live preview.

The backend PUSHES the display state over an SSE stream (``GET /api/events``) the
instant it changes, mirroring the Matrix gateway's own ``/api/events`` (firmware 3.0).
One pump task per display diffs the snapshot on a short cadence and broadcasts it to
every connected browser; the browser's ``EventSource`` applies each frame exactly as it
applies a ``/api/current_state`` poll result, and falls back to polling if the stream
drops.

Why diff-poll the snapshot rather than signal on every state mutation? Because the two
things the preview cares about live in different places — the flap characters in
``DisplayState``, the "a canvas is drawing" flag on the controller — and a single cheap
snapshot comparison catches a change in either without threading a notification through
every write site. The cadence (~7 Hz) is imperceptible as latency and collapses a flip
cascade into a few frames, the same shape as the firmware's own rate limit.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

log = logging.getLogger("companion.events")

# The preview follows the wall closely but need not be frame-perfect: a short diff cadence
# keeps latency invisible while coalescing a cascade. 15 s of quiet earns a keepalive so
# proxies (and HA ingress) hold the idle connection open.
_TICK_S = 0.15
_KEEPALIVE_S = 15.0
# A generous ceiling: the companion is not RAM-tight like the panel, but an unbounded set
# of streams (a reconnect storm, a wedged client) should still be impossible. A refused
# stream degrades to client-side polling, so the cap is safe to hit.
_MAX_SUBSCRIBERS = 16


class TooManyStreams(RuntimeError):
    """Raised by :meth:`StateHub.subscribe` at the subscriber ceiling; the route turns it
    into a 503 — the same refusal the firmware gives a surplus stream."""


def _frame(snap: dict) -> str:
    return "event: display\ndata: " + json.dumps(snap, separators=(",", ":"), ensure_ascii=False) + "\n\n"


class StateHub:
    """Fan-out of one display's live state to every connected SSE client.

    A single pump task per display builds the snapshot on a short cadence and, when it
    differs from the last, hands it to every subscriber's queue. The pump runs only while
    someone is listening — it starts on the first subscriber and stops after the last
    leaves — so an idle display costs nothing.
    """

    def __init__(self, snapshot_fn: Callable[[], dict]):
        self._snapshot = snapshot_fn
        self._subs: set[asyncio.Queue] = set()
        self._pump: asyncio.Task | None = None
        self._last_json: str | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    # -- subscription -------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        """Register a client and return its frame queue. Raises :class:`TooManyStreams`
        at the ceiling. Eager (not deferred to the generator's first iteration) so the
        route can answer 503 before it commits to a streaming response."""
        if len(self._subs) >= _MAX_SUBSCRIBERS:
            raise TooManyStreams("too many event streams")
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subs.add(q)
        self._ensure_pump()
        return q

    def _unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    async def stream(self, q: asyncio.Queue):
        """Yield SSE text for one subscribed client: a reconnect hint, an immediate
        snapshot so the preview paints at once, then live frames off the queue. The
        ``finally`` unsubscribes whenever the client goes away (the generator is closed
        on disconnect), which lets the pump wind down once the last client leaves."""
        try:
            yield "retry: 3000\n\n"
            try:
                yield _frame(self._snapshot())
            except Exception:                       # a bad snapshot must not abort the stream
                pass
            while True:
                yield await q.get()
        finally:
            self._unsubscribe(q)

    # -- pump ---------------------------------------------------------------
    def _ensure_pump(self) -> None:
        # Created on the event loop (subscribe() only runs inside a request handler), with
        # no await between the check and create_task, so two pumps can never race into life.
        if self._pump is not None and not self._pump.done():
            return
        # Seed the diff baseline with the CURRENT state so the pump's first tick doesn't
        # re-push a frame identical to the immediate snapshot stream() just sent the client.
        try:
            self._last_json = json.dumps(self._snapshot(), separators=(",", ":"), ensure_ascii=False)
        except Exception:
            self._last_json = None
        self._pump = asyncio.create_task(self._run())

    async def _run(self) -> None:
        since_ka = 0.0
        while self._subs:
            await asyncio.sleep(_TICK_S)
            try:
                payload = json.dumps(self._snapshot(), separators=(",", ":"), ensure_ascii=False)
            except Exception as e:                  # never let the pump die on one bad snapshot
                log.debug("live snapshot failed: %s", e)
                continue
            if payload != self._last_json:
                self._last_json = payload
                since_ka = 0.0
                self._broadcast("event: display\ndata: " + payload + "\n\n")
            else:
                since_ka += _TICK_S
                if since_ka >= _KEEPALIVE_S:
                    since_ka = 0.0
                    self._broadcast(": ka\n\n")

    def _broadcast(self, frame: str) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                # A wedged client must not hold the wall's state hostage: drop its oldest
                # frame for the newest. The preview only ever wants the latest state.
                try:
                    q.get_nowait()
                    q.put_nowait(frame)
                except Exception:
                    pass
