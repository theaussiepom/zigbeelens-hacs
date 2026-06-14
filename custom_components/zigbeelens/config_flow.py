"""Config flow for ZigbeeLens."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZigbeeLensApiClient
from .const import (
    CONF_CORE_URL,
    CONF_PANEL_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_CORE_URL,
    DEFAULT_PANEL_ENABLED,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .exceptions import ZigbeeLensApiError

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CORE_URL, default=DEFAULT_CORE_URL): str,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
        vol.Optional(CONF_PANEL_ENABLED, default=DEFAULT_PANEL_ENABLED): bool,
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CORE_URL): str,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
        vol.Optional(CONF_PANEL_ENABLED, default=DEFAULT_PANEL_ENABLED): bool,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            int, vol.Range(min=15, max=900)
        ),
    }
)


def _normalize_core_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("invalid_url")
    return f"{parsed.scheme}://{parsed.netloc}"


async def _validate_core(hass: HomeAssistant, core_url: str, verify_ssl: bool) -> dict[str, Any]:
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    client = ZigbeeLensApiClient(session, core_url, verify_ssl=verify_ssl)
    return await client.async_validate_core()


class ZigbeeLensConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZigbeeLens."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                core_url = _normalize_core_url(user_input[CONF_CORE_URL])
            except ValueError:
                return self.async_show_form(
                    step_id="user",
                    data_schema=STEP_USER_SCHEMA,
                    errors={"base": "invalid_url"},
                )

            await self.async_set_unique_id(core_url)
            self._abort_if_unique_id_configured()

            verify_ssl = user_input[CONF_VERIFY_SSL]
            try:
                await _validate_core(self.hass, core_url, verify_ssl)
            except ZigbeeLensApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="ZigbeeLens",
                    data={
                        CONF_CORE_URL: core_url,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_PANEL_ENABLED: user_input[CONF_PANEL_ENABLED],
                    },
                    options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ZigbeeLensOptionsFlow:
        return ZigbeeLensOptionsFlow(config_entry)


class ZigbeeLensOptionsFlow(config_entries.OptionsFlow):
    """Handle options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                core_url = _normalize_core_url(user_input[CONF_CORE_URL])
            except ValueError:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._schema(),
                    errors={"base": "invalid_url"},
                )
            verify_ssl = user_input[CONF_VERIFY_SSL]
            try:
                await _validate_core(self.hass, core_url, verify_ssl)
            except ZigbeeLensApiError:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={
                        **self._config_entry.data,
                        CONF_CORE_URL: core_url,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_PANEL_ENABLED: user_input[CONF_PANEL_ENABLED],
                    },
                    options={
                        CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    },
                )
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=self._schema(),
            errors=errors,
        )

    def _schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    CONF_CORE_URL,
                    default=self._config_entry.data.get(CONF_CORE_URL, DEFAULT_CORE_URL),
                ): str,
                vol.Optional(
                    CONF_VERIFY_SSL,
                    default=self._config_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                ): bool,
                vol.Optional(
                    CONF_PANEL_ENABLED,
                    default=self._config_entry.data.get(CONF_PANEL_ENABLED, DEFAULT_PANEL_ENABLED),
                ): bool,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): vol.All(int, vol.Range(min=15, max=900)),
            }
        )
