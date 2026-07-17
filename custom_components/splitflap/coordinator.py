"""Polls the companion and holds the snapshot every entity reads from."""

from __future__ import annotations

import logging
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SplitFlapClient, SplitFlapError
from .const import (DEFAULT_SCAN_INTERVAL, DOMAIN, LISTS_SCAN_INTERVAL,
                    stable_unique_base)

_LOGGER = logging.getLogger(__name__)


class SplitFlapCoordinator(DataUpdateCoordinator[dict]):
    """One poll gathers board state, the running app/playlist, and the option lists.

    Kept in a single coordinator because the entities are all views of the same board —
    a select's options and a sensor's value come from the same fetch, so they stay
    consistent and there's one request per interval instead of one per entity.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 client: SplitFlapClient) -> None:
        try:
            super().__init__(
                hass, _LOGGER, name=DOMAIN, config_entry=entry,
                update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            )
        except TypeError:
            # HA < 2024.8 has no config_entry kwarg (newer HA deprecates omitting it).
            super().__init__(
                hass, _LOGGER, name=DOMAIN,
                update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            )
        self.client = client
        self.title = entry.title
        # netloc[/display] — the stable base every entity's unique_id hangs off.
        self.uid_base = stable_unique_base(entry)
        self._apps: list = []
        self._playlists: dict = {}
        self._lists_at: float | None = None

    async def _async_update_data(self) -> dict:
        try:
            # Grid geometry is one tiny GET — fetch it every cycle so a resized wall
            # recovers on the next poll instead of only after an HA restart.
            grid = await self.client.grid()
            state = await self.client.state()
            # The app/playlist lists change on installs/saves, not flap turns —
            # refresh them on a slower cadence and reuse between refreshes.
            if self._lists_at is None or monotonic() - self._lists_at >= LISTS_SCAN_INTERVAL:
                apps = await self.client.apps()
                playlists = await self.client.playlists()
                self._apps = apps.get("apps", [])
                self._playlists = playlists.get("playlists", {})
                self._lists_at = monotonic()
        except SplitFlapError as err:
            raise UpdateFailed(str(err)) from err

        rows = int(grid.get("rows", 3))
        cols = int(grid.get("cols", 15))
        chars = state.get("chars") or []
        lines = ["".join(chars[r * cols:(r + 1) * cols]) for r in range(rows)]

        # A canvas app draws on the Matrix panel, not the flaps — so the board image
        # should show that frame, not the (bypassed, stale) flap grid. Only fetch the
        # PNG when one is live; a flap app or an on-device effect has none.
        canvas_png = None
        if state.get("canvas"):
            try:
                canvas_png = await self.client.canvas_png()
            except SplitFlapError:
                canvas_png = None

        return {
            "state": state,
            "rows": rows,
            "cols": cols,
            "lines": lines,
            "text": " ".join(line.strip() for line in lines if line.strip()),
            "apps": self._apps,
            "active_app": state.get("active_app"),
            "active_playlist": state.get("active_playlist"),
            "current_app": state.get("current_app"),
            "playlists": self._playlists,
            "canvas_png": canvas_png,
        }
