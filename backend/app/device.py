"""device.py — what a wall CAN DO, in one place.

There are two kinds of wall now, and they do not have the same alphabet:

  * a **split-flap** — real modules on a real reel, driven by a one-byte protocol;
  * a **Matrix Portal** — the same modules DRAWN on a HUB75 panel, so nothing is rationed:
    237 flaps instead of 64, every Windows-1252 glyph, every lowercase letter, and fourteen
    pictographs.

The difference is not cosmetic, and it is worth stating precisely, because it is the reason
several things in this codebase look the way they do. From the firmware's own reel.h:

    The legacy wire carries ONE BYTE per character, and it has a problem it can never
    solve: the byte for lowercase 'r' already means RED. So on that path lowercase must
    fold to uppercase, and a heart -- which has no Windows-1252 byte -- cannot be
    addressed by character in ANY way.

So the capability is not "a nicer font". It is that a Matrix Portal addresses flaps by
INDEX (POST /api/display/cells), which frees the lowercase and pictograph flaps that were
always on the reel and simply unreachable, and NAMES its colours instead of spending seven
letters on them.

This module exists because that knowledge had spread out under three different names —
`supports_cells` in gateway.py, `transport.cells`, `controller.rich` — which is three ways
of asking one question, and no way of asking a second one when the firmware grows.

It is a property of the GATEWAY ON THE OTHER END, not a setting: with several displays a
companion can drive a Matrix Portal and a split-flap side by side, so it belongs to the
display, and every display answers for itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Capabilities:
    """What the wall on the other end can show."""

    lowercase: bool     # it has lowercase flaps, and a way to address them
    pictographs: bool   # the fourteen extra flaps (heart, sun, arrows...)
    named_colours: bool # colours are named, rather than spelled with r/o/y/g/b/p/w

    @property
    def indexed(self) -> bool:
        """Whether the wall takes POST /api/display/cells (the index-addressed API).

        The three capabilities above all come from that one endpoint — they are the same
        fact seen from three sides — but naming them separately is what lets a caller say
        WHY it is asking, and lets the firmware grow one without the others.
        """
        return self.named_colours

    def __bool__(self) -> bool:
        return self.indexed


# A real reel: 64 leaves, one byte per character, and seven of its letters spent on colours.
SPLIT_FLAP = Capabilities(lowercase=False, pictographs=False, named_colours=False)

# Drawn modules: nothing to ration.
MATRIX_PORTAL = Capabilities(lowercase=True, pictographs=True, named_colours=True)


def of(gateway_config: dict | None) -> Capabilities:
    """What the gateway at the other end can do, from its GET /api/config.

    Keyed on the product and its firmware version, NOT on the gateway API level: that stays
    3.1 for both (it is the API level, not the firmware's), so keying off it would claim the
    capability for every 3.1 gateway — including every physical wall.
    """
    gw = gateway_config or {}
    if "matrix portal" not in str(gw.get("product") or "").lower():
        return SPLIT_FLAP
    m = re.search(r"(\d+)\.(\d+)", str(gw.get("fwVersion") or ""))
    if not m or (int(m.group(1)), int(m.group(2))) < (1, 6):
        return SPLIT_FLAP        # the index-addressed API arrived in firmware 1.6
    return MATRIX_PORTAL
