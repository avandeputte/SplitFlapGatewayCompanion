"""device.py — what a wall CAN DO, asked rather than assumed.

There are several kinds of wall — a physical split-flap, a Matrix Gateway drawing the same
modules on an LED panel, and anything in between, including a MIXED wall whose modules do not
all carry the same reel. They do not have the same alphabet, and the difference is not
cosmetic. From the Matrix Gateway firmware's own reel.h:

    The legacy wire carries ONE BYTE per character, and it has a problem it can never
    solve: the byte for lowercase 'r' already means RED. So on that path lowercase must
    fold to uppercase, and a heart -- which has no Windows-1252 byte -- cannot be
    addressed by character in ANY way.

ASKED, NOT INFERRED
-------------------
Inferring capabilities from ``GET /api/config`` — the product name implying lowercase,
pictographs and named colors — is wrong in every direction that matters:

  * it cannot see a PHYSICAL wall's reel at all, so the companion has no idea which
    characters that wall can actually show. It sends the character and hopes. A module asked
    for a flap it does not carry simply HOMES — a blank hole in the middle of a word — and
    nothing reports it. On that path only ASCII is safe: `é` is a gamble nobody can take;
  * it assumes every module on a wall carries the same reel, which a wall built from two
    batches does not;
  * and it hard-codes a version number, so the next firmware to gain a capability would
    need an edit to this file.

Gateways therefore answer ``GET /api/capabilities``, and it says what the wall can do:

    {"features": ["cells", "colors", "index", "lowercase", "pictographs", ...],
     "colors":   ["red", "orange", ...],
     "charset":  {"uniform": true,
                  "common": "…the characters EVERY module can show…",
                  "union":  "…the characters SOME module can show…"},
     "maxFlaps": 237}

So we ask. ``common`` is the honest answer to "may I send this character": on a uniform wall it
is the reel; on a mixed wall it is the intersection, because a character only *some* modules
carry is a character that will punch a hole in the ones that do not.

The inference exists as ``of()`` and is used only when a gateway is too old to answer — it
must keep working, and on that path we are guessing and confined to ASCII.

It is a property of the GATEWAY ON THE OTHER END, not a setting: with several displays a
companion can drive a Matrix Gateway and a split-flap side by side, so it belongs to the
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
    named_colors: bool  # colors are named, rather than spelled with r/o/y/g/b/p/w
    indexed: bool = False  # it takes POST /api/display/cells (address a flap by INDEX)

    # How the wall MOVES — from the capabilities document's `motion` key (physical
    # Gateway 3.10+; every current Matrix firmware states it). "drawn": a cell is a repaint, interruptible, nothing queues —
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
    # Reserved for the app contract (apps/README.md documents named_colors as app-facing);
    # no bundled consumer yet.
    colors: tuple[str, ...] = ()

    # CANVAS — a Matrix wall with a framebuffer can draw ANYTHING, free of the flap grid:
    # arbitrary pixels/lines/rects/text (POST /api/canvas/ops), a full raw frame
    # (PUT /api/canvas/frame), or an on-device effect that the panel renders itself at
    # ~70 fps (POST /api/canvas/effect). A physical split-flap wall has no framebuffer, so
    # these stay empty and every canvas gate is False.
    canvas_w: int = 0                       # framebuffer size (0 = no canvas)
    canvas_h: int = 0
    canvas_formats: tuple[str, ...] = ()    # raw-frame pixel formats, e.g. ("rgb888", "rgb565", "qoi")
    effects: tuple[str, ...] = ()           # on-device effect names, e.g. ("plasma", "fire", "matrix")
    # The canvas extras, from the capabilities canvas object; a key the document omits
    # parses False/empty, and the companion falls back to the raw-frame path.
    canvas_rect: bool = False               # PUT /api/canvas/rect — update one rectangle only
    canvas_anim: bool = False               # PUT /api/canvas/anim — upload a loop, plays on-device
    canvas_ticker: bool = False             # POST /api/canvas/ticker — on-device scrolling text
    effect_params: tuple[str, ...] = ()     # effect knobs, e.g. ("hue", "density")
    # Self-describing effects ("effectDefs"): one entry per effect declaring exactly
    # the params it consumes (key/type/min/max/default/label), so effect UIs are built from
    # the wall's own description. The flat effect_params union is derived from these (the sole source).
    effect_defs: tuple = ()

    # `fw_version` is the wall's firmware, parsed from the capabilities document's `fw`
    # field ((0, 0) = not stated) — informational (the panel-caps display), never a gate:
    # the companion supports exactly the current Matrix firmware.
    fw_version: tuple[int, int] = (0, 0)
    canvas_readback: bool = False           # GET /api/canvas/frame — read the lit panel back
    canvas_ops: tuple[str, ...] = ()        # POST /api/canvas/ops draw ops the wall honors
    canvas_ops_bin: bool = False            # the binary ops encoding ("opsBin"), whole
    canvas_composite: bool = False          # canvas.compositing: per-color alpha, blend modes, AA
    # PUT /api/canvas/rects — a frame-push app sends only the rectangles that changed since
    # its last frame instead of the whole panel. Advertised as canvas.rects.
    canvas_rects: bool = False
    # PUT /api/canvas/stream — a persistent TLV draw channel. One long-lived connection carries
    # frame/rect/ops records back-to-back, no per-frame HTTP round trip. Advertised as canvas.stream.
    canvas_stream: bool = False
    events: bool = False                    # GET /api/events — the gateway's own SSE display stream
                                            # (parsed for completeness; no companion consumer yet)
    can_sound: bool = False                 # POST /api/sound — on-device tone synthesis (the "sound" feature)
    can_sd: bool = False                    # /api/sd/* — a microSD card is mounted (the "sd" feature)
    can_timer: bool = False                 # /api/timer — the on-device kitchen timer (the "timer" feature)
    can_alarms: bool = False                # /api/alarms — the four daily alarm slots (the "alarms" feature)

    def __bool__(self) -> bool:
        return self.indexed

    @property
    def canvas_sprite(self) -> bool:
        """Whether the ops path can blit atlas sprites — the `sprite` op, fed by the named
        atlas library (PUT /api/canvas/atlas/<name>). Carried in the ops vocabulary."""
        return "sprite" in self.canvas_ops

    @property
    def canvas_endpoints(self) -> bool:
        """The canvas endpoint families that are not separately advertised — the overlay
        ticker, frame transitions, the animation/font libraries, GIF import, the boot
        splash. Every current Matrix firmware has them: they come with the framebuffer."""
        return self.has_canvas

    @property
    def has_canvas(self) -> bool:
        """Can this wall draw arbitrary graphics — pixels, a raw frame — bypassing the
        flap grid entirely? Only a Matrix wall with a framebuffer."""
        return self.canvas_w > 0 and self.canvas_h > 0

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

        Unknown charset -> True, deliberately: send it and hope — the inference path's
        behavior — is better than silently blanking text on a wall we simply have not asked.
        """
        if not self.charset:
            return True
        return ch in self.charset


