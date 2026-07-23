"""An app can ask what the wall can SHOW, and must, before it uses a pictograph.

A Matrix Portal has fourteen flaps a real reel does not (♥ ♦ ♣ ♠ ☺ ♪ ● ■ ⌂ ← ↑ → ↓ ☀). A wall
without them substitutes the nearest character it has — and only some of those still MEAN
anything:

    ← ↑ → ↓  ->  < ^ > v      still reads. Safe to use unconditionally.
    ♥ ♪ ● ☀  ->  *            the meaning is gone. Ask first.

So `caps` is injected by parameter name, exactly like `i18n` and `get_location`, and only for
an app that asks. It defaults to None — what a host that injects nothing passes — which
correctly means "a plain reel", so an app using it stays drop-in both ways.
"""
import pytest

from app import device, renderer
from conftest import APPS_DIR as APPS
from conftest import make_runtime


def _runtime(caps, app_id, rows=5, cols=15, **settings):
    return make_runtime(installed=[app_id], rows=rows, cols=cols,
                        caps=caps, settings=settings)


TIDES = {"predictions": [
    {"t": "2026-07-14 09:28", "type": "H", "v": "11.2"},
    {"t": "2026-07-14 15:48", "type": "L", "v": "0.9"},
]}


@pytest.fixture
def tide_api(monkeypatch):
    import requests

    class R:
        def json(self):
            return TIDES
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())


def test_the_runtime_only_injects_caps_when_an_app_asks(tide_api):
    """Additive: an app that never heard of it is called exactly as before."""
    rt = _runtime(device.MATRIX_PORTAL, "tides")
    assert "caps" in rt._wants["tides"]
    assert "caps" not in rt._wants.get("time", frozenset())     # not loaded here


def test_a_matrix_portal_gets_arrows(tide_api):
    page = _runtime(device.MATRIX_PORTAL, "tides").get_pages("tides")[0]
    assert "↑" in page and "↓" in page


def test_a_plain_reel_keeps_the_words(tide_api):
    """A ↑ falls back to "^", which is not what a tide table should say."""
    page = _runtime(device.SPLIT_FLAP, "tides").get_pages("tides")[0]
    assert "↑" not in page and "↓" not in page
    assert "H " in page and "L " in page


def test_the_default_is_the_pessimistic_answer():
    """A runtime nobody told assumes a plain reel — a pictograph nobody can see is worse
    than a word everybody can."""
    rt = make_runtime(load=False)
    assert rt._caps() == device.SPLIT_FLAP


# ---------------------------------------------------------------------------
# arrows need no permission — they survive the fallback
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("arrow,fallback", [("←", "<"), ("↑", "^"), ("→", ">"), ("↓", "v")])
def test_an_arrow_still_reads_on_a_plain_reel(arrow, fallback):
    assert renderer.for_legacy(arrow) == fallback


def test_stocks_shows_the_direction_even_with_colours_off():
    """It was a COLOUR, and a colour is nothing at all with colours disabled — or on a mono
    wall. The arrow carries the meaning; the colour reinforces it."""
    src = (APPS / "stocks" / "app.py").read_text("utf-8")
    assert "\\u2191" in src and "\\u2193" in src
    assert "arrow if no_color else" in src, "the arrow must survive with colours off"
