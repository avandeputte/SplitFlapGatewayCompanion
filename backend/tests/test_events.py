"""The companion's live-preview SSE stream — the StateHub broadcaster and GET /api/events.

The browser rides this stream instead of polling; these pin the pieces that make that
safe: an immediate snapshot on connect, a push only when the state actually changes, a
keepalive when it doesn't, a bounded subscriber set, and the route shape (text/event-stream,
503 at the ceiling).
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import app.events as ev
from app.events import StateHub, TooManyStreams


# -- StateHub (no HTTP) -----------------------------------------------------

def test_subscribe_is_bounded_and_frees():
    async def run():
        hub = StateHub(lambda: {"chars": [" "]})
        qs = [hub.subscribe() for _ in range(ev._MAX_SUBSCRIBERS)]
        assert hub.subscriber_count == ev._MAX_SUBSCRIBERS
        with pytest.raises(TooManyStreams):
            hub.subscribe()                       # the ceiling refuses a surplus stream
        hub._unsubscribe(qs[0])
        hub.subscribe()                           # a freed slot can be taken again
        assert hub.subscriber_count == ev._MAX_SUBSCRIBERS
    asyncio.run(run())


def test_immediate_snapshot_then_push_only_on_change(monkeypatch):
    monkeypatch.setattr(ev, "_TICK_S", 0.02)      # don't wait real cadence
    async def run():
        state = {"chars": ["A"]}
        hub = StateHub(lambda: dict(state))
        q = hub.subscribe()
        gen = hub.stream(q)
        assert (await gen.__anext__()).startswith("retry:")     # reconnect hint first
        first = await gen.__anext__()                           # then an immediate snapshot
        assert first.startswith("event: display") and '"A"' in first
        # No spurious duplicate: the very next frame is the CHANGE, not a re-send of A.
        state["chars"] = ["B"]
        frame = await asyncio.wait_for(gen.__anext__(), timeout=2)
        assert frame.startswith("event: display") and '"B"' in frame
        await gen.aclose()                        # unsubscribe; the pump winds down
        assert hub.subscriber_count == 0
    asyncio.run(run())


def test_keepalive_when_state_is_quiet(monkeypatch):
    monkeypatch.setattr(ev, "_TICK_S", 0.02)
    monkeypatch.setattr(ev, "_KEEPALIVE_S", 0.1)
    async def run():
        hub = StateHub(lambda: {"chars": ["A"]})  # never changes
        q = hub.subscribe()
        gen = hub.stream(q)
        await gen.__anext__()                      # retry
        await gen.__anext__()                      # immediate snapshot
        frame = await asyncio.wait_for(gen.__anext__(), timeout=2)
        assert frame.startswith(": ka")            # a comment keepalive, no data
        await gen.aclose()
    asyncio.run(run())


def test_broadcast_drops_oldest_for_a_wedged_client():
    async def run():
        hub = StateHub(lambda: {"n": 0})
        q = hub.subscribe()
        for i in range(hub_qsize := q.maxsize + 5):
            hub._broadcast(f"f{i}\n\n")            # more frames than the queue holds
        assert q.full()                            # bounded — a slow client can't grow it
        newest = None
        while not q.empty():
            newest = await q.get()
        assert newest == f"f{hub_qsize - 1}\n\n"    # the LATEST frame survived
    asyncio.run(run())


# -- GET /api/events route --------------------------------------------------

def test_events_route_streams_an_immediate_snapshot():
    # The route handler is called directly and its body iterator read a couple frames deep:
    # consuming an ENDLESS SSE body over an HTTP client (TestClient/ASGITransport) buffers to
    # completion and would never return. This exercises the real wiring — resolve display,
    # subscribe, StreamingResponse(text/event-stream) whose first frames are the snapshot.
    from app import main

    endpoint = next(r.endpoint for r in main.app.routes
                    if getattr(r, "path", None) == "/api/events")

    async def run():
        scope = {"type": "http", "method": "GET", "path": "/api/events",
                 "query_string": b"", "headers": []}
        resp = await endpoint(Request(scope, receive=lambda: None))
        assert resp.media_type == "text/event-stream"
        it = resp.body_iterator
        chunks = [await asyncio.wait_for(it.__anext__(), timeout=2) for _ in range(2)]
        await it.aclose()                          # unsubscribe; let the pump wind down
        await asyncio.sleep(0.2)
        return "".join(c if isinstance(c, str) else c.decode() for c in chunks)

    buf = asyncio.run(run())
    assert "event: display" in buf
    payload = json.loads(buf.split("data: ", 1)[1].split("\n", 1)[0])
    assert "chars" in payload and "canvas" in payload   # the live-preview snapshot shape


def test_events_route_503_when_no_hub(monkeypatch):
    from app import main
    monkeypatch.setattr(main.displays.default, "events", None, raising=False)
    r = TestClient(main.app).get("/api/events")
    assert r.status_code == 503
