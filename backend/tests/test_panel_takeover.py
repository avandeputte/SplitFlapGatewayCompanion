"""canvas.take_over — the app-switch panel reset.

A new app run takes the panel with ``set_active(True)`` (the firmware clears the whole
panel on that takeover). But a device-side renderer left by the PREVIOUS app — an
effect, a looping anim, a ticker — keeps re-claiming the panel every frame and paints
over the newcomer: the "pixels from the last app linger" bug. ``take_over`` polls the
canvas state once and, only when such a layer is live, stands the device down first
(``{active:false}`` + an empty ticker text for the overlay ticker). The everyday
switch between plain draw apps stays on the flash-free single-POST path.
"""

import app.canvas as canvas
import app.gateway as gateway
from conftest import json_response


def _wire(monkeypatch, state):
    """Route gateway._request: GET /api/canvas answers ``state``, everything else 200-OK.
    Returns the (method, path, json) call log."""
    calls = []

    def fake(method, url, path, *, timeout, **kw):
        calls.append((method, path, kw.get("json")))
        if method == "GET" and path == "/api/canvas":
            return json_response(state)
        return json_response({"ok": True, "active": True})

    monkeypatch.setattr(gateway, "_request", fake)
    return calls


def test_quiet_wall_takes_over_without_the_flashing_stand_down(monkeypatch):
    calls = _wire(monkeypatch, {"active": False, "effect": "none",
                                "anim": False, "ticker": False})
    assert canvas.take_over("http://gw") is True
    posts = [(p, j) for m, p, j in calls if m == "POST"]
    assert posts == [("/api/canvas", {"active": True})]   # one POST, no {active: false} blink


def test_live_effect_is_stood_down_before_the_takeover(monkeypatch):
    calls = _wire(monkeypatch, {"active": True, "effect": "plasma",
                                "anim": False, "ticker": False})
    assert canvas.take_over("http://gw") is True
    posts = [(p, (j or {}).get("active")) for m, p, j in calls
             if m == "POST" and p == "/api/canvas"]
    assert posts == [("/api/canvas", False), ("/api/canvas", True)]
    assert not any(p == "/api/canvas/ticker" for _, p, _ in calls)


def test_looping_anim_is_stood_down(monkeypatch):
    calls = _wire(monkeypatch, {"active": False, "effect": "none",
                                "anim": True, "ticker": False})
    canvas.take_over("http://gw")
    posts = [(j or {}).get("active") for m, p, j in calls
             if m == "POST" and p == "/api/canvas"]
    assert posts == [False, True]


def test_leftover_ticker_gets_the_explicit_empty_text_stop(monkeypatch):
    calls = _wire(monkeypatch, {"active": False, "effect": "none",
                                "anim": False, "ticker": True})
    canvas.take_over("http://gw")
    idx = {(m, p): i for i, (m, p, _j) in enumerate(calls)}
    tick = next(i for i, (m, p, j) in enumerate(calls)
                if p == "/api/canvas/ticker" and (j or {}).get("text") == "")
    offs = [i for i, (m, p, j) in enumerate(calls)
            if p == "/api/canvas" and m == "POST" and (j or {}).get("active") is False]
    ons = [i for i, (m, p, j) in enumerate(calls)
           if p == "/api/canvas" and m == "POST" and (j or {}).get("active") is True]
    assert offs and ons and offs[0] < tick < ons[0]       # off → ticker stop → clean take


def test_state_poll_failure_still_takes_the_panel(monkeypatch):
    calls = []

    def fake(method, url, path, *, timeout, **kw):
        calls.append((method, path, kw.get("json")))
        if method == "GET":
            raise OSError("wall rebooting")
        return json_response({"ok": True, "active": True})

    monkeypatch.setattr(gateway, "_request", fake)
    assert canvas.take_over("http://gw") is True          # degraded to plain set_active(True)
    assert ("POST", "/api/canvas", {"active": True}) in calls


def test_engine_take_panel_claims_quietly(monkeypatch):
    """The switch path must NOT go through take_over (set_active(True)): the firmware
    clears-and-presents on that takeover — a black hole between apps plus one more full-panel
    present, i.e. one more visible blink per switch on the LCD. The engine stands down any live
    device renderer and lets the app's first push claim the panel (canvasEnter(false): park the
    reel renderer, keep the pixels). take_over stays for the paths that WANT the wipe (stop-blank)."""
    import inspect
    from app import engine
    src = inspect.getsource(engine.DisplayController._take_panel)
    assert "canvas.stand_down" in src and "canvas.forget_frame" in src
    assert "canvas.take_over" not in src and "canvas.set_active" not in src
    # the stop-blank keeps the authoritative clear
    blank = inspect.getsource(engine.DisplayController._blank_panel)
    assert "canvas.take_over" in blank and "canvas.release" in blank


def test_stand_down_stops_a_live_renderer_without_claiming(monkeypatch):
    """stand_down = the quiet half of take_over: with an effect live it stops it (active:false),
    with nothing live it touches nothing — and it NEVER sends active:true (the clearing claim)."""
    calls = _wire(monkeypatch, {"active": False, "effect": "plasma",
                                "anim": False, "ticker": False})
    assert canvas.stand_down("http://gw") is True
    assert ("POST", "/api/canvas", {"active": False}) in calls
    assert not any((j or {}).get("active") is True for _m, p, j in calls if p == "/api/canvas")
    calls2 = _wire(monkeypatch, {"active": False, "effect": "none",
                                 "anim": False, "ticker": False})
    assert canvas.stand_down("http://gw") is True
    assert [c for c in calls2 if c[0] == "POST"] == []     # idle wall: one GET, no writes
