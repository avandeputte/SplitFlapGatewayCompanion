"""Channel apps (data.json) are language-aware.

A channel app ships its default pages in data.json and may add translations as
``data_<lang>.json`` sidecars. The pages served must follow the effective Language
with the same precedence a functional app's Localizer gets: an exact locale wins,
then the base language, then data.json. These pin that behaviour -- and pin the
fallback, which is what keeps an untranslated language from rendering blanks.
"""
import json

import pytest

from app.plugins import LANG_DATA_FILE, PluginRuntime
from conftest import make_runtime


def _runtime(tmp_path, files, settings=None):
    """A PluginRuntime with one channel app built from `files` ({name: pages}).

    Built with the REAL constructor (via make_runtime): this used to go through
    ``PluginRuntime.__new__``, which broke silently every time ``__init__`` gained
    state (it did — ``_gen``, ``_wants``, ``_trigger_wants``)."""
    app_dir = tmp_path / "demo"
    app_dir.mkdir()
    (app_dir / "manifest.json").write_text(json.dumps(
        {"id": "demo", "name": "Demo", "type": "channel"}), "utf-8")
    for name, pages in files.items():
        (app_dir / name).write_text(json.dumps(
            {"pages": [{"lines": [p, "", ""]} for p in pages]}), "utf-8")

    rt = make_runtime(tmp_path / "_data", ["demo"], rows=3, cols=15,
                      apps_dir=tmp_path, settings=settings, load=False)
    # Keep the assertions readable: a "page" here is its first line, not a 45-flap wall.
    rt.format_lines = lambda *lines, cols=None, align="center": lines[0]
    rt._registry["demo"] = json.loads((app_dir / "manifest.json").read_text())
    rt._load_channel("demo", app_dir)
    return rt


PAGES = {
    "data.json":     ["HELLO"],
    "data_fr.json":  ["BONJOUR"],
    "data_de.json":  ["HALLO"],
    "data_pt-BR.json": ["OLA BRASIL"],
}


@pytest.mark.parametrize("lang,expected", [
    ("en-US", "HELLO"),      # no translation -> data.json
    ("fr",    "BONJOUR"),    # exact match on the base language
    ("fr-BE", "BONJOUR"),    # regional variant falls back to its base language
    ("fr_CA", "BONJOUR"),    # underscore form is normalised
    ("DE",    "HALLO"),      # case-insensitive
    ("de-AT", "HALLO"),
    ("pt-BR", "OLA BRASIL"), # exact locale beats the base language...
    ("pt",    "HELLO"),      # ...and the base language does NOT inherit from it
    ("sv",    "HELLO"),      # untranslated language falls back rather than blanking
    ("",      "HELLO"),
    (None,    "HELLO"),
])
def test_language_selects_page_set(tmp_path, lang, expected):
    rt = _runtime(tmp_path, PAGES)
    assert rt._channel_pages("demo", lang) == [expected]


def test_translations_mark_the_app_i18n(tmp_path):
    """The globe badge and the per-app Language override hang off manifest['i18n'].
    An app that ships translations gets it whether or not its manifest says so."""
    rt = _runtime(tmp_path, PAGES)
    assert rt._registry["demo"]["i18n"] is True


def test_untranslated_app_is_not_marked_i18n(tmp_path):
    rt = _runtime(tmp_path, {"data.json": ["HELLO"]})
    assert not rt._registry["demo"].get("i18n")
    assert rt._channel_pages("demo", "fr") == ["HELLO"]


def test_per_app_language_override_wins_over_global(tmp_path):
    """A playlist entry / per-app Language beats the global one -- the whole point
    being that one playlist can show French and German back to back."""
    rt = _runtime(tmp_path, PAGES, settings={
        "language": "en-US",                 # global
        "plugin_demo_language": "de",        # per-app override
    })
    lang = rt._perapp_value("demo", "language", rt.settings) or rt.settings.get("language")
    assert rt._channel_pages("demo", lang) == ["HALLO"]


def test_malformed_translation_is_skipped_not_fatal(tmp_path):
    """A broken sidecar must not take the app down -- it falls back to data.json."""
    app_dir = tmp_path / "demo"
    rt = _runtime(tmp_path, {"data.json": ["HELLO"], "data_fr.json": ["BONJOUR"]})
    (app_dir / "data_fr.json").write_text("{ not json", "utf-8")
    rt._load_channel("demo", app_dir)
    assert rt._channel_pages("demo", "fr") == ["HELLO"]


@pytest.mark.parametrize("name,ok", [
    ("data_fr.json", True),
    ("data_pt-BR.json", True),
    ("data_zh-Hans.json", False),   # not a 2-letter region
    ("data_.json", False),
    ("data.json", False),           # the default set, not a translation
    ("data_backup.json", False),    # a stray file must not be read as a language
])
def test_lang_filename_pattern(name, ok):
    assert bool(LANG_DATA_FILE.match(name)) is ok


def test_upload_validator_checks_translations(tmp_path):
    """A translation with no pages is rejected at upload, not at render time."""
    (tmp_path / "data.json").write_text(json.dumps({"pages": ["HI"]}), "utf-8")
    PluginRuntime._validate_channel(tmp_path)          # valid so far

    (tmp_path / "data_fr.json").write_text(json.dumps({"pages": []}), "utf-8")
    with pytest.raises(ValueError, match="data_fr.json"):
        PluginRuntime._validate_channel(tmp_path)

    (tmp_path / "data_fr.json").write_text("{ not json", "utf-8")
    with pytest.raises(ValueError, match="invalid data_fr.json"):
        PluginRuntime._validate_channel(tmp_path)
