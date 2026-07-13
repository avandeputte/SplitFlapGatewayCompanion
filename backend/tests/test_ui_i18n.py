"""The UI (chrome) language: resolution chain, string catalogs, app metadata.

Three suites:
  1. uilang.resolve — the URL > explicit setting > env > Accept-Language chain,
     including the seeded-en-US wrinkle (language_explicit / migration).
  2. The string catalogs — every t("...") key and data-i18n attribute in the
     SPA exists in en.json, and every language file is a subset of it. This is
     what keeps catalogs from silently drifting away from the source.
  3. Translated app metadata — central catalog + per-app sidecar resolution,
     and the localized flap fallback pages.
"""
import json
import re
from pathlib import Path

import pytest

from app import uilang
from app.plugins import APP_I18N_DIR, I18N_META_FILE, PluginRuntime

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "app" / "static"
I18N = STATIC / "i18n"


# ---------------------------------------------------------------------------
# 1. the resolution chain
# ---------------------------------------------------------------------------
class S(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


@pytest.mark.parametrize("query,settings,env,accept,expected", [
    # 1: the URL parameter always wins
    ("fr", {"language": "de", "language_explicit": True}, "es", "it", "fr"),
    # 2: the setting wins once explicit...
    (None, {"language": "de", "language_explicit": True}, "es", "it", "de"),
    # ...but the seeded default is NOT explicit: falls through to env
    (None, {"language": "en-US"}, "es", "it", "es"),
    # migration heuristic: a stored non-default language counts as explicit
    (None, {"language": "fr"}, "es", "it", "fr"),
    # 3: env beats the browser
    (None, {"language": "en-US"}, "de", "fr;q=0.9", "de"),
    # 4: the browser, best q first, base-matched against the offered list
    (None, {"language": "en-US"}, "", "de-DE;q=0.8,fr;q=0.9", "fr"),
    (None, None, None, "sw,de;q=0.5", "sw"),
    # nothing anywhere -> default
    (None, None, "", "", "en-US"),
    # unknown codes pass to the next level rather than sticking
    ("xx", {"language": "en-US"}, "zz", "yy,es;q=0.4", "es"),
])
def test_resolution_chain(query, settings, env, accept, expected):
    s = S(settings) if settings is not None else None
    assert uilang.resolve(query, s, env, accept) == expected


@pytest.mark.parametrize("query,settings,env,expected", [
    ("fr", None, None, "fr"),                                  # URL locks it
    (None, {"language": "de", "language_explicit": True}, None, "de"),   # setting locks it
    (None, None, "es", "es"),                                  # ui_language locks it
    (None, {"language": "en-US"}, "", None),                   # nothing explicit -> unlocked
    (None, None, "auto", None),                                # the add-on sentinel is not a choice
])
def test_locked_levels_are_exactly_1_to_3(query, settings, env, expected):
    """Locked = the client must not substitute Home Assistant's language. Only an
    explicit choice locks; the browser (level 5) never does."""
    s = S(settings) if settings is not None else None
    # config.py maps the add-on's "auto" sentinel to unset before it reaches here
    env = "" if env == "auto" else env
    assert uilang.resolve_locked(query, s, env) == expected


def test_auto_sentinel_means_unset(monkeypatch, tmp_path):
    """The add-on's ui_language is a dropdown, which cannot offer a blank option, so
    "auto" carries "not set" — it must not be treated as a language."""
    from app.config import Config
    monkeypatch.setattr("app.config.addon_options", lambda: {"ui_language": "auto"})
    monkeypatch.delenv("COMPANION_UI_LANGUAGE", raising=False)
    assert Config(tmp_path).ui_language == ""
    monkeypatch.setattr("app.config.addon_options", lambda: {"ui_language": "fr"})
    assert Config(tmp_path).ui_language == "fr"


def test_normalize_exact_beats_base_and_is_case_insensitive():
    assert uilang.normalize("PT-br") == "pt-BR"      # exact variant, any case
    assert uilang.normalize("pt_PT") == "pt"         # underscore + base match
    assert uilang.normalize("de-AT") == "de-AT"      # offered variant
    assert uilang.normalize("nope") is None


def test_accept_language_parsing_orders_by_q():
    codes = uilang.parse_accept_language("fr;q=0.3, de, *;q=0.1, es;q=0.5")
    assert codes == ["de", "es", "fr"]


# ---------------------------------------------------------------------------
# 2. catalogs frozen to the source
# ---------------------------------------------------------------------------
def _source_keys() -> set:
    js = (STATIC / "app.js").read_text("utf-8")
    keys = set()
    for pat in (r'(?<![A-Za-z0-9_$.])t\(\s*"((?:[^"\\]|\\.)+?)"',
                r"(?<![A-Za-z0-9_$.])t\(\s*'((?:[^'\\]|\\.)+?)'"):
        for m in re.finditer(pat, js):
            keys.add(m.group(1).replace('\\"', '"').replace("\\'", "'"))
    html = (STATIC / "index.html").read_text("utf-8")
    for m in re.finditer(r'data-i18n(?:-title|-label)?="([^"]+)"', html):
        keys.add(m.group(1))
    return {k for k in keys
            if any(ch.isalpha() for ch in k) and not k.startswith(("/", "."))}


def test_en_catalog_covers_every_source_string():
    """en.json is the frozen key list: rerun scripts/extract_ui_strings.py
    whenever a UI string is added or reworded."""
    en = set(json.loads((I18N / "en.json").read_text("utf-8")))
    missing = _source_keys() - en
    assert not missing, f"run scripts/extract_ui_strings.py — missing: {sorted(missing)[:5]}"


def test_language_catalogs_are_subsets_of_en():
    en = set(json.loads((I18N / "en.json").read_text("utf-8")))
    for f in sorted(I18N.glob("*.json")):
        if f.name == "en.json":
            continue
        cat = json.loads(f.read_text("utf-8"))
        assert isinstance(cat, dict), f.name
        stale = set(cat) - en
        assert not stale, f"{f.name} has keys en.json lost: {sorted(stale)[:5]}"
        # %s placeholders must survive translation (t() feeds them positionally)
        for k, v in cat.items():
            assert k.count("%s") == v.count("%s"), (f.name, k)


# Left in English deliberately: product names, unit symbols/codes, and the language
# self-names in the Language dropdown (a French speaker still picks "Deutsch").
DO_NOT_TRANSLATE = {
    "Open-Meteo", "OpenWeather", "QWeather", "WeatherAPI.com", "Companion",
    "FR24", "FlightAware", "AirLabs*", "AStack*", "OpenSky*",
    "Aviationstack API Key", "AirLabs API Key", "FlightAware AeroAPI Key",
    "FR24 RapidAPI Host", "FR24 RapidAPI Key", "BirdNET-Pi Host",
    "OpenSky Client ID (optional; leave blank for free API)",
    "OpenSky Client Secret (optional; higher limits with free account)",
    "C", "F", "K", "KM", "MI", "NM", "KT", "KMH", "MPH", "Flight Level",
    # Attribution wording the providers require verbatim.
    "Powered by WeatherAPI.com", "Powered by QWeather",
}


def _translatable(en: set) -> set:
    selfnames = {k for k in en if "(" in k and any(
        n in k for n in ("English", "Français", "Deutsch", "Español", "Italiano",
                         "Português", "Nederlands", "Dansk", "Norsk", "Svenska",
                         "Suomi", "Íslenska", "Gaeilge", "Català", "Galego",
                         "Euskara", "Eesti", "Malay", "Swahili"))}
    selfnames |= {"Afrikaans", "Bahasa Indonesia"}
    return en - DO_NOT_TRANSLATE - selfnames


def test_fr_de_es_cover_the_translatable_surface():
    """Everything a user reads must be covered — including the labels the apps
    declare in their manifests, which the settings form renders through t()."""
    en = set(json.loads((I18N / "en.json").read_text("utf-8")))
    for code in ("fr", "de", "es"):
        cat = set(json.loads((I18N / f"{code}.json").read_text("utf-8")))
        missing = _translatable(en) - cat
        assert not missing, f"{code}.json missing: {sorted(missing)[:5]}"


def test_manifest_settings_labels_are_in_the_catalog():
    """The settings dialog was English because the manifest labels never reached
    the catalog. Every declared label/note/option must be a key."""
    en = set(json.loads((I18N / "en.json").read_text("utf-8")))
    # A label with no letters ("1", "30") is a number, not language — the extractor
    # skips those and so must this.
    def wanted(s):
        return s and any(ch.isalpha() for ch in s) and s not in en

    missing = set()
    for mf in sorted((ROOT / "apps").glob("*/manifest.json")):
        m = json.loads(mf.read_text("utf-8"))
        for s in m.get("settings") or []:
            if not isinstance(s, dict):
                continue
            for k in ("label", "note", "ph"):
                if wanted(s.get(k)):
                    missing.add(s[k])
            for o in s.get("options") or []:
                if isinstance(o, dict) and wanted(str(o.get("label") or "")):
                    missing.add(str(o["label"]))
    assert not missing, f"run scripts/extract_ui_strings.py — missing: {sorted(missing)[:5]}"


def test_server_side_ui_t_reads_the_same_catalogs():
    assert uilang.ui_t("fr", "Save") == "Enregistrer"
    assert uilang.ui_t("fr-BE", "Save") == "Enregistrer"   # base fallback
    assert uilang.ui_t("en-US", "Save") == "Save"
    assert uilang.ui_t("fr", "not-a-key") == "not-a-key"


# ---------------------------------------------------------------------------
# 3. app metadata + flap fallbacks
# ---------------------------------------------------------------------------
def _runtime(tmp_path, settings=None):
    rt = PluginRuntime.__new__(PluginRuntime)
    rt.apps_dir = tmp_path
    rt.user_apps_dir = tmp_path / "_user"
    rt._registry, rt._channel, rt._modules, rt._caches = {}, {}, {}, {}
    rt.settings = S(settings or {})
    rt.get_cols, rt.get_rows = (lambda: 15), (lambda: 3)
    rt.format_lines = lambda *l, cols=None: "|".join(x for x in l)
    return rt


def _app(tmp_path, app_id="demo", sidecar=None):
    d = tmp_path / app_id
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps(
        {"id": app_id, "name": "Demo", "type": "channel",
         "description": "English words"}), "utf-8")
    (d / "data.json").write_text(json.dumps({"pages": ["HI"]}), "utf-8")
    if sidecar:
        (d / "i18n").mkdir()
        for lang, meta in sidecar.items():
            (d / "i18n" / f"{lang}.json").write_text(
                json.dumps(meta, ensure_ascii=False), "utf-8")
    return d


