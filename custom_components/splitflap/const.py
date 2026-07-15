"""Constants for the SplitFlap integration."""

from __future__ import annotations

DOMAIN = "splitflap"

CONF_URL = "url"
CONF_DISPLAY = "display"   # display id; "" = the companion's default display

# How often to poll the companion for board state + what's driving it. The board only
# changes as fast as the flaps turn, so a few seconds is plenty and keeps the load light.
DEFAULT_SCAN_INTERVAL = 5

# The value a select shows when nothing of its kind is running.
OFF = "Off"

SERVICE_MESSAGE = "message"
ATTR_TEXT = "text"
ATTR_STYLE = "style"
ATTR_SECONDS = "seconds"
