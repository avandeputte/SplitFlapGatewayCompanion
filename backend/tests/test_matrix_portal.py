"""The Matrix Portal's index-addressed display API (POST /api/display/cells).

WHY IT EXISTS — from the firmware's own reel.h, and it is the crux of everything here:

    The legacy wire carries ONE BYTE per character, and it has a problem it can never
    solve: the byte for lowercase 'r' already means RED. So on that path lowercase must
    fold to uppercase, and a heart — which has no Windows-1252 byte at all — cannot be
    addressed by character in ANY way.

Both flaps EXIST on the reel (lowercase at 163..222, pictographs at 223..236). They are
simply unreachable by character. /api/display/cells addresses them by INDEX and NAMES the
colours instead of stealing seven letters for them.

Three things follow, and each is a way to get this wrong:

  * **The colour ambiguity moves into the companion.** If a wall can show a lowercase `r`,
    then a page has to say whether it meant the letter or the colour — and a bare `r`
    cannot. So a colour becomes its own codepoint internally, produced only where a colour
    is unambiguously meant.
  * **A page with an unrenderable character is a 400** — deliberately: "a half-written wall
    is worse than a rejected request". Right for the firmware; a trap for us, because one
    stray glyph from an app would blank the whole wall. We sanitise before sending.
  * **It is a per-DISPLAY capability.** One companion now drives a Matrix Portal and a
    physical wall side by side, and they do not have the same alphabet.
"""
import json

import pytest

from app import device, renderer
from app.transport.rest import RestTransport


# ---------------------------------------------------------------------------
# capability — a property of the gateway on the other end
# ---------------------------------------------------------------------------
# (gateway.supports_cells is gone — it was dead code whose only caller was its own
# test; device.of() is the real fallback inference and answers the same question.)
@pytest.mark.parametrize("gw,expected", [
    ({"product": "Matrix Portal Gateway", "fwVersion": "1.7.0"}, True),
    ({"product": "Matrix Portal Gateway", "fwVersion": "1.6.0"}, True),
    ({"product": "Matrix Portal Gateway", "fwVersion": "1.5.9"}, False),   # predates it
    ({"product": "SplitFlap Gateway", "fwVersion": "3.4.0"}, False),       # a real wall
    ({}, False),
])
def test_only_a_matrix_portal_has_the_index_api(gw, expected):
    assert bool(device.of(gw)) is expected


def test_the_api_level_is_not_the_capability():
    """The gateway API version stays 3.1 — that is the API level, not the firmware's. Keying
    off it would claim the capability for every 3.1 gateway, including physical walls."""
    assert bool(device.of({"version": "3.1.0", "product": "SplitFlap Gateway"})) is False


# ---------------------------------------------------------------------------
# a colour has to stop being a letter
# ---------------------------------------------------------------------------
def test_a_colour_tile_becomes_a_colour_not_a_letter_when_case_is_kept():
    """`🟥 hello` on a rich wall: the tile is a COLOUR and the `h` `e` `l` `l` `o` are
    LETTERS. Carrying the colour as `r` — as the legacy encoding does — would make it
    indistinguishable from the r in a word."""
    out = renderer.normalize("🟥 red", 5)
    assert renderer.is_color(out[0])
    assert out[1:].rstrip() == " red"               # the letters are letters
    assert not any(renderer.is_color(c) for c in out[1:])


def test_legacy_pages_are_byte_for_byte_what_they_always_were():
    """A split-flap must see exactly what it always saw. It does — but the fold happens at
    the WALL now (renderer.fold), and a colour is its own codepoint on the way there."""
    assert renderer.fold(renderer.normalize("hello", 5)) == "HELLO"
    assert "".join(renderer.for_legacy(c) for c in renderer.normalize("🟥🟩", 2)) == "rg"
    anim = renderer.normalize("www ggg", 7, frame=True)
    assert "".join(renderer.for_legacy(c) for c in anim) == "www ggg"


# ---------------------------------------------------------------------------
# the wire format
# ---------------------------------------------------------------------------
class _FakeClient:
    def __init__(self):
        self.posts = []

    async def post(self, path, content=None, headers=None, timeout=None):
        self.posts.append((path, json.loads(content.decode("utf-8"))))

        class R:
            status_code = 200

            def raise_for_status(self):
                pass
        return R()


def _rich_transport():
    t = RestTransport("http://gw")
    t._client = _FakeClient()
    t.caps = device.MATRIX_PORTAL
    return t