def test_central_catalog_translates_the_vendored_library():
    """The shipped app_i18n files must reference real apps and real languages."""
    ids = {p.name for p in (ROOT / "apps").iterdir()
           if (p / "manifest.json").is_file()}
    files = sorted(APP_I18N_DIR.glob("*.json"))
    assert files, "app_i18n/ catalogs missing"
    for f in files:
        assert I18N_META_FILE.match(f.name), f.name
        cat = json.loads(f.read_text("utf-8"))
        unknown = set(cat) - ids
        assert not unknown, f"{f.name} names unknown apps: {sorted(unknown)[:5]}"
        for app_id, meta in cat.items():
            assert set(meta) <= {"name", "flap_name", "description", "settings"}, app_id


def test_sidecar_beats_central_and_base_feeds_variant(tmp_path):
    rt = _runtime(tmp_path)
    d = _app(tmp_path, sidecar={
        "fr": {"name": "Démo", "description": "Des mots"},
        "fr-CA": {"name": "Démo QC"},
    })
    meta = rt.app_meta_i18n("demo", d, "fr-CA")
    assert meta["name"] == "Démo QC"            # exact wins
    assert meta["description"] == "Des mots"    # base fills the rest
    assert rt.app_meta_i18n("demo", d, "en-US") == {}
    assert rt.app_meta_i18n("demo", d, "sv") == {}


