"""The v3.2 canvas stream transport (`PUT /api/canvas/stream`): a persistent TLV draw channel.

These pin the wire contract with a fake socket — record framing, the firmware requirement that the
first record carries the request head, clean teardown, and error fallback — plus the `canvas.stream`
capability detection. The transport isn't wired into the engine yet (that's a separate step); here we
prove the bytes are right so that wiring is safe.
"""

import pytest

from app import canvas, device
from conftest import canvas_surface


class FakeSock:
    """Captures every sendall; hands back one canned HTTP reply on recv, then EOF."""

    def __init__(self, reply=b'HTTP/1.1 200 OK\r\n\r\n{"ok":true,"records":2}', fail_on=None):
        self.writes = []
        self._reply = reply
        self.fail_on = fail_on          # sendall raises on the Nth call (1-based)
        self._n = 0
        self.closed = False

    def settimeout(self, _):
        pass

    def sendall(self, b):
        self._n += 1
        if self.fail_on and self._n >= self.fail_on:
            raise OSError("broken pipe")
        self.writes.append(bytes(b))

    def recv(self, n):
        r, self._reply = self._reply, b""
        return r

    def close(self):
        self.closed = True


def _stream(fs):
    return canvas.CanvasStream("http://gw:80", connect=lambda: fs)


# --- capability detection ---------------------------------------------------

def test_canvas_stream_capability_is_detected():
    base = {"features": ["cells"], "charset": {"uniform": True, "common": "A"}}
    on = device.from_capabilities({**base, "canvas": {"formats": ["rgb888"], "width": 256,
                                                      "height": 64, "stream": True}})
    off = device.from_capabilities({**base, "canvas": {"formats": ["rgb888"], "width": 256,
                                                       "height": 64}})
    assert on.canvas_stream is True
    assert off.canvas_stream is False
    assert canvas_surface("http://gw", 256, 64, ("rgb888",), stream=True).can_stream is True
    assert canvas_surface("http://gw", 256, 64, ("rgb888",)).can_stream is False


# --- the wire contract ------------------------------------------------------

def test_open_sends_nothing_until_the_first_record():
    fs = FakeSock()
    s = _stream(fs)
    assert s.open() is True and s.alive is True
    assert fs.writes == []                                   # a bare head would parse-block the worker


def test_first_record_carries_the_request_head_in_one_write():
    fs = FakeSock()
    s = _stream(fs)
    s.open()
    s.present()                                              # 0x05
    assert len(fs.writes) == 1
    head, _, body = fs.writes[0].partition(b"\r\n\r\n")
    assert head.startswith(b"PUT /api/canvas/stream HTTP/1.1")
    assert b"Host: gw:80" in head and b"Content-Length:" in head
    assert body == canvas._tlv(0x05)                         # head + present, together
    s.present()
    assert fs.writes[1] == canvas._tlv(0x05)                 # later records: no head


def test_record_framing_type_len_payload():
    fs = FakeSock()
    s = _stream(fs)
    s.open()
    s.frame(2, b"\x01\x02\x03\x04")                          # 0x01: u8 fmt + pixels
    body = fs.writes[0].partition(b"\r\n\r\n")[2]
    assert body[0] == 0x01
    assert int.from_bytes(body[1:4], "big") == 5            # payload = fmt(1) + 4 px
    assert body[4] == 2 and body[5:9] == b"\x01\x02\x03\x04"


def test_rects_record_reuses_the_put_rects_body():
    fs = FakeSock()
    s = _stream(fs)
    s.open()
    rects = [(0, 0, 2, 1, b"\xaa\xbb\xcc\xdd")]
    s.rects(rects, fmt=2)
    body = fs.writes[0].partition(b"\r\n\r\n")[2]
    n = int.from_bytes(body[1:4], "big")
    assert body[0] == 0x02 and body[4:4 + n] == canvas._rects_body(rects, 2)


def test_close_sends_end_record_and_tears_down():
    fs = FakeSock()
    s = _stream(fs)
    s.open()
    s.present()
    s.present()
    n = s.close()
    assert fs.writes[-1] == canvas._tlv(0x00)                # 0x00 end
    assert n == 2 and fs.closed is True and s.alive is False
    assert s.close() is None                                 # idempotent


def test_close_without_any_record_just_closes():
    fs = FakeSock()
    s = _stream(fs)
    s.open()
    assert s.close() == 0                                    # nothing ever sent -> no end record
    assert fs.writes == [] and fs.closed is True


def test_a_socket_error_kills_the_session_for_fallback():
    fs = FakeSock(fail_on=1)                                 # first sendall blows up
    s = _stream(fs)
    s.open()
    assert s.present() is False and s.alive is False
    assert s.present() is False                              # stays dead; caller falls back to HTTP


# --- registry lifecycle + _push_rgb routing (Phase 3) -----------------------

@pytest.fixture
def clean_stream_state():
    yield
    canvas._WALLS.clear()                # one object owns all per-wall state now


def test_stream_begin_end_and_frame_routing(monkeypatch, clean_stream_state):
    url = "http://gw:80"
    fs = FakeSock()
    monkeypatch.setattr(canvas.socket, "create_connection", lambda *a, **k: fs)
    assert canvas.stream_begin(url) is True and canvas.has_stream(url) is True

    # With a stream open, a frame push must go over it — never the HTTP transport.
    import app.gateway as gateway
    monkeypatch.setattr(gateway, "_request", lambda *a, **k: pytest.fail("HTTP used while streaming"))
    surf = canvas_surface(url, 4, 2, ("rgb888", "rgb565"), rects=True)
    assert surf.frame(bytes(4 * 2 * 3)) is True              # first frame -> full frame record
    assert canvas.last_push_was_frame(url) is True
    assert fs.writes[0].startswith(b"PUT /api/canvas/stream")   # head rode the first record
    body = fs.writes[0].partition(b"\r\n\r\n")[2]
    assert body[0] == 0x01                                   # a full-frame record
    assert fs.writes[-1] == canvas._tlv(0x05)                # ...then present

    canvas.stream_end(url)
    assert canvas.has_stream(url) is False
    assert fs.writes[-1] == canvas._tlv(0x00)                # end record on close


def test_push_rgb_falls_back_to_http_when_the_stream_dies(monkeypatch, clean_stream_state):
    url = "http://gw2"
    fs = FakeSock(fail_on=1)                                 # the first record (the frame) fails
    monkeypatch.setattr(canvas.socket, "create_connection", lambda *a, **k: fs)
    canvas.stream_begin(url)                                 # open() doesn't send, so it's still alive here
    calls = []
    import app.gateway as gateway
    monkeypatch.setattr(gateway, "_request",
                        lambda m, u, p, **k: calls.append(p) or type("R", (), {"status_code": 200,
                                                                              "json": lambda s: {}})())
    surf = canvas_surface(url, 4, 2, ("rgb888",))       # no rects/qoi -> a raw full frame
    assert surf.frame(bytes(4 * 2 * 3)) is True
    assert calls == ["/api/canvas/frame"]                    # stream send failed -> HTTP fallback
    assert canvas.has_stream(url) is False                   # the dead stream stays closed
