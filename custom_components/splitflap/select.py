"""App and Playlist selects — run one, or pick Off to stop."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, OFF
from .coordinator import SplitFlapCoordinator
from .entity import SplitFlapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SplitFlapAppSelect(coordinator, entry.entry_id),
        SplitFlapPlaylistSelect(coordinator, entry.entry_id),
    ])


class SplitFlapAppSelect(SplitFlapEntity, SelectEntity):
    _attr_translation_key = "app"
    _attr_icon = "mdi:application"

    def __init__(self, coordinator: SplitFlapCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_app"

    @property
    def options(self) -> list[str]:
        return [OFF] + [a["name"] for a in self.coordinator.data["apps"]]

    @property
    def current_option(self) -> str:
        active = self.coordinator.data["active_app"]
        if not active:
            return OFF
        for a in self.coordinator.data["apps"]:
            if a["id"] == active:
                return a["name"]
        return OFF

    async def async_select_option(self, option: str) -> None:
        if option == OFF:
            await self.coordinator.client.stop_app()
        else:
            app_id = next((a["id"] for a in self.coordinator.data["apps"]
                           if a["name"] == option), None)
            if app_id:
                await self.coordinator.client.run_app(app_id)
        await self.coordinator.async_request_refresh()


class SplitFlapPlaylistSelect(SplitFlapEntity, SelectEntity):
    _attr_translation_key = "playlist"
    _attr_icon = "mdi:playlist-play"

    def __init__(self, coordinator: SplitFlapCoordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id)
        self._attr_unique_id = f"{entry_id}_playlist"

    @property
    def options(self) -> list[str]:
        return [OFF] + list(self.coordinator.data["playlists"].keys())

    @property
    def current_option(self) -> str:
        return self.coordinator.data["active_playlist"] or OFF

    async def async_select_option(self, option: str) -> None:
        if option == OFF:
            # Stopping the playlist is the same stop as an app — it clears the driver.
            await self.coordinator.client.stop_app()
        else:
            pl = self.coordinator.data["playlists"].get(option)
            if pl:
                await self.coordinator.client.run_playlist(
                    option, pl.get("entries", []), bool(pl.get("loop", True)))
        await self.coordinator.async_request_refresh()
