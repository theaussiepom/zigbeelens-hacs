"""Sidebar panel registration for ZigbeeLens Core UI."""

from __future__ import annotations

import logging

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_register_panel(hass: HomeAssistant, entry_id: str, core_url: str) -> None:
    """Register a sidebar panel that embeds or links to Core UI."""
    runtime = hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})
    if runtime.get("panel_registered"):
        return

    frontend.async_register_built_in_panel(
        hass,
        component_name=DOMAIN,
        sidebar_title="ZigbeeLens",
        sidebar_icon="mdi:zigbee",
        frontend_url_path=DOMAIN,
        require_admin=False,
        config={
            "mode": "iframe",
            "url": core_url,
            "icon": "mdi:zigbee",
            "title": "ZigbeeLens",
            "trust_external": False,
        },
    )
    runtime["panel_registered"] = True
    _LOGGER.debug("Registered ZigbeeLens sidebar panel for %s", core_url)


async def async_unregister_panel(hass: HomeAssistant, entry_id: str) -> None:
    runtime = hass.data.get(DOMAIN, {}).get(entry_id, {})
    if not runtime.get("panel_registered"):
        return
    frontend.async_remove_panel(hass, DOMAIN)
    runtime["panel_registered"] = False
