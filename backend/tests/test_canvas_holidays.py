"""The Public Holidays app is dual-view: flap pages on a reel, a desk-calendar rendering on a
Matrix panel. These pin that BOTH paths work off the same bundled dataset — the canvas branch pushes
a panel-sized frame at every size (card layout on a roomy panel, compact on a small one), advances
through the upcoming holidays, and never crashes offline — while the flap branch still returns pages.
"""

from PIL import Image

from conftest import capture_surface as _Cap
from conftest import load_app

HOL = load_app("holidays")
_SETTINGS = {"country": "US", "fun_days": "on"}     # fun_days => always at least one upcoming


def test_canvas_branch_pushes_a_panel_sized_frame_at_every_size():
    for w, h in [(256, 64), (128, 64), (96, 48), (64, 32)]:
        cap = _Cap(w, h)
        hold = HOL.fetch_canvas(_SETTINGS, cap, i18n=None)
        assert isinstance(hold, (int, float)) and hold > 0
        assert isinstance(cap.img, Image.Image) and cap.img.size == (w, h)


def test_canvas_slideshow_advances_through_the_holidays():
    cap = _Cap(128, 64)
    HOL.fetch_canvas.__dict__.pop("_state", None)         # fresh slideshow
    seen = []
    for _ in range(4):
        HOL.fetch_canvas(_SETTINGS, cap, i18n=None)
        seen.append(cap.img.tobytes())
    assert len(set(seen)) > 1                                # not stuck on one holiday


def test_flap_branch_still_returns_pages():
    pages = HOL.fetch(_SETTINGS, lambda *a: " | ".join(str(x) for x in a),
                      lambda: 2, lambda: 20, i18n=None)
    assert isinstance(pages, list) and pages and all(isinstance(p, str) for p in pages)


def test_no_holidays_shows_a_message_not_a_crash(monkeypatch):
    monkeypatch.setattr(HOL, "_upcoming", lambda *a, **k: ([], "ZZ"))
    cap = _Cap(128, 64)
    hold = HOL.fetch_canvas({}, cap, i18n=None)
    assert isinstance(cap.img, Image.Image) and hold >= 10
