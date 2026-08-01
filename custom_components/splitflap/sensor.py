"""Sensors: what's on the flaps, and which app is putting it there."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SplitFlapCoordinator
from .entity import SplitFlapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        SplitFlapMessageSensor(coordinator),
        SplitFlapShowingSensor(coordinator),
    ]
    if (coordinator.data or {}).get("timer", {}).get("supported", {}).get("timer"):
        entities.append(SplitFlapTimerEndsSensor(coordinator))
    async_add_entities(entities)


class SplitFlapMessageSensor(SplitFlapEntity, SensorEntity):
    """What the flaps read right now, as text."""

    _attr_translation_key = "message"
    _attr_icon = "mdi:message-text"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "message")

    @property
    def native_value(self) -> str:
        return self.coordinator.data["text"]

    @property
    def extra_state_attributes(self) -> dict:
        # The individual rows, for a template that wants the layout rather than one string.
        return {"lines": self.coordinator.data["lines"]}


class SplitFlapShowingSensor(SplitFlapEntity, SensorEntity):
    """Which app is on screen — the one thing active_app can't say while a playlist runs."""

    _attr_translation_key = "showing"
    _attr_icon = "mdi:eye"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "showing")

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        current = data["current_app"]
        if current:
            for a in data["apps"]:
                if a["id"] == current:
                    return a["name"]
            return current
        if data["active_playlist"] or data["active_app"]:
            return "Starting…"
        return "Message" if data["text"] else "Idle"

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        return {
            "app_id": data["current_app"],
            "playlist": data["active_playlist"],
        }


class SplitFlapTimerEndsSensor(SplitFlapEntity, SensorEntity):
    """When the Matrix Gateway's countdown lands — a timestamp, so HA renders the
    live remaining time from one stable value. None while no timer runs."""

    _attr_translation_key = "timer_ends"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "timer_ends")

    @property
    def available(self) -> bool:
        return super().available and bool(
            (self.coordinator.data or {}).get("timer", {}).get("supported", {}).get("timer"))

    @property
    def native_value(self):
        from datetime import datetime
        t = (self.coordinator.data or {}).get("timer", {}).get("timer", {})
        ends = t.get("ends_at")
        if not ends:
            return None
        try:
            return datetime.fromisoformat(ends)
        except ValueError:
            return None
