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
import importlib.util
import sys
import tempfile
import types
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2] / "apps"


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"_{name}", APPS / name / "app.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _runtime(rows, cols, app_id, **settings):
    from app.config import Config
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime

    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp)
    cfg.update({"grid": {"rows": rows, "cols": cols}})
    st = PluginSettings(cfg.data_dir)
    st.set("installed_apps", [app_id])
    for k, v in settings.items():
        st.set(k, v)
    rt = PluginRuntime(cfg, st, APPS, cfg.data_dir / "apps")
    rt.load()
    return rt


def _lines(page, rows, cols):
    return [page[r * cols:(r + 1) * cols] for r in range(rows)]


def _body(page, rows, cols):
    return [l for l in _lines(page, rows, cols) if l.strip()]


# ---------------------------------------------------------------------------
# the shared trick: a full-width line survives centring untouched
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("app", ["world_clock", "stocks", "tides"])
def test_row_pins_the_left_and_right_edges(app):
    row = _mod(app)._row
    line = row("AAPL", "$231.40", 15)
    assert len(line) == 15, "a line that is not exactly cols wide gets re-centred"
    assert line.startswith("AAPL")
    assert line.endswith("$231.40")


@pytest.mark.parametrize("app", ["world_clock", "stocks", "tides"])
def test_row_trims_the_label_never_the_number(app):
    """The number is the thing you are reading. A long city name gives way; the time does
    not get its digits cut off."""
    row = _mod(app)._row
    line = row("SOME VERY LONG CITY NAME", "11:45PM", 15)
    assert len(line) == 15
    assert line.endswith("11:45PM")


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
ROCKET = {"results": [{"name": "FALCON 9 | STARLINK 12-5", "net": "2026-07-16T14:21:00Z"}]}


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


@pytest.fixture
def stub_net(monkeypatch):
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
    assert body[0].startswith("SUNRISE") and body[0].endswith("5:31AM")


# ---------------------------------------------------------------------------
# tides — the day's tides are a list, so they go on one page
# ---------------------------------------------------------------------------
def test_the_days_tides_fit_on_one_page(stub_net):
    rt = _runtime(5, 15, "tides")
    pages = rt.get_pages("tides")
    assert len(pages) == 1, "still paging through the tides one at a time"

    body = _body(pages[0], 5, 15)
    assert body[0].strip() == "TIDES"
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
    assert "NEXT LAUNCH" in text
    assert "FALCON 9" in text
    assert "STARLINK" in text
    assert "IN " in text                        # the countdown


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
