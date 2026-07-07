"""
Plugin conformance tests — the guardrail for drop-in compatibility.

Every app under ../../apps must load and satisfy the splitflap-os contract:
a valid manifest, and either a functional app.py exposing fetch() or a channel
data.json. If this breaks, a vendored (or dropped-in) app would fail to run.
"""

from pathlib import Path

import pytest

from app.config import Config
from app.plugin_settings import PluginSettings
from app.plugins import PluginRuntime

APPS_DIR = Path(__file__).resolve().parents[2] / "apps"


def _runtime(tmp_path, installed):
    cfg = Config(data_dir=tmp_path)
    ps = PluginSettings(tmp_path)
    ps.set_installed(installed)
    rt = PluginRuntime(cfg, ps, APPS_DIR)
    rt.load()
    return rt


def test_apps_directory_populated():
    rt_ids = PluginRuntime(Config(data_dir=Path("/tmp")), PluginSettings(Path("/tmp")), APPS_DIR).discover()
    assert len(rt_ids) >= 40, f"expected the vendored app library, found {len(rt_ids)}"


def test_every_app_loads(tmp_path):
    """Install ALL discovered apps and assert each one loads cleanly."""
    rt = _runtime(tmp_path, [])  # tmp for discover only
    all_ids = rt.discover()
    rt = _runtime(tmp_path, all_ids)

    failures = []
    for app_id in all_ids:
        manifest = rt.manifest(app_id)
        if manifest is None:
            failures.append(f"{app_id}: manifest missing/failed")
            continue
        if not manifest.get("name"):
            failures.append(f"{app_id}: manifest has no name")
        kind = manifest.get("type")
        if kind == "functional":
            if app_id not in rt._modules:
                failures.append(f"{app_id}: functional but fetch() not loaded")
        elif kind == "channel":
            if app_id not in rt._channel:
                failures.append(f"{app_id}: channel but no data pages")
        else:
            failures.append(f"{app_id}: unknown type {kind!r}")
    assert not failures, "app load failures:\n" + "\n".join(failures)


def test_format_lines_dimensions(tmp_path):
    rt = _runtime(tmp_path, [])
    # 3x15 default grid -> 45 chars, each line centered in 15.
    out = rt.format_lines("HI", "THERE")
    assert len(out) == 45
    assert out[:15] == "HI".center(15)
    assert out[15:30] == "THERE".center(15)


def test_channel_app_returns_pages(tmp_path):
    rt = _runtime(tmp_path, ["dad-jokes"])
    pages = rt.get_pages("dad-jokes")
    assert isinstance(pages, list) and pages
    assert all(isinstance(p, str) for p in pages)
    assert all(len(p) == rt.get_rows() * rt.get_cols() for p in pages)


def test_local_functional_app_runs(tmp_path):
    """`date` renders from the clock only — no network — so it must produce a page."""
    rt = _runtime(tmp_path, ["date"])
    pages = rt.get_pages("date")
    assert isinstance(pages, list) and pages
    assert all(isinstance(p, str) for p in pages)


def test_settings_schema_resolves_keys(tmp_path):
    # weather mixes per-app keys (temperature_unit, ...) with a global_key
    # (weather_api_key), so it exercises both resolution paths.
    rt = _runtime(tmp_path, ["weather"])
    schema = rt.settings_schema("weather")
    assert schema["id"] == "weather"
    keys = {f["key"] for f in schema["fields"]}
    # per-app (non-global) keys are namespaced plugin_<id>_<key>
    assert "plugin_weather_temperature_unit" in keys
    # global_key settings keep their bare key
    assert "weather_api_key" in keys
    for f in schema["fields"]:
        assert f["key"] in schema["values"]


def test_save_settings_roundtrip(tmp_path):
    rt = _runtime(tmp_path, ["weather"])
    rt.save_settings("weather", {"plugin_weather_temperature_unit": "celsius",
                                 "weather_api_key": "abc123"})
    schema = rt.settings_schema("weather")
    assert schema["values"]["plugin_weather_temperature_unit"] == "celsius"
    assert schema["values"]["weather_api_key"] == "abc123"
    # a key that isn't this app's namespace or a known global is rejected
    rt.save_settings("weather", {"plugin_other_x": "nope"})
    assert rt.settings.get("plugin_other_x") is None


def test_missing_app_pages_error(tmp_path):
    rt = _runtime(tmp_path, [])
    pages = rt.get_pages("does-not-exist")
    assert pages and "NOT FOUND" in pages[0]
