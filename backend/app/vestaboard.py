"""vestaboard.py — the Vestaboard character codec and layout, no I/O.

A Vestaboard is a commercial split-flap display whose Local API is widely spoken:
Home Assistant ``rest_command``s, the HACS integration, scripts, client libraries.
Teach the companion that API and every one of those drives a SplitFlap wall
unchanged. This module is the translation; the endpoints live in main.py.

The formats line up almost exactly:

* A Vestaboard **Note** is 3x15 — the companion's default grid. (A **Flagship** is
  6x22, which `fit()` compacts down.)
* A Vestaboard message is a matrix of character *codes*; a companion page is a flat
  row-major string of the same cells.
* Vestaboard's color chips are the companion's color flaps ``r o y g b p w``.

Two house rules this module has to respect:

1. **The companion doesn't police characters** (see renderer.py): a module blanks
   whatever glyph it lacks, so we translate a code to its character and pass it on —
   no substituting, no stripping. ``°`` (62) goes through as ``°``.
2. **Case is load-bearing**: ``y`` is a yellow tile, ``Y`` is the letter Y. Everything
   this module emits is final, so callers must send it with ``raw=True`` to skip the
   uppercasing that would turn every color chip into a letter.
"""

from __future__ import annotations

from . import renderer


# The published table (docs.vestaboard.com/docs/charactercodes). Codes 43, 45, 51,
# 57, 58 and 61 are absent there — a real board has no flap for them — so they are
# simply not in this map and decode to a blank, like any other gap.
CODE_TO_CHAR: dict[int, str] = {
    0: " ",
    **{i: chr(ord("A") + i - 1) for i in range(1, 27)},        # 1-26  -> A-Z
    **{i: str(i - 26) for i in range(27, 36)},                 # 27-35 -> 1-9
    36: "0",
    37: "!", 38: "@", 39: "#", 40: "$", 41: "(", 42: ")",
    44: "-", 46: "+", 47: "&", 48: "=", 49: ";", 50: ":",
    52: "'", 53: '"', 54: "%", 55: ",", 56: ".", 59: "/", 60: "?",
    62: "°",                                                   # Flagship degree (a Note shows a heart)
    # Color chips -> the firmware's COLOR FLAPS, as their own codepoints — never the
    # letters r/o/y/g/b/p/w: decoding a red chip to "r" would write the LETTER r on a Matrix
    # Portal, and encoding the r of "Hello" would read back as a red chip. Violet is `p`.
    63: renderer.COLOR_PUA["r"], 64: renderer.COLOR_PUA["o"], 65: renderer.COLOR_PUA["y"],
    66: renderer.COLOR_PUA["g"], 67: renderer.COLOR_PUA["b"], 68: renderer.COLOR_PUA["p"],
    69: renderer.COLOR_PUA["w"],
    # Two chips the flaps don't have. Black is already spelled "blank" here — see
    # the ⬛ -> " " entry in renderer.COLOR_MAP — and `filled` is a solid tile, so
    # it lands on white. Both are lossy: they do NOT survive a write->read round trip.
    70: " ",
    71: renderer.COLOR_PUA["w"],
}

MAX_CODE = 71   # a real board rejects anything outside 0..71, and so do we

# Characters back to codes, for reading the board out. Built from the table above,
# minus the lossy aliases: a blank reads back as 0 (not 70), a white tile as 69
# (not 71). Anything with no Vestaboard code at all — an accent, €, the letters a
# module carries that a Vestaboard doesn't — reads back as a blank, because the
# format simply has no way to say it.
CHAR_TO_CODE: dict[str, int] = {
    char: code for code, char in CODE_TO_CHAR.items() if code not in (70, 71)
}

BLANK = 0

# Vestaboard's animation strategies, mapped onto the transition styles we already
# have (renderer.ALL_STYLES). `step_interval_ms` / `step_size` are accepted and
# ignored on purpose: Vestaboard's default is 3000 ms *per animation step*, while a
# style's speed here is milliseconds *per module frame* — honoring it literally
# would stretch one message into a multi-minute crawl across the wall.
STRATEGY_TO_STYLE: dict[str, str] = {
    "column": "columns",
    "reverse-column": "columns_rtl",
    "edges-to-center": "outside_in",
    "row": "ltr",
    "diagonal": "diagonal",
    "random": "random",
}


class VestaboardError(ValueError):
    """A payload a real board would reject too (bad shape, bad code)."""


def decode(matrix: list) -> list[str]:
    """A Vestaboard character-code matrix -> one string per row.

    Raises VestaboardError on anything a real board would refuse: a non-rectangular
    body, a non-integer cell, or a code outside 0..71.
    """
    if not isinstance(matrix, list) or not matrix:
        raise VestaboardError("characters must be a non-empty array of rows")
    rows: list[str] = []
    width = None
    for r, row in enumerate(matrix):
        if not isinstance(row, list) or not row:
            raise VestaboardError(f"row {r} must be a non-empty array of character codes")
        if width is None:
            width = len(row)
        elif len(row) != width:
            # A board's message is a rectangle. A ragged one means the client built it
            # wrong, and silently padding it would put the text somewhere they didn't ask.
            raise VestaboardError(f"row {r} has {len(row)} cells, expected {width} — "
                                  "every row must be the same width")
        out = []
        for c, code in enumerate(row):
            # bool is an int subclass, and `True` is not a character code.
            if not isinstance(code, int) or isinstance(code, bool):
                raise VestaboardError(f"cell [{r}][{c}] must be an integer character code")
            if not 0 <= code <= MAX_CODE:
                raise VestaboardError(f"character code {code} at [{r}][{c}] is outside 0..{MAX_CODE}")
            out.append(CODE_TO_CHAR.get(code, " "))   # a gap in the table is a blank flap
        rows.append("".join(out))
    return rows


