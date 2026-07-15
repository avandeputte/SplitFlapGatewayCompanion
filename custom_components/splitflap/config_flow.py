"""Config flow: point Home Assistant at a running companion — and pick a wall.

A companion can drive several displays. Each config entry is ONE display's device,
so a multi-wall companion is added once per wall: the flow asks for the URL, then —
only when there is actually a choice — which display this entry is for.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SplitFlapClient, SplitFlapError
from .const import CONF_DISPLAY, CONF_URL, DOMAIN

DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_URL, default="http://homeassistant.local:8000"): str,
})


class SplitFlapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the companion's URL, confirm it answers, then pick a display."""

    VERSION = 1

    def __init__(self) -> None:
        self._url: str = ""
        self._displays: dict[str, str] = {}   # id -> name

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            if "://" not in url:
                url = f"http://{url}"

            client = SplitFlapClient(async_get_clientsession(self.hass), url)
            try:
                await client.health()          # proves it's reachable AND is a companion
            except SplitFlapError:
                errors["base"] = "cannot_connect"
            else:
                self._url = url
                # Which wall? Only ask when there is actually a choice. An older
                # companion has no /api/displays at all — one wall, no registry.
                try:
                    doc = await client.displays()
                    displays = [d for d in (doc.get("displays") or [])
                                if d.get("enabled", True) and d.get("id")]
                except SplitFlapError:
                    displays = []
                if len(displays) > 1:
                    self._displays = {d["id"]: str(d.get("name") or d["id"]) for d in displays}
                    return await self.async_step_display()

                # One companion per URL — a second entry for the same box is a mistake.
                await self.async_set_unique_id(urlparse(url).netloc)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="SplitFlap", data={CONF_URL: url})

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

    async def async_step_display(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            display = user_input[CONF_DISPLAY]
            # One entry per (companion, display) — adding the office wall twice is a
            # mistake; adding the office wall NEXT TO the hall wall is the point.
            await self.async_set_unique_id(f"{urlparse(self._url).netloc}/{display}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._displays.get(display, display),
                data={CONF_URL: self._url, CONF_DISPLAY: display},
            )

        return self.async_show_form(
            step_id="display",
            data_schema=vol.Schema({vol.Required(CONF_DISPLAY): vol.In(self._displays)}),
        )
