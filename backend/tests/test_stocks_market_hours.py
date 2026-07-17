"""Stocks pauses polling when every followed market is shut.

yfinance's fast_info carries no market-open flag, but it does carry the exchange's
timezone — so the app computes a generous 'might be trading' window (weekday, roughly
04:00-20:00 local) and, when every followed market is outside it, re-shows the last
good pages instead of hitting the network again.
"""
import sys
import types
from datetime import datetime, timezone

from conftest import load_app as _mod


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_exchange_open_reads_the_exchanges_own_clock():
    ex = _mod("stocks")._exchange_open
    ny = "America/New_York"                       # ET is UTC-4 in July
    assert ex(ny, _utc(2026, 7, 15, 17))          # Wed 13:00 ET -> open
    assert not ex(ny, _utc(2026, 7, 15, 6))       # Wed 02:00 ET -> overnight, closed
    assert not ex(ny, _utc(2026, 7, 18, 17))      # Saturday -> closed
    # A London stock is on London's clock, not New York's.
    assert ex("Europe/London", _utc(2026, 7, 15, 10))    # Wed 11:00 BST -> open
    # An unknown timezone counts as open: never skip a refresh on a guess.
    assert ex("Not/AZone", _utc(2026, 7, 18, 3))


class _Ticker:
    calls = 0

    def __init__(self, sym):
        self.sym = sym

    @property
    def fast_info(self):
        _Ticker.calls += 1
        return {"lastPrice": 100.0, "previousClose": 99.0,
                "currency": "USD", "timezone": "America/New_York"}


def _fresh_app(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_Ticker))
    app = _mod("stocks")
    if hasattr(app.fetch, "_state"):
        del app.fetch._state
    _Ticker.calls = 0
    return app


def _fmt(*lines):
    return "|".join(lines)


def test_a_closed_market_serves_the_cache_without_calling_yfinance(monkeypatch):
    app = _fresh_app(monkeypatch)
    monkeypatch.setattr(app, "_exchange_open", lambda tz, now: False)   # force 'all shut'
    s = {"stocks_list": "AAPL,MSFT", "market_hours_only": "yes"}
    p1 = app.fetch(s, _fmt, lambda: 5, lambda: 15)
    first = _Ticker.calls
    assert first > 0, "the first render must actually fetch"
    p2 = app.fetch(s, _fmt, lambda: 5, lambda: 15)
    assert _Ticker.calls == first, "a closed market must not hit yfinance again"
    assert p2 == p1


def test_an_open_market_keeps_refreshing(monkeypatch):
    app = _fresh_app(monkeypatch)
    monkeypatch.setattr(app, "_exchange_open", lambda tz, now: True)    # force 'open'
    s = {"stocks_list": "AAPL", "market_hours_only": "yes"}
    app.fetch(s, _fmt, lambda: 5, lambda: 15)
    first = _Ticker.calls
    app.fetch(s, _fmt, lambda: 5, lambda: 15)
    assert _Ticker.calls > first, "an open market must refetch"


def test_the_setting_off_always_refreshes(monkeypatch):
    app = _fresh_app(monkeypatch)
    monkeypatch.setattr(app, "_exchange_open", lambda tz, now: False)   # even 'all shut'
    s = {"stocks_list": "AAPL", "market_hours_only": "no"}
    app.fetch(s, _fmt, lambda: 5, lambda: 15)
    first = _Ticker.calls
    app.fetch(s, _fmt, lambda: 5, lambda: 15)
    assert _Ticker.calls > first, "with the pause off, it must refetch regardless"