@pytest.mark.anyio
async def test_cells_carry_lowercase_named_colours_and_pictographs():
    t = _rich_transport()
    page = renderer.normalize("He♥ 🟥", 5)
    await t.send_batch(list(enumerate(page)), 10)

    path, body = t._client.posts[0]
    assert path == "/api/display/cells"
    assert body["start"] == 0
    assert body["cells"][0] == {"ch": "H"}
    assert body["cells"][1] == {"ch": "e"}, "lowercase must survive — that is the point"
    assert body["cells"][2] == {"ch": "♥"}, "a pictograph goes as itself"
    assert body["cells"][4] == {"color": "red"}, "colours are NAMED on this path"


@pytest.mark.anyio
async def test_an_unrenderable_character_is_never_sent():
    """The firmware 400s the whole page for one bad glyph — correctly, but that would mean
    a single stray emoji from an app blanks the wall."""
    t = _rich_transport()
    page = renderer.normalize("A\U0001f600B", 3)     # 😀 has no flap
    await t.send_batch(list(enumerate(page)), 0)

    _, body = t._client.posts[0]
    assert body["cells"] == [{"ch": "A"}, {"ch": " "}, {"ch": "B"}]


@pytest.mark.anyio
async def test_unchanged_cells_are_skipped():
    """A clock moving one digit should move one flap, not repaint seventy-five."""
    t = _rich_transport()
    await t.send_batch(list(enumerate("12:00")), 0)
    t._client.posts.clear()

    await t.send_batch(list(enumerate("12:01")), 0)
    _, body = t._client.posts[0]
    assert [c for c in body["cells"] if "skip" not in c] == [{"ch": "1"}]
    assert sum(1 for c in body["cells"] if c.get("skip")) == 4


@pytest.mark.anyio
async def test_an_unchanged_page_sends_nothing_at_all():
    t = _rich_transport()
    await t.send_batch(list(enumerate("HELLO")), 0)
    t._client.posts.clear()
    await t.send_batch(list(enumerate("HELLO")), 0)
    assert t._client.posts == []


@pytest.mark.anyio
async def test_a_failure_forces_the_next_page_to_be_sent_whole():
    """After an error the wall is in an unknown state, so `skip` is a lie."""
    t = _rich_transport()
    await t.send_batch(list(enumerate("HELLO")), 0)

    class Boom:
        async def post(self, *a, **k):
            raise RuntimeError("gateway went away")
    t._client = Boom()
    with pytest.raises(RuntimeError):
        await t.send_batch(list(enumerate("WORLD")), 0)
    assert t._shown == {}, "we still think we know what is on the wall"


@pytest.mark.anyio
async def test_a_legacy_wall_still_gets_the_old_protocol():
    t = RestTransport("http://gw")
    t._client = _FakeClient()
    t.caps = device.SPLIT_FLAP                       # a physical split-flap
    await t.send_batch([(0, "A"), (1, "r")], 5)

    path, body = t._client.posts[0]
    assert path == "/api/rs485/batch"
    assert "frames" in body and "cells" not in body


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# the bug this whole design exists to prevent
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_hello_does_not_come_out_as_hell_orange():
    """The letter `o` is not the ORANGE FLAP. A transport that decides a cell is a colour
    because the character happens to be one of the seven colour letters renders "Hello" as
    "Hell<orange>" — which is exactly what it did until the page was made to say which it
    meant."""
    t = _rich_transport()
    # NB: a keep-case page is NOT colorized — in it, a lowercase letter is a letter. That
    # is the whole distinction, and colorizing it here would recreate the bug.
    page = renderer.normalize("Hello world", 11)
    await t.send_batch(list(enumerate(page)), 0)

    _, body = t._client.posts[0]
    assert [c.get("ch") for c in body["cells"]] == list("Hello world")
    assert not any("color" in c for c in body["cells"])


@pytest.mark.anyio
async def test_a_legacy_page_still_gets_its_colour_flaps():
    """The other half: weather's 🟩 and stocks' 🟥 are COLOURS, and they must stay colours
    on a Matrix Portal — the fix for the letters must not cost us the colours."""
    t = _rich_transport()
    page = (renderer.normalize("AQI 🟩", 5))
    await t.send_batch(list(enumerate(page)), 0)

    _, body = t._client.posts[0]
    assert body["cells"][:3] == [{"ch": "A"}, {"ch": "Q"}, {"ch": "I"}]
    assert body["cells"][4] == {"color": "green"}


