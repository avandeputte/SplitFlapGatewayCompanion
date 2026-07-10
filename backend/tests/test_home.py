"""Home All: gateway broadcast + preview blanking + endpoint error shaping."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import gateway
from app.config import Config
from app.engine import DisplayController
from app.state import DisplayState


class _Recorder:
    """Stand-in for gateway.home_all that records the URLs it was called with."""

    def __init__(self, ret=True):
        self.calls = []
        self.ret = ret

    async def __call__(self, url, timeout=10.0):
        self.calls.append(url)
        return self.ret


def _controller(tmp_path):
    cfg = Config(data_dir=tmp_path)
    st = DisplayState(cfg.module_count())
    return DisplayController(cfg, st), cfg, st


def test_home_all_sim_skips_gateway_and_blanks(tmp_path, monkeypatch):
    ctrl, cfg, st = _controller(tmp_path)
    cfg.set_sim_mode(True)
    rec = _Recorder()
    monkeypatch.setattr(gateway, "home_all", rec)
    st.set_module(0, "X")                     # dirty the preview
    ok = asyncio.run(ctrl.home_all())
    assert ok is True
    assert rec.calls == []                    # nothing reaches the wall while simulating
    assert "".join(st.snapshot()["chars"]).strip() == ""   # preview blanked


def test_home_all_live_broadcasts_to_gateway(tmp_path, monkeypatch):
    ctrl, cfg, st = _controller(tmp_path)
    cfg.update({"transport": {"gateway_url": "http://gw"}})
    rec = _Recorder()
    monkeypatch.setattr(gateway, "home_all", rec)
    st.set_module(0, "X")
    ok = asyncio.run(ctrl.home_all())
    assert ok is True
    assert rec.calls == ["http://gw"]         # single broadcast to the configured gateway
    assert st.snapshot()["chars"][0] == " "


def test_home_all_no_gateway_raises(tmp_path):
    ctrl, cfg, st = _controller(tmp_path)     # default: not simulating, no gateway_url
    with pytest.raises(RuntimeError):
        asyncio.run(ctrl.home_all())


def test_home_endpoint_ok_shape(monkeypatch):
    from app import main

    async def fake_home():
        return True

    monkeypatch.setattr(main.controller, "home_all", fake_home)
    assert TestClient(main.app).post("/api/display/home").json() == {"ok": True, "error": None}


def test_home_endpoint_reports_failure_reason(monkeypatch):
    from app import main

    async def boom():
        raise RuntimeError("no gateway_url configured")

    monkeypatch.setattr(main.controller, "home_all", boom)
    body = TestClient(main.app).post("/api/display/home").json()
    assert body["ok"] is False and "gateway" in body["error"]
