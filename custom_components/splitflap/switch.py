"""Switches: Quiet Time (now + schedule), the speaker, the dim schedule, and the
four alarm slots — each created only when its capability section is supported."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SplitFlapCoordinator
from .entity import SplitFlapEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    tsup = data.get("timer", {}).get("supported", {})
    gsup = data.get("gw", {}).get("supported", {})
    entities: list[SwitchEntity] = []
    if gsup.get("quiet"):
        entities.append(SplitFlapQuietNow(coordinator))
        entities.append(SplitFlapQuietSchedule(coordinator))
    if gsup.get("sound"):
        entities.append(SplitFlapSoundEnabled(coordinator))
    if gsup.get("brightness"):
        entities.append(SplitFlapDimEnabled(coordinator))
    if tsup.get("alarms"):
        entities += [SplitFlapAlarmEnabled(coordinator, slot) for slot in range(4)]
    if entities:
        async_add_entities(entities)


class _GwSwitch(SplitFlapEntity, SwitchEntity):
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


class SplitFlapQuietNow(_GwSwitch):
    """Quiet Time right now. Turning it OFF inside a scheduled window is refused by
    the firmware (the schedule wins) — the next refresh shows what actually holds."""

    _attr_translation_key = "quiet_now"
    _attr_icon = "mdi:volume-off"
    _section = "quiet"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "quiet_now")

    @property
    def is_on(self) -> bool:
        return bool(self._gw().get("quiet", {}).get("on"))

    async def async_turn_on(self, **kwargs) -> None:
        await self._patch({"quiet": {"on": True}})

    async def async_turn_off(self, **kwargs) -> None:
        await self._patch({"quiet": {"on": False}})


class SplitFlapQuietSchedule(_GwSwitch):
    _attr_translation_key = "quiet_schedule"
    _attr_icon = "mdi:calendar-clock"
    _section = "quiet"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "quiet_schedule")

    @property
    def is_on(self) -> bool:
        return bool(self._gw().get("quiet", {}).get("schedule", {}).get("enabled"))

    async def async_turn_on(self, **kwargs) -> None:
        await self._patch({"quiet": {"schedule": {"enabled": True}}})

    async def async_turn_off(self, **kwargs) -> None:
        await self._patch({"quiet": {"schedule": {"enabled": False}}})


class SplitFlapSoundEnabled(_GwSwitch):
    _attr_translation_key = "sound_enabled"
    _attr_icon = "mdi:volume-high"
    _section = "sound"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "sound_enabled")

    @property
    def is_on(self) -> bool:
        return bool(self._gw().get("sound", {}).get("enabled"))

    async def async_turn_on(self, **kwargs) -> None:
        await self._patch({"sound": {"enabled": True}})

    async def async_turn_off(self, **kwargs) -> None:
        await self._patch({"sound": {"enabled": False}})


class SplitFlapDimEnabled(_GwSwitch):
    _attr_translation_key = "dim_enabled"
    _attr_icon = "mdi:theme-light-dark"
    _section = "brightness"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "dim_enabled")

    @property
    def is_on(self) -> bool:
        return bool(self._gw().get("dim", {}).get("enabled"))

    async def async_turn_on(self, **kwargs) -> None:
        await self._patch({"dim": {"enabled": True}})

    async def async_turn_off(self, **kwargs) -> None:
        await self._patch({"dim": {"enabled": False}})


class SplitFlapAlarmEnabled(SplitFlapEntity, SwitchEntity):
    _attr_icon = "mdi:alarm"

    def __init__(self, coordinator: SplitFlapCoordinator, slot: int) -> None:
        super().__init__(coordinator, f"alarm{slot + 1}")
        self._slot = slot
        self._attr_translation_key = f"alarm{slot + 1}"

    def _slot_doc(self) -> dict:
        alarms = (self.coordinator.data or {}).get("timer", {}).get("alarms") or []
        return alarms[self._slot] if self._slot < len(alarms) else {}

    @property
    def available(self) -> bool:
        return super().available and bool(
            (self.coordinator.data or {}).get("timer", {}).get("supported", {}).get("alarms"))

    @property
    def is_on(self) -> bool:
        return bool(self._slot_doc().get("enabled"))

    @property
    def extra_state_attributes(self) -> dict:
        s = self._slot_doc()
        return {"time": s.get("time"), "days": s.get("days")}

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.client.alarm_patch(self._slot, enabled=True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.client.alarm_patch(self._slot, enabled=False)
        await self.coordinator.async_request_refresh()
