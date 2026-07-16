"""SplitFlap — drive a split-flap wall from Home Assistant.

A native integration that talks to a running SplitFlap Gateway Companion over its REST API.
It exposes what a Vestaboard integration can't: the installed apps and saved playlists, a
sensor for which app is on screen, and a message service with a timed auto-revert.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import SplitFlapClient, SplitFlapError
from .const import (ATTR_SECONDS, ATTR_STYLE, ATTR_TEXT, CONF_DISPLAY, CONF_URL,
                    DOMAIN, SERVICE_MESSAGE, stable_unique_base)
from .coordinator import SplitFlapCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.BUTTON, Platform.IMAGE]

MESSAGE_SCHEMA = vol.Schema({
    vol.Required(ATTR_TEXT): cv.string,
    vol.Optional(ATTR_STYLE): cv.string,
    # 0/absent = show until something else changes it; >0 = revert after N seconds.
    vol.Optional(ATTR_SECONDS): vol.All(vol.Coerce(int), vol.Range(min=0, max=86400)),
})


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 → v2: entity unique_ids were ``{entry_id}_<key>`` — gone forever on a
    remove + re-add, orphaning history and customisations. Rewrite them onto the
    stable ``netloc[/display]`` base the config flow already computes."""
    if entry.version > 2:
        return False   # entry from a future version — nothing safe to do

    if entry.version == 1:
        base = stable_unique_base(entry)
        old_prefix = f"{entry.entry_id}_"

        @callback
        def _new_unique_id(entity_entry: er.RegistryEntry) -> dict[str, str] | None:
            if entity_entry.unique_id.startswith(old_prefix):
                key = entity_entry.unique_id[len(old_prefix):]
                return {"new_unique_id": f"{base}_{key}"}
            return None   # already migrated (or never entry_id-based)

        try:
            await er.async_migrate_entries(hass, entry.entry_id, _new_unique_id)
        except ValueError:
            # A collision means the stable id is already taken (e.g. the same box
            # added twice pre-unique_id). Keep the old ids rather than brick setup.
            _LOGGER.warning("Could not migrate unique_ids for %s; keeping old ids",
                            entry.title)

        # The device rides along so its name/area customisations survive too.
        dev_reg = dr.async_get(hass)
        if device := dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)}):
            dev_reg.async_update_device(device.id, new_identifiers={(DOMAIN, base)})

        hass.config_entries.async_update_entry(
            entry, unique_id=entry.unique_id or base, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # An entry is ONE display's device. Entries created before multi-display
    # support carry no display id, which the companion reads as its default wall.
    client = SplitFlapClient(async_get_clientsession(hass), entry.data[CONF_URL],
                             entry.data.get(CONF_DISPLAY, ""))
    coordinator = SplitFlapCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_message_service(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_MESSAGE)
    return unloaded


def _register_message_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_MESSAGE):
        return

    async def handle_message(call: ServiceCall) -> None:
        # Send to every configured display (usually one). A device/target selector is
        # unnecessary for the common single-wall case and keeps the service simple;
        # with several walls configured this is deliberately a broadcast.
        for coordinator in hass.data.get(DOMAIN, {}).values():
            try:
                await coordinator.client.message(
                    call.data[ATTR_TEXT], call.data.get(ATTR_STYLE), call.data.get(ATTR_SECONDS))
            except SplitFlapError as err:
                raise HomeAssistantError(str(err)) from err
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_MESSAGE, handle_message, schema=MESSAGE_SCHEMA)
