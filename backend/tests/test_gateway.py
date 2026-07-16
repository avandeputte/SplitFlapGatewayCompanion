"""Tests for mapping the gateway's /api/config into a companion config patch."""

from app.gateway import build_sync_patch


GATEWAY_CONFIG = {
    "wSSID": "home",
    "mqHost": "192.168.1.50",
    "mqPort": 1883,
    "mqUser": "flap",
    "mqPfx": "splitflap",
    "gridRows": 3,
    "gridCols": 20,
    "baud": 9600,
    "otaPasswordSet": True,
}


def test_build_sync_patch_full():
    patch = build_sync_patch(GATEWAY_CONFIG)
    assert patch["grid"] == {"rows": 3, "cols": 20}
    # (mqPfx no longer rides along: transport.mqtt.prefix was written by three
    # sources and read by nothing — the key is gone.)
    assert patch["transport"]["mqtt"] == {
        "broker": "192.168.1.50",
        "port": 1883,
        "username": "flap",
    }
    # Never syncs a password / transport type / gateway url.
    assert "password" not in patch["transport"]["mqtt"]
    assert "type" not in patch.get("transport", {})


def test_build_sync_patch_ignores_missing_and_bad_types():
    patch = build_sync_patch({"gridRows": 2, "mqHost": "", "mqPort": "nope"})
    assert patch == {"grid": {"rows": 2}}  # empty broker + bad port dropped


def test_build_sync_patch_clamps_grid_min():
    patch = build_sync_patch({"gridRows": 0, "gridCols": 16})
    assert patch["grid"] == {"rows": 1, "cols": 16}


def test_build_sync_patch_empty():
    assert build_sync_patch({}) == {}
    assert build_sync_patch({"unrelated": 5}) == {}
