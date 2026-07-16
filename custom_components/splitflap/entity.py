"""Shared base: every entity belongs to the one SplitFlap device and reads the coordinator."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SplitFlapCoordinator


class SplitFlapEntity(CoordinatorEntity[SplitFlapCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SplitFlapCoordinator, key: str) -> None:
        super().__init__(coordinator)
        # uid_base is the flow's netloc[/display] id — stable across remove +
        # re-add, unlike a config entry_id (async_migrate_entry rewrites old ids).
        self._attr_unique_id = f"{coordinator.uid_base}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.uid_base)},
            name=coordinator.title,
            manufacturer="SplitFlap",
            model="Gateway Companion",
            configuration_url=coordinator.client.base_url,  # opens the companion UI
        )
