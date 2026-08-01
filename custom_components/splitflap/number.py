"""Numbers: start the kitchen timer (minutes), speaker volume, panel brightness,
dim level — each created only when its capability section is supported."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    entities: list[NumberEntity] = []
    if tsup.get("timer"):
        entities.append(SplitFlapTimerMinutes(coordinator))
    if gsup.get("sound"):
        entities.append(SplitFlapVolume(coordinator))
    if gsup.get("brightness"):
        entities.append(SplitFlapBrightness(coordinator))
        entities.append(SplitFlapDimLevel(coordinator))
    if entities:
        async_add_entities(entities)


class SplitFlapTimerMinutes(SplitFlapEntity, NumberEntity):
    """Write-to-start: setting the value starts that many minutes on the panel. The
    state tracks the remaining minutes while a countdown runs (else the last start)."""

    _attr_translation_key = "timer_minutes"
    _attr_icon = "mdi:timer-play-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 1440
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "timer_minutes")
        self._last = 5

    @property
    def available(self) -> bool:
        return super().available and bool(
            (self.coordinator.data or {}).get("timer", {}).get("supported", {}).get("timer"))

    @property
    def native_value(self) -> float:
        t = (self.coordinator.data or {}).get("timer", {}).get("timer", {})
        if t.get("active"):
            return max(1, -(-int(t.get("remaining") or 0) // 60))
        return self._last

    async def async_set_native_value(self, value: float) -> None:
        self._last = max(1, int(value))
        await self.coordinator.client.timer_start(self._last * 60)
        await self.coordinator.async_request_refresh()


class _SettingsNumber(SplitFlapEntity, NumberEntity):
    _section = ""                                     # "sound" | "brightness"

    @property
    def available(self) -> bool:
        return super().available and bool(
            (self.coordinator.data or {}).get("gw", {}).get("supported", {}).get(self._section))


class SplitFlapVolume(_SettingsNumber):
    _attr_translation_key = "sound_volume"
    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5
    _section = "sound"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "sound_volume")

    @property
    def native_value(self) -> float | None:
        snd = (self.coordinator.data or {}).get("gw", {}).get("sound")
        return None if snd is None else snd.get("volume")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.gateway_settings_patch(
            {"sound": {"volume": int(value)}})
        await self.coordinator.async_request_refresh()


class SplitFlapBrightness(_SettingsNumber):
    _attr_translation_key = "panel_brightness"
    _attr_icon = "mdi:brightness-6"
    _attr_native_min_value = 1
    _attr_native_max_value = 255
    _attr_native_step = 1
    _section = "brightness"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "panel_brightness")

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data or {}).get("gw", {}).get("brightness")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.gateway_settings_patch({"brightness": int(value)})
        await self.coordinator.async_request_refresh()


class SplitFlapDimLevel(_SettingsNumber):
    _attr_translation_key = "dim_level"
    _attr_icon = "mdi:brightness-4"
    _attr_native_min_value = 1
    _attr_native_max_value = 255
    _attr_native_step = 1
    _section = "brightness"

    def __init__(self, coordinator: SplitFlapCoordinator) -> None:
        super().__init__(coordinator, "dim_level")

    @property
    def native_value(self) -> float | None:
        dim = (self.coordinator.data or {}).get("gw", {}).get("dim")
        return None if dim is None else dim.get("level")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.client.gateway_settings_patch(
            {"dim": {"level": int(value)}})
        await self.coordinator.async_request_refresh()
