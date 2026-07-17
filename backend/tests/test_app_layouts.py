"""Columns, and using the wall you paid for.

Two things here:

  * **Justification.** format_lines centres each line horizontally, so a line that is
    ALREADY `cols` wide passes through untouched. That is the seam these apps use to pin a
    label flush left and its number flush right — which is what makes a list of stocks, or
    clocks, or tides readable: you read DOWN the numbers, and they line up.
  * **A tall wall is not a short wall with gaps.** rocket-launch split one sentence across
    two pages; tides put each of the day's four tides on a page of its own; art-clock drew
    a 3-row clock and returned it RAW, so on anything that was not exactly 3x15 it sat in
    the top-left corner.
"""
import sys
import types

import pytest

from conftest import APPS_DIR as APPS
from conftest import Resp as _Resp
from conftest import load_app as _mod
from conftest import make_runtime


def _runtime(rows, cols, app_id, **settings):
    return make_runtime(installed=[app_id], rows=rows, cols=cols, settings=settings)


def _lines(page, rows, cols):
    return [page[r * cols:(r + 1) * cols] for r in range(rows)]


def _body(page, rows, cols):
    return [l for l in _lines(page, rows, cols) if l.strip()]


# ---------------------------------------------------------------------------
# the shared trick: a full-width line survives centring untouched
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("app", ["weather"])
def test_row_pins_the_left_and_right_edges(app):
    row = _mod(app)._row
    line = row("AAPL", "$231.40", 15)
    assert len(line) == 15, "a line that is not exactly cols wide gets re-centred"
    assert line.startswith("AAPL")
    assert line.endswith("$231.40")


@pytest.mark.parametrize("app", ["weather"])
def test_row_trims_the_label_never_the_number(app):
    """The number is the thing you are reading. A long city name gives way; the time does
    not get its digits cut off."""
    row = _mod(app)._row
    line = row("SOME VERY LONG CITY NAME", "11:45PM", 15)
    assert len(line) == 15
    assert line.endswith("11:45PM")


@pytest.mark.parametrize("app", ["world_clock", "stocks", "tides", "sun-times", "metro"])
def test_columns_keep_the_pair_together_on_a_wide_wall(app):
    """The apps that lay two columns with _columns: on a WIDE wall the block stays
    only as wide as its content (+ a gap) so format_lines centres the pair, instead
    of stranding the label and the value at opposite edges. The value column still
    lines up down the page, and a narrow wall trims the label, never the value."""
    columns = _mod(app)._columns
    out = columns([("AAPL", "$231.40"), ("MSFT", "$1420.55")], 40)
    assert all(len(ln) < 40 for ln in out), "the pair must not spread to the full width"
    assert len({len(ln) for ln in out}) == 1, "every line the same width => values align"
    assert out[0].startswith("AAPL") and out[0].rstrip().endswith("$231.40")
    narrow = columns([("SOME VERY LONG CITY", "11:45PM")], 15)
    assert len(narrow[0]) == 15 and narrow[0].endswith("11:45PM")


# ---------------------------------------------------------------------------
# stubbed providers — layout tests must not need the internet
# ---------------------------------------------------------------------------
SUN = {"daily": {"sunrise": ["2026-07-14T05:31"], "sunset": ["2026-07-14T21:47"],
                 "daylight_duration": [58560]}}
TIDES = {"predictions": [
    {"t": "2026-07-14 03:12", "type": "L", "v": "0.4"},
    {"t": "2026-07-14 09:28", "type": "H", "v": "11.2"},
    {"t": "2026-07-14 15:48", "type": "L", "v": "0.9"},
    {"t": "2026-07-14 21:57", "type": "H", "v": "10.8"},
]}
# The launch must stay in the FUTURE relative to every run — a fixed date here
# was a time bomb that started failing the day it quietly became the past.
from datetime import datetime as _dt, timedelta as _td, timezone as _tz
ROCKET = {"results": [{"name": "FALCON 9 | STARLINK 12-5",
                       "net": (_dt.now(_tz.utc) + _td(days=2, hours=3)).strftime("%Y-%m-%dT%H:%M:00Z")}]}


@pytest.fixture
def stub_net(monkeypatch):
    # NOTE: this stubs the REQUESTS layer (the apps call requests.get themselves);
    # conftest.stub_http is for the weather helper's httpx client — different seam.
    import requests

    def fake_get(url, **kw):
        if "tidesandcurrents" in url:
            return _Resp(TIDES)
        if "thespacedevs" in url:
            return _Resp(ROCKET)
        if "open-meteo" in url:
            return _Resp(SUN)
        if "nominatim" in url:
            return _Resp([{"lat": "50.85", "lon": "4.35"}])
        raise AssertionError(f"unstubbed: {url}")

    monkeypatch.setattr(requests, "get", fake_get)


