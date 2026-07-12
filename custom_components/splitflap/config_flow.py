"""Config flow: point Home Assistant at a running companion."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SplitFlapClient, SplitFlapError
from .const import CONF_URL, DOMAIN

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_URL, default="http://homeassistant.local:8000"): str,
})


class SplitFlapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the companion's URL, then confirm it answers."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            if "://" not in url:
                url = f"http://{url}"
            # One companion per URL — a second entry for the same box is a mistake.
            await self.async_set_unique_id(urlparse(url).netloc)
            self._abort_if_unique_id_configured()

            client = SplitFlapClient(async_get_clientsession(self.hass), url)
            try:
                await client.health()          # proves it's reachable AND is a companion
            except SplitFlapError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="SplitFlap", data={CONF_URL: url})

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)
