"""gestures.py — the companion's consumer of the gateway's clap/tap events.

The Matrix Gateway detects CLAPS (microphones) and TAPS (the IMU) on-device and
broadcasts them on its SSE stream (GET /api/events) as ``event: clap`` / ``event:
tap`` with ``{"count": N, "seq": M}`` — after first offering them to its own
timer/alarm (a double gesture dismisses those on-device; singles reach us).

Per display, ``watch()`` rides that stream for the process lifetime (reconnecting
with backoff) and turns each gesture into the user's configured action:

  * ``playlist_next`` (the default) — advance the running playlist to its next
    entry via the engine's skip; outside a playlist the gesture does nothing.
  * ``stop``   — stop whatever is running.
  * ``none``   — ignore.

The settings keys are ``gesture_clap`` / ``gesture_tap`` (per display, alongside
the other global settings). ``seq`` dedupes SSE re-delivery; a short debounce
keeps an enthusiastic burst of applause from skipping three entries at once.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx2 as httpx

log = logging.getLogger("companion.gestures")

ACTIONS = ("none", "playlist_next", "stop")
_DEBOUNCE_S = 1.2
_BACKOFF_MIN, _BACKOFF_MAX = 2.0, 30.0


def action_for(settings, kind: str) -> str:
    """The configured action for a gesture kind ('clap'/'tap') — playlist_next unless
    the user chose otherwise."""
    v = str(settings.get(f"gesture_{kind}", "playlist_next") or "").strip().lower()
    return v if v in ACTIONS else "playlist_next"


async def dispatch(d, kind: str) -> str:
    """Run the configured action for one gesture. Returns what actually happened —
    'none' when there was nothing to do (no playlist running, action off)."""
    act = action_for(d.settings, kind)
    if act == "playlist_next":
        return "playlist_next" if d.controller.skip_playlist_entry() else "none"
    if act == "stop":
        if d.controller.active_app or d.controller.active_playlist:
            await d.controller.stop_app()
            return "stop"
        return "none"
    return "none"


class GestureState:
    """Per-stream dedupe + debounce (seq guards SSE re-delivery; the clock guards
    bursts). Pure bookkeeping, so tests can drive frames straight through it."""

    def __init__(self) -> None:
        self.seen = {"clap": None, "tap": None}
        self.last = {"clap": -1e9, "tap": -1e9}

    def admit(self, kind: str, seq, now: float) -> bool:
        if kind not in self.seen:
            return False
        if seq is not None and seq == self.seen[kind]:
            return False
        if now - self.last[kind] < _DEBOUNCE_S:
            return False
        self.seen[kind] = seq
        self.last[kind] = now
        return True


async def handle_frame(d, state: GestureState, event: str, data: str, now: float) -> str | None:
    """One SSE frame: admit clap/tap through the dedupe/debounce and dispatch.
    Returns the action taken, or None for frames that are not (fresh) gestures."""
    if event not in ("clap", "tap"):
        return None
    try:
        doc = json.loads(data) if data else {}
    except ValueError:
        doc = {}
    if not state.admit(event, doc.get("seq"), now):
        return None
    act = await dispatch(d, event)
    if act != "none":
        log.info("display %r: %s -> %s", d.id, event, act)
    return act


async def watch(d) -> None:
    """The per-display task: consume the gateway's SSE stream forever, dispatching
    gestures; reconnect with backoff on any drop. Cancelled at display stop."""
    url = str(d.gateway_url or "").rstrip("/")
    if not url:
        return
    state = GestureState()
    backoff = _BACKOFF_MIN
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client:
                async with client.stream("GET", f"{url}/api/events") as r:
                    if r.status_code != 200:
                        raise OSError(f"events stream answered {r.status_code}")
                    backoff = _BACKOFF_MIN
                    event = ""
                    loop = asyncio.get_running_loop()
                    async for line in r.aiter_lines():
                        line = line.rstrip("\n")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            await handle_frame(d, state, event, line[5:].strip(), loop.time())
                        elif not line:
                            event = ""                 # frame boundary
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("display %r gesture stream dropped: %s", d.id, e)
        await asyncio.sleep(backoff)
        backoff = min(_BACKOFF_MAX, backoff * 1.7)
