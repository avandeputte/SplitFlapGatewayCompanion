"""
renderer.py — faithful port of splitflap-os's display rendering / normalization.

The split-flap protocol, character set, colour-tile mapping, and animation
orderings here are behaviour-identical ports of splitflap-os
(CC BY-NC-SA 4.0, csader). Keeping them identical is what lets any splitflap-os
app render correctly on the companion. See ATTRIBUTION.md.

This module is pure logic: it turns a page of text into an ordered "send plan"
of per-module frames. The actual I/O (MQTT / REST / sim) and inter-step timing
are handled by the transport + engine layers, so this file stays testable with
no hardware.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# The physical flap character set, in flap order (index 0..63). Ported verbatim
# from splitflap-os app.py. Index into this string == the flap's position.
FLAP_CHARS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$&()-+=;q:%'.,/?*roygbpw"

# Emoji colour tiles -> the single-char firmware colour codes (r o y g b p w),
# black tile -> blank. Ported verbatim from splitflap-os.
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


def char_to_index(ch: str) -> int:
    """Flap index for a character; unknown chars fall back to 0 (blank)."""
    idx = FLAP_CHARS.find(ch)
    return idx if idx != -1 else 0


def normalize(text: str, n: int, *, raw: bool = False, currency: str = "$") -> str:
    """Normalize display text to exactly ``n`` module characters.

    Mirrors splitflap-os send_to_display: uppercase (unless ``raw`` — animation
    pages keep their lowercase colour codes), emoji tiles -> colour codes,
    user currency symbol -> ``$`` (the physical flap), ``"`` -> ``q`` (the
    firmware alias for the double-quote flap), then pad/truncate to ``n``.
    """
    clean = text if raw else text.upper()
    for emoji, code in COLOR_MAP.items():
        clean = clean.replace(emoji, code)
    currency = (currency or "$").strip()
    if currency and currency != "$":
        clean = clean.replace(currency.upper(), "$")
    clean = clean.replace('"', "q")
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
    current_indices: list[int],
) -> list[Step]:
    """Turn a normalized page into an ordered list of timed steps.

    ``clean_text`` must already be normalized to ``rows*cols`` chars.
    ``current_indices`` is the last-known flap index per module (or -1 if
    unknown) and is used only to compute stagger distances for ``sync``.
    """
    n = rows * cols
    step_delay = max(0, speed_ms) / 1000.0

    if style == "sync":
        return _plan_sync(clean_text, n, current_indices)
    if style == "slot":
        return _plan_slot(clean_text, n, effect_speed_ms=speed_ms or 80)

    order = get_animation_order(style, rows, cols)
    steps: list[Step] = []
    for i in order:
        if i >= len(clean_text):
            continue
        steps.append(Step(frames=[(i, clean_text[i])], delay_after=step_delay))
    return steps


def _plan_sync(clean_text: str, n: int, current_indices: list[int]) -> list[Step]:
    """All modules staggered so they arrive at their target simultaneously.

    Port of send_to_display_sync: total sweep is ~4s (4/64 s per flap step),
    modules with the shortest distance start last. We convert the continuous
    stagger into discrete steps by delaying between successive sends.
    """
    dists = []
    for i in range(n):
        ch = clean_text[i] if i < len(clean_text) else " "
        target = char_to_index(ch)
        current = 0 if current_indices[i] == -1 else current_indices[i]
        dist = (target - current) % 64
        dists.append((i, ch, dist))

    max_dist = max((d[2] for d in dists), default=0)
    # Longest distance first (starts immediately); shortest last.
    dists_sorted = sorted(dists, key=lambda x: -x[2])

    steps: list[Step] = []
    prev_start = 0.0
    for i, ch, dist in dists_sorted:
        start = (max_dist - dist) * (4.0 / 64.0)
        # Wait the gap since the previous module's send before emitting this one.
        if steps:
            steps[-1].delay_after = max(0.0, start - prev_start)
        steps.append(Step(frames=[(i, ch)], delay_after=0.0))
        prev_start = start
    return steps


def _plan_slot(clean_text: str, n: int, effect_speed_ms: int) -> list[Step]:
    """Slot-machine: spin all to random chars, pause, then lock in L->R."""
    spin_chars: list[str] = []
    for i in range(n):
        ch = clean_text[i] if i < len(clean_text) else " "
        target = char_to_index(ch)
        # Candidate spin chars exclude the 4 trailing colour codes and the target.
        candidates = [
            c for c in FLAP_CHARS[1: len(FLAP_CHARS) - 4] if char_to_index(c) != target
        ]
        spin_chars.append(random.choice(candidates) if candidates else FLAP_CHARS[1])

    steps: list[Step] = []
    # Phase 1: everyone spins at once, then hold 1.5s.
    steps.append(Step(frames=[(i, spin_chars[i]) for i in range(n)], delay_after=1.5))
    # Phase 2: lock in final chars left-to-right.
    lock_delay = max(0, effect_speed_ms) / 1000.0
    for i in range(n):
        ch = clean_text[i] if i < len(clean_text) else " "
        steps.append(Step(frames=[(i, ch)], delay_after=lock_delay))
    return steps
