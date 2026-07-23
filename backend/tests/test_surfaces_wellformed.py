"""Every bundled app declares which surfaces it renders on, and its code backs that up. This is the
invariant behind the whole surfaces model: a manifest's ``surfaces`` is a non-empty subset of the
known displays, and a functional app has exactly the entry points its surfaces promise — ``fetch``
for "flap", ``fetch_matrix`` for "matrix". A channel/quiz is data-driven (no app.py); its "matrix"
surface is drawn generically, so it needs no code.
"""

import json
import re
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2] / "apps"
KNOWN = {"flap", "matrix"}     # "lcd" reserved for later


def _apps():
    for mf in sorted(APPS.glob("*/manifest.json")):
        yield mf.parent.name, json.loads(mf.read_text("utf-8")), mf.parent


ALL = list(_apps())


@pytest.mark.parametrize("name,manifest,_dir", ALL, ids=[a[0] for a in ALL])
def test_surfaces_is_a_nonempty_subset_of_known_displays(name, manifest, _dir):
    surf = manifest.get("surfaces")
    assert isinstance(surf, list) and surf, f"{name}: surfaces must be a non-empty list"
    assert set(surf) <= KNOWN, f"{name}: unknown surface(s) {set(surf) - KNOWN}"
    assert len(surf) == len(set(surf)), f"{name}: duplicate surfaces"


@pytest.mark.parametrize("name,manifest,app_dir", ALL, ids=[a[0] for a in ALL])
def test_functional_apps_have_the_entry_points_their_surfaces_promise(name, manifest, app_dir):
    if manifest.get("type") != "functional":
        return                                  # channels/quizzes are data-driven, drawn generically
    src = (app_dir / "app.py").read_text("utf-8")
    has_fetch = bool(re.search(r"^def fetch\(", src, re.M))
    has_matrix = bool(re.search(r"^def fetch_matrix\(", src, re.M))
    surf = manifest.get("surfaces", [])
    if "flap" in surf:
        assert has_fetch, f"{name}: declares flap but has no fetch()"
    if "matrix" in surf:
        assert has_matrix, f"{name}: declares matrix but has no fetch_matrix()"
    # And no dead entry point: a function with no surface to drive it is a bug either way.
    assert has_fetch == ("flap" in surf), f"{name}: fetch() vs 'flap' surface mismatch"
    assert has_matrix == ("matrix" in surf), f"{name}: fetch_matrix() vs 'matrix' surface mismatch"
