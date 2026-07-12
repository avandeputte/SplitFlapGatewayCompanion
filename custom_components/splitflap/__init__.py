"""SplitFlap — drive a split-flap wall from Home Assistant.

A native integration that talks to a running SplitFlap Gateway Companion over its REST API.
It exposes what a Vestaboard integration can't: the installed apps and saved playlists, a
sensor for which app is on screen, and a message service with a timed auto-revert.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import SplitFlapClient, SplitFlapError
from .const import (ATTR_SECONDS, ATTR_STYLE, ATTR_TEXT, CONF_URL, DOMAIN,
                    SERVICE_MESSAGE)
from .coordinator import SplitFlapCoordinator

PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.BUTTON]

MESSAGE_SCHEMA = vol.Schema({
    vol.Required(ATTR_TEXT): cv.string,
    vol.Optional(ATTR_STYLE): cv.string,
    # 0/absent = show until something else changes it; >0 = revert after N seconds.
    vol.Optional(ATTR_SECONDS): vol.All(vol.Coerce(int), vol.Range(min=0, max=86400)),
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = SplitFlapClient(async_get_clientsession(hass), entry.data[CONF_URL])
    coordinator = SplitFlapCoordinator(hass, client, entry.title)
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
        # Send to every configured companion (usually one). A device/target selector is
        # unnecessary for the common single-wall case and keeps the service simple.
        for coordinator in hass.data.get(DOMAIN, {}).values():
            try:
                await coordinator.client.message(
                    call.data[ATTR_TEXT], call.data.get(ATTR_STYLE), call.data.get(ATTR_SECONDS))
            except SplitFlapError as err:
                raise HomeAssistantError(str(err)) from err
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_MESSAGE, handle_message, schema=MESSAGE_SCHEMA)