def _trim(rows: list[str]) -> list[str]:
    """Drop the blank margin around the content — blank rows top and bottom, blank
    columns left and right. Vestaboard clients center text inside the full board, so
    a 6x22 message is mostly padding; cropping that padding is what lets the message
    itself survive the trip to a smaller wall."""
    keep = [r for r in rows if r.strip()]
    if not keep:
        return []
    left = min(len(r) - len(r.lstrip(" ")) for r in keep)
    right = min(len(r) - len(r.rstrip(" ")) for r in keep)
    return [r[left:len(r) - right] if right else r[left:] for r in keep]


def _center_block(rows: list[str], target_rows: int, target_cols: int) -> str:
    """Center `rows` in a target_rows x target_cols grid, cropping what overflows.
    Returns the flat row-major page string the engine wants."""
    if len(rows) > target_rows:                       # too tall: keep the middle rows
        top = (len(rows) - target_rows) // 2
        rows = rows[top:top + target_rows]
    pad_top = (target_rows - len(rows)) // 2
    grid = [" " * target_cols] * pad_top
    for r in rows:
        if len(r) > target_cols:                      # too wide: keep the middle columns
            left = (len(r) - target_cols) // 2
            r = r[left:left + target_cols]
        grid.append(r.center(target_cols)[:target_cols])
    grid += [" " * target_cols] * (target_rows - len(grid))
    return "".join(grid)


def fit(rows: list[str], target_rows: int, target_cols: int) -> str:
    """Fit decoded rows onto this wall, as a flat row-major page string.

    A matrix that already matches the grid (a Note-shaped 3x15 payload on a 3x15
    wall) passes through cell-for-cell. Anything else is *compacted*: the blank
    margin is trimmed and the remaining content is centered, so a vertically-centered
    6x22 Flagship message lands as the message — not as the blank rows a naive crop
    of its top-left corner would give.
    """
    if len(rows) == target_rows and all(len(r) == target_cols for r in rows):
        return "".join(rows)
    return _center_block(_trim(rows), target_rows, target_cols)


def layout_text(text: str, target_rows: int, target_cols: int, caps=None) -> str:
    """Lay plain text out on the wall — the `{"text": ...}` extension.

    Vestaboard's own text API centers a message, and so do the companion's apps
    (PluginRuntime.format_lines), so this does the same: explicit newlines split
    lines, anything longer than the wall is greedily word-wrapped (a word too long
    to fit is hard-split), every line is centered, and the block is centered vertically.
    Uppercasing is left to the caller — the board has no lowercase flaps.

    `caps` (what the wall can show) is taken here and NOT further down, because a character
    the reel cannot show may need TWO flaps — ß becomes SS on a reel with no ß — and this is
    the last point at which the text is still text. One line down it is a grid, one flap per
    character, and the wrapping has already been decided.
    """
    from . import renderer

    text = renderer.expand(text, caps)

    lines: list[str] = []
    for para in text.split("\n"):
        words, line = para.split(), ""
        if not words:
            lines.append("")
            continue
        for w in words:
            while len(w) > target_cols:               # a word longer than the wall
                if line:
                    lines.append(line)
                    line = ""
                lines.append(w[:target_cols])
                w = w[target_cols:]
            if not line:
                line = w
            elif len(line) + 1 + len(w) <= target_cols:
                line += " " + w
            else:
                lines.append(line)
                line = w
        if line:
            lines.append(line)
    return _center_block(lines, target_rows, target_cols)


def encode(chars: list[str], rows: int, cols: int) -> list[list[int]]:
    """The live board -> a Vestaboard character-code matrix (for reads).

    `chars` is DisplayState.current_chars: one character per module, row-major. A
    character with no Vestaboard code reads back as a blank — see CHAR_TO_CODE.
    """
    out: list[list[int]] = []
    for r in range(rows):
        row = []
        for c in range(cols):
            i = r * cols + c
            ch = chars[i] if i < len(chars) else " "
            # A Vestaboard has no lowercase flaps, so a letter reads back as its capital.
            # A COLOR is not a letter — it is its own codepoint — so it can never be
            # confused with the r of "Hello".
            row.append(CHAR_TO_CODE.get(ch, CHAR_TO_CODE.get(ch.upper(), BLANK)))
        out.append(row)
    return out


def style_for(strategy: str | None, default: str) -> str:
    """The transition style for a Vestaboard animation strategy (unknown -> default)."""
    if not strategy:
        return default
    return STRATEGY_TO_STYLE.get(str(strategy).strip().lower(), default)
