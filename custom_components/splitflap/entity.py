"""Shared base: every entity belongs to the one SplitFlap device and reads the coordinator."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SplitFlapCoordinator


class SplitFlapEntity(CoordinatorEntity[SplitFlapCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SplitFlapCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=coordinator.title,
            manufacturer="SplitFlap",
            model="Gateway Companion",
            configuration_url=coordinator.client._base,  # opens the companion UI
        )
