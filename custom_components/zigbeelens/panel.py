"""Sidebar panel registration for ZigbeeLens Core UI."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _normalize_core_panel_url(core_url: str) -> str:
    """Return a stable Core UI URL for HA iframe panels."""
    parsed = urlparse(core_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return core_url.rstrip("/") + "/"
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}/"


async def async_register_panel(hass: HomeAssistant, entry_id: str, core_url: str) -> None:
    """Register a sidebar panel that embeds Core UI via HA's iframe panel."""
    runtime = hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})
    if runtime.get("panel_registered"):
        return

    panel_url = _normalize_core_panel_url(core_url)
    frontend.async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="ZigbeeLens",
        sidebar_icon="mdi:zigbee",
        frontend_url_path=DOMAIN,
        require_admin=False,
        config={"url": panel_url},
    )
    runtime["panel_registered"] = True
    _LOGGER.debug("Registered ZigbeeLens iframe panel for %s", panel_url)


async def async_unregister_panel(hass: HomeAssistant, entry_id: str) -> None:
    runtime = hass.data.get(DOMAIN, {}).get(entry_id, {})
    if not runtime.get("panel_registered"):
        return
    frontend.async_remove_panel(hass, DOMAIN)
    runtime["panel_registered"] = False
