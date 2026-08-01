"""Texts: the HH:MM and day fields behind the alarms, the Quiet-Time schedule and
the dim schedule — each created only when its capability section is supported."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SplitFlapCoordinator
from .entity import SplitFlapEntity

_HHMM = r"([01]?\d|2[0-3]):[0-5]\d"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    tsup = data.get("timer", {}).get("supported", {})
    gsup = data.get("gw", {}).get("supported", {})
    entities: list[TextEntity] = []
    if gsup.get("quiet"):
        entities.append(SplitFlapQuietField(coordinator, "start"))
        entities.append(SplitFlapQuietField(coordinator, "end"))
        entities.append(SplitFlapQuietDays(coordinator))
    if gsup.get("brightness"):
        entities.append(SplitFlapDimField(coordinator, "start"))
        entities.append(SplitFlapDimField(coordinator, "end"))
    if tsup.get("alarms"):
        for slot in range(4):
            entities.append(SplitFlapAlarmTime(coordinator, slot))
            entities.append(SplitFlapAlarmDays(coordinator, slot))
    if entities:
        async_add_entities(entities)


class _GwText(SplitFlapEntity, TextEntity):
    _section = ""

    def _gw(self) -> dict:
        return (self.coordinator.data or {}).get("gw", {})

    @property
    def available(self) -> bool:
        return super().available and bool(
            self._gw().get("supported", {}).get(self._section))

    async def _patch(self, patch: dict) -> None:
        await self.coordinator.client.gateway_settings_patch(patch)
        await self.coordinator.async_request_refresh()


class SplitFlapQuietField(_GwText):
    _attr_pattern = _HHMM
    _attr_icon = "mdi:clock-outline"
    _section = "quiet"

    def __init__(self, coordinator: SplitFlapCoordinator, field: str) -> None:
        super().__init__(coordinator, f"quiet_{field}")
        self._field = field
        self._attr_translation_key = f"quiet_{field}"

    @property
    def native_value(self) -> str | None:
        return self._gw().get("quiet", {}).get("schedule", {}).get(self._field)

    async def async_set_value(self, value: str) -> None:
        await self._patch({"quiet": {"schedule": {self._field: value}}})


class SplitFlapQuietDays(_GwText):
    _attr_icon = "mdi:calendar-week"
    _section = "quiet"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "quiet_days")
        self._attr_translation_key = "quiet_days"

    @property
    def native_value(self) -> str | None:
        return self._gw().get("quiet", {}).get("schedule", {}).get("days")

    async def async_set_value(self, value: str) -> None:
        await self._patch({"quiet": {"schedule": {"days": value}}})


class SplitFlapDimField(_GwText):
    _attr_pattern = _HHMM
    _attr_icon = "mdi:clock-outline"
    _section = "brightness"

    def __init__(self, coordinator: SplitFlapCoordinator, field: str) -> None:
        super().__init__(coordinator, f"dim_{field}")
        self._field = field
        self._attr_translation_key = f"dim_{field}"

    @property
    def native_value(self) -> str | None:
        return self._gw().get("dim", {}).get(self._field)

    async def async_set_value(self, value: str) -> None:
        await self._patch({"dim": {self._field: value}})


class _AlarmText(SplitFlapEntity, TextEntity):
    def __init__(self, coordinator: SplitFlapCoordinator, slot: int, kind: str) -> None:
        super().__init__(coordinator, f"alarm{slot + 1}_{kind}")
        self._slot = slot
        self._attr_translation_key = f"alarm{slot + 1}_{kind}"

    def _slot_doc(self) -> dict:
        alarms = (self.coordinator.data or {}).get("timer", {}).get("alarms") or []
        return alarms[self._slot] if self._slot < len(alarms) else {}

    @property
    def available(self) -> bool:
        return super().available and bool(
            (self.coordinator.data or {}).get("timer", {}).get("supported", {}).get("alarms"))


class SplitFlapAlarmTime(_AlarmText):
    _attr_pattern = _HHMM
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator: SplitFlapCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot, "time")

    @property
    def native_value(self) -> str | None:
        return self._slot_doc().get("time")

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.client.alarm_patch(self._slot, time=value)
        await self.coordinator.async_request_refresh()


class SplitFlapAlarmDays(_AlarmText):
    _attr_icon = "mdi:calendar-week"

    def __init__(self, coordinator: SplitFlapCoordinator, slot: int) -> None:
        super().__init__(coordinator, slot, "days")

    @property
    def native_value(self) -> str | None:
        return self._slot_doc().get("days")

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.client.alarm_patch(self._slot, days=value)
        await self.coordinator.async_request_refresh()
