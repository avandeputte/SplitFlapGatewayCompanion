"""Tests for POST /api/message — the plain-text 'show a message' endpoint the Home
Assistant integration and rest_commands use (no Vestaboard key needed).

Unlike /api/compose/send (which takes a raw grid string from the click-to-type editor),
this centres and word-wraps ordinary text, and `seconds` makes it a temporary takeover.
"""

import pytest
from fastapi.testclient import TestClient

ROWS, COLS = 3, 15


@pytest.fixture
def client(monkeypatch):
    from app import main

    calls = {}

    def fake_send(text, style=None, speed=None, raw=False):
        calls["send"] = {"text": text, "style": style, "raw": raw}
        return text

    def fake_temp(text, seconds, *, style="ltr", raw=True):
        calls["temp"] = {"text": text, "seconds": seconds, "style": style}
        return True                      # pretend something was running

    monkeypatch.setattr(main.controller, "send_text_bg", fake_send)
    monkeypatch.setattr(main.controller, "show_temporary", fake_temp)
    monkeypatch.setattr(main.ha, "publish_state", lambda: None)
    c = TestClient(main.app)
    c.calls = calls
    return c


def test_a_message_is_centred_on_the_board(client):
    r = client.post("/api/message", json={"text": "hello world"})
    assert r.status_code == 200 and r.json()["ok"] is True
    page = client.calls["send"]["text"]
    assert len(page) == ROWS * COLS
    lines = [page[i * COLS:(i + 1) * COLS] for i in range(ROWS)]
    assert "HELLO WORLD" in "".join(lines)         # uppercased, laid out
    assert client.calls["send"]["raw"] is True     # final chars, sent raw


def test_seconds_makes_it_a_temporary_takeover(client):
    r = client.post("/api/message", json={"text": "dinner", "seconds": 30})
    assert r.status_code == 200
    assert r.json()["seconds"] == 30 and r.json()["reverts_to"] == "app/playlist"
    assert client.calls["temp"]["seconds"] == 30
    assert "send" not in client.calls            # NOT a permanent send


def test_an_unknown_style_is_rejected(client):
    assert client.post("/api/message", json={"text": "x", "style": "nope"}).status_code == 400
