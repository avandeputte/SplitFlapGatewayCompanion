"""App-catalog conformance — the contracts the July 2026 audit found being broken.

Three rules, each of which failed silently somewhere in the catalog:

**A channel's min_cols must fit its data.** Channel pages are authored at a fixed
width; a wall narrower than that truncates every line, and nothing reports it. All
twelve channels declared ``min_cols: 10`` over 15-wide data — a 12-column wall was
offered an app guaranteed to cut every page. The manifest minimum must be at least
the widest line in ANY data file, translations included.

**A channel translation must be on that language's reel.** Same argument as
test_translations_fit_the_wall: a physical module asked for a flap it doesn't carry
homes to blank. `É` is fine on a French wall and a hole on a Danish one. Checked
against the folded (uppercase) form, for every ``data_<lang>.json`` whose language
has a published reel.

**The i18n badge is a promise.** ``"i18n": true`` in a functional app's manifest
surfaces the globe badge and the per-app Language override. If fetch() doesn't even
accept the ``i18n`` helper, that promise is empty — the override would change
nothing.
"""

import json
from pathlib import Path

import pytest

from test_translations_fit_the_wall import REELS

APPS = Path(__file__).resolve().parents[2] / "apps"


def _manifests():
    for d in sorted(APPS.iterdir()):
        mf = d / "manifest.json"
        if mf.exists():
            yield d, json.loads(mf.read_text("utf-8"))


CHANNELS = [(d, m) for d, m in _manifests() if m.get("type") == "channel"]
FUNCTIONAL = [(d, m) for d, m in _manifests() if m.get("type") == "functional"]


@pytest.mark.parametrize("app_dir,manifest", CHANNELS, ids=[d.name for d, _ in CHANNELS])
def test_channel_min_cols_fits_its_data(app_dir, manifest):
    min_cols = manifest.get("min_cols")
    assert min_cols, f"{app_dir.name}: channel declares no min_cols"
    widest = 0
    for f in sorted(app_dir.glob("data*.json")):
        doc = json.loads(f.read_text("utf-8"))
        for page in doc["pages"]:
            for line in page["lines"]:
                widest = max(widest, len(line))
    assert min_cols >= widest, (
        f"{app_dir.name}: data is {widest} wide but min_cols is {min_cols} — "
        f"every page truncates on a {min_cols}-column wall")


def _channel_translation_lines():
    for app_dir, _m in CHANNELS:
        for f in sorted(app_dir.glob("data_*.json")):
            lang = f.stem[len("data_"):].lower().split("-")[0]
            reel = REELS.get(lang)
            if not reel:
                continue  # no published reel for this language — nothing to check
            doc = json.loads(f.read_text("utf-8"))
            yield app_dir.name, f.name, reel, doc


CASES = list(_channel_translation_lines())


@pytest.mark.parametrize("app,fname,reel,doc", CASES,
                         ids=[f"{a}:{f}" for a, f, _, _ in CASES])
def test_channel_translation_is_on_the_reel(app, fname, reel, doc):
    holes = set()
    for page in doc["pages"]:
        for line in page["lines"]:
            holes |= {ch for ch in line.upper() if ch not in reel}
    assert not holes, (
        f"{app}/{fname}: {sorted(holes)} are not on that language's reel — "
        f"a physical module homes to BLANK for them")


@pytest.mark.parametrize("app_dir,manifest",
                         [(d, m) for d, m in FUNCTIONAL if m.get("i18n")],
                         ids=[d.name for d, m in FUNCTIONAL if m.get("i18n")])
def test_i18n_badge_means_fetch_takes_i18n(app_dir, manifest):
    import ast
    tree = ast.parse((app_dir / "app.py").read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch":
            params = {a.arg for a in node.args.args + node.args.kwonlyargs}
            assert "i18n" in params or node.args.kwarg is not None, (
                f"{app_dir.name}: manifest says i18n:true but fetch() never "
                f"accepts the i18n helper — the Language override does nothing")
            return
    pytest.fail(f"{app_dir.name}: functional app without fetch()")
