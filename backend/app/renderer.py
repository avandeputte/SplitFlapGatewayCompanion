"""
renderer.py — display text normalization + per-module "send plan" ordering.

The animation orderings and colour-tile mapping are ports of the upstream
app-plugin renderer (see ATTRIBUTION.md). The companion deliberately does
**not** model a fixed flap character set: every module can carry a different
char array, and a module renders a blank for any character it lacks, so the
companion never validates, maps, or strips the characters it sends — it passes
each one through verbatim and lets the module decide.

This module is pure logic: it turns a page of text into an ordered "send plan"
of per-module frames. The actual I/O (REST / sim) and inter-step timing are
handled by the transport + engine layers, so this file stays testable with no
hardware.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# Emoji colour tiles -> the single-char firmware colour codes (r o y g b p w),
# black tile -> blank. A colour-tile syntax (not character policing).
COLOR_MAP = {
    "\U0001f7e5": "r",  # 🟥
    "\U0001f7e7": "o",  # 🟧
    "\U0001f7e8": "y",  # 🟨
    "\U0001f7e9": "g",  # 🟩
    "\U0001f7e6": "b",  # 🟦
    "\U0001f7ea": "p",  # 🟪
    "⬜": "w",      # ⬜
    "⬛": " ",      # ⬛
}

# ---------------------------------------------------------------------------
# The seven colour flaps, and why they are not simply the letters r o y g b p w.
#
# The legacy wire carries ONE BYTE per character, and it spent seven of its letters on
# colours: the byte for lowercase `r` MEANS RED. That is fine as long as everything is
# uppercased on the way out — which it was, so a lowercase letter could never occur and
# `r` was unambiguous.
#
# The Matrix Portal's index-addressed API (POST /api/display/cells) breaks that deal open:
# it can show lowercase, so `r` has to be allowed to mean the LETTER r, and colours are
# NAMED instead. Which means a page must now say which one it meant — and a bare `r` in a
# string cannot.
#
# So colours become their own codepoints inside the companion, in the Unicode private-use
# area. They are produced where a colour is unambiguously intended (an emoji tile; a
# lowercase colour code in a RAW page, which is the splitflap-os animation convention) and
# consumed by the transport, which renders them as a colour flap however its wall wants:
# the legacy byte `r`, or {"color": "red"}. Nothing else in the pipeline has to care.
COLOR_NAMES = ("red", "orange", "yellow", "green", "blue", "purple", "white")
COLOR_CODES = "roygbpw"                       # the legacy bytes, in the same order
COLOR_PUA = {c: chr(0xE000 + i) for i, c in enumerate(COLOR_CODES)}   # 'r' -> U+E000
PUA_TO_CODE = {v: k for k, v in COLOR_PUA.items()}                    # U+E000 -> 'r'
PUA_TO_NAME = {chr(0xE000 + i): n for i, n in enumerate(COLOR_NAMES)}


def is_color(ch: str) -> bool:
    return ch in PUA_TO_CODE


# The fourteen pictographs the Matrix Portal's reel carries beyond Windows-1252 (they have
# no CP1252 byte, so they are reachable only by index — see the firmware's reel.h). A wall
# that cannot show them gets the fallback instead, rather than a rejected page.
PICTOGRAPHS = {
    "\u2665": "heart", "\u2666": "diamond", "\u2663": "club", "\u2660": "spade",
    "\u263a": "smiley", "\u266a": "note", "\u25cf": "circle", "\u25a0": "square",
    "\u2302": "house", "\u2190": "left", "\u2191": "up", "\u2192": "right",
    "\u2193": "down", "\u2600": "sun",
}
# What each becomes on a wall that has no flap for it. The arrows have honest CP1252
# stand-ins; the rest degrade to a character that at least occupies the cell.
PICTOGRAPH_FALLBACK = {
    "\u2190": "<", "\u2191": "^", "\u2192": ">", "\u2193": "v",
    "\u2665": "*", "\u2666": "*", "\u2663": "*", "\u2660": "*",
    "\u263a": ":", "\u266a": "*", "\u25cf": "*", "\u25a0": "#",
    "\u2302": "^", "\u2600": "*",
}

# Styles handled by the generic "one module per step" ordering path.
ORDERED_STYLES = (
    "ltr", "rtl", "center_out", "outside_in", "spiral", "diagonal",
    "anti_diagonal", "random", "rain", "reverse_rain", "columns",
    "columns_rtl", "alternating",
)
# Styles with bespoke timing/behaviour.
SPECIAL_STYLES = ("sync", "slot")
ALL_STYLES = ORDERED_STYLES + SPECIAL_STYLES

# Transient characters for the slot-machine spin effect only — never a "valid
# set" the display is limited to; a module just blanks any it doesn't carry.
_SPIN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def in_cp1252(s: str) -> bool:
    try:
        s.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def cp1252_upper(text: str) -> str:
    """Uppercase for a Windows-1252 display.

    Like ``str.upper()`` but never expands or drops a glyph the modules can show
    as a single Windows-1252 byte. Notably Python maps ``ß`` -> ``"SS"``, which
    both loses the ß glyph (0xDF, which the gateway/modules support) and changes
    the string length; here ß — and any cp1252 character whose uppercase would
    leave the code page — is kept intact. Accented letters that DO have a cp1252
    uppercase (é->É, ü->Ü, ç->Ç, …) still uppercase normally.
    """
    out = []
    for c in text:
        u = c.upper()
        if len(u) == 1 and in_cp1252(u):
            out.append(u)
        elif in_cp1252(c):
            out.append(c)
        else:
            out.append(u)
    return "".join(out)


def normalize(text: str, n: int, *, frame: bool = False) -> str:
    """Normalize display text to exactly ``n`` module characters, and make its COLOURS
    explicit so that nothing downstream has to guess.

    ONE question decides everything here:

        **Is a lowercase letter in this page a COLOUR, or a LETTER?**

    ``frame=True`` says COLOUR. That is the splitflap-os convention and the only way an
    animation can ask for one: art-clock and the anim_* apps draw with lowercase
    r/o/y/g/b/p/w, and a raw grid from the Compose editor may too. Such a page must NOT be
    folded here, because folding it would turn its colours into the letters R, O, Y…
    before anyone could tell they were colours.

    ``frame=False`` (the default) says LETTER — a page made of WORDS. Its colours can only
    have come from a colour tile (🟥, 🟩 …), which is unambiguous.

    Either way the result says explicitly which cells are colours (COLOR_PUA), so a
    transport, the live preview and the Vestaboard codec never have to decide whether the
    `o` of "Hello" is the letter o or the orange flap. They did have to, once, and it came
    out as "Hell<orange>".

    NOTE what is NOT decided here: whether to UPPERCASE. That is not a property of the
    text, it is a property of the WALL — a reel with no lowercase flaps gets uppercase, a
    Matrix Portal does not — so the engine does it last, once, for everyone. This used to
    be two overlapping flags (``raw`` and ``keep_case``) which encoded the same axis
    inverted, and whose fourth combination silently destroyed an animation's colours.
    """
    clean = str(text)
    for tile, code in COLOR_MAP.items():
        clean = clean.replace(tile, COLOR_PUA.get(code, code))
    if frame:
        # In a frame, a lowercase colour code IS a colour. Say so, before anything else can
        # mistake it for a letter.
        clean = "".join(COLOR_PUA.get(c, c) if c in COLOR_CODES else c for c in clean)
    return clean.ljust(n)[:n]


def fold(page: str) -> str:
    """A wall with no lowercase flaps gets uppercase — every cell that is not a colour.

    The wall has the LAST word on case, and it is the only one that has any word on it. A
    caller that folded early (as two of them did) discarded the one thing a Matrix Portal
    was for, and a caller that forgot to fold sent lowercase to a reel that has none.
    """
    return "".join(c if is_color(c) else cp1252_upper(c) for c in page)


# Stand-ins, ONE CHARACTER FOR ONE CHARACTER. That constraint is not stylistic: a page is a
# fixed grid of modules, one cell per character, so a two-character stand-in would shift every
# cell after it and re-wrap the line. Which means the honest expansions — ss for ss, ae for ae —
# are not available here, and the best we can do is the first letter. It is a visible
# compromise ("STRASE"), but it is legible, and the alternative is a homed module and "STRA E".
#
# Typographic punctuation is the common case, and the happiest one: a news feed sends curly
# quotes and an em dash, no 64-flap reel carries either, and the ASCII forms are exactly right.
_LOOKALIKE = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',   # curly quotes
    "\u2013": "-", "\u2014": "-", "\u2212": "-",                   # en dash, em dash, minus
    "\u2026": ".",                                                 # ellipsis
    "\u00a0": " ", "\u2007": " ", "\u202f": " ",                   # the non-breaking spaces
    "\u00b7": ".", "\u2022": ".",                                  # middot, bullet
    ":": ".",       # a reel with no colon (the fr-FR one) still reads "15.30" as a time
    "\u00d7": "X", "\u00f7": "/",
    "\u00df": "S",                          # SS would not fit in one cell -- see above
    "\u00e6": "A", "\u00c6": "A", "\u0153": "O", "\u0152": "O",
}


def _strip_accent(ch: str) -> str:
    """É -> E. One character in, one character out, or "" if there is nothing under it."""
    import unicodedata

    decomposed = unicodedata.normalize("NFD", ch)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    return base if len(base) == 1 else ""


def degrade(page: str, caps) -> str:
    """Replace every character this wall cannot show with the best one it CAN.

    This exists because of how a split-flap fails. Send a module a character that is not
    printed on its reel and it does not complain, it does not substitute, and nothing upstream
    hears about it: it HOMES. You get a blank cell in the middle of a word, and the only way
    anyone finds out is by looking at the wall.

    Until the gateway grew /api/capabilities we could not do anything about that, because we
    did not know what was on the reel — which is why the shipped translations were written in
    stripped-down ASCII, and why "Prévu" was a risk nobody could take. Now the wall tells us,
    so a character it cannot show becomes the nearest one it can:

        é -> E      (the accent goes, the word survives)
        ♥ -> *      (the pictograph's documented stand-in)
        — -> -      (typographic punctuation, from a feed that does not know about flaps)

    and only when nothing works does it become a space — which is what the module would have
    done anyway, except now it is a deliberate space and not a hole nobody knew about.

    A wall that has not told us its charset is left alone entirely (``can_show`` answers True
    for everything), so an old gateway behaves exactly as it did.
    """
    if not caps.knows_charset():
        return page

    out = []
    for ch in page:
        if is_color(ch) or caps.can_show(ch):
            out.append(ch)                       # a colour is a flap index, not a character
            continue
        for cand in (PICTOGRAPH_FALLBACK.get(ch),
                     _LOOKALIKE.get(ch),
                     _strip_accent(ch),
                     ch.upper(), ch.lower()):
            if cand and all(caps.can_show(c) for c in cand):
                # A multi-character stand-in (ß -> SS) would shift every cell after it and
                # re-wrap the line, so it is only taken when it fits in one cell.
                out.append(cand if len(cand) == 1 else cand[0])
                break
        else:
            out.append(" ")
    return "".join(out)


def for_legacy(ch: str) -> str:
    """One cell, as the legacy one-byte protocol wants it: a colour becomes its letter, and
    a pictograph — which has no byte at all — becomes the nearest thing that does."""
    return PUA_TO_CODE.get(ch, PICTOGRAPH_FALLBACK.get(ch, ch))


def for_text(ch: str) -> str:
    """One cell, as READABLE TEXT (an MCP tool's `lines`, a log line). A colour has no
    letter now — using one would be a lie, since `r` is the letter r — so it comes back as
    the tile a person would have typed."""
    tile = {v: k for k, v in COLOR_MAP.items() if k in COLOR_CODES}
    return tile.get(PUA_TO_CODE.get(ch, ""), ch)


def get_animation_order(style: str = "ltr", rows: int = 3, cols: int = 15) -> list[int]:
    """Return grid indices in the requested send order. Verbatim port."""
    total = rows * cols

    def m(r: int, c: int) -> int:
        return r * cols + c

    if style == "rtl":
        return list(range(total - 1, -1, -1))

    if style == "center_out":
        order: list[int] = []
        seen: set[int] = set()
        center = cols // 2
        for d in range(center + 1):
            for r in range(rows):
                cs = [center] if d == 0 else [center - d, center + d]
                for c in cs:
                    if 0 <= c < cols:
                        idx = m(r, c)
                        if idx not in seen:
                            seen.add(idx)
                            order.append(idx)
        return order

    if style == "outside_in":
        return list(reversed(get_animation_order("center_out", rows, cols)))

    if style == "spiral":
        vis = [[False] * cols for _ in range(rows)]
        order = []
        top, bottom, left, right = 0, rows - 1, 0, cols - 1
        while top <= bottom and left <= right:
            for c in range(left, right + 1):
                if not vis[top][c]:
                    vis[top][c] = True
                    order.append(m(top, c))
            for r in range(top + 1, bottom + 1):
                if not vis[r][right]:
                    vis[r][right] = True
                    order.append(m(r, right))
            if top < bottom:
                for c in range(right - 1, left - 1, -1):
                    if not vis[bottom][c]:
                        vis[bottom][c] = True
                        order.append(m(bottom, c))
            if left < right:
                for r in range(bottom - 1, top, -1):
                    if not vis[r][left]:
                        vis[r][left] = True
                        order.append(m(r, left))
            top += 1
            bottom -= 1
            left += 1
            right -= 1
        return order

    if style == "diagonal":
        order, seen = [], set()
        for d in range(rows + cols - 1):
            for r in range(rows):
                c = d - r
                if 0 <= c < cols:
                    idx = m(r, c)
                    if idx not in seen:
                        seen.add(idx)
                        order.append(idx)
        return order

    if style == "anti_diagonal":
        order, seen = [], set()
        for d in range(rows + cols - 1):
            for r in range(rows):
                c = (cols - 1 - d) + r
                if 0 <= c < cols:
                    idx = m(r, c)
                    if idx not in seen:
                        seen.add(idx)
                        order.append(idx)
        return order

    if style == "random":
        return random.sample(range(total), total)

    if style == "rain":
        return [m(r, c) for r in range(rows) for c in range(cols)]

    if style == "reverse_rain":
        return [m(r, c) for r in range(rows - 1, -1, -1) for c in range(cols)]

    if style == "columns":
        return [m(r, c) for c in range(cols) for r in range(rows)]

    if style == "columns_rtl":
        return [m(r, c) for c in range(cols - 1, -1, -1) for r in range(rows)]

    if style == "alternating":
        order = []
        for c in range(cols):
            for r in range(rows):
                ac = c if r % 2 == 0 else (cols - 1 - c)
                order.append(m(r, ac))
        return order

    return list(range(total))  # default ltr


@dataclass
class Step:
    """One tick of a send plan: emit these frames, then wait ``delay_after``.

    ``frames`` is a list of ``(grid_index, char)``. The engine maps
    ``grid_index`` to a module id and updates preview state as it emits.
    """

    frames: list[tuple[int, str]] = field(default_factory=list)
    delay_after: float = 0.0  # seconds


def build_send_plan(
    clean_text: str,
    *,
    style: str,
    speed_ms: int,
    rows: int,
    cols: int,
) -> list[Step]:
    """Turn a normalized page into an ordered list of timed steps.

    ``clean_text`` must already be normalized to ``rows*cols`` chars.
    """
    n = rows * cols
    step_delay = max(0, speed_ms) / 1000.0

    if style == "sync":
        return _plan_sync(clean_text, n)
    if style == "slot":
        return _plan_slot(clean_text, n, effect_speed_ms=speed_ms or 80)

    order = get_animation_order(style, rows, cols)
    steps: list[Step] = []
    for i in order:
        if i >= len(clean_text):
            continue
        steps.append(Step(frames=[(i, clean_text[i])], delay_after=step_delay))
    return steps


def _plan_sync(clean_text: str, n: int) -> list[Step]:
    """All modules update together: emit every frame in a single step.

    A true "arrive at the same instant" stagger would need each module's flap
    layout and per-character flip distance — which the companion no longer models
    (modules own their char maps and flip concurrently). Sending them together is
    the layout-agnostic equivalent and needs no character-set knowledge.
    """
    frames = [(i, clean_text[i]) for i in range(n) if i < len(clean_text)]
    return [Step(frames=frames, delay_after=0.0)]


def _plan_slot(clean_text: str, n: int, effect_speed_ms: int) -> list[Step]:
    """Slot-machine: spin all to random chars, pause, then lock in L->R."""
    spin_chars: list[str] = []
    for i in range(n):
        ch = clean_text[i] if i < len(clean_text) else " "
        # Spin chars are drawn from a generic set (not any module's real map) and
        # simply differ from the target; a module blanks any it doesn't carry.
        candidates = [c for c in _SPIN_CHARS if c != ch]
        spin_chars.append(random.choice(candidates) if candidates else _SPIN_CHARS[0])

    steps: list[Step] = []
    # Phase 1: everyone spins at once, then hold 1.5s.
    steps.append(Step(frames=[(i, spin_chars[i]) for i in range(n)], delay_after=1.5))
    # Phase 2: lock in final chars left-to-right.
    lock_delay = max(0, effect_speed_ms) / 1000.0
    for i in range(n):
        ch = clean_text[i] if i < len(clean_text) else " "
        steps.append(Step(frames=[(i, ch)], delay_after=lock_delay))
    return steps
