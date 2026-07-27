"""Config flow for ZigbeeLens."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Self

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import ZigbeeLensApiClient
from .api_token import optional_core_api_token
from .const import (
    CONF_API_TOKEN,
    CONF_CORE_URL,
    CONF_PANEL_ENABLED,
    CONF_REMOVE_API_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    CONFIG_ENTRY_VERSION,
    DEFAULT_CORE_URL,
    DEFAULT_PANEL_ENABLED,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .core_origin import InvalidCoreOrigin, canonicalize_core_origin
from .exceptions import (
    ZigbeeLensApiError,
    ZigbeeLensAuthError,
    ZigbeeLensConnectionError,
    ZigbeeLensInvalidResponseError,
)

_LOGGER = logging.getLogger(__name__)

_API_TOKEN_SELECTOR = TextSelector(
    TextSelectorConfig(
        type=TextSelectorType.PASSWORD,
        autocomplete="off",
    )
)


def _normalize_core_url(url: str) -> str:
    """Return a canonical HTTP(S) Core origin or raise ValueError('invalid_url')."""
    try:
        return canonicalize_core_origin(url)
    except InvalidCoreOrigin:
        raise ValueError("invalid_url") from None


def _token_schema_field(*, required: bool = False) -> Any:
    key = vol.Required(CONF_API_TOKEN) if required else vol.Optional(CONF_API_TOKEN, default="")
    return {key: _API_TOKEN_SELECTOR}


def _user_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CORE_URL, default=DEFAULT_CORE_URL): str,
            **_token_schema_field(),
            vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
            vol.Optional(CONF_PANEL_ENABLED, default=DEFAULT_PANEL_ENABLED): bool,
        }
    )


def _reauth_schema() -> vol.Schema:
    return vol.Schema({**_token_schema_field()})


def _reconfigure_schema(entry: ConfigEntry) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_CORE_URL,
                default=entry.data.get(CONF_CORE_URL, DEFAULT_CORE_URL),
            ): str,
            **_token_schema_field(),
            vol.Optional(CONF_REMOVE_API_TOKEN, default=False): bool,
            vol.Optional(
                CONF_VERIFY_SSL,
                default=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): bool,
        }
    )


async def _validate_core(
    hass: HomeAssistant,
    core_url: str,
    verify_ssl: bool,
    api_token: str = "",
) -> dict[str, Any]:
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    client = ZigbeeLensApiClient(
        session,
        core_url,
        verify_ssl=verify_ssl,
        api_token=api_token,
    )
    return await client.async_validate_core()


def _map_validation_error(exc: BaseException) -> str:
    if isinstance(exc, ZigbeeLensAuthError):
        return "invalid_auth"
    if isinstance(exc, ZigbeeLensConnectionError):
        return "cannot_connect"
    if isinstance(exc, ZigbeeLensInvalidResponseError):
        return "invalid_response"
    if isinstance(exc, ZigbeeLensApiError):
        return "cannot_connect"
    return "unknown"


class ZigbeeLensConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ZigbeeLens."""

    VERSION = CONFIG_ENTRY_VERSION

    @callback
    def _update_entry_and_abort_once(
        self,
        entry: ConfigEntry,
        *,
        reason: str,
        data: Mapping[str, Any] | None = None,
        data_updates: Mapping[str, Any] | None = None,
        unique_id: str | None = None,
    ) -> ConfigFlowResult:
        """Update through APIs shared by HA 2025.1/current and reload once."""
        if data is not None and data_updates is not None:
            raise ValueError("data and data_updates are mutually exclusive")
        updated_data: Mapping[str, Any] | None = data
        if data_updates is not None:
            updated_data = {**entry.data, **data_updates}
        kwargs: dict[str, Any] = {}
        if updated_data is not None:
            kwargs["data"] = updated_data
        if unique_id is not None:
            kwargs["unique_id"] = unique_id

        had_update_listener = bool(entry.update_listeners)
        changed = self.hass.config_entries.async_update_entry(entry, **kwargs)
        if not changed or not had_update_listener:
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason=reason)

    def is_matching(self, other_flow: Self) -> bool:
        """Match any concurrent ZigbeeLens user flow (single-entry ownership)."""
        return True

    async def _async_abort_if_single_instance_busy(self) -> ConfigFlowResult | None:
        """Abort when another entry exists or another user flow is in progress."""
        # Companion panel/repair IDs are integration-level singletons.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if self.hass.config_entries.flow.async_has_matching_flow(self):
            return self.async_abort(reason="single_instance_allowed")
        try:
            # Domain-scoped unique ID: concurrent flows share one claim.
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
        except AbortFlow:
            return self.async_abort(reason="single_instance_allowed")
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        abort = await self._async_abort_if_single_instance_busy()
        if abort is not None:
            return abort

        if user_input is not None:
            try:
                core_url = _normalize_core_url(user_input[CONF_CORE_URL])
            except ValueError:
                return self.async_show_form(
                    step_id="user",
                    data_schema=_user_schema(),
                    errors={"base": "invalid_url"},
                )

            try:
                api_token = optional_core_api_token(user_input.get(CONF_API_TOKEN, ""))
            except ValueError:
                return self.async_show_form(
                    step_id="user",
                    data_schema=_user_schema(),
                    errors={"base": "invalid_auth"},
                )

            verify_ssl = bool(user_input[CONF_VERIFY_SSL])
            try:
                await _validate_core(self.hass, core_url, verify_ssl, api_token)
            except Exception as err:  # noqa: BLE001 — map known API errors; unknown → unknown
                if isinstance(
                    err,
                    (
                        ZigbeeLensAuthError,
                        ZigbeeLensConnectionError,
                        ZigbeeLensInvalidResponseError,
                        ZigbeeLensApiError,
                    ),
                ):
                    errors["base"] = _map_validation_error(err)
                else:
                    _LOGGER.exception("Unexpected ZigbeeLens config validation error")
                    errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="ZigbeeLens",
                    data={
                        CONF_CORE_URL: core_url,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_API_TOKEN: api_token,
                    },
                    options={
                        CONF_PANEL_ENABLED: bool(user_input[CONF_PANEL_ENABLED]),
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start linked reauthentication when Core rejects stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace or clear the HACS bearer credential after Core rejection."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            try:
                api_token = optional_core_api_token(user_input.get(CONF_API_TOKEN, ""))
            except ValueError:
                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=_reauth_schema(),
                    errors={"base": "invalid_auth"},
                )

            core_url = reauth_entry.data[CONF_CORE_URL]
            verify_ssl = bool(reauth_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
            try:
                await _validate_core(self.hass, core_url, verify_ssl, api_token)
            except Exception as err:  # noqa: BLE001
                if isinstance(
                    err,
                    (
                        ZigbeeLensAuthError,
                        ZigbeeLensConnectionError,
                        ZigbeeLensInvalidResponseError,
                        ZigbeeLensApiError,
                    ),
                ):
                    errors["base"] = _map_validation_error(err)
                else:
                    _LOGGER.exception("Unexpected ZigbeeLens reauth validation error")
                    errors["base"] = "unknown"
            else:
                return self._update_entry_and_abort_once(
                    reauth_entry,
                    reason="reauth_successful",
                    data_updates={CONF_API_TOKEN: api_token},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Proactively change Core URL, TLS policy, or bearer credential."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                core_url = _normalize_core_url(user_input[CONF_CORE_URL])
            except ValueError:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=_reconfigure_schema(entry),
                    errors={"base": "invalid_url"},
                )

            submitted_token = user_input.get(CONF_API_TOKEN, "")
            remove_token = bool(user_input.get(CONF_REMOVE_API_TOKEN, False))
            if submitted_token and remove_token:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=_reconfigure_schema(entry),
                    errors={"base": "token_conflict"},
                )

            current_token = entry.data.get(CONF_API_TOKEN, "")
            if not isinstance(current_token, str):
                current_token = ""

            if remove_token:
                api_token = ""
            elif submitted_token:
                try:
                    api_token = optional_core_api_token(submitted_token)
                except ValueError:
                    return self.async_show_form(
                        step_id="reconfigure",
                        data_schema=_reconfigure_schema(entry),
                        errors={"base": "invalid_auth"},
                    )
            else:
                try:
                    api_token = optional_core_api_token(current_token)
                except ValueError:
                    return self.async_show_form(
                        step_id="reconfigure",
                        data_schema=_reconfigure_schema(entry),
                        errors={"base": "invalid_auth"},
                    )

            await self.async_set_unique_id(core_url)
            existing = self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, core_url
            )
            if existing is not None and existing.entry_id != entry.entry_id:
                return self.async_abort(reason="already_configured")

            verify_ssl = bool(user_input[CONF_VERIFY_SSL])
            try:
                await _validate_core(self.hass, core_url, verify_ssl, api_token)
            except Exception as err:  # noqa: BLE001
                if isinstance(
                    err,
                    (
                        ZigbeeLensAuthError,
                        ZigbeeLensConnectionError,
                        ZigbeeLensInvalidResponseError,
                        ZigbeeLensApiError,
                    ),
                ):
                    errors["base"] = _map_validation_error(err)
                else:
                    _LOGGER.exception("Unexpected ZigbeeLens reconfigure validation error")
                    errors["base"] = "unknown"
            else:
                updated_data = {
                    **entry.data,
                    CONF_CORE_URL: core_url,
                    CONF_VERIFY_SSL: verify_ssl,
                    CONF_API_TOKEN: api_token,
                }
                return self._update_entry_and_abort_once(
                    entry,
                    reason="reconfigure_successful",
                    unique_id=core_url,
                    data=updated_data,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_reconfigure_schema(entry),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ZigbeeLensOptionsFlow:
        return ZigbeeLensOptionsFlow()


class ZigbeeLensOptionsFlow(OptionsFlow):
    """Optional panel and polling behavior (credentials live in Reconfigure)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.config_entry

        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_PANEL_ENABLED: bool(user_input[CONF_PANEL_ENABLED]),
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                },
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PANEL_ENABLED,
                        default=entry.options.get(
                            CONF_PANEL_ENABLED,
                            entry.data.get(CONF_PANEL_ENABLED, DEFAULT_PANEL_ENABLED),
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(int, vol.Range(min=15, max=900)),
                }
            ),
            errors=errors,
        )