@pytest.mark.anyio
async def test_an_animations_colour_codes_survive():
    """art-clock and the anim_* apps draw with lowercase colour codes in a RAW page. That
    convention is splitflap-os's, and it has to keep working."""
    t = _rich_transport()
    page = (renderer.normalize("rgb", 3, frame=True))
    await t.send_batch(list(enumerate(page)), 0)

    _, body = t._client.posts[0]
    assert body["cells"] == [{"color": "red"}, {"color": "green"}, {"color": "blue"}]


# ---------------------------------------------------------------------------
# apps no longer shout; the WALL decides
# ---------------------------------------------------------------------------
# An app used to call .upper() on everything it displayed. That was always redundant — the
# companion folds a non-raw page anyway (renderer.normalize) — and once a wall could show
# lowercase it became actively harmful: the case was destroyed before the display ever got
# a say. So the apps hand over the text as written, and the fold happens at the one place
# that knows what the wall can do.
#
# The consequence to protect: a PHYSICAL wall must render byte-for-byte what it did before.
def test_a_physical_wall_still_gets_uppercase():
    from app import renderer
    page = "Martin Luther King Jr. Day"
    assert renderer.fold(renderer.normalize(page, 26)) == "MARTIN LUTHER KING JR. DAY"


def test_a_matrix_portal_gets_it_as_written():
    from app import renderer
    page = "Martin Luther King Jr. Day"
    assert renderer.normalize(page, 26) == "Martin Luther King Jr. Day"


def test_folding_is_the_only_difference():
    """Whatever an app emits, the physical wall's page is exactly the rich page uppercased.
    If that ever stops being true, an app has started doing something other than case."""
    from app import renderer
    for page in ("Manufacturers Trust Company Building", "Eastern  6:33PM", "July 13"):
        rich = renderer.normalize(page, 40)
        assert renderer.fold(rich) == renderer.cp1252_upper(rich)


def test_an_animation_is_never_touched():
    """An animation's lowercase is a COLOUR, not a letter — so folding must not reach it. It
    is the one place an app still uppercases its own text, because nothing else will."""
    from app import renderer
    anim = renderer.normalize("www rgb", 7, frame=True)
    assert renderer.fold(anim) == anim, "the fold ate a colour flap"
    assert "".join(renderer.for_legacy(c) for c in anim) == "www rgb"


@pytest.mark.parametrize("app_id", ["anim_matrix", "art-clock"])
def test_animation_apps_still_uppercase_their_own_text(app_id):
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "apps" / app_id / "app.py").read_text("utf-8")
    # they draw with lowercase colour codes, so nothing may fold their pages for them
    assert ".upper()" in src or "font" in src


# ---------------------------------------------------------------------------
# "raw" and "keep case" are two questions, not one
# ---------------------------------------------------------------------------
# `raw` used to mean BOTH "already laid out, do not fold it" AND "a lowercase letter is a
# colour". They are independent, and conflating them is what put an orange flap in the
# middle of "Hello world": a composed message is laid out by the caller (raw) and made of
# words (keep_case), while an ANIMATION is laid out and made of COLOURS.
def _controller(rich: bool):
    import tempfile
    from pathlib import Path

    from app.config import Config
    from app.engine import DisplayController
    from app.state import DisplayState

    cfg = Config(Path(tempfile.mkdtemp()))
    cfg.update({"grid": {"rows": 1, "cols": 16}})
    c = DisplayController(cfg, DisplayState(16))

    class T:
        caps = device.MATRIX_PORTAL if rich else device.SPLIT_FLAP
    c.transport = T()
    return c


def test_a_composed_message_keeps_its_letters():
    """The reported bug. Every o, r and w in "Hello world" was becoming a colour flap."""
    from app import renderer
    c = _controller(rich=True)
    page = c._normalize("Hello world")          # words: NOT a frame
    assert page.startswith("Hello world")
    assert not any(renderer.is_color(ch) for ch in page), "a letter became a colour flap"


def test_a_colour_tile_in_a_message_is_still_a_colour():
    from app import renderer
    c = _controller(rich=True)
    page = c._normalize("Hi 🟥", frame=True)
    assert page[:2] == "Hi"
    assert renderer.is_color(page[3])


def test_an_animation_still_paints_with_lowercase():
    """art-clock and the anim_* apps draw colours as lowercase r/o/y/g/b/p/w. Keeping the
    case there would turn a red flap into the letter r — the exact inverse of the bug."""
    from app import renderer
    c = _controller(rich=True)
    page = c._normalize("rgb", frame=True)
    assert [renderer.PUA_TO_NAME[ch] for ch in page[:3]] == ["red", "green", "blue"]


