"""Upload / delete of user apps into the plugin runtime."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.config import Config
from app.plugin_settings import PluginSettings
from app.plugins import PluginRuntime

FUNC_APP = "def fetch(settings, format_lines, get_rows, get_cols):\n    return [format_lines('HI')]\n"


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def _runtime(tmp_path):
    cfg = Config(data_dir=tmp_path)
    ps = PluginSettings(tmp_path)
    ps.set_installed([])
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    rt = PluginRuntime(cfg, ps, builtin, tmp_path / "userapps")
    rt.load()
    return rt


def test_upload_functional_app(tmp_path):
    rt = _runtime(tmp_path)
    info = rt.install_zip(_zip({
        "myapp/manifest.json": json.dumps({"name": "My App", "type": "functional", "icon": "🎯"}),
        "myapp/app.py": FUNC_APP,
    }))
    assert info == {"id": "myapp", "name": "My App", "type": "functional"}
    assert "myapp" in rt.discover()
    assert rt.is_builtin("myapp") is False
    assert "myapp" in rt.settings.installed_apps
    pages = rt.get_pages("myapp")
    assert pages and pages[0].strip() == "HI"


def test_upload_channel_app(tmp_path):
    rt = _runtime(tmp_path)
    info = rt.install_zip(_zip({
        "quotes/manifest.json": json.dumps({"name": "Quotes", "type": "channel"}),
        "quotes/data.json": json.dumps({"pages": ["HELLO", "WORLD"]}),
    }))
    assert info["id"] == "quotes"
    assert rt.get_pages("quotes") == ["HELLO", "WORLD"]


def test_upload_root_level_uses_manifest_id(tmp_path):
    rt = _runtime(tmp_path)
    info = rt.install_zip(_zip({
        "manifest.json": json.dumps({"id": "rootapp", "name": "Root", "type": "channel"}),
        "data.json": json.dumps({"pages": ["R"]}),
    }))
    assert info["id"] == "rootapp"


def test_reject_no_manifest(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError, match="manifest"):
        rt.install_zip(_zip({"x/app.py": "x = 1"}))


def test_reject_functional_without_fetch(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError, match="fetch"):
        rt.install_zip(_zip({
            "a/manifest.json": json.dumps({"name": "A", "type": "functional"}),
            "a/app.py": "x = 1\n",
        }))


def test_reject_app_py_import_error(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError, match="import"):
        rt.install_zip(_zip({
            "a/manifest.json": json.dumps({"name": "A", "type": "functional"}),
            # valid fetch() (passes the static checks) but a missing dependency —
            # surfaced when the vetted module is imported.
            "a/app.py": "import a_module_that_does_not_exist\n"
                        "def fetch(settings, format_lines, get_rows, get_cols):\n"
                        "    return [format_lines('HI')]\n",
        }))


def test_reject_bad_type(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError, match="type"):
        rt.install_zip(_zip({"a/manifest.json": json.dumps({"name": "A", "type": "widget"})}))


def test_reject_bad_zip(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError, match="zip"):
        rt.install_zip(b"this is not a zip")


def test_reject_path_traversal(tmp_path):
    rt = _runtime(tmp_path)
    with pytest.raises(ValueError, match="unsafe"):
        rt.install_zip(_zip({
            "../evil.py": "x = 1",
            "a/manifest.json": json.dumps({"name": "A", "type": "channel"}),
        }))


def test_delete_user_app(tmp_path):
    rt = _runtime(tmp_path)
    rt.install_zip(_zip({
        "z/manifest.json": json.dumps({"name": "Z", "type": "channel"}),
        "z/data.json": json.dumps({"pages": ["Z"]}),
    }))
    assert "z" in rt.discover()
    rt.delete_app("z")
    assert "z" not in rt.discover()
    assert not (rt.user_apps_dir / "z").exists()


def test_cannot_delete_builtin(tmp_path):
    rt = _runtime(tmp_path)
    b = rt.apps_dir / "b"
    b.mkdir()
    (b / "manifest.json").write_text(json.dumps({"name": "B", "type": "channel"}))
    (b / "data.json").write_text(json.dumps({"pages": ["B"]}))
    rt.settings.set_installed(["b"])
    rt.load()
    assert rt.is_builtin("b")
    with pytest.raises(ValueError, match="built-in"):
        rt.delete_app("b")


def test_user_app_overrides_builtin(tmp_path):
    rt = _runtime(tmp_path)
    # built-in "dup"
    b = rt.apps_dir / "dup"; b.mkdir()
    (b / "manifest.json").write_text(json.dumps({"name": "Builtin Dup", "type": "channel"}))
    (b / "data.json").write_text(json.dumps({"pages": ["OLD"]}))
    # user "dup" wins
    rt.install_zip(_zip({
        "dup/manifest.json": json.dumps({"name": "User Dup", "type": "channel"}),
        "dup/data.json": json.dumps({"pages": ["NEW"]}),
    }))
    assert rt.manifest("dup")["name"] == "User Dup"
    assert rt.get_pages("dup") == ["NEW"]
