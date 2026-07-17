"""Tests for the app-data helper endpoints (pure parts)."""

from app import helpers


def test_timezones_query():
    out = helpers.timezones("tokyo")
    assert any(z["value"] == "Asia/Tokyo" for z in out["zones"])


def test_timezones_common_when_empty():
    out = helpers.timezones("")
    assert out["zones"]
    assert all("value" in z and "label" in z for z in out["zones"])


def test_sports_search_lists_whole_leagues():
    """With no query, every league is offered as a whole-league follow, and the
    chip values are comma-free so they round-trip through the comma-joined list."""
    import asyncio
    out = asyncio.run(helpers.sports_search(""))
    vals = {r["value"] for r in out["results"]}
    assert any(v.startswith("nfl:*|") for v in vals)
    assert any(v.startswith("ger:*|") for v in vals)   # Bundesliga
    assert all("," not in v for v in vals)


def test_location_reverse_builds_a_place_chip(monkeypatch):
    """A GPS fix reverse-geocodes to a location_precise chip ('lat,lon|Name'), the same
    shape a picked search result has — so the browser's geolocation fills the field."""
    import asyncio

    async def fake(url, **kw):
        assert "reverse" in url
        return {"address": {"city": "Boston"}, "display_name": "Boston, MA, USA"}

    monkeypatch.setattr(helpers, "_get_json", fake)
    r = asyncio.run(helpers.location_reverse("42.3601", "-71.0589"))["result"]
    assert r["value"] == "42.3601,-71.0589|Boston"
    assert r["label"] == "Boston"


def test_location_reverse_falls_back_to_coordinates(monkeypatch):
    """If the name lookup fails, the raw coordinates stand in as the label — the fix
    still works, which is the whole point."""
    import asyncio

    async def boom(url, **kw):
        raise RuntimeError("offline")

    monkeypatch.setattr(helpers, "_get_json", boom)
    r = asyncio.run(helpers.location_reverse("42.3601", "-71.0589"))["result"]
    assert r["value"].startswith("42.3601,-71.0589|") and "42.3601" in r["label"]


def test_location_reverse_rejects_bad_input():
    import asyncio
    assert asyncio.run(helpers.location_reverse("", ""))["result"] is None
    assert asyncio.run(helpers.location_reverse("x", "y"))["result"] is None
