"""REST batch transport + engine batch path (Gateway 3.0+; no legacy fallback)."""

import asyncio
import json

import pytest

from app.config import Config
from app.engine import DisplayController
from app.state import DisplayState
from app.transport.rest import RestTransport


class FakeResp:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=self)


class FakeHttp:
    def __init__(self, status=200):
        self.status = status
        self.calls = []

    async def post(self, url, json=None, content=None, headers=None, timeout=None):
        # The transport now sends a Windows-1252-encoded body via `content=`.
        self.calls.append({"url": url, "content": content, "headers": headers})
        return FakeResp(self.status)


def _rest(status=200):
    t = RestTransport("http://gw")
    t._client = FakeHttp(status)
    return t


def test_send_batch_one_request_per_page():
    t = _rest(200)
    asyncio.run(t.send_batch([(0, "A"), (1, "B"), (2, "y")], 15))
    call = t._client.calls[0]
    assert call["url"] == "/api/rs485/batch"
    payload = json.loads(call["content"].decode("cp1252"))
    assert payload["frames"] == ["m00-A\n", "m01-B\n", "m02-y\n"]
    assert payload["step_ms"] == 15
    assert t.connected is True


def test_send_batch_encodes_accents_as_windows_1252():
    """Accented characters must reach the gateway as single cp1252 bytes."""
    t = _rest(200)
    asyncio.run(t.send_batch([(0, "É"), (1, "Ü"), (2, "ß")], 0))
    body = t._client.calls[0]["content"]
    assert isinstance(body, bytes)
    # Each accent is a single cp1252 byte after the "mNN-" prefix (the trailing
    # newline is JSON-escaped as \n, so it isn't matched here).
    assert b"m00-\xc9" in body   # É -> 0xC9
    assert b"m01-\xdc" in body   # Ü -> 0xDC
    assert b"m02-\xdf" in body   # ß -> 0xDF
    # and NOT the UTF-8 two-byte form of É
    assert b"\xc3\x89" not in body
    assert "windows-1252" in t._client.calls[0]["headers"]["Content-Type"]


def test_send_batch_raises_on_error():
    t = _rest(500)
    with pytest.raises(Exception):
        asyncio.run(t.send_batch([(0, "A")], 0))
    assert t.connected is False


# --- engine integration ---
class FakeBatchTransport:
    type_name = "fake"
    batch_capable = True

    def __init__(self, raise_on_batch=False):
        self.raise_on_batch = raise_on_batch
        self.batches = []
        self.frames = []

    @property
    def connected(self):
        return True

    @property
    def last_error(self):
        return None

    async def send_frame(self, mid, ch):
        self.frames.append((mid, ch))

    async def send_batch(self, frames, step_ms):
        self.batches.append((frames, step_ms))
        if self.raise_on_batch:
            raise RuntimeError("boom")


def _controller(tmp_path, transport):
    cfg = Config(data_dir=tmp_path)
    st = DisplayState(cfg.module_count())
    ctrl = DisplayController(cfg, st)
    ctrl.transport = transport
    return ctrl, cfg


def test_engine_batches_the_whole_page(tmp_path):
    tr = FakeBatchTransport()
    ctrl, cfg = _controller(tmp_path, tr)
    asyncio.run(ctrl.send_text("HELLO", style="ltr", speed=0))
    assert len(tr.batches) == 1     # one request for the whole page
    assert tr.frames == []          # never per-frame
    frames, step = tr.batches[0]
    assert len(frames) == cfg.module_count()
    assert frames[0] == (0, "H")
    assert "".join(ctrl.state.snapshot()["chars"]).startswith("HELLO")


def test_engine_handles_batch_error_without_per_frame(tmp_path):
    tr = FakeBatchTransport(raise_on_batch=True)
    ctrl, cfg = _controller(tmp_path, tr)
    asyncio.run(ctrl.send_text("HI", style="ltr", speed=0))  # must not raise
    assert len(tr.batches) == 1
    assert tr.frames == []          # no legacy per-frame fallback
