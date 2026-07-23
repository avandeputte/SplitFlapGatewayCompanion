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
    assert caps.lowercase and caps.pictographs and caps.named_colors and caps.indexed
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
    """Exactly the old behavior. An empty charset must NOT read as "shows nothing"."""
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


def test_a_color_is_never_degraded(fr):
    """A color is a flap INDEX, not a character; it is not in any charset and must pass
    through untouched or every animation turns to spaces."""
    page = renderer.normalize("rgb", 3, frame=True)          # -> three color sentinels
    assert renderer.degrade(page, fr) == page
    assert all(renderer.is_color(c) for c in page)


def test_the_matrix_portal_degrades_nothing_it_can_show():
    caps = device.from_capabilities(MATRIX)
    assert renderer.degrade("Prévu ♥", caps) == "Prévu ♥"


# --- ß, which needs TWO flaps ------------------------------------------------
#
# The rule, and it is not the obvious one:
#   1. an uppercase page still uses the LOWERCASE ß, because that is the flap reels carry —
#      there is no uppercase ẞ flap;
#   2. and a reel with no ß at all gets SS. Two flaps. Not "S": a single S is not a shorter
#      spelling of SS, it is a misspelling ("STRASE").
#
# Two flaps means the substitution can only happen while the text is still TEXT — before it is
# centered onto the grid, where one character is one module. That is expand(); degrade() runs
# later, on the finished page, and must never truncate a two-character stand-in to get it to
# fit.

DE_REEL = " ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜß0123456789€!?.,'-:%&()@#+="


def _wall(text, caps, shows_lowercase):
    """Text -> what the modules receive, in the real order: expand, fold, degrade."""
    out = renderer.expand(text, caps)
    if not shows_lowercase:
        out = renderer.fold(out)
    return renderer.degrade(out, caps)


@pytest.fixture
def de():
    return device.from_capabilities({
        "features": ["colors"], "charset": {"uniform": True, "common": DE_REEL}})


def test_an_uppercase_page_still_uses_the_lowercase_flap(de):
    """Reels carry ß, not ẞ. So an all-caps German page shows ß, and that is correct."""
    assert _wall("Straße", de, shows_lowercase=False) == "STRAßE"
    assert _wall("STRAẞE", de, shows_lowercase=False) == "STRAßE"


def test_a_reel_with_no_eszett_gets_SS_not_S(fr):
    """The documented German fallback, and the reason expand() exists at all."""
    assert _wall("Straße", fr, shows_lowercase=False) == "STRASSE"
    assert _wall("STRAẞE", fr, shows_lowercase=False) == "STRASSE"


def test_the_expansion_happens_while_the_text_can_still_grow(fr):
    """Six characters in, seven out. If this ran on the finished page instead, the extra S
    would shift every cell after it and re-wrap the line."""
    assert renderer.expand("Straße", fr) == "StraSSe"
    assert len(renderer.expand("Straße", fr)) == 7


def test_degrade_never_truncates_a_two_flap_standin_to_one(fr):
    """The bug this guards: ß.upper() is "SS", and taking its first letter puts the
    misspelling "STRASE" on someone's wall — silently, which is worse than the hole it was
    avoiding.

    Reached directly, with no expand() in front of it, degrade() has a fixed cell and no room
    for SS. The honest answer is a space, NOT half of a two-letter spelling.
    """
    page = renderer.fold("Straße")            # ß survives the fold, as it must
    assert renderer.degrade(page, fr) == "STRA E"


def test_ae_and_oe_expand_the_same_way(fr):
    """Æ and Œ are the same shape of problem: two flaps, or nothing."""
    assert renderer.expand("Æon", fr) == "AEon"
    assert renderer.expand("Œuvre", fr) == "OEuvre"
    assert _wall("Æon", fr, shows_lowercase=False) == "AEON"


def test_a_reel_that_has_the_character_is_left_alone(de):
    assert renderer.expand("Straße", de) == "Straße"


# --- the feature list is a list, not a taxonomy -------------------------------
#
# These two are the REAL feature lists, copied verbatim from a physical Split-Flap Gateway
# (fw 3.7.4) and a Matrix Portal (fw 1.10.1). They are here because reading them wrong took a
# working wall down: the physical gateway advertises "index", the companion read that as "has
# the index-addressed page API", and posted every page to /api/display/cells — which that
# gateway does not have. 404 on every write, display shown as offline, while the gateway
# answered /api/status, /api/config, /api/capabilities and the whole proxied UI perfectly.
#
# "index" is POST /api/flap/index {"id":5,"index":3} — turn ONE module to a flap by number.
# Every gateway has it. "cells" is POST /api/display/cells — the bulk page API. Only a Matrix
# Portal has it. They are different endpoints and only one of them picks the wire format.

PHYSICAL_FEATURES = ["colors", "index", "batch", "quiet", "maintenance", "ha", "ota", "flapconfig"]
MATRIX_FEATURES = ["cells", "colors", "index", "lowercase", "pictographs",
                   "quiet", "maintenance", "ha", "ota"]


def test_a_physical_gateway_advertising_index_is_NOT_index_addressed():
    """The regression. `index` != `cells`, and mistaking them 404s every page."""
    caps = device.from_capabilities({
        "product": "Split-Flap Gateway", "fw": "3.7.4",
        "features": PHYSICAL_FEATURES,
        "charset": {"uniform": False, "common": " ABC0123456789"},
    })
    assert caps.indexed is False, (
        "a physical gateway has /api/flap/index but NOT /api/display/cells — posting a page "
        "to the cells endpoint 404s and the wall goes dark")
    assert caps.lowercase is False and caps.pictographs is False


def test_only_cells_means_the_bulk_page_api():
    assert device.from_capabilities({"features": ["cells"], "charset": {}}).indexed is True
    assert device.from_capabilities({"features": ["index"], "charset": {}}).indexed is False
    assert device.from_capabilities({"features": ["batch"], "charset": {}}).indexed is False


def test_a_real_matrix_portal_still_uses_the_cells_api():
    caps = device.from_capabilities({
        "product": "Matrix Portal Gateway", "fw": "1.10.1",
        "features": MATRIX_FEATURES,
        "charset": {"uniform": True, "common": " ABCabc"},
    })
    assert caps.indexed and caps.lowercase and caps.pictographs


def test_a_physical_gateway_advertising_colors_does_not_change_the_wire():
    """It has color FLAPS (r/o/y/g/b/p/w), which is not the same as naming them in an API.
    Only `indexed` selects the wire format, so this must not drag the cells API in with it."""
    caps = device.from_capabilities({
        "features": PHYSICAL_FEATURES, "colors": ["red", "blue"], "charset": {}})
    assert caps.named_colors is True
    assert caps.indexed is False
