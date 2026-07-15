"""The motion capability: the wall states how it moves, and `instant` reads
that statement — falling back to the drawn-wall inference (`cells`) only for
gateways too old to have made one.

The point of the field (and these tests): motion is a fact the wall declares,
not a taxonomy inferred from which endpoints exist. The day the RS-485 gateway
advertises its `cells` endpoint, nothing here starts ticking seconds at
mechanical flaps.
"""
from app import device


def _doc(**extra):
    base = {"features": ["colors", "index"], "charset": {"uniform": True, "common": "ABC"}}
    base.update(extra)
    return base


def test_a_drawn_wall_says_so_and_instant_believes_it():
    caps = device.from_capabilities(_doc(motion={"kind": "drawn", "settleMs": 3840}))
    assert caps.motion == "drawn" and caps.settle_ms == 3840
    assert caps.instant is True


def test_a_mechanical_wall_with_a_cells_endpoint_is_still_not_instant():
    # The anti-brittleness case: the RS-485 gateway HAS /api/display/cells and may
    # one day advertise it. Its motion statement must win over the wire-format flag.
    caps = device.from_capabilities(
        _doc(features=["colors", "index", "cells"],
             motion={"kind": "mechanical", "settleMs": 4000}))
    assert caps.indexed is True          # wire format: cells is fine to use
    assert caps.instant is False         # motion: still a machine, no ticking seconds
    assert caps.settle_ms == 4000


def test_an_old_gateway_without_motion_falls_back_to_the_cells_inference():
    assert device.from_capabilities(_doc(features=["cells"])).instant is True
    assert device.from_capabilities(_doc()).instant is False


def test_junk_motion_counts_as_not_reported():
    caps = device.from_capabilities(_doc(motion={"kind": "quantum", "settleMs": "soon"}))
    assert caps.motion == "" and caps.settle_ms is None
    assert caps.instant is False         # falls back: no cells in the base doc


def test_the_builtin_guesses_carry_motion():
    assert device.SPLIT_FLAP.instant is False
    assert device.SPLIT_FLAP.settle_ms == 4000
    assert device.MATRIX_PORTAL.instant is True
