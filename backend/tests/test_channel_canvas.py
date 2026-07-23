"""Channel-on-canvas: a channel app can draw itself on a Matrix panel — its text laid out big with
a themed icon — instead of the plain flap text. Opt-in per app (a toggle greyed out on a flap-only
wall), art motif from the manifest. These pin the wiring; the look is eyeballed off-device.
"""

import json
import os
import tempfile

import pytest
from PIL import Image, ImageFont

from app import channel_art, device
from app.canvas import _FONT_DIR
from conftest import APPS_DIR, make_runtime
from test_canvas import CANVAS_DOC


def _channels():
    out = []
    for d in sorted(APPS_DIR.iterdir()):
        mf = d / "manifest.json"
        if mf.is_file() and json.loads(mf.read_text("utf-8")).get("type") in ("channel", "quiz"):
            out.append(d.name)
    return out


CHANNELS = _channels()


class _Cap:
    def __init__(self, w=256, h=64):
        self.width, self.height, self.img = w, h, None

    def blank(self, color=(0, 0, 0)):
        return Image.new("RGB", (self.width, self.height), tuple(color))

    def font(self, size, name="DejaVuSans-Bold.ttf"):
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), max(5, int(size)))

    def frame(self, image):
        self.img = image
        return True


@pytest.mark.parametrize("app_id", CHANNELS, ids=CHANNELS)
def test_every_channel_declares_a_known_motif(app_id):
    """Each channel names an art motif the renderer actually has — otherwise it'd fall back to a
    generic quote on the panel with no signal in review."""
    doc = json.loads((APPS_DIR / app_id / "manifest.json").read_text("utf-8"))
    motif = doc.get("canvas_art")
    assert motif, f"{app_id}: no canvas_art motif"
    assert motif in channel_art.MOTIFS, f"{app_id}: unknown motif {motif!r}"


def test_the_matrix_toggle_shows_only_on_a_matrix_wall_and_leads_the_form():
    ch = CHANNELS[0]
    flap = make_runtime(tmp_path=tempfile.mkdtemp(), installed=[ch], caps=device.SPLIT_FLAP)
    assert not any(f["key"].endswith("_matrix")                 # absent entirely on a flap-only wall
                   for f in flap.settings_schema(ch)["fields"])

    matrix = make_runtime(tmp_path=tempfile.mkdtemp(), installed=[ch],
                          caps=device.from_capabilities(CANVAS_DOC))
    fields = matrix.settings_schema(ch)["fields"]
    assert fields[0]["key"].endswith("_matrix")                 # present, and leads the form


def test_channel_matrix_defaults_on_and_can_be_turned_off():
    ch = CHANNELS[0]
    rt = make_runtime(tmp_path=tempfile.mkdtemp(), installed=[ch], caps=device.from_capabilities(CANVAS_DOC))
    assert rt.matrix_on(ch) is True                            # default: render on the panel
    rt.settings.set(f"plugin_{ch}_matrix", "no")
    assert rt.matrix_on(ch) is False                           # explicit opt-out honoured
    assert rt.matrix_on("does-not-exist") is False             # only real matrix-capable apps


def test_items_are_plain_text_and_render_a_non_black_frame():
    ch = "magic-8-ball"
    rt = make_runtime(tmp_path=tempfile.mkdtemp(), installed=[ch],
                      caps=device.from_capabilities(CANVAS_DOC), rows=3, cols=15)
    items = rt.channel_canvas_items(ch)
    assert items and all(isinstance(s, str) and "  " not in s for s in items)   # padding collapsed
    cv = _Cap()
    channel_art.render(cv, items[0], rt.channel_canvas_motif(ch))
    assert isinstance(cv.img, Image.Image) and cv.img.getbbox() is not None      # something was drawn
