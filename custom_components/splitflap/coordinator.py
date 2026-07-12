"""Polls the companion and holds the snapshot every entity reads from."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SplitFlapClient, SplitFlapError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class SplitFlapCoordinator(DataUpdateCoordinator[dict]):
    """One poll gathers board state, the running app/playlist, and the option lists.

    Kept in a single coordinator because the entities are all views of the same board —
    a select's options and a sensor's value come from the same fetch, so they stay
    consistent and there's one request per interval instead of one per entity.
    """

    def __init__(self, hass: HomeAssistant, client: SplitFlapClient, title: str) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.title = title
        self._grid: dict | None = None

    async def _async_update_data(self) -> dict:
        try:
            # Grid geometry only changes when the gateway is reconfigured, so fetch it
            # once and reuse it (it's what turns the flat char list into lines).
            if self._grid is None:
                self._grid = await self.client.grid()
            state = await self.client.state()
            apps = await self.client.apps()
            playlists = await self.client.playlists()
        except SplitFlapError as err:
            raise UpdateFailed(str(err)) from err

        rows = int(self._grid.get("rows", 3))
        cols = int(self._grid.get("cols", 15))
        chars = state.get("chars") or []
        lines = ["".join(chars[r * cols:(r + 1) * cols]) for r in range(rows)]

        return {
            "state": state,
            "lines": lines,
            "text": " ".join(line.strip() for line in lines if line.strip()),
            "apps": apps.get("apps", []),
            "active_app": state.get("active_app"),
            "active_playlist": state.get("active_playlist"),
            "current_app": state.get("current_app"),
            "playlists": playlists.get("playlists", {}),
        }
