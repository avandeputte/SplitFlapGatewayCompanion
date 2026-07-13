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

from app import renderer
from app.gateway import supports_cells
from app.transport.rest import RestTransport


# ---------------------------------------------------------------------------
# capability — a property of the gateway on the other end
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("gw,expected", [
    ({"product": "Matrix Portal Gateway", "fwVersion": "1.7.0"}, True),
    ({"product": "Matrix Portal Gateway", "fwVersion": "1.6.0"}, True),
    ({"product": "Matrix Portal Gateway", "fwVersion": "1.5.9"}, False),   # predates it
    ({"product": "SplitFlap Gateway", "fwVersion": "3.4.0"}, False),       # a real wall
    ({}, False),
])
def test_only_a_matrix_portal_has_the_index_api(gw, expected):
    assert supports_cells(gw) is expected


def test_the_api_level_is_not_the_capability():
    """The gateway API version stays 3.1 — that is the API level, not the firmware's. Keying
    off it would claim the capability for every 3.1 gateway, including physical walls."""
    assert supports_cells({"version": "3.1.0", "product": "SplitFlap Gateway"}) is False


# ---------------------------------------------------------------------------
# a colour has to stop being a letter
# ---------------------------------------------------------------------------
def test_a_colour_tile_becomes_a_colour_not_a_letter_when_case_is_kept():
    """`🟥 hello` on a rich wall: the tile is a COLOUR and the `h` `e` `l` `l` `o` are
    LETTERS. Carrying the colour as `r` — as the legacy encoding does — would make it
    indistinguishable from the r in a word."""
    out = renderer.normalize("🟥 red", 5, keep_case=True)
    assert renderer.is_color(out[0])
    assert out[1:].rstrip() == " red"               # the letters are letters
    assert not any(renderer.is_color(c) for c in out[1:])


def test_legacy_pages_are_byte_for_byte_what_they_always_were():
    """Everything that is not a rich wall must be untouched by any of this."""
    assert renderer.normalize("hello", 5) == "HELLO"
    assert renderer.normalize("🟥🟩", 2) == "rg"
    assert renderer.normalize("www ggg", 7, raw=True) == "www ggg"   # an animation's colours


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
    t.cells = True
    return t


@pytest.mark.anyio
async def test_cells_carry_lowercase_named_colours_and_pictographs():
    t = _rich_transport()
    page = renderer.normalize("He♥ 🟥", 5, keep_case=True)
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
    page = renderer.normalize("A\U0001f600B", 3, keep_case=True)     # 😀 has no flap
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
    t.cells = False                                  # a physical split-flap
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
    page = renderer.normalize("Hello world", 11, keep_case=True)
    await t.send_batch(list(enumerate(page)), 0)

    _, body = t._client.posts[0]
    assert [c.get("ch") for c in body["cells"]] == list("Hello world")
    assert not any("color" in c for c in body["cells"])


@pytest.mark.anyio
async def test_a_legacy_page_still_gets_its_colour_flaps():
    """The other half: weather's 🟩 and stocks' 🟥 are COLOURS, and they must stay colours
    on a Matrix Portal — the fix for the letters must not cost us the colours."""
    t = _rich_transport()
    page = renderer.colorize(renderer.normalize("AQI 🟩", 5))
    await t.send_batch(list(enumerate(page)), 0)

    _, body = t._client.posts[0]
    assert body["cells"][:3] == [{"ch": "A"}, {"ch": "Q"}, {"ch": "I"}]
    assert body["cells"][4] == {"color": "green"}


@pytest.mark.anyio
async def test_an_animations_colour_codes_survive():
    """art-clock and the anim_* apps draw with lowercase colour codes in a RAW page. That
    convention is splitflap-os's, and it has to keep working."""
    t = _rich_transport()
    page = renderer.colorize(renderer.normalize("rgb", 3, raw=True))
    await t.send_batch(list(enumerate(page)), 0)

    _, body = t._client.posts[0]
    assert body["cells"] == [{"color": "red"}, {"color": "green"}, {"color": "blue"}]
