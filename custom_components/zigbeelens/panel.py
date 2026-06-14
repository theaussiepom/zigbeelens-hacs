"""Native companion panel for ZigbeeLens.

Registers a custom Home Assistant sidebar panel (a status/launcher surface, not
the full product UI) plus a websocket command that returns a redacted summary
built entirely from HA-side coordinator data.

The sidebar never iframes Core by default. Optional Try Embedded View in the
panel JS is manual only; Open Full Dashboard always opens Core in a new tab.
"""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DATA_FRONTEND_REGISTERED, DOMAIN, PANEL_STATE_KEY
from .coordinator import ZigbeeLensDataUpdateCoordinator
from .panel_data import build_panel_summary

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = DOMAIN
PANEL_WEBCOMPONENT = "zigbeelens-panel"
PANEL_STATIC_URL = "/zigbeelens_static/zigbeelens-panel.js"
PANEL_JS_PATH = Path(__file__).parent / "panel" / "zigbeelens-panel.js"
WS_TYPE_SUMMARY = "zigbeelens/panel_summary"


@callback
def _find_coordinator(
    hass: HomeAssistant,
) -> tuple[ZigbeeLensDataUpdateCoordinator | None, str]:
    """Return the first available coordinator and its configured Core URL."""
    for key, value in (hass.data.get(DOMAIN) or {}).items():
        if key.startswith("_") or not isinstance(value, dict):
            continue
        coordinator = value.get("coordinator")
        if coordinator is not None:
            client = value.get("client")
            core_url = client.core_url if client else ""
            return coordinator, core_url
    return None, ""


@callback
@websocket_api.websocket_command({vol.Required("type"): WS_TYPE_SUMMARY})
def _ws_panel_summary(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return a redacted panel summary to the frontend over the HA websocket."""
    coordinator, core_url = _find_coordinator(hass)
    if coordinator is None:
        connection.send_result(
            msg["id"], build_panel_summary(None, core_url=core_url, connected=False)
        )
        return

    connected = bool(coordinator.last_update_success and coordinator.data is not None)
    summary = build_panel_summary(
        coordinator.data if connected else None,
        core_url=core_url,
        connected=connected,
        last_exception=getattr(coordinator, "last_exception", None),
    )
    connection.send_result(msg["id"], summary)


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Register the websocket command and static panel asset once per HA run."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(DATA_FRONTEND_REGISTERED):
        return
    websocket_api.async_register_command(hass, _ws_panel_summary)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_STATIC_URL, str(PANEL_JS_PATH), False)]
    )
    domain_data[DATA_FRONTEND_REGISTERED] = True


async def async_register_panel(hass: HomeAssistant, entry_id: str, core_url: str) -> None:
    """Register the native companion panel in the Home Assistant sidebar."""
    await async_setup_frontend(hass)
    state = hass.data.setdefault(DOMAIN, {}).setdefault(PANEL_STATE_KEY, {})

    panels = hass.data.get(frontend.DATA_PANELS, {})
    existing = panels.get(PANEL_URL_PATH)
    if existing is not None:
        async_update_panel_core_url(hass, core_url)
        state["panel_registered"] = True
        return

    if state.get("panel_registered"):
        async_update_panel_core_url(hass, core_url)
        return

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_WEBCOMPONENT,
        sidebar_title="ZigbeeLens",
        sidebar_icon="mdi:zigbee",
        module_url=PANEL_STATIC_URL,
        embed_iframe=False,
        require_admin=False,
        config={"core_url": core_url},
    )
    state["panel_registered"] = True
    _LOGGER.debug("Registered ZigbeeLens companion panel (core_url=%s)", core_url)


@callback
def async_update_panel_core_url(hass: HomeAssistant, core_url: str) -> None:
    """Update the companion panel launcher URL after Configure / options changes."""
    panels = hass.data.get(frontend.DATA_PANELS, {})
    panel = panels.get(PANEL_URL_PATH)
    if panel is not None:
        panel["config"] = {**(panel.get("config") or {}), "core_url": core_url}


async def async_unregister_panel(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the sidebar panel when the config entry unloads."""
    state = hass.data.get(DOMAIN, {}).get(PANEL_STATE_KEY, {})
    if not state.get("panel_registered"):
        return
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
    state["panel_registered"] = False
