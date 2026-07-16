"""device.py — what a wall CAN DO, asked rather than assumed.

There are several kinds of wall now — a physical split-flap, a Matrix Portal drawing the same
modules on an LED panel, and anything in between, including a MIXED wall whose modules do not
all carry the same reel. They do not have the same alphabet, and the difference is not
cosmetic. From the Matrix Portal firmware's own reel.h:

    The legacy wire carries ONE BYTE per character, and it has a problem it can never
    solve: the byte for lowercase 'r' already means RED. So on that path lowercase must
    fold to uppercase, and a heart -- which has no Windows-1252 byte -- cannot be
    addressed by character in ANY way.

WHAT CHANGED, AND WHY THIS FILE LOOKS DIFFERENT NOW
---------------------------------------------------
This module used to GUESS. It read ``GET /api/config``, looked for the word "matrix portal" in
the product name and a firmware number of at least 1.6, and inferred from that that the wall
had lowercase, pictographs and named colours. That was the best available answer and it was
wrong in every direction that mattered:

  * it could not see a PHYSICAL wall's reel at all, so the companion had no idea which
    characters that wall could actually show. It sent the character and hoped. A module asked
    for a flap it does not carry simply HOMES — a blank hole in the middle of a word — and
    nothing reported it. That is why the translations had to be written in ASCII: `é` was a
    gamble nobody could take;
  * it assumed every module on a wall carries the same reel, which a wall built from two
    batches does not;
  * and it hard-coded a version number, so the next firmware to gain a capability would have
    to come and edit this file.

Gateways now answer ``GET /api/capabilities``, and it says what the wall can do:

    {"features": ["cells", "colors", "index", "lowercase", "pictographs", ...],
     "colors":   ["red", "orange", ...],
     "charset":  {"uniform": true,
                  "common": "…the characters EVERY module can show…",
                  "union":  "…the characters SOME module can show…"},
     "maxFlaps": 237}

So we ask. ``common`` is the honest answer to "may I send this character": on a uniform wall it
is the reel; on a mixed wall it is the intersection, because a character only *some* modules
carry is a character that will punch a hole in the ones that do not.

The old inference is kept as ``of()`` and used only when a gateway is too old to answer — it
must keep working, and on that path we are back to guessing and back to ASCII.

It is a property of the GATEWAY ON THE OTHER END, not a setting: with several displays a
companion can drive a Matrix Portal and a split-flap side by side, so it belongs to the
display, and every display answers for itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capabilities:
    """What the wall on the other end can show."""

    lowercase: bool      # it has lowercase flaps, and a way to address them
    pictographs: bool    # the heart, sun, arrows … flaps
    named_colours: bool  # colours are named, rather than spelled with r/o/y/g/b/p/w
    indexed: bool = False  # it takes POST /api/display/cells (address a flap by INDEX)

    # How the wall MOVES — from the capabilities document's `motion` key (Gateway 3.10+ /
    # Matrix Portal 1.12+). "drawn": a cell is a repaint, interruptible, nothing queues —
    # sub-second updates are honest. "mechanical": motion must physically complete; commands
    # queue behind it. "" means the gateway predates the key and motion must be inferred.
    motion: str = ""              # "drawn" | "mechanical" | "" (not reported)
    # Worst-case transition; a physical constraint only when mechanical. Not read yet:
    # part of the published motion-capability contract, reserved for pacing decisions.
    settle_ms: int | None = None

    # Every character the wall can show — the INTERSECTION across its modules, so sending one
    # of these is safe on all of them. Empty means "we do not know", which is what an older
    # gateway leaves us with; nothing may be degraded against an empty set, because "not in the
    # set" would then mean "everything".
    charset: frozenset[str] = field(default_factory=frozenset)
    uniform: bool = True                    # do all the modules carry the same reel?
    colours: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.indexed

    @property
    def instant(self) -> bool:
        """Whether sub-second updates are honest on this wall — a ticking seconds field, a
        fast progress bar. The wall's own `motion` statement decides when it made one; a
        gateway too old to have said falls back to the drawn-wall inference (the cells API
        only existed on drawn walls before motion was a stated fact)."""
        if self.motion:
            return self.motion == "drawn"
        return self.indexed

    def knows_charset(self) -> bool:
        """Whether the wall told us its alphabet. If it did not, we must not second-guess it:
        an unknown charset means send the character and let the module do what it does."""
        return bool(self.charset)

    def can_show(self, ch: str) -> bool:
        """Can EVERY module on this wall show this character?

        Unknown charset -> True, deliberately: that is the old behaviour (send it and hope),
        and it is better than silently blanking text on a wall we simply have not asked.
        """
        if not self.charset:
            return True
        return ch in self.charset


# A real reel, as it used to be assumed: 64 leaves, one byte per character, seven of its letters
# spent on colours, and no idea which characters are actually printed on it.
SPLIT_FLAP = Capabilities(lowercase=False, pictographs=False, named_colours=False, indexed=False,
                          motion="mechanical", settle_ms=4000)

# Drawn modules: nothing to ration. Used only as the fallback guess for a Matrix Portal too old
# to answer /api/capabilities.
MATRIX_PORTAL = Capabilities(lowercase=True, pictographs=True, named_colours=True, indexed=True,
                             motion="drawn")


def of(gateway_config: dict | None) -> Capabilities:
    """THE FALLBACK. What the gateway probably is, from its ``GET /api/config``.

    Only for a gateway too old to answer /api/capabilities. Keyed on the product and its
    firmware version, NOT on the gateway API level: that stays 3.1 for both (it is the API's
    level, not the firmware's), so keying off it would claim the capability for every 3.1
    gateway — including every physical wall.
    """
    gw = gateway_config or {}
    if "matrix portal" not in str(gw.get("product") or "").lower():
        return SPLIT_FLAP
    m = re.search(r"(\d+)\.(\d+)", str(gw.get("fwVersion") or ""))
    if not m or (int(m.group(1)), int(m.group(2))) < (1, 6):
        return SPLIT_FLAP        # the index-addressed API arrived in firmware 1.6
    return MATRIX_PORTAL


def from_capabilities(doc: dict | None) -> Capabilities | None:
    """What the wall says it can do, from ``GET /api/capabilities``.

    Returns None if the document is not one — which is how a 404 from an older gateway, or a
    proxy returning an HTML error page, ends up on the fallback path instead of being read as
    "a wall that can do nothing".
    """
    if not isinstance(doc, dict):
        return None
    feats = doc.get("features")
    charset = doc.get("charset")
    if not isinstance(feats, list) and not isinstance(charset, dict):
        return None                      # not a capabilities document at all

    features = {str(f).strip().lower() for f in (feats or [])}
    cs = charset if isinstance(charset, dict) else {}

    # `common` is what EVERY module can show. `union` is what ANY module can — which is exactly
    # the wrong answer to "is this safe to send", so it is not used for the charset. A wall that
    # is not uniform still reports both, and we take the cautious one.
    common = cs.get("common")
    if not isinstance(common, str):
        common = ""

    colours = tuple(str(c) for c in (doc.get("colors") or []) if isinstance(c, str))

    # The wall's own statement about how it moves (Gateway 3.10+ / Matrix Portal 1.12+). Only
    # the two known kinds are accepted; anything else counts as "not reported", so `instant`
    # falls back to inference rather than trusting a typo.
    motion = doc.get("motion") if isinstance(doc.get("motion"), dict) else {}
    kind = motion.get("kind") if motion.get("kind") in ("drawn", "mechanical") else ""
    try:
        settle = int(motion["settleMs"])
    except (KeyError, TypeError, ValueError):
        settle = None

    return Capabilities(
        lowercase="lowercase" in features,
        pictographs="pictographs" in features,
        named_colours="colors" in features or bool(colours),
        # ONLY "cells". This is the one flag that picks the WIRE FORMAT, and it names an
        # endpoint: POST /api/display/cells, the bulk index-addressed page API, which only a
        # Matrix Portal has.
        #
        # It is emphatically NOT "index". A physical Split-Flap Gateway advertises `index`
        # too, and means something else by it — POST /api/flap/index {"id":5,"index":3}, which
        # turns ONE module to a flap by number. Every gateway can do that. Reading `index` as
        # "has the cells API" made the companion post every page to /api/display/cells on a
        # physical wall, get a 404, and show the display as offline while the gateway sat there
        # answering everything else perfectly.
        #
        # So: the feature list is a list of things the gateway HAS, not a taxonomy to infer
        # from. Match the endpoint you are about to call, and nothing else.
        indexed="cells" in features,
        charset=frozenset(common),
        uniform=bool(cs.get("uniform", True)),
        colours=colours,
        motion=kind,
        settle_ms=settle,
    )
