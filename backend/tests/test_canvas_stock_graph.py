"""Stock Graph canvas app — it paints a price line behind the quote, rotates through a watchlist of
symbols, and holds one refresh per poll while idling long when a lone symbol's market is shut.
yfinance is faked so these run offline. (Rendering fidelity is eyeballed off-device; here we pin
the data / rotation / cadence contract.)
"""

import importlib.util
import sys
import types
from pathlib import Path

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


def _fake_yf(data, tz="America/New_York"):
    """``data``: symbol -> (closes, last, prev). Faked yfinance module."""
    class _Hist:
        def __init__(self, closes):
            self._c = closes

        def __getitem__(self, k):
            assert k == "Close"
            return types.SimpleNamespace(tolist=lambda: list(self._c))

    class _Ticker:
        def __init__(self, sym):
            self._d = data[sym.upper()]

        def history(self, period, interval):
            return _Hist(self._d[0])

        @property
        def fast_info(self):
            _, last, prev = self._d
            return {"lastPrice": last, "previousClose": prev, "timezone": tz}

    mod = types.ModuleType("yfinance")
    mod.Ticker = _Ticker
    return mod


def _install(monkeypatch, data, tz="America/New_York"):
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(data, tz))


def test_draws_a_frame_and_holds_one_poll_while_trading(monkeypatch):
    m = _load()
    m.fetch_matrix.__dict__.pop("_state", None)
    _install(monkeypatch, {"^DJI": ([100, 101, 103, 102, 105], 105.0, 100.0)})
    monkeypatch.setattr(m, "_exchange_open", lambda tz, now: True)      # trading now
    cv = _Cap()
    hold = m.fetch_matrix({"graph_symbols": "^DJI", "graph_range": "1D", "polling_rate": 45}, cv)
    assert isinstance(cv.img, Image.Image)                 # a frame was painted
    assert cv.img.getbbox() is not None                    # and it isn't all black
    assert hold == 45.0                                    # one refresh per poll


def test_idles_long_when_a_single_market_is_shut(monkeypatch):
    m = _load()
    m.fetch_matrix.__dict__.pop("_state", None)
    _install(monkeypatch, {"^DJI": ([100, 101, 102], 102.0, 100.0)})
    monkeypatch.setattr(m, "_exchange_open", lambda tz, now: False)
    cv = _Cap()
    hold = m.fetch_matrix({"graph_symbols": "^DJI", "market_hours_only": "yes"}, cv)
    assert isinstance(cv.img, Image.Image)                 # still repaints the last-known graph
    assert hold == 900.0                                   # but won't refetch for a while


def test_rotates_through_a_watchlist_on_the_dwell(monkeypatch):
    """Several symbols cycle one per call, each cached on its own, and the hold is the rotation
    dwell — not the poll — so the board keeps moving."""
    m = _load()
    m.fetch_matrix.__dict__.pop("_state", None)
    _install(monkeypatch, {
        "^DJI": ([100, 105], 105.0, 100.0),
        "^GSPC": ([200, 198], 198.0, 200.0),
    })
    monkeypatch.setattr(m, "_exchange_open", lambda tz, now: True)
    hold1 = m.fetch_matrix({"graph_symbols": "^DJI, ^GSPC", "rotate_seconds": 7}, _Cap())
    st = m.fetch_matrix.__dict__["_state"]
    assert hold1 == 7.0                                    # dwell, not the poll
    assert set(st["data"]) == {"^DJI"}                     # only the first symbol fetched so far
    m.fetch_matrix({"graph_symbols": "^DJI, ^GSPC", "rotate_seconds": 7}, _Cap())
    assert set(st["data"]) == {"^DJI", "^GSPC"}            # the second call advanced and fetched the next


def test_no_data_shows_a_message_and_keeps_rotating(monkeypatch):
    m = _load()
    m.fetch_matrix.__dict__.pop("_state", None)
    _install(monkeypatch, {"BADSYM": ([], float("nan"), float("nan"))})   # empty history
    cv = _Cap()
    hold = m.fetch_matrix({"graph_symbols": "BADSYM"}, cv)
    assert isinstance(cv.img, Image.Image)
    assert hold == 60.0                                    # a short retry for a lone bad symbol


def test_percentage_is_measured_from_previous_close_intraday(monkeypatch):
    """A 1D chart reads against yesterday's close, not the first intraday bar — so an up day that
    opened down still shows green off the prior close."""
    m = _load()
    m.fetch_matrix.__dict__.pop("_state", None)
    # opens at 99 (below prev close 100), climbs to 106 -> +6% vs prev close, green.
    _install(monkeypatch, {"^DJI": ([99, 101, 104, 106], 106.0, 100.0)})
    monkeypatch.setattr(m, "_exchange_open", lambda tz, now: True)
    m.fetch_matrix({"graph_symbols": "^DJI", "graph_range": "1D"}, _Cap())
    cache = m.fetch_matrix.__dict__["_state"]["data"]["^DJI"]
    assert cache["prev"] == 100.0 and cache["last"] == 106.0