# The inference path's assumed real reel: 64 leaves, one byte per character, seven of its letters
# spent on colors, and no idea which characters are actually printed on it.
SPLIT_FLAP = Capabilities(lowercase=False, pictographs=False, named_colors=False, indexed=False,
                          motion="mechanical", settle_ms=4000)

# Drawn modules: nothing to ration. No production consumer — of() never infers Matrix;
# kept as the canonical Matrix baseline the test suite builds walls from.
MATRIX_GATEWAY = Capabilities(lowercase=True, pictographs=True, named_colors=True, indexed=True,
                             motion="drawn")


def of(gateway_config: dict | None) -> Capabilities:
    """THE FALLBACK. What the gateway probably is, from its ``GET /api/config``.

    Only for a gateway too old to answer /api/capabilities — which, with every current
    Matrix firmware answering it, means an old PHYSICAL gateway: assume the split-flap.
    (A Matrix wall that fails the capabilities probe is a transient, and the probe retries
    on resync — inferring Matrix powers from a config document is legacy support the
    companion no longer carries.)
    """
    return SPLIT_FLAP


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

    colors = tuple(str(c) for c in (doc.get("colors") or []) if isinstance(c, str))

    # The wall's own statement about how it moves (physical Gateway 3.10+; every current
    # Matrix firmware states it). Only
    # the two known kinds are accepted; anything else counts as "not reported", so `instant`
    # falls back to inference rather than trusting a typo.
    motion = doc.get("motion") if isinstance(doc.get("motion"), dict) else {}
    kind = motion.get("kind") if motion.get("kind") in ("drawn", "mechanical") else ""
    try:
        settle = int(motion["settleMs"])
    except (KeyError, TypeError, ValueError):
        settle = None

    # The canvas framebuffer and the on-device effect set. Both are
    # objects/lists the gateway only emits when it HAS them; a physical wall omits them and
    # every canvas gate stays False. The feature flags "canvas"/"effects" corroborate but the
    # canvas dimensions are what actually decide can-it-draw.
    canvas = doc.get("canvas") if isinstance(doc.get("canvas"), dict) else {}
    try:
        canvas_w, canvas_h = int(canvas.get("width") or 0), int(canvas.get("height") or 0)
    except (TypeError, ValueError):
        canvas_w = canvas_h = 0
    canvas_formats = tuple(str(f) for f in (canvas.get("formats") or []) if isinstance(f, str))
    effects = tuple(str(e) for e in (doc.get("effects") or []) if isinstance(e, str))
    effect_defs = tuple(d for d in (doc.get("effectDefs") or [])
                        if isinstance(d, dict) and isinstance(d.get("id"), str)
                        and isinstance(d.get("params"), list))
    # The flat knob union (canvas.effect_params): derived from effectDefs — the sole
    # source — as the union of the defs' param keys minus "speed".
    seen: list[str] = []
    for d in effect_defs:
        for p in d.get("params") or []:
            k = str(p.get("key") or "")
            if k and k != "speed" and k not in seen:
                seen.append(k)
    effect_params = tuple(seen)
    # The draw-op vocabulary and the panel readback flag, advertised directly. The
    # ops list is what gates a specific op the app is about to send — an unknown op is skipped by
    # the firmware, but knowing up front lets an app choose the frame path instead.
    canvas_ops = tuple(str(o) for o in (canvas.get("ops") or []) if isinstance(o, str))
    try:
        canvas_ops_bin = bool(canvas.get("opsBin"))
    except (TypeError, ValueError):
        canvas_ops_bin = False
    canvas_composite = bool(canvas.get("compositing"))
    canvas_readback = bool(canvas.get("readback"))
    # `fw` is the firmware version string; take the leading major.minor. Informational
    # only (surfaced by /api/panel/caps) — never a gate.
    fw_version = _parse_fw(doc.get("fw"))

    return Capabilities(
        lowercase="lowercase" in features,
        pictographs="pictographs" in features,
        named_colors="colors" in features or bool(colors),
        # ONLY "cells". This is the one flag that picks the WIRE FORMAT, and it names an
        # endpoint: POST /api/display/cells, the bulk index-addressed page API, which only a
        # Matrix Gateway has.
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
        colors=colors,
        motion=kind,
        settle_ms=settle,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        canvas_formats=canvas_formats,
        effects=effects,
        canvas_rect=bool(canvas.get("rect")),
        canvas_anim=bool(canvas.get("anim")),
        canvas_ticker=bool(canvas.get("ticker")),
        effect_params=effect_params,
        effect_defs=effect_defs,
        canvas_ops_bin=canvas_ops_bin,
        canvas_composite=canvas_composite,
        fw_version=fw_version,
        canvas_readback=canvas_readback,
        canvas_ops=canvas_ops,
        canvas_rects=bool(canvas.get("rects")),
        canvas_stream=bool(canvas.get("stream")),
        can_sound="sound" in features,
        can_sd="sd" in features,
        can_timer="timer" in features,
        can_alarms="alarms" in features,
        events="events" in features,
    )


def _parse_fw(fw: object) -> tuple[int, int]:
    """(major, minor) from a firmware version string like ``"2.1.0"``; ``(0, 0)`` when it is
    absent or unparseable — which reads as "too old to have the version-gated features"."""
    if not isinstance(fw, str):
        return (0, 0)
    parts = fw.strip().lstrip("vV").split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return (0, 0)
