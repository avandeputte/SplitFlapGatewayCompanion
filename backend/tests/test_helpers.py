"""Tests for the app-data helper endpoints (pure parts)."""

from app import helpers


def test_timezones_query():
    out = helpers.timezones("tokyo")
    assert any(z["value"] == "Asia/Tokyo" for z in out["zones"])


def test_timezones_common_when_empty():
    out = helpers.timezones("")
    assert out["zones"]
    assert all("value" in z and "label" in z for z in out["zones"])


def test_sports_leagues_uses_settings():
    class FakeSettings:
        def get(self, k, d=None):
            return "NE" if k == "sports_nfl" else ""
    out = helpers.sports_leagues(FakeSettings())
    nfl = next(l for l in out["leagues"] if l["key"] == "nfl")
    assert nfl["followed"] == "NE" and nfl["follow_all"] is False
