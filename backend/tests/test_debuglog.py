"""The wire log (debuglog.py): opt-in capture of gateway I/O + app fetches, downloadable.

Off by default and a no-op then; on, it appends readable lines (hex+ascii for binary
bodies, secret query values redacted) to a bounded, rotating file. Also pinned: the two
gateway-send chokepoints (gateway._request and the REST transport's POSTs) and the
requests hook actually funnel through it when it's on.
"""
import asyncio
from pathlib import Path

import pytest

from app import debuglog


@pytest.fixture
def log(tmp_path):
    """A configured, ENABLED wire log at a temp path (small cap so rotation is testable);
    reset to disabled after so the process-wide singleton never bleeds into other tests."""
    p = tmp_path / "debug" / "wire.log"
    debuglog.configure(p, True, max_bytes=2000)
    yield p
    debuglog.configure(p, False)


def _text() -> str:
    return debuglog.read_bytes().decode("utf-8")


def test_disabled_is_a_no_op(tmp_path):
    p = tmp_path / "w.log"
    debuglog.configure(p, False)
    debuglog.gw_send("POST", "http://gw", "/api/rs485/batch", b"xx")
    debuglog.app_send("GET", "http://x/y")
    assert not p.exists()                 # nothing was even opened
    assert debuglog.read_bytes() == b""
    assert debuglog.is_enabled() is False


def test_gateway_send_and_recv_are_captured(log):
    debuglog.gw_send("POST", "http://192.168.1.204", "/api/rs485/batch",
                     b'{"frames":[[0,"H"]]}', "application/json", note="1 frame")
    debuglog.gw_recv("POST", "http://192.168.1.204", "/api/rs485/batch", 200, 5.0,
                     body=b'{"ok":true}', ctype="application/json")
    txt = _text()
    assert "GW POST 192.168.1.204/api/rs485/batch  1 frame" in txt
    assert "GW 200 POST 192.168.1.204/api/rs485/batch" in txt


def test_binary_body_shows_hex_and_ascii(log):
    debuglog.gw_send("POST", "http://gw", "/api/rs485/send", b"\x00\xffAB")
    txt = _text()
    assert "hex: 00 ff 41 42" in txt      # the actual bytes — the point of the feature
    assert "asc: ..AB" in txt             # non-printables collapse to '.'


def test_app_fetch_redacts_secret_query_values(log):
    debuglog.app_send("GET", "https://api.example/v1?lat=1&api_key=SEEKRIT&token=T")
    debuglog.app_recv("GET", "https://api.example/v1?api_key=SEEKRIT", 200, 9.0, size=1843)
    txt = _text()
    assert "api_key=REDACTED" in txt and "token=REDACTED" in txt
    assert "SEEKRIT" not in txt           # the key never lands in a shareable log
    assert "lat=1" in txt                 # ordinary params are kept
    assert "1843B" in txt


def test_gateway_recv_error_is_captured(log):
    debuglog.gw_recv("POST", "http://gw", "/api/x", None, 12.0, error=RuntimeError("boom"))
    txt = _text()
    assert "FAILED after 12ms" in txt and "boom" in txt


def test_rotation_bounds_the_file(log):
    prev = Path(str(log) + ".1")
    for _ in range(400):
        debuglog.gw_send("POST", "http://gw", "/api/rs485/batch", b"x" * 40)
    assert prev.exists(), "the log never rotated despite exceeding the cap"
    assert log.stat().st_size <= 2000 + 500, "current file grew past the cap + one line"
    assert debuglog.status()["bytes"] >= log.stat().st_size    # counts both halves


def test_clear_empties_the_log_but_keeps_it_on(log):
    debuglog.gw_send("POST", "http://gw", "/api/x", b"hello")
    assert "hello" in _text()
    debuglog.clear()
    txt = _text()
    assert "hello" not in txt              # the captured bytes are gone
    assert "log cleared" in txt            # still recording — the clear marker is written


