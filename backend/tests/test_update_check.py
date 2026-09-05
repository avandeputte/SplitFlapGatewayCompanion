"""The optional 'check for a newer release' feature (update_check.py) + /api/update-check.

Off by default; when the companion-wide setting is on it compares this build against GitHub's
latest release and the SPA shows a banner. The GitHub answer is cached and served stale on
error, and no network is ever touched in these tests (the fetch is stubbed).
"""
import pytest
from fastapi.testclient import TestClient

from app import update_check


@pytest.fixture(autouse=True)
def _reset_cache():
    update_check._cache.update(at=0.0, latest="", url="")
    yield
    update_check._cache.update(at=0.0, latest="", url="")


def test_vtuple_parses_and_drops_the_prerelease_suffix():
    assert update_check._vtuple("v2.10.15") == (2, 10, 15)
    assert update_check._vtuple("2.10.15-beta.1") == (2, 10, 15)   # a beta compares as its base
    assert update_check._vtuple("2.11") == (2, 11)
    assert update_check._vtuple("nonsense") == (0,)


def test_status_reports_an_update(monkeypatch):
    monkeypatch.setattr(update_check, "_fetch_latest",
                        lambda: ("v9.9.9", "https://example/releases/tag/v9.9.9"))
    st = update_check.status(force=True)
    assert st["update_available"] is True
    assert st["latest"] == "9.9.9" and st["current"] == update_check.current_version()
    assert st["url"].endswith("v9.9.9")


def test_status_is_up_to_date_when_latest_equals_this_build(monkeypatch):
    monkeypatch.setattr(update_check, "_fetch_latest",
                        lambda: (f"v{update_check.current_version()}", "https://x"))
    assert update_check.status(force=True)["update_available"] is False


def test_status_serves_the_last_good_value_on_error(monkeypatch):
    monkeypatch.setattr(update_check, "_fetch_latest", lambda: ("v9.9.9", "https://x"))
    update_check.status(force=True)                       # warm the cache

    def boom():
        raise RuntimeError("github unreachable")

    monkeypatch.setattr(update_check, "_fetch_latest", boom)
    st = update_check.status(force=True)                  # error -> keep the warm result
    assert st["latest"] == "9.9.9" and st["update_available"] is True


def test_route_off_by_default_then_toggles_and_reports(monkeypatch):
    from app import main
    monkeypatch.setattr(update_check, "_fetch_latest",
                        lambda: ("v9.9.9", "https://example/releases/tag/v9.9.9"))
    main.plugins.settings.set("check_for_updates", False)
    client = TestClient(main.app)
    try:
        assert client.get("/api/update-check").json() == {"enabled": False}
        r = client.post("/api/update-check", json={"on": True}).json()
        assert r["enabled"] is True and r["update_available"] is True and r["latest"] == "9.9.9"
        assert main.plugins.settings.get("check_for_updates") is True
        g = client.get("/api/update-check").json()
        assert g["enabled"] is True and g["update_available"] is True and g["url"].endswith("v9.9.9")
        assert client.post("/api/update-check", json={"on": False}).json() == {"enabled": False}
        assert main.plugins.settings.get("check_for_updates") is False
    finally:
        main.plugins.settings.set("check_for_updates", False)
