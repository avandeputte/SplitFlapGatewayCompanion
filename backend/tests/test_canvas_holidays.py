"""The Public Holidays app is dual-view: flap pages on a reel, a desk-calendar rendering on a
Matrix panel. These pin that BOTH paths work off the same bundled dataset — the canvas branch pushes
a panel-sized frame at every size (card layout on a roomy panel, compact on a small one), advances
through the upcoming holidays, and never crashes offline — while the flap branch still returns pages.
"""

import importlib.util
from pathlib import Path

from PIL import Image, ImageFont

from app.canvas import _FONT_DIR

ROOT = Path(__file__).resolve().parents[2]


def _load():
    p = ROOT / "apps" / "holidays" / "app.py"
    spec = importlib.util.spec_from_file_location("_holidays", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Cap:
    """A canvas that renders for real (so the font/wrap/fit maths run) and captures the frame."""

    def __init__(self, w, h):
        self.width, self.height, self.img = w, h, None

    def blank(self, color=(0, 0, 0)):
        return Image.new("RGB", (self.width, self.height), tuple(color))

    def vgrad(self, top, bottom):
        img = Image.new("RGB", (self.width, self.height))
        for y in range(self.height):
            f = y / max(1, self.height - 1)
            row = tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
            img.paste(row, (0, y, self.width, y + 1))
        return img

    def font(self, size, name="DejaVuSans-Bold.ttf"):
        import os
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), max(5, int(size)))

    def frame(self, image):
        self.img = image
        return True


HOL = _load()
_SETTINGS = {"country": "US", "fun_days": "on"}     # fun_days => always at least one upcoming


def test_canvas_branch_pushes_a_panel_sized_frame_at_every_size():
    for w, h in [(256, 64), (128, 64), (96, 48), (64, 32)]:
        cap = _Cap(w, h)
        hold = HOL.fetch_matrix(_SETTINGS, cap, i18n=None)
        assert isinstance(hold, (int, float)) and hold > 0
        assert isinstance(cap.img, Image.Image) and cap.img.size == (w, h)


def test_canvas_slideshow_advances_through_the_holidays():
    cap = _Cap(128, 64)
    HOL.fetch_matrix.__dict__.pop("_state", None)         # fresh slideshow
    seen = []
    for _ in range(4):
        HOL.fetch_matrix(_SETTINGS, cap, i18n=None)
        seen.append(cap.img.tobytes())
    assert len(set(seen)) > 1                                # not stuck on one holiday


def test_flap_branch_still_returns_pages():
    pages = HOL.fetch(_SETTINGS, lambda *a: " | ".join(str(x) for x in a),
                      lambda: 2, lambda: 20, i18n=None)
    assert isinstance(pages, list) and pages and all(isinstance(p, str) for p in pages)


def test_no_holidays_shows_a_message_not_a_crash(monkeypatch):
    monkeypatch.setattr(HOL, "_upcoming", lambda *a, **k: ([], "ZZ"))
    cap = _Cap(128, 64)
    hold = HOL.fetch_matrix({}, cap, i18n=None)
    assert isinstance(cap.img, Image.Image) and hold >= 10