def test_a_physical_wall_ignores_all_of_this():
    c = _controller(rich=False)
    assert c._normalize("Hello world").startswith("HELLO WORLD")


# ---------------------------------------------------------------------------
# a trigger shows WORDS, unless the app is an animation
# ---------------------------------------------------------------------------
def test_a_trigger_does_not_paint_colour_flaps_through_the_words():
    """fire_interrupt() defaulted to "raw" — i.e. "a lowercase letter is a COLOUR FLAP".

    That was harmless only while every app uppercased its own output, so no lowercase letter
    could reach it. The apps stopped doing that (1.9.0-beta.13), and the default became a
    quiet corruption of every trigger: "Partly cloudy" reached the wall with its r, o and y
    replaced by a red, an orange and a yellow flap.
    """
    from app import renderer
    c = _controller(rich=True)

    words = c._normalize("Partly cloudy")
    assert not any(renderer.is_color(ch) for ch in words), "a letter became a colour flap"
    assert words.startswith("Partly cloudy")

    # …and an ANIMATION still paints, because that is the only way it can ask for a colour
    frame = c._normalize("rgb", frame=True)
    assert [renderer.PUA_TO_NAME[ch] for ch in frame[:3]] == ["red", "green", "blue"]


def test_the_scheduler_says_which_kind_of_page_it_is():
    """The engine cannot know: only the scheduler knows whether the app it is firing is an
    animation. It has to say so, and nothing else may guess."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "scheduler.py").read_text("utf-8")
    assert "frame=self.plugins.is_anim(app_id)" in src


def test_there_is_exactly_one_place_that_folds():
    """Two places folding is how a caller folds EARLY and throws away the one thing a Matrix
    Portal is for. main._cased did exactly that; the engine is the only one now."""
    from pathlib import Path
    app = Path(__file__).resolve().parents[1] / "app"
    assert "_cased" not in (app / "main.py").read_text("utf-8")
    engine = (app / "engine.py").read_text("utf-8")
    assert "renderer.fold(clean)" in engine


# ---------------------------------------------------------------------------
# "Always uppercase": what the wall CAN do vs what it WILL do
# ---------------------------------------------------------------------------
# A capability is a fact about the hardware; whether to use it is a preference. Keeping the
# two apart is what lets a Matrix Portal shout WITHOUT giving up anything else it can do:
# it is still driven by the index-addressed API, still shows its pictographs, still gets its
# colours by name. It is simply in capitals.
def _with_setting(value):
    import tempfile
    from pathlib import Path

    from app.config import Config
    from app.engine import DisplayController
    from app.plugin_settings import PluginSettings
    from app.plugins import PluginRuntime
    from app.state import DisplayState

    tmp = Path(tempfile.mkdtemp())
    cfg = Config(tmp)
    cfg.update({"grid": {"rows": 1, "cols": 20}})
    st = PluginSettings(cfg.data_dir)
    st.set("force_uppercase", value)
    c = DisplayController(cfg, DisplayState(20))
    c.attach_plugins(PluginRuntime(cfg, st, APPS, cfg.data_dir / "apps"))

    class T:
        caps = device.MATRIX_PORTAL
    c.transport = T()
    return c


APPS = __import__("pathlib").Path(__file__).resolve().parents[2] / "apps"


def test_a_matrix_portal_can_be_told_to_shout():
    c = _with_setting("yes")
    assert c.caps.lowercase is True, "the hardware can still do it"
    assert c.shows_lowercase is False, "…the user asked it not to"
    assert c._normalize("Hello").startswith("HELLO")


def test_it_is_off_by_default():
    c = _with_setting("no")
    assert c.shows_lowercase is True
    assert c._normalize("Hello").startswith("Hello")


def test_shouting_costs_nothing_else():
    """Not a fallback to the legacy protocol: the wall keeps its pictographs and its named
    colours, because those are capabilities and this is only a preference about CASE."""
    from app import renderer
    c = _with_setting("yes")
    page = c._normalize("Hi ♥ 🟥")
    assert "♥" in page, "the pictograph was thrown away with the lowercase"
    assert any(renderer.is_color(ch) for ch in page), "the colour flap was lost"
    assert c.caps.indexed is True, "it must still use the index-addressed API"


def test_a_split_flap_shouts_whatever_the_setting_says():
    """It has no lowercase flaps. The setting cannot turn some on."""
    c = _with_setting("no")

    class T:
        caps = device.SPLIT_FLAP
    c.transport = T()
    assert c.shows_lowercase is False
    assert c._normalize("Hello").startswith("HELLO")
