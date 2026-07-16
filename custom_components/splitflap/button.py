"""Buttons: clear, stop, home."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SplitFlapCoordinator
from .entity import SplitFlapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        SplitFlapButton(coordinator, "clear", "mdi:eraser", lambda c: c.clear()),
        SplitFlapButton(coordinator, "stop", "mdi:stop", lambda c: c.stop_app()),
        SplitFlapButton(coordinator, "home", "mdi:home", lambda c: c.home()),
    ])


class SplitFlapButton(SplitFlapEntity, ButtonEntity):
    def __init__(self, coordinator: SplitFlapCoordinator, key: str,
                 icon: str, action: Callable[..., Awaitable[None]]) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._action = action

    async def async_press(self) -> None:
        await self._action(self.coordinator.client)
        await self.coordinator.async_request_refresh()
