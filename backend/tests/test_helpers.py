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
