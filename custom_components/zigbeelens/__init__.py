"""ZigbeeLens Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZigbeeLensApiClient
from .const import (
    CONF_CORE_URL,
    CONF_PANEL_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import ZigbeeLensDataUpdateCoordinator
from .panel import async_register_panel, async_unregister_panel, async_update_panel_core_url
from .repairs import async_clear_repairs, async_manage_repairs

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up ZigbeeLens from configuration.yaml (unused; config flow only)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ZigbeeLens from a config entry."""
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, False))
    client = ZigbeeLensApiClient(
        session,
        entry.data[CONF_CORE_URL],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, False),
    )
    scan_interval = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    coordinator = ZigbeeLensDataUpdateCoordinator(hass, client, scan_interval, entry)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator, "client": client}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if entry.data.get(CONF_PANEL_ENABLED, True):
        await async_register_panel(hass, entry.entry_id, client.core_url)
    else:
        async_update_panel_core_url(hass, client.core_url)

    async def _handle_coordinator_update() -> None:
        async_manage_repairs(hass, coordinator)

    entry.async_on_unload(coordinator.async_add_listener(_handle_coordinator_update))
    async_manage_repairs(hass, coordinator)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        if entry.data.get(CONF_PANEL_ENABLED, True):
            await async_unregister_panel(hass, entry.entry_id)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        async_clear_repairs(hass)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry."""
    if entry.version == 1:
        return True
    return False