@pytest.fixture
def stub_yf(monkeypatch):
    class Ticker:
        P = {"AAPL": (231.4, 228.1), "MSFT": (1420.55, 1450.2)}

        def __init__(self, sym):
            self.sym = sym

        @property
        def fast_info(self):
            p, prev = self.P.get(self.sym, (10.0, 10.0))
            return {"lastPrice": p, "previousClose": prev, "currency": "USD"}

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))


# ---------------------------------------------------------------------------
# stocks — ticker left, price right
# ---------------------------------------------------------------------------
def test_stock_prices_line_up_in_a_column(stub_yf):
    rt = _runtime(5, 15, "stocks", plugin_stocks_stocks_list="AAPL,MSFT")
    body = _body(rt.get_pages("stocks")[0], 5, 15)
    assert body[0].startswith("AAPL") and body[0].rstrip().endswith("231.40")
    assert body[1].startswith("MSFT")
    # the prices end at the same column — that is the whole point
    assert len(body[0].rstrip()) == len(body[1].rstrip()) == 15


def test_stocks_combine_price_and_change_on_one_page_when_wide(stub_yf):
    """On an ultra-wide Matrix panel the ticker, its price AND the day's change fit
    on one line, so the watchlist is a SINGLE page — not a price page and then a
    change page. A narrow wall, where all three won't fit, keeps the two-page split."""
    wide = _runtime(6, 42, "stocks", plugin_stocks_stocks_list="AAPL,MSFT")
    pages = wide.get_pages("stocks")
    assert len(pages) == 1                                  # combined onto one page
    body = _body(pages[0], 6, 42)
    assert len(body) == 2
    for l in body:
        assert "$" in l and l.rstrip().endswith("%")        # price AND change on the line
    narrow = _runtime(6, 15, "stocks", plugin_stocks_stocks_list="AAPL,MSFT")
    assert len(narrow.get_pages("stocks")) == 2             # split-flap keeps two pages


def test_stocks_three_columns_align_price_and_change():
    """ticker flush left, price and change each flush right — every line the same
    width, so the price column and the change column both line up down the page."""
    cols3 = _mod("stocks")._columns3
    out = cols3([("AAPL", "$231.40", "+1.4%"), ("MSFT", "$1,420.55", "-2.0%")], 42)
    assert len(out) == 2 and len({len(l) for l in out}) == 1
    assert out[0].startswith("AAPL") and out[1].startswith("MSFT")
    # the price column is right-aligned: both prices END at the same column
    assert out[0].index("$231.40") + len("$231.40") == \
           out[1].index("$1,420.55") + len("$1,420.55")
    assert out[0].rstrip().endswith("+1.4%") and out[1].rstrip().endswith("-2.0%")


# ---------------------------------------------------------------------------
# crypto — the stocks treatment: a coin, its price and change on one wide line
# ---------------------------------------------------------------------------
@pytest.fixture
def stub_coingecko(monkeypatch):
    import requests

    def fake_get(url, **kw):
        if "coingecko" in url:
            return _Resp({"bitcoin": {"usd": 67000.0, "usd_24h_change": 2.34},
                          "ethereum": {"usd": 3450.55, "usd_24h_change": -1.12}})
        if "nominatim" in url:                  # no location -> currency falls back to USD
            return _Resp([])
        raise AssertionError(f"unstubbed: {url}")

    monkeypatch.setattr(requests, "get", fake_get)


def test_crypto_combines_price_and_change_on_one_page_when_wide(stub_coingecko):
    """On an ultra-wide Matrix panel each coin is one line — ticker, price AND the
    day's change together — so the watchlist is a page of one-liners instead of the
    name/price/change stacked over three rows. A narrow wall keeps the stack."""
    wide = _runtime(6, 42, "crypto", plugin_crypto_crypto_list="bitcoin,ethereum")
    pages = wide.get_pages("crypto")
    assert len(pages) == 1                              # one page of one-liners
    body = _body(pages[0], 6, 42)
    assert len(body) == 2
    for l in body:
        assert "$" in l and l.rstrip().endswith("%")   # price AND change on the line
    narrow = _runtime(5, 15, "crypto", plugin_crypto_crypto_list="bitcoin,ethereum")
    assert len(narrow.get_pages("crypto")) == 2         # stacked: one coin per page


