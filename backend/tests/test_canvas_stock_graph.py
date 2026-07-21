"""Stock Graph canvas app — it paints a price line behind the quote and holds one refresh per
poll, idling long while the market's shut. yfinance is faked so these run offline and don't hit
the network. (Rendering fidelity is eyeballed off-device; here we pin the data/cadence contract.)
"""

import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageFont

from app.canvas import _FONT_DIR

ROOT = Path(__file__).resolve().parents[2]


def _load():
    p = ROOT / "apps" / "canvas-stock-graph" / "app.py"
    spec = importlib.util.spec_from_file_location("_stockgraph", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Cap:
    """A canvas that renders for real (so _fit/font/blank behave) and captures the pushed frame."""
    width, height = 256, 64

    def __init__(self):
        self.img = None

    def blank(self, color=(0, 0, 0)):
        return Image.new("RGB", (self.width, self.height), tuple(color))

    def font(self, size, name="DejaVuSans-Bold.ttf"):
        import os
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), max(5, int(size)))

    def frame(self, image):
        self.img = image if isinstance(image, Image.Image) else True
        return True


def _fake_yf(closes, last, prev, tz="America/New_York"):
    class _Hist:
        def __getitem__(self, k):
            assert k == "Close"
            return types.SimpleNamespace(tolist=lambda: list(closes))

    class _Ticker:
        def __init__(self, sym):
            pass

        def history(self, period, interval):
            return _Hist()

        @property
        def fast_info(self):
            return {"lastPrice": last, "previousClose": prev, "timezone": tz}

    mod = types.ModuleType("yfinance")
    mod.Ticker = _Ticker
    return mod


def _install(monkeypatch, closes, last, prev, tz="America/New_York"):
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(closes, last, prev, tz))


def test_draws_a_frame_and_holds_one_poll_while_trading(monkeypatch):
    m = _load()
    m.fetch.__dict__.pop("_state", None)
    _install(monkeypatch, [100, 101, 103, 102, 105], last=105.0, prev=100.0)
    # A timezone that is trading right now, so the app polls rather than idling.
    monkeypatch.setattr(m, "_exchange_open", lambda tz, now: True)
    cv = _Cap()
    hold = m.fetch({"graph_symbol": "^DJI", "graph_range": "1D", "polling_rate": 45},
                   None, None, None, canvas=cv)
    assert isinstance(cv.img, Image.Image)                 # a frame was painted
    assert cv.img.getbbox() is not None                    # and it isn't all black
    assert hold == 45.0                                    # one refresh per poll


def test_idles_long_when_the_market_is_shut(monkeypatch):
    m = _load()
    m.fetch.__dict__.pop("_state", None)
    _install(monkeypatch, [100, 101, 102], last=102.0, prev=100.0)
    monkeypatch.setattr(m, "_exchange_open", lambda tz, now: False)
    cv = _Cap()
    hold = m.fetch({"graph_symbol": "^DJI", "market_hours_only": "yes"},
                   None, None, None, canvas=cv)
    assert isinstance(cv.img, Image.Image)                 # still repaints the last-known graph
    assert hold == 900.0                                   # but won't refetch for a while


def test_no_data_shows_a_message_and_retries_soon(monkeypatch):
    m = _load()
    m.fetch.__dict__.pop("_state", None)
    _install(monkeypatch, [], last=float("nan"), prev=float("nan"))   # empty history
    cv = _Cap()
    hold = m.fetch({"graph_symbol": "BADSYM"}, None, None, None, canvas=cv)
    assert isinstance(cv.img, Image.Image)
    assert hold == 60.0                                    # a short retry, not the full poll


def test_percentage_is_measured_from_previous_close_intraday(monkeypatch):
    """A 1D chart reads against yesterday's close, not the first intraday bar — so an up day that
    opened down still shows green off the prior close."""
    m = _load()
    m.fetch.__dict__.pop("_state", None)
    # opens at 99 (below prev close 100), climbs to 106 -> +6% vs prev close, green.
    _install(monkeypatch, [99, 101, 104, 106], last=106.0, prev=100.0)
    monkeypatch.setattr(m, "_exchange_open", lambda tz, now: True)
    cv = _Cap()
    m.fetch({"graph_symbol": "^DJI", "graph_range": "1D"}, None, None, None, canvas=cv)
    st = m.fetch.__dict__["_state"]
    assert st["prev"] == 100.0 and st["last"] == 106.0
