"""REST batch transport + engine batch path (the animation-gap fix)."""

import asyncio

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

    async def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return FakeResp(self.status)


def _rest(status=200):
    t = RestTransport("http://gw")
    t._client = FakeHttp(status)
    return t


def test_send_batch_one_request_per_page():
    t = _rest(200)
    ok = asyncio.run(t.send_batch([(0, "A"), (1, "B"), (2, "y")], 15))
    assert ok is True
    url, payload = t._client.calls[0]
    assert url == "/api/rs485/batch"
    assert payload["frames"] == ["m00-A\n", "m01-B\n", "m02-y\n"]
    assert payload["step_ms"] == 15


def test_send_batch_404_falls_back_and_sticks():
    t = _rest(404)
    assert asyncio.run(t.send_batch([(0, "A")], 15)) is False
    assert t._no_batch is True
    # once we know there's no batch endpoint, we don't POST again
    assert asyncio.run(t.send_batch([(0, "A")], 15)) is False
    assert len(t._client.calls) == 1


# --- engine integration ---
class FakeBatchTransport:
    type_name = "fake"
    batch_capable = True

    def __init__(self, batch_ok=True):
        self.batch_ok = batch_ok
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
        return self.batch_ok


def _controller(tmp_path, transport):
    cfg = Config(data_dir=tmp_path)
    st = DisplayState(cfg.module_count())
    ctrl = DisplayController(cfg, st)
    ctrl.transport = transport
    return ctrl, cfg


def test_engine_uses_batch_when_supported(tmp_path):
    tr = FakeBatchTransport(batch_ok=True)
    ctrl, cfg = _controller(tmp_path, tr)
    asyncio.run(ctrl.send_text("HELLO", style="ltr", speed=0))
    assert len(tr.batches) == 1          # one request for the whole page
    assert tr.frames == []               # no per-frame calls
    frames, step = tr.batches[0]
    assert len(frames) == cfg.module_count()
    assert frames[0] == (0, "H")
    # preview state reflects the page
    assert "".join(ctrl.state.snapshot()["chars"]).startswith("HELLO")


def test_engine_falls_back_to_per_frame(tmp_path):
    tr = FakeBatchTransport(batch_ok=False)   # gateway lacks /batch
    ctrl, cfg = _controller(tmp_path, tr)
    asyncio.run(ctrl.send_text("HI", style="ltr", speed=0))
    assert len(tr.batches) == 1               # tried batch once
    assert len(tr.frames) == cfg.module_count()  # then per-frame for the page
