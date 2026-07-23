"""Planes Overhead: route (from→to) from keyed providers, the field picker, and the
adaptive layout (drop a field to fit one line; if it must wrap, still pack aircraft)."""
import sys
import time
import types

from conftest import load_app as _mod


class _Resp:
    def __init__(self, d):
        self._d = d

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def _plane(cs, lat, lon, dep, arr):
    return {"lat": lat, "lng": lon, "alt": 10000, "speed": 800, "dir": 200,
            "updated": int(time.time()), "flight_iata": cs,
            "dep_iata": dep, "arr_iata": arr, "status": "en-route"}


PLANES = [_plane("UAL245", 42.40, -71.10, "PIT", "SFO"),
          _plane("DAL89", 42.30, -71.00, "ATL", "BOS")]


def _run(monkeypatch, cols, rows, settings):
    def get(url, **kw):
        return _Resp({"response": PLANES})

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(
        get=get, post=lambda *a, **k: _Resp({}),
        Timeout=Exception, ConnectionError=Exception, HTTPError=Exception))
    app = _mod("planes_overhead")
    if hasattr(app.fetch, "_state"):
        del app.fetch._state
    base = {"data_source": "airlabs", "airlabs_api_key": "k", "radius": "200",
            "max_results": "6", "dwell_seconds": "4", "location": "42.3601,-71.0589"}
    base.update(settings)
    pages, seen = app.fetch(base, lambda *a: "|".join(a), lambda: rows, lambda: cols, None), []
    for pg in pages:
        if pg not in seen:
            seen.append(pg)
    return [ln for pg in seen for ln in pg.split("|") if ln.strip()]


def test_route_shows_origin_to_destination(monkeypatch):
    text = " ".join(_run(monkeypatch, 42, 6, {}))
    assert "PIT→SFO" in text and "ATL→BOS" in text


def test_a_field_can_be_turned_off(monkeypatch):
    assert "KT" in " ".join(_run(monkeypatch, 42, 6, {}))                 # speed on by default
    assert "KT" not in " ".join(_run(monkeypatch, 42, 6, {"show_speed": "no"}))


def test_drops_a_field_to_fit_one_line_keeping_the_route(monkeypatch):
    lines = _run(monkeypatch, 30, 6, {})
    assert len(lines) == 2                              # one line per aircraft, both on a page
    assert all("→" in l and len(l) <= 30 for l in lines)   # route kept; nothing wrapped


def test_packs_multiple_aircraft_even_when_wrapped(monkeypatch):
    lines = _run(monkeypatch, 14, 6, {})
    # two aircraft × two lines each = four lines on ONE page, not one aircraft per page
    assert len(lines) == 4


def test_rows_stay_the_same_width_so_the_columns_align(monkeypatch):
    """Regression: a right-stripped short row was re-centered a column over, drifting the
    columns down the page. With mixed-width last columns (altitude A4050 vs A38K) every
    aircraft line must still be the SAME width."""
    def alt_plane(cs, lat, lon, alt_m):
        return {"lat": lat, "lng": lon, "alt": alt_m, "speed": 800, "dir": 200,
                "updated": int(time.time()), "flight_iata": cs, "status": "en-route"}

    def get(url, **kw):
        return _Resp({"response": [alt_plane("UAL1", 42.40, -71.10, 1234),    # ~A4050 ft
                                   alt_plane("DAL2", 42.30, -71.00, 11582)]})  # ~A38K ft

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(
        get=get, post=lambda *a, **k: _Resp({}),
        Timeout=Exception, ConnectionError=Exception, HTTPError=Exception))
    app = _mod("planes_overhead")
    if hasattr(app.fetch, "_state"):
        del app.fetch._state
    s = {"data_source": "airlabs", "airlabs_api_key": "k", "units_preset": "imperial",
         "radius": "200", "max_results": "6", "dwell_seconds": "4",
         "location": "42.3601,-71.0589", "show_route": "no", "show_speed": "no"}
    pages = app.fetch(s, lambda *a: "|".join(a), lambda: 6, lambda: 30, None)
    lines = [ln for ln in pages[0].split("|") if ln.strip()]
    assert len(lines) == 2
    assert len({len(l) for l in lines}) == 1, f"rows differ in width: {lines}"
