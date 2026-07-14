"""The wall is ASKED what it can do, not guessed at.

The companion used to infer a display's alphabet from its product name and firmware version.
That could not see a PHYSICAL wall's reel at all — so the companion sent a character and hoped,
and a module asked for a flap it does not carry HOMES. A blank hole in the middle of a word,
reported by nothing. It is the reason the shipped translations were written in stripped ASCII.

Gateways now answer GET /api/capabilities with their feature list and, crucially, their
charset: `common` is what EVERY module can show. So we ask, and what the wall cannot show we
degrade to the nearest thing it can.
"""

from __future__ import annotations

import pytest

from app import device, renderer

# A physical French wall: 64 flaps, spending thirteen of them on the accents French needs —
# and therefore carrying no colon, no asterisk and no lowercase.
FR_REEL = " ABCDEFGHIJKLMNOPQRSTUVWXYZÀÂÇÈÉÊËÎÏÔÙÛÜ0123456789€!?.,'-"

FR_WALL = {
    "product": "SplitFlap Gateway",
    "features": ["colors"],
    "colors": ["red", "orange", "yellow", "green", "blue", "purple", "white"],
    "charset": {"uniform": True, "common": FR_REEL, "union": FR_REEL},
}

# A Matrix Portal, as the real one answers: everything, 237 flaps.
MATRIX = {
    "product": "Matrix Portal Gateway",
    "features": ["cells", "colors", "index", "lowercase", "pictographs"],
    "colors": ["red", "orange", "yellow", "green", "blue", "purple", "white"],
    "charset": {"uniform": True,
                "common": " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
                          ":.,'-éÉ♥→"},
}


# --- reading the document ---------------------------------------------------

def test_a_matrix_portal_reports_what_it_can_do():
    caps = device.from_capabilities(MATRIX)
    assert caps.lowercase and caps.pictographs and caps.named_colours and caps.indexed
    assert caps.can_show("é") and caps.can_show("♥")


def test_a_physical_wall_reports_its_reel():
    caps = device.from_capabilities(FR_WALL)
    assert not caps.lowercase and not caps.pictographs and not caps.indexed
    assert caps.can_show("É"), "the French reel carries É — that is what it is for"
    assert not caps.can_show(":"), "and it has no colon"


def test_a_mixed_wall_reports_the_INTERSECTION():
    """`union` is what SOME module can show, which is precisely the wrong answer to "is this
    safe to send" — a character only half the wall carries punches holes in the other half."""
    caps = device.from_capabilities({
        "features": [],
        "charset": {"uniform": False, "common": "ABC", "union": "ABCDEF"},
    })
    assert not caps.uniform
    assert caps.can_show("A") and not caps.can_show("D")


def test_a_gateway_too_old_to_answer_falls_back_to_the_guess():
    """No /api/capabilities -> None, so the caller uses the old product-name inference and
    keeps working exactly as it did."""
    assert device.from_capabilities(None) is None
    assert device.from_capabilities({"nothing": "useful"}) is None
    assert device.from_capabilities("<html>404</html>") is None      # a proxy error page

    # …and the old inference still works.
    assert device.of({"product": "Matrix Portal Gateway", "fwVersion": "1.7.0"}).indexed
    assert not device.of({"product": "SplitFlap Gateway", "fwVersion": "3.1"}).indexed


def test_an_unknown_charset_means_send_it_and_hope():
    """Exactly the old behaviour. An empty charset must NOT read as "shows nothing"."""
    caps = device.of({"product": "SplitFlap Gateway"})
    assert not caps.knows_charset()
    assert caps.can_show("é") and caps.can_show("♥")
    assert renderer.degrade("Prévu ♥", caps) == "Prévu ♥", "nothing may be degraded blind"


# --- degrading --------------------------------------------------------------

@pytest.fixture
def fr():
    return device.from_capabilities(FR_WALL)


def test_an_accent_the_reel_HAS_survives(fr):
    """The point of the whole exercise. É is on the French reel, so "PRÉVU" goes out as
    "PRÉVU" — not the stripped "PREVU" the translations used to be written in."""
    assert renderer.degrade(renderer.fold("Prévu"), fr) == "PRÉVU"


def test_an_accent_the_reel_LACKS_degrades_to_its_base_letter(fr):
    """A Swedish Å on a French wall: better an A than a hole."""
    assert renderer.degrade(renderer.fold("Åre"), fr) == "ARE"


def test_typographic_punctuation_becomes_ascii(fr):
    """A news feed sends curly quotes and an em dash. No reel carries them."""
    assert renderer.degrade(renderer.fold("Dinner — 7pm"), fr) == "DINNER - 7PM"
    assert renderer.degrade(renderer.fold("it’s"), fr) == "IT'S"


def test_a_colon_becomes_a_period_on_a_reel_that_has_no_colon(fr):
    """"15.30" still reads as a time. "15 30" reads as a fault."""
    assert renderer.degrade(renderer.fold("15:30"), fr) == "15.30"


def test_a_pictograph_with_no_stand_in_becomes_a_space(fr):
    """The heart's documented fallback is `*`, which the French reel does not carry either.
    A deliberate space, then — which is what the module would have done anyway, except now
    we know about it."""
    assert renderer.degrade(renderer.fold("A ♥ B"), fr) == "A   B"


def test_a_colour_is_never_degraded(fr):
    """A colour is a flap INDEX, not a character; it is not in any charset and must pass
    through untouched or every animation turns to spaces."""
    page = renderer.normalize("rgb", 3, frame=True)          # -> three colour sentinels
    assert renderer.degrade(page, fr) == page
    assert all(renderer.is_color(c) for c in page)


def test_the_matrix_portal_degrades_nothing_it_can_show():
    caps = device.from_capabilities(MATRIX)
    assert renderer.degrade("Prévu ♥", caps) == "Prévu ♥"
