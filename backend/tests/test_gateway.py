"""Tests for mapping the gateway's /api/config into a companion config patch."""

from app.gateway import build_sync_patch


GATEWAY_CONFIG = {
    "wSSID": "home",
    # Firmware 3.0 dropped MQTT from the gateway; a wall that still reported these
    # (an older one) must not have them synced either — the companion's HA broker is
    # local now, and the transport never used them.
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
    # Grid is synced (and step pacing when the gateway reports it — not here); nothing else is.
    assert patch == {"grid": {"rows": 3, "cols": 20}}
    # An MQTT broker the gateway happens to still report is NOT pulled in: the
    # companion's Home Assistant broker is local config, never the gateway's.
    assert "transport" not in patch


def test_build_sync_patch_maps_step_ms_to_transition_speed():
    """The gateway's default cascade pacing flows into the companion as the step_ms every
    page send inherits (display.transition_speed)."""
    patch = build_sync_patch({"gridRows": 3, "gridCols": 20, "stepMs": 25})
    assert patch["grid"] == {"rows": 3, "cols": 20}
    assert patch["display"] == {"transition_speed": 25}


def test_build_sync_patch_clamps_step_ms():
    assert build_sync_patch({"stepMs": 999})["display"] == {"transition_speed": 200}
    assert build_sync_patch({"stepMs": -5})["display"] == {"transition_speed": 0}


def test_build_sync_patch_without_step_ms_leaves_the_companion_default():
    # Older firmware reports no stepMs: no display patch, so the companion keeps its own default.
    assert "display" not in build_sync_patch({"gridRows": 3})


def test_build_sync_patch_ignores_missing_and_bad_types():
    patch = build_sync_patch({"gridRows": 2, "mqHost": "mq", "mqPort": "nope"})
    assert patch == {"grid": {"rows": 2}}  # MQTT ignored, bad-typed grid dropped


def test_build_sync_patch_clamps_grid_min():
    patch = build_sync_patch({"gridRows": 0, "gridCols": 16})
    assert patch["grid"] == {"rows": 1, "cols": 16}


def test_build_sync_patch_empty():
    assert build_sync_patch({}) == {}
    assert build_sync_patch({"unrelated": 5}) == {}