# --- the two gateway-send chokepoints actually funnel through it ---------------
def test_gateway_request_records_send_and_receive(tmp_path, monkeypatch):
    from app import gateway

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"ok":true}'

    class FakeClient:
        def request(self, method, path, *, timeout, **kw):
            return FakeResp()

    monkeypatch.setattr(gateway, "_client", lambda url: FakeClient())
    debuglog.configure(tmp_path / "w.log", True)
    try:
        r = gateway._request("POST", "http://192.168.1.204", "/api/canvas/ops",
                             timeout=5.0, json={"op": "clear"})
        assert r.status_code == 200
        txt = _text()
        assert "GW POST 192.168.1.204/api/canvas/ops" in txt
        assert "GW 200 POST 192.168.1.204/api/canvas/ops" in txt
    finally:
        debuglog.configure(tmp_path / "w.log", False)


def test_rest_transport_post_records(tmp_path):
    from app.transport.rest import RestTransport

    class FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"ok":true}'

        def raise_for_status(self):
            pass

    class FakeClient:
        async def post(self, path, *, content, headers, timeout):
            return FakeResp()

    t = RestTransport("http://192.168.1.50")
    t._client = FakeClient()
    debuglog.configure(tmp_path / "w.log", True)
    try:
        asyncio.run(t.send_frame(3, "A"))
        txt = _text()
        assert "GW POST 192.168.1.50/api/rs485/send  1 frame" in txt
        assert "GW 200 POST 192.168.1.50/api/rs485/send" in txt
    finally:
        debuglog.configure(tmp_path / "w.log", False)


def test_requests_hook_captures_an_app_fetch(tmp_path, monkeypatch):
    """The requests seam: wrapping requests.Session.request once catches every requests.get."""
    import requests.sessions as rs

    class FakeResp:
        status_code = 200
        content = b"x" * 512

    calls = []

    def fake_request(self, method, url, **kw):
        calls.append((method, url))
        return FakeResp()

    # Pretend nothing is wrapped yet, and make the "real" request our stub.
    monkeypatch.setattr(rs.Session, "_companion_wire_wrapped", False, raising=False)
    monkeypatch.setattr(rs.Session, "request", fake_request, raising=False)

    debuglog.configure(tmp_path / "w.log", True)
    try:
        debuglog.install_http_hooks()
        import requests
        requests.Session().request("GET", "https://api.example/v1?zip=90210&key=SECRET")
        assert calls == [("GET", "https://api.example/v1?zip=90210&key=SECRET")]
        txt = _text()
        assert "APP GET https://api.example/v1?zip=90210&key=REDACTED" in txt
        assert "APP 200 GET" in txt and "512B" in txt
        assert "SECRET" not in txt
    finally:
        debuglog.configure(tmp_path / "w.log", False)


def test_urllib_hook_captures_an_app_fetch(tmp_path, monkeypatch):
    """The stdlib seam: several apps fetch with urllib.request.urlopen, not requests."""
    import urllib.request as ur

    class FakeResp:
        status = 200

    calls = []

    def fake_urlopen(url, *a, **kw):
        calls.append(url.full_url if hasattr(url, "full_url") else url)
        return FakeResp()

    monkeypatch.setattr(ur, "_companion_wire_wrapped", False, raising=False)
    monkeypatch.setattr(ur, "urlopen", fake_urlopen, raising=False)

    debuglog.configure(tmp_path / "w.log", True)
    try:
        debuglog.install_http_hooks()
        req = ur.Request("https://feed.example/rss?token=SECRET",
                         headers={"User-Agent": "SplitFlap/1.0"})
        ur.urlopen(req, timeout=8)
        assert calls and calls[0] == "https://feed.example/rss?token=SECRET"
        txt = _text()
        assert "APP GET https://feed.example/rss?token=REDACTED" in txt
        assert "APP 200 GET" in txt
        assert "SECRET" not in txt
    finally:
        debuglog.configure(tmp_path / "w.log", False)
