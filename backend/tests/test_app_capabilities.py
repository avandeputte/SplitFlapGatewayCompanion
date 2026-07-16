"""An app can ask what the wall can SHOW, and must, before it uses a pictograph.

A Matrix Portal has fourteen flaps a real reel does not (♥ ♦ ♣ ♠ ☺ ♪ ● ■ ⌂ ← ↑ → ↓ ☀). A wall
without them substitutes the nearest character it has — and only some of those still MEAN
anything:

    ← ↑ → ↓  ->  < ^ > v      still reads. Safe to use unconditionally.
    ♥ ♪ ● ☀  ->  *            the meaning is gone. Ask first.

So `caps` is injected by parameter name, exactly like `i18n` and `get_location`, and only for
an app that asks. It defaults to None — which is also what a stock splitflap-os host passes,
and which correctly means "a plain reel", so an app using it stays drop-in both ways.
"""
import tempfile
from pathlib import Path

import pytest

from app import device, renderer

APPS = Path(__file__).resolve().parents[2] / "apps"


def _runtime(caps, app_id, rows=5, cols=15, **settings):
    from app.config import Config
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime

    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp)
    cfg.update({"grid": {"rows": rows, "cols": cols}})
    st = PluginSettings(cfg.data_dir)
    st.set("installed_apps", [app_id])
    for k, v in settings.items():
        st.set(k, v)
    rt = PluginRuntime(cfg, st, APPS, cfg.data_dir / "apps")
    rt.attach_caps(lambda: caps)
    rt.load()
    return rt


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
    from app.config import Config
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime

    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp)
    rt = PluginRuntime(cfg, PluginSettings(cfg.data_dir), APPS, cfg.data_dir / "apps")
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
