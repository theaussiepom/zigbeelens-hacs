"""ZigbeeLens Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZigbeeLensApiClient
from .api_token import optional_core_api_token
from .compatibility import EnrichmentContractState
from .const import (
    CONF_API_TOKEN,
    CONF_CORE_URL,
    CONF_PANEL_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    CONFIG_ENTRY_VERSION,
    DEFAULT_PANEL_ENABLED,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import ZigbeeLensDataUpdateCoordinator
from .enrichment_manager import HomeAssistantEnrichmentManager
from .exceptions import (
    ZigbeeLensApiError,
    ZigbeeLensAuthError,
    ZigbeeLensInvalidResponseError,
)
from .panel import async_register_panel, async_unregister_panel
from .repairs import async_clear_repairs, async_manage_repairs

_LOGGER = logging.getLogger(__name__)

_AUTH_FAILED_MESSAGE = "Core credentials need to be updated"
_GLOBAL_OWNER_KEY = "_global_owner_entry_id"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _panel_enabled(entry: ConfigEntry) -> bool:
    """Return the canonical panel option with a legacy-data fallback."""
    return bool(
        entry.options.get(
            CONF_PANEL_ENABLED,
            entry.data.get(CONF_PANEL_ENABLED, DEFAULT_PANEL_ENABLED),
        )
    )


def _primary_entry_id(hass: HomeAssistant) -> str | None:
    """Deterministic primary entry when multiple unsupported entries exist."""
    entries = [
        entry
        for entry in (hass.config_entries.async_entries(DOMAIN) or [])
        if not getattr(entry, "disabled_by", None)
    ]
    if not entries:
        return None
    return sorted(entries, key=lambda item: item.entry_id)[0].entry_id


def _entry_owns_global_resources(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Only the primary entry may own the singleton panel/repair resources."""
    domain_data = hass.data.get(DOMAIN) or {}
    runtime_owner = domain_data.get(_GLOBAL_OWNER_KEY)
    if (
        isinstance(runtime_owner, str)
        and runtime_owner in domain_data
        and runtime_owner == entry.entry_id
    ):
        return True
    primary = _primary_entry_id(hass)
    # No known entries yet (or unreadable listing): treat current entry as owner.
    return primary is None or entry.entry_id == primary


def _loaded_runtime_entry_ids(hass: HomeAssistant) -> list[str]:
    """Return deterministic loaded entry IDs, excluding domain-global state."""
    domain_data = hass.data.get(DOMAIN) or {}
    return sorted(
        str(entry_id)
        for entry_id, runtime in domain_data.items()
        if not str(entry_id).startswith("_")
        and isinstance(runtime, dict)
        and runtime.get("coordinator") is not None
    )


