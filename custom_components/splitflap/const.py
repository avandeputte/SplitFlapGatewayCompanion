"""Constants for the SplitFlap integration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

DOMAIN = "splitflap"

CONF_URL = "url"
CONF_DISPLAY = "display"   # display id; "" = the companion's default display

# How often to poll the companion for board state + what's driving it. The board only
# changes as fast as the flaps turn, so a few seconds is plenty and keeps the load light.
DEFAULT_SCAN_INTERVAL = 5

# The app/playlist *lists* only change when someone installs an app or saves a playlist,
# so they ride a much slower cadence than the board itself.
LISTS_SCAN_INTERVAL = 60


def stable_unique_base(entry: ConfigEntry) -> str:
    """The flow's stable id — ``netloc`` or ``netloc/display`` — for unique_ids
    that survive a remove + re-add (a config entry_id does not).

    Entries from before the flow set a unique_id derive the same value from
    their stored data.
    """
    if entry.unique_id:
        return entry.unique_id
    base = urlparse(entry.data[CONF_URL]).netloc
    display = entry.data.get(CONF_DISPLAY, "")
    return f"{base}/{display}" if display else base

# The value a select shows when nothing of its kind is running.
OFF = "Off"

SERVICE_MESSAGE = "message"
ATTR_TEXT = "text"
ATTR_STYLE = "style"
ATTR_SECONDS = "seconds"
