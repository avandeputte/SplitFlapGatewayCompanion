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

from . import canvas

log = logging.getLogger("companion.gestures")

ACTIONS = ("none", "playlist_next", "stop")
_DEBOUNCE_S = 1.2
_BACKOFF_MIN, _BACKOFF_MAX = 2.0, 30.0

# The acknowledgment chirp: the wall says "heard you" the moment a gesture lands.
# A rising blip for "next", a falling one for "stop" — tiny and quiet. Only when the
# action actually happened (an idle clap stays silent), only on a wall with a speaker,
# and Quiet Time's 409 is swallowed inside play_sound, so quiet hours stay quiet.
_CHIRP = {"playlist_next": ([[880, 35], [1175, 55]], 35),
          "stop": ([[660, 45], [440, 70]], 35)}


def _chirp(d, act: str) -> None:
    try:
        caps = d.controller.caps
    except Exception:
        return
    if not getattr(caps, "can_sound", False):
        return
    notes_vol = _CHIRP.get(act)
    if notes_vol:
        canvas.play_sound(str(d.gateway_url or ""), notes=notes_vol[0], vol=notes_vol[1])


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
        _chirp(d, act)
    return act


async def watch(d) -> None:
    """The per-display task: consume the gateway's SSE stream forever, dispatching
    gestures; reconnect with backoff on any drop. Cancelled at display stop.

    The wall's capabilities are re-read EVERY cycle, never snapshotted: caps come
    from a probe that fails when the wall is down/mid-flash at companion startup,
    and a one-time check there quietly lost gestures for the whole process life.
    While the wall advertises no "events" capability this idles and re-checks."""
    url = str(d.gateway_url or "").rstrip("/")
    if not url:
        return
    state = GestureState()
    backoff = _BACKOFF_MIN
    announced = waiting = False
    while True:
        caps = getattr(d.controller, "caps", None)
        if not getattr(caps, "events", False):
            if not waiting:
                log.info("display %r: gesture watcher waiting for the wall's 'events' "
                         "capability (wall down, or an older firmware)", d.id)
                waiting, announced = True, False
            await asyncio.sleep(60.0)
            continue
        waiting = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None)) as client:
                async with client.stream("GET", f"{url}/api/events") as r:
                    if r.status_code != 200:
                        raise OSError(f"events stream answered {r.status_code}")
                    backoff = _BACKOFF_MIN
                    if not announced:                  # visible in the add-on log once
                        log.info("display %r: gesture watcher connected to %s/api/events",
                                 d.id, url)
                        announced = True
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