async def _async_promote_loaded_global_owner(hass: HomeAssistant) -> None:
    """Promote a loaded legacy secondary after the current owner unloads."""
    domain_data = hass.data.get(DOMAIN) or {}
    domain_data.pop(_GLOBAL_OWNER_KEY, None)
    entries_by_id = {
        candidate.entry_id: candidate
        for candidate in (hass.config_entries.async_entries(DOMAIN) or [])
    }
    for entry_id in _loaded_runtime_entry_ids(hass):
        entry = entries_by_id.get(entry_id)
        runtime = domain_data.get(entry_id)
        if entry is None or not isinstance(runtime, dict):
            continue
        coordinator = runtime.get("coordinator")
        client = runtime.get("client")
        manager = runtime.get("enrichment_manager")
        if coordinator is None or client is None:
            continue
        domain_data[_GLOBAL_OWNER_KEY] = entry_id
        if _panel_enabled(entry):
            await async_register_panel(hass, entry_id, client.core_url)
        else:
            await async_unregister_panel(hass, entry_id)
        async_manage_repairs(hass, coordinator, manager)
        return


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up ZigbeeLens from configuration.yaml (unused; config flow only)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZigbeeLens from a config entry."""
    existing = hass.config_entries.async_entries(DOMAIN)
    owns_globals = _entry_owns_global_resources(hass, entry)
    if len(existing) > 1:
        # Unsupported multi-entry state (legacy/broken). Continue setup per entry
        # without touching other entries' runtime data or credentials.
        _LOGGER.error(
            "Multiple ZigbeeLens config entries are present; only one is supported"
        )
        if not owns_globals:
            _LOGGER.error(
                "ZigbeeLens entry is not the primary singleton owner; "
                "skipping panel and repair registration"
            )

    try:
        api_token = optional_core_api_token(entry.data.get(CONF_API_TOKEN, ""))
    except ValueError:
        raise ConfigEntryAuthFailed(_AUTH_FAILED_MESSAGE) from None

    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, False))
    try:
        client = ZigbeeLensApiClient(
            session,
            entry.data[CONF_CORE_URL],
            verify_ssl=entry.data.get(CONF_VERIFY_SSL, False),
            api_token=api_token,
        )
    except ZigbeeLensAuthError:
        raise ConfigEntryAuthFailed(_AUTH_FAILED_MESSAGE) from None
    except ZigbeeLensInvalidResponseError:
        # Fail closed without initiating HTTP or logging credential-bearing URLs.
        raise ConfigEntryError("Invalid ZigbeeLens Core URL") from None

    scan_interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    coordinator = ZigbeeLensDataUpdateCoordinator(hass, client, scan_interval, entry)

    await coordinator.async_config_entry_first_refresh()

    manager: HomeAssistantEnrichmentManager

    def _handle_enrichment_diagnostics(_diagnostics: object) -> None:
        """Refresh singleton repairs immediately for the current runtime owner."""
        domain_data = hass.data.get(DOMAIN) or {}
        runtime = domain_data.get(entry.entry_id)
        if (
            domain_data.get(_GLOBAL_OWNER_KEY) != entry.entry_id
            or not isinstance(runtime, dict)
            or runtime.get("enrichment_manager") is not manager
        ):
            return
        async_manage_repairs(hass, coordinator, manager)

    manager = HomeAssistantEnrichmentManager(
        hass,
        entry,
        client,
        capability_provider=lambda: (
            coordinator.data.enrichment_contract_state
            if coordinator.data is not None
            else EnrichmentContractState.UNAVAILABLE
        ),
        on_diagnostics_changed=_handle_enrichment_diagnostics,
    )
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "enrichment_manager": manager,
    }

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await manager.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _handle_coordinator_update() -> None:
        if getattr(coordinator, "auth_failed", False) is not True:
            manager.async_request_sync()
        if hass.data.get(DOMAIN, {}).get(_GLOBAL_OWNER_KEY) == entry.entry_id:
            async_manage_repairs(hass, coordinator, manager)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))

    if owns_globals:
        prior_owner = hass.data[DOMAIN].get(_GLOBAL_OWNER_KEY)
        if isinstance(prior_owner, str) and prior_owner != entry.entry_id:
            await async_unregister_panel(hass, prior_owner)
            async_clear_repairs(hass)
        hass.data[DOMAIN][_GLOBAL_OWNER_KEY] = entry.entry_id
        if _panel_enabled(entry):
            await async_register_panel(hass, entry.entry_id, client.core_url)
        else:
            await async_unregister_panel(hass, entry.entry_id)

        async_manage_repairs(hass, coordinator, manager)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    owns_globals = _entry_owns_global_resources(hass, entry)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if owns_globals:
            await async_unregister_panel(hass, entry.entry_id)
            async_clear_repairs(hass)
            hass.data[DOMAIN].pop(_GLOBAL_OWNER_KEY, None)
            # A disabled legacy primary will not immediately set up again.
            # Removal promotion is handled after HA removes the entry; a normal
            # reload avoids briefly handing globals to a secondary.
            if bool(getattr(entry, "disabled_by", False)):
                await _async_promote_loaded_global_owner(hass)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear only Core-local HA enrichment after explicit entry removal."""
    remaining_entries = [
        candidate
        for candidate in (hass.config_entries.async_entries(DOMAIN) or [])
        if candidate.entry_id != entry.entry_id
    ]
    if remaining_entries:
        domain_data = hass.data.get(DOMAIN) or {}
        owner = domain_data.get(_GLOBAL_OWNER_KEY)
        if owner not in _loaded_runtime_entry_ids(hass):
            await _async_promote_loaded_global_owner(hass)
        # Legacy multi-entry installs are unsupported. Clearing any Core while
        # another entry survives could erase that entry's accepted snapshot.
        _LOGGER.warning(
            "Skipped Core-local Home Assistant enrichment clear because another "
            "legacy ZigbeeLens config entry remains"
        )
        return
    try:
        api_token = optional_core_api_token(entry.data.get(CONF_API_TOKEN, ""))
        session = async_get_clientsession(
            hass,
            verify_ssl=entry.data.get(CONF_VERIFY_SSL, False),
        )
        client = ZigbeeLensApiClient(
            session,
            entry.data[CONF_CORE_URL],
            verify_ssl=entry.data.get(CONF_VERIFY_SSL, False),
            api_token=api_token,
        )
        await client.async_clear_home_assistant_enrichment()
    except (ValueError, ZigbeeLensApiError, KeyError) as err:
        # Config-entry removal must complete even if Core is unreachable. Log
        # only the categorical exception type; never the URL, token, or payload.
        _LOGGER.warning(
            "Could not clear Core-local Home Assistant enrichment during "
            "config-entry removal (%s)",
            type(err).__name__,
        )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry."""
    if entry.version == 1:
        data = dict(entry.data)
        options = dict(entry.options)
        options.setdefault(
            CONF_PANEL_ENABLED,
            bool(data.pop(CONF_PANEL_ENABLED, DEFAULT_PANEL_ENABLED)),
        )
        options.setdefault(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=CONFIG_ENTRY_VERSION,
        )
        return True
    return entry.version == CONFIG_ENTRY_VERSION