def test_crypto_three_columns_align_price_and_change():
    cols3 = _mod("crypto")._columns3
    out = cols3([("BTC", "$67,000", "+2.3%"), ("ETH", "$3,451", "-1.1%")], 42)
    assert len(out) == 2 and len({len(l) for l in out}) == 1
    assert out[0].startswith("BTC") and out[1].startswith("ETH")
    assert out[0].index("$67,000") + len("$67,000") == \
           out[1].index("$3,451") + len("$3,451")
    assert out[0].rstrip().endswith("+2.3%") and out[1].rstrip().endswith("-1.1%")


# ---------------------------------------------------------------------------
# world clock — city left, time right
# ---------------------------------------------------------------------------
def test_clock_times_line_up_in_a_column():
    rt = _runtime(5, 15, "world_clock",
                  plugin_world_clock_world_clock_zones="US/Eastern,Europe/Paris,Asia/Tokyo")
    body = _body(rt.get_pages("world_clock")[0], 5, 15)
    assert len(body) == 3
    for l in body:
        assert len(l) == 15, "the line was re-centred, so the times will not line up"
        assert l[0] != " ", "the city must be flush left"
        assert l[-1] != " ", "the time must be flush right"
    # The app no longer uppercases — the wall does, if it needs to.
    assert body[0].startswith("Eastern") and body[1].startswith("Paris")


# ---------------------------------------------------------------------------
# sun times — label left, time right
# ---------------------------------------------------------------------------
def test_sun_times_line_up_in_a_column(stub_net):
    rt = _runtime(3, 15, "sun-times")
    body = _body(rt.get_pages("sun-times")[0], 3, 15)
    assert len(body) == 3
    for l in body:
        assert len(l) == 15 and l[0] != " " and l[-1] != " "
    assert body[0].startswith("Sunrise") and body[0].endswith("5:31AM")


# ---------------------------------------------------------------------------
# tides — the day's tides are a list, so they go on one page
# ---------------------------------------------------------------------------
def test_the_days_tides_fit_on_one_page(stub_net):
    rt = _runtime(5, 15, "tides")
    pages = rt.get_pages("tides")
    assert len(pages) == 1, "still paging through the tides one at a time"

    body = _body(pages[0], 5, 15)
    assert body[0].strip() == "Tides"
    assert len(body) == 5                       # header + four tides
    for l in body[1:]:
        assert l.endswith("FT"), "the height must be flush right, in a column"


def test_tides_still_page_on_a_three_row_wall(stub_net):
    rt = _runtime(3, 15, "tides")
    assert len(rt.get_pages("tides")) == 4


# ---------------------------------------------------------------------------
# rocket launch — one sentence, one page
# ---------------------------------------------------------------------------
def test_the_launch_fits_on_one_page(stub_net):
    """Splitting the rocket from its mission was a three-row compromise. On a taller wall
    it just means waiting for a page turn to read the other half of one sentence."""
    rt = _runtime(5, 15, "rocket-launch")
    pages = rt.get_pages("rocket-launch")
    assert len(pages) == 1

    text = " ".join(_body(pages[0], 5, 15))
    assert "Next launch" in text
    assert "FALCON 9" in text
    assert "STARLINK" in text
    assert "In " in text                        # the countdown


def test_rocket_launch_still_pages_on_a_three_row_wall(stub_net):
    rt = _runtime(3, 15, "rocket-launch")
    assert len(rt.get_pages("rocket-launch")) == 2


# ---------------------------------------------------------------------------
# art clock — a clock, on the wall it is actually on
# ---------------------------------------------------------------------------
def test_the_art_clock_uses_all_five_rows():
    rt = _runtime(5, 15, "art-clock")
    lines = _lines(rt.get_pages("art-clock")[0], 5, 15)
    assert all(l.strip() for l in lines), "a 3-row clock on a 5-row wall wastes 40% of it"


def test_the_art_clock_is_centred_rather_than_stranded():
    """It used to return a RAW 3x15 page, so on any other geometry it sat in the top-left
    corner. Going through format_lines centres it — horizontally and vertically."""
    src = (APPS / "art-clock" / "app.py").read_text("utf-8")
    assert "return [format_lines(*lines)]" in src
    assert "return [raw]" not in src

    rt = _runtime(3, 22, "art-clock")           # a wall that is NOT 15 wide
    lines = _lines(rt.get_pages("art-clock")[0], 3, 22)
    for l in lines:
        assert l.startswith(" ") and l.endswith(" "), "not centred on a wider wall"
