"""The board as a picture: an image entity drawing the wall's current state.

The same approach ha-vestaboard takes — the board is rendered server-side as a
PNG and exposed through Home Assistant's image platform, so it shows up on the
device page and drops into any Picture card, with no custom Lovelace card to
install. Drawn in the companion's own visual language: dark split-flap tiles
with a hinge line, the seven colour flaps as solid colour tiles.

Colour flaps arrive from the companion as private-use characters (U+E000..E006,
one per legacy colour code r/o/y/g/b/p/w) — the same encoding its live preview
reads — so the letter `o` and the orange flap can never be confused.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from homeassistant.components.image import ImageEntity

from .const import DOMAIN
from .coordinator import SplitFlapCoordinator
from .entity import SplitFlapEntity

# One colour per PUA code point, in r/o/y/g/b/p/w order — the gateway UI's own
# calibration palette, so the picture matches what the companion's preview shows.
_COLOURS = ("#e23b3b", "#ff9f0a", "#ffd60a", "#2fb84a", "#3b82f6", "#a855f7", "#e8e8e8")
_PUA_COLOUR = {chr(0xE000 + i): c for i, c in enumerate(_COLOURS)}

_BG = "#10141f"        # page background (matches the companion UI)
_TILE = "#1a2440"      # an idle flap
_HINGE = "#0c1120"     # the split across the middle
_GLYPH = "#f2f5fb"


def render_board(lines: list[str], cols: int, *, scale: int = 16) -> bytes:
    """Draw the board as a PNG. Pure — testable without Home Assistant.

    ``scale`` is the tile width in pixels; tiles are taller than wide (3:4),
    like the real flaps.
    """
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageFont   # HA core ships Pillow

    rows = len(lines)
    tw, th = scale * 3, scale * 4                 # tile size
    gap, margin = max(2, scale // 4), scale       # between tiles, around the board
    w = margin * 2 + cols * tw + (cols - 1) * gap
    h = margin * 2 + rows * th + (rows - 1) * gap

    img = Image.new("RGB", (w, h), _BG)
    draw = ImageDraw.Draw(img)
    # Bundled DejaVu Sans Bold: Pillow's built-in font has no glyphs for the wall's
    # fourteen pictograph flaps (♥ ☀ ♪ the arrows …) and drew them as blanks.
    # DejaVu covers all fourteen (verified) and is freely redistributable
    # (fonts/LICENSE rides along).
    try:
        font = ImageFont.truetype(
            str(Path(__file__).parent / "fonts" / "DejaVuSans-Bold.ttf"),
            int(th * 0.58))
    except OSError:
        font = ImageFont.load_default(size=int(th * 0.62))

    for r, line in enumerate(lines):
        for c in range(cols):
            ch = line[c] if c < len(line) else " "
            x = margin + c * (tw + gap)
            y = margin + r * (th + gap)
            colour = _PUA_COLOUR.get(ch)
            draw.rounded_rectangle((x, y, x + tw, y + th), radius=max(2, scale // 3),
                                   fill=colour or _TILE)
            if colour is None and ch != " ":
                draw.text((x + tw / 2, y + th / 2 - scale // 8), ch,
                          font=font, fill=_GLYPH, anchor="mm")
            # the hinge, over everything — it is what makes a tile read as a flap
            draw.line((x, y + th / 2, x + tw, y + th / 2), fill=_HINGE,
                      width=max(1, scale // 8))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SplitFlapCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SplitFlapBoardImage(hass, coordinator, entry.entry_id)])


class SplitFlapBoardImage(SplitFlapEntity, ImageEntity):
    """The wall, as a live picture."""

    _attr_content_type = "image/png"
    _attr_translation_key = "board"

    def __init__(self, hass: HomeAssistant, coordinator: SplitFlapCoordinator,
                 entry_id: str) -> None:
        SplitFlapEntity.__init__(self, coordinator, entry_id)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{entry_id}_board"
        self._lines: list[str] | None = None
        self._png: bytes | None = None
        self._attr_image_last_updated: datetime | None = None

    def _handle_coordinator_update(self) -> None:
        # Only a changed board is a new picture — the frontend re-fetches on the
        # timestamp, and a clock app would otherwise re-download every poll.
        lines = self.coordinator.data["lines"]
        if lines != self._lines:
            self._lines = list(lines)
            self._png = None                       # drawn lazily, off the loop
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    def image(self) -> bytes | None:
        """PNG bytes — called by HA in an executor, so drawing here is fine."""
        if self._lines is None:
            self._lines = list(self.coordinator.data["lines"])
            self._attr_image_last_updated = dt_util.utcnow()
        if self._png is None:
            self._png = render_board(self._lines, self.coordinator.data["cols"])
        return self._png
