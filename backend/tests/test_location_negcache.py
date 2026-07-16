"""location.py negative caching: a FAILED geocode / reverse-geocode is remembered
for a short TTL, so an un-geocodable ZIP or a Nominatim outage doesn't cost two
blocking 6 s calls on every app fetch, from every location app, against a
1-req/s-policy service.

No real network anywhere: location deliberately speaks `requests` (the layout-test
fixtures stub the net by patching requests.get — see the module docstring), so
these tests patch the same seam.
"""
import requests

from app import location


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _reset():
    location._coord_cache.clear()
    location._geo_cache.clear()
    location._neg_cache.clear()


def _count_gets(monkeypatch, response=None):
    """Patch requests.get to raise (default) or answer, counting calls."""
    calls = []

    def get(url, **kw):
        calls.append(url)
        if response is None:
            raise requests.exceptions.ConnectionError("nominatim is down")
        return _Resp(response)

    monkeypatch.setattr(requests, "get", get)
    return calls


# ---------------------------------------------------------------------------
# forward geocode (zip -> coordinates)
# ---------------------------------------------------------------------------
def test_a_nominatim_outage_is_not_retried_on_every_fetch(monkeypatch):
    _reset()
    calls = _count_gets(monkeypatch)
    s = {"zip_code": "02118"}
    assert location.coordinates(s) is None
    assert location.coordinates(s) is None
    assert len(calls) == 1, "the second lookup must be served by the negative cache"


def test_an_ungeocodable_zip_is_negative_cached_too(monkeypatch):
    _reset()
    calls = _count_gets(monkeypatch, response=[])   # Nominatim answers: no match
    s = {"zip_code": "00000"}
    assert location.coordinates(s) is None
    assert location.coordinates(s) is None
    assert len(calls) == 1


def test_a_failed_geocode_is_retried_after_the_ttl(monkeypatch):
    _reset()
    calls = _count_gets(monkeypatch)
    s = {"zip_code": "02118"}
    assert location.coordinates(s) is None
    # age the recorded failure past the TTL: the next call tries again
    location._neg_cache[("geocode", "02118")] -= location._NEG_TTL + 1
    assert location.coordinates(s) is None
    assert len(calls) == 2, "an expired negative entry must be retried"


def test_a_success_after_the_ttl_heals_and_caches_positively(monkeypatch):
    _reset()
    calls = _count_gets(monkeypatch)
    s = {"zip_code": "02118"}
    assert location.coordinates(s) is None
    location._neg_cache[("geocode", "02118")] -= location._NEG_TTL + 1

    good = [{"lat": "42.34", "lon": "-71.07", "address": {"city": "Boston"},
             "display_name": "Boston, MA"}]
    calls2 = _count_gets(monkeypatch, response=good)
    assert location.coordinates(s) == (42.34, -71.07, "BOSTON")
    assert location.coordinates(s) == (42.34, -71.07, "BOSTON")
    assert len(calls2) == 1, "the recovered answer must be positively cached"


def test_no_location_configured_is_not_a_failure(monkeypatch):
    """Blank settings mean 'nothing to look up' — no network call, and nothing
    to negative-cache."""
    _reset()
    calls = _count_gets(monkeypatch)
    assert location.coordinates({}) is None
    assert calls == [] and location._neg_cache == {}


# ---------------------------------------------------------------------------
# reverse geocode (coordinates -> country/subdivision)
# ---------------------------------------------------------------------------
def test_a_failed_reverse_geocode_is_negative_cached(monkeypatch):
    _reset()
    calls = _count_gets(monkeypatch)
    # precise coordinates: no forward geocode needed, only the reverse lookup
    s = {"location_lat": "42.35", "location_lon": "-71.06"}
    empty = {"country": None, "subdivision": None}
    assert location._geo(s) == empty
    assert location._geo(s) == empty
    assert len(calls) == 1, "the second reverse lookup must be served by the negative cache"


def test_a_countryless_reverse_answer_is_negative_cached(monkeypatch):
    _reset()
    calls = _count_gets(monkeypatch, response={"address": {}})   # 200, but nothing usable
    s = {"location_lat": "0.0", "location_lon": "0.0"}
    assert location._geo(s)["country"] is None
    assert location._geo(s)["country"] is None
    assert len(calls) == 1


def test_a_failed_reverse_geocode_is_retried_after_the_ttl(monkeypatch):
    _reset()
    calls = _count_gets(monkeypatch)
    s = {"location_lat": "42.35", "location_lon": "-71.06"}
    location._geo(s)
    location._neg_cache[("reverse", (42.35, -71.06))] -= location._NEG_TTL + 1
    location._geo(s)
    assert len(calls) == 2
