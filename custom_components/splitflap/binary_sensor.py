"""Binary sensors: the Matrix Gateway's countdown running + an alarm firing.

Created only when the wall's first snapshot says the timer capability is there —
a physical split-flap wall keeps a clean device page.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (BinarySensorDeviceClass,
                                                    BinarySensorEntity)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SplitFlapCoordinator
from .entity import SplitFlapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    sup = (coordinator.data or {}).get("timer", {}).get("supported", {})
    if not sup.get("timer"):
        return
    async_add_entities([
        SplitFlapTimerRunning(coordinator),
        SplitFlapAlarmFiring(coordinator),
    ])


class _TimerBinary(SplitFlapEntity, BinarySensorEntity):
    def _t(self) -> dict:
        return (self.coordinator.data or {}).get("timer", {}).get("timer", {})

    @property
    def available(self) -> bool:
        return super().available and bool(
            (self.coordinator.data or {}).get("timer", {}).get("supported", {}).get("timer"))


class SplitFlapTimerRunning(_TimerBinary):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "timer_running"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "timer_running")

    @property
    def is_on(self) -> bool:
        return bool(self._t().get("active"))


class SplitFlapAlarmFiring(_TimerBinary):
    _attr_device_class = BinarySensorDeviceClass.SOUND
    _attr_translation_key = "alarm_firing"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "alarm_firing")

    @property
    def is_on(self) -> bool:
        return bool(self._t().get("alarm_firing"))
