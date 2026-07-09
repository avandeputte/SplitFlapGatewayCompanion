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


def _in_cp1252(s: str) -> bool:
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
        if len(u) == 1 and _in_cp1252(u):
            out.append(u)
        elif _in_cp1252(c):
            out.append(c)
        else:
            out.append(u)
    return "".join(out)


def normalize(text: str, n: int, *, raw: bool = False) -> str:
    """Normalize display text to exactly ``n`` module characters.

    Uppercase (unless ``raw`` — animation pages keep their lowercase colour
    codes), map emoji tiles to colour codes, then pad/truncate to ``n``.

    The companion does NOT police characters: every other character — accents,
    punctuation, quotes, currency symbols, anything — is passed through verbatim.
    Modules carry their own (possibly per-module) char maps and render a blank
    for anything they lack, so there's nothing to validate or substitute here.
    Uppercasing is Windows-1252-aware (see ``cp1252_upper``) so single-byte
    accents and ``ß`` survive intact.
    """
    clean = text if raw else cp1252_upper(text)
    for emoji, code in COLOR_MAP.items():
        clean = clean.replace(emoji, code)
    return clean.ljust(n)[:n]


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
