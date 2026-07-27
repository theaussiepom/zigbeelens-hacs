"""Native companion panel for ZigbeeLens.

Registers a custom Home Assistant sidebar panel (a status/launcher surface, not
the full product UI) plus a websocket command that returns a redacted summary
built entirely from HA-side coordinator data.

The sidebar never iframes Core by default. Embedding the full Core UI is opt-in
via Try Embedded View when schemes match and Core allows the HA frame ancestor.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DATA_FRONTEND_REGISTERED, DOMAIN, PANEL_STATE_KEY
from .coordinator import ZigbeeLensDataUpdateCoordinator
from .core_origin import InvalidCoreOrigin, canonicalize_core_origin
from .panel_data import build_panel_summary

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = DOMAIN
PANEL_WEBCOMPONENT = "zigbeelens-panel"
PANEL_STATIC_URL = "/zigbeelens_static/zigbeelens-panel.js"
PANEL_JS_PATH = Path(__file__).parent / "panel" / "zigbeelens-panel.js"
WS_TYPE_SUMMARY = "zigbeelens/panel_summary"


def _panel_state(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(DOMAIN, {}).setdefault(PANEL_STATE_KEY, {})


def _runtime_entry_items(
    hass: HomeAssistant,
) -> list[tuple[str, dict[str, Any]]]:
    """Return (entry_id, runtime) pairs with a live coordinator."""
    items: list[tuple[str, dict[str, Any]]] = []
    for key, value in (hass.data.get(DOMAIN) or {}).items():
        if key.startswith("_") or not isinstance(value, dict):
            continue
        if value.get("coordinator") is not None:
            items.append((str(key), value))
    return items


@callback
def _find_coordinator(
    hass: HomeAssistant,
) -> tuple[ZigbeeLensDataUpdateCoordinator | None, str]:
    """Return the panel-owner coordinator and its configured Core URL.

    Prefer the stamped ``owner_entry_id``. Never fall back across entries when
    the owner is missing or when multiple unmarked runtimes exist.
    """
    state = (hass.data.get(DOMAIN) or {}).get(PANEL_STATE_KEY) or {}
    owner_entry_id = state.get("owner_entry_id")
    domain_data = hass.data.get(DOMAIN) or {}

    if owner_entry_id is not None:
        value = domain_data.get(owner_entry_id)
        if not isinstance(value, dict) or value.get("coordinator") is None:
            return None, ""
        client = value.get("client")
        return value["coordinator"], client.core_url if client else ""

    # Backward compatibility before an owner marker has been stamped.
    runtimes = _runtime_entry_items(hass)
    if len(runtimes) != 1:
        return None, ""
    _entry_id, value = runtimes[0]
    client = value.get("client")
    return value["coordinator"], client.core_url if client else ""


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
    try:
        summary = build_panel_summary(
            coordinator.data if connected else None,
            core_url=core_url,
            connected=connected,
            last_exception=getattr(coordinator, "last_exception", None),
        )
    except Exception:
        _LOGGER.exception("Failed to build ZigbeeLens panel summary")
        summary = build_panel_summary(
            None,
            core_url=core_url,
            connected=False,
            last_exception="Panel summary unavailable",
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


def _safe_panel_core_url(core_url: str) -> str | None:
    try:
        return canonicalize_core_origin(core_url)
    except InvalidCoreOrigin:
        return None


def _stamp_panel_owner(state: dict[str, Any], entry_id: str) -> None:
    state["panel_registered"] = True
    state["owner_entry_id"] = entry_id


async def async_register_panel(hass: HomeAssistant, entry_id: str, core_url: str) -> None:
    """Register the native companion panel in the Home Assistant sidebar."""
    await async_setup_frontend(hass)
    safe_url = _safe_panel_core_url(core_url)
    if safe_url is None:
        _LOGGER.error(
            "Refusing to register ZigbeeLens panel with an invalid Core URL"
        )
        return
    state = _panel_state(hass)

    panels = hass.data.get(frontend.DATA_PANELS, {})
    existing = panels.get(PANEL_URL_PATH)
    if existing is not None:
        config = existing.get("config") or {}
        custom_meta = config.get("_panel_custom") or {}
        if custom_meta.get("embed_iframe") or not config.get("core_url"):
            frontend.async_remove_panel(hass, PANEL_URL_PATH)
            state["panel_registered"] = False
            state.pop("owner_entry_id", None)
        else:
            current_url = str(config.get("core_url") or "").rstrip("/")
            new_url = safe_url
            if current_url != new_url:
                frontend.async_remove_panel(hass, PANEL_URL_PATH)
                state["panel_registered"] = False
                state.pop("owner_entry_id", None)
            else:
                async_update_panel_core_url(hass, safe_url)
                _stamp_panel_owner(state, entry_id)
                return

    if state.get("panel_registered"):
        if PANEL_URL_PATH in panels:
            async_update_panel_core_url(hass, safe_url)
            _stamp_panel_owner(state, entry_id)
            return
        state["panel_registered"] = False
        state.pop("owner_entry_id", None)

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_WEBCOMPONENT,
        sidebar_title="ZigbeeLens",
        sidebar_icon="mdi:zigbee",
        module_url=PANEL_STATIC_URL,
        embed_iframe=False,
        require_admin=False,
        config={"core_url": safe_url},
    )
    _stamp_panel_owner(state, entry_id)
    _LOGGER.debug("Registered ZigbeeLens companion panel")


@callback
def async_update_panel_core_url(hass: HomeAssistant, core_url: str) -> None:
    """Update the companion panel launcher URL after Configure / options changes."""
    safe_url = _safe_panel_core_url(core_url)
    if safe_url is None:
        _LOGGER.error("Refusing to update ZigbeeLens panel with an invalid Core URL")
        return
    panels = hass.data.get(frontend.DATA_PANELS, {})
    panel = panels.get(PANEL_URL_PATH)
    if panel is not None:
        panel["config"] = {**(panel.get("config") or {}), "core_url": safe_url}


async def async_unregister_panel(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the sidebar panel when the owning config entry unloads."""
    state = (hass.data.get(DOMAIN) or {}).get(PANEL_STATE_KEY, {})
    if not state.get("panel_registered"):
        return

    owner_entry_id = state.get("owner_entry_id")
    if owner_entry_id is not None and owner_entry_id != entry_id:
        # Secondary/stale entry must never remove the primary panel.
        return
    if owner_entry_id is None:
        # No owner marker: only clean up when there is no multi-entry ambiguity.
        if len(_runtime_entry_items(hass)) > 1:
            return

    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
    state["panel_registered"] = False
    state.pop("owner_entry_id", None)