def test_listings_carry_translated_metadata(tmp_path):
    rt = _runtime(tmp_path)
    d = _app(tmp_path, sidecar={"fr": {"name": "Démo", "description": "Des mots"}})
    rt._registry["demo"] = json.loads((d / "manifest.json").read_text())
    fr = rt._entry("demo", rt._registry["demo"], True, True, lang="fr", app_dir=d)
    en = rt._entry("demo", rt._registry["demo"], True, True, lang="en-US", app_dir=d)
    assert (fr["name"], fr["description"]) == ("Démo", "Des mots")
    assert (en["name"], en["description"]) == ("Demo", "English words")


def test_flap_fallback_uses_content_language_and_flap_name(tmp_path):
    rt = _runtime(tmp_path, settings={"language": "fr"})
    _app(tmp_path, sidecar={"fr": {"name": "Démo", "flap_name": "DEMO FR"}})
    page = rt._flap_fallback("demo", {"name": "Demo"}, rt.settings,
                             "{name}", "NO DATA", "")
    name, msg, _ = page.split("|")
    assert name == "DEMO FR"            # flap_name beats the pretty name
    assert msg == "PAS DE DONNEES"      # translated via i18n_data.json
    page_en = rt._flap_fallback("demo", {"name": "Demo"},
                                S({"language": "en-US"}), "{name}", "NO DATA", "")
    assert page_en.split("|")[1] == "NO DATA"


def test_upload_validator_rejects_broken_sidecars(tmp_path):
    d = _app(tmp_path)
    (d / "i18n").mkdir()
    (d / "i18n" / "fr.json").write_text("{ nope", "utf-8")
    with pytest.raises(ValueError, match="i18n/fr.json"):
        PluginRuntime._validate_i18n_sidecars(d)
    (d / "i18n" / "fr.json").write_text(json.dumps({"name": 3}), "utf-8")
    with pytest.raises(ValueError, match="'name' must be a string"):
        PluginRuntime._validate_i18n_sidecars(d)
    (d / "i18n" / "fr.json").write_text(json.dumps({"name": "Démo"}), "utf-8")
    PluginRuntime._validate_i18n_sidecars(d)   # now valid
