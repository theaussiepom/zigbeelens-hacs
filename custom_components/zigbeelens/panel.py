"""Native companion panel for ZigbeeLens.

Registers a custom Home Assistant sidebar panel (a status/launcher surface, not
the full product UI) plus a websocket command that returns a redacted summary
built entirely from HA-side coordinator data.

The panel deliberately does not iframe Core and never makes the browser fetch
Core directly. This means it works reliably even when Home Assistant is served
over HTTPS and ZigbeeLens Core is served over HTTP, without a reverse proxy.
"""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import ZigbeeLensDataUpdateCoordinator
from .panel_data import build_panel_summary

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = DOMAIN
PANEL_WEBCOMPONENT = "zigbeelens-panel"
PANEL_STATIC_URL = "/zigbeelens_static/zigbeelens-panel.js"
PANEL_JS_PATH = Path(__file__).parent / "panel" / "zigbeelens-panel.js"
WS_TYPE_SUMMARY = "zigbeelens/panel_summary"
_FRONTEND_REGISTERED = "_frontend_registered"


@callback
def _find_coordinator(
    hass: HomeAssistant,
) -> tuple[ZigbeeLensDataUpdateCoordinator | None, str]:
    """Return the first available coordinator and its configured Core URL."""
    for runtime in (hass.data.get(DOMAIN) or {}).values():
        if not isinstance(runtime, dict):
            continue
        coordinator = runtime.get("coordinator")
        if coordinator is not None:
            client = runtime.get("client")
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
    if domain_data.get(_FRONTEND_REGISTERED):
        return
    websocket_api.async_register_command(hass, _ws_panel_summary)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_STATIC_URL, str(PANEL_JS_PATH), False)]
    )
    domain_data[_FRONTEND_REGISTERED] = True


async def async_register_panel(hass: HomeAssistant, entry_id: str, core_url: str) -> None:
    """Register the native companion panel in the Home Assistant sidebar."""
    runtime = hass.data.setdefault(DOMAIN, {}).setdefault(entry_id, {})
    if runtime.get("panel_registered"):
        return

    await async_setup_frontend(hass)

    if PANEL_URL_PATH not in hass.data.get(frontend.DATA_PANELS, {}):
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
    runtime["panel_registered"] = True
    _LOGGER.debug("Registered ZigbeeLens companion panel (core_url=%s)", core_url)


async def async_unregister_panel(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the sidebar panel when the last config entry unloads."""
    runtime = hass.data.get(DOMAIN, {}).get(entry_id, {})
    if not runtime.get("panel_registered"):
        return
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
    runtime["panel_registered"] = False
