"""Regressions for the July 2026 backend-audit fixes (main.py / plugins.py side).

Each of these pins a bug that shipped: a zip bomb that inflated past the
compressed-size cap, live tokens readable from /api/config, a playlist entry
that wasn't a dict 500ing forever, and the per-app settings save leaving a
playlist entry's override-keyed cache stale.
"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.config import Config
from app.plugin_settings import PluginSettings
from app.plugins import PluginRuntime

APPS_DIR = Path(__file__).resolve().parents[2] / "apps"


def _upload_runtime(tmp_path):
    rt = PluginRuntime(Config(data_dir=tmp_path), PluginSettings(tmp_path),
                       APPS_DIR, user_apps_dir=tmp_path / "user_apps")
    rt.load()
    return rt


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_bomb_is_rejected_before_extraction(tmp_path):
    """64 MB+ of zeros compresses to almost nothing — the judge must be the
    UNCOMPRESSED size, because the tempdir is RAM (tmpfs) in Docker."""
    rt = _upload_runtime(tmp_path)
    bomb = _zip_bytes([("app/manifest.json", b'{"name":"x","type":"channel"}'),
                       ("app/data.json", b"\0" * (65 * 1024 * 1024))])
    assert len(bomb) < 1024 * 1024          # compressed: sails under the route cap
    with pytest.raises(ValueError, match="expands too large"):
        rt.install_zip(bomb)


def test_zip_with_too_many_files_is_rejected(tmp_path):
    rt = _upload_runtime(tmp_path)
    many = _zip_bytes([("app/manifest.json", b'{"name":"x","type":"channel"}')]
                      + [(f"app/f{i}.txt", b"x") for i in range(600)])
    with pytest.raises(ValueError, match="too many files"):
        rt.install_zip(many)


def test_config_endpoint_redacts_every_secret():
    from app.main import _redact
    cfg = {
        "transport": {"mqtt": {"password": "hunter2"}},
        "vestaboard": {"api_key": "vbkey", "enablement_token": "enable-me"},
        "mcp": {"token": "bearer-secret"},
    }
    out = _redact(cfg)
    dumped = json.dumps(out)
    for secret in ("hunter2", "vbkey", "enable-me", "bearer-secret"):
        assert secret not in dumped
    # and the original was not mutated
    assert cfg["mcp"]["token"] == "bearer-secret"


def test_playlist_rejects_non_dict_entries():
    """A string entry used to 500 deep in the engine — and PERSIST. Now the model
    rejects it at the door."""
    from pydantic import ValidationError

    from app.main import PlaylistSave
    with pytest.raises(ValidationError):
        PlaylistSave(name="x", entries=["clock"])
    PlaylistSave(name="x", entries=[{"app": "clock", "seconds": 10}])   # fine


def test_saving_settings_drops_override_keyed_caches(tmp_path):
    """A playlist entry renders under a cache key of app_id + overrides; editing
    the app's settings must forget THOSE pages too, not only the bare key."""
    rt = _upload_runtime(tmp_path)
    rt._registry["demo"] = {"id": "demo", "name": "Demo", "type": "functional"}
    rt._caches["demo"] = {"pages": ["OLD"], "fetched_at": 9e18}
    rt._caches["demo\x00style=x"] = {"pages": ["OLD-OVR"], "fetched_at": 9e18}
    rt._caches["other"] = {"pages": ["KEEP"], "fetched_at": 9e18}
    rt.save_settings("demo", {"plugin_demo_thing": "1"})
    assert "demo" not in rt._caches
    assert "demo\x00style=x" not in rt._caches
    assert "other" in rt._caches


def test_wanted_helpers_precomputed_for_fetch_and_trigger(tmp_path):
    d = tmp_path / "user_apps" / "wdemo"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(
        {"id": "wdemo", "name": "W", "type": "functional"}), "utf-8")
    (d / "app.py").write_text(
        "def fetch(settings, format_lines, get_rows, get_cols, i18n=None):\n"
        "    return ['X']\n"
        "def trigger(settings, conditions, caps=None):\n"
        "    return False\n", "utf-8")
    ps = PluginSettings(tmp_path)
    ps.set_installed(["wdemo"])
    rt = PluginRuntime(Config(data_dir=tmp_path), ps, APPS_DIR,
                       user_apps_dir=tmp_path / "user_apps")
    rt.load()
    assert rt._wants["wdemo"] == frozenset({"i18n"})
    assert rt._trigger_wants["wdemo"] == frozenset({"caps"})
