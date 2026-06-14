"""Repair issues for ZigbeeLens integration."""

from __future__ import annotations

from urllib.parse import urlparse

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_CORE_URL,
    CONF_PANEL_ENABLED,
    DOMAIN,
    ISSUE_COLLECTOR_DISCONNECTED,
    ISSUE_CORE_UNREACHABLE,
    ISSUE_MOCK_MODE,
    ISSUE_NO_MQTT_DATA,
    ISSUE_NO_NETWORKS,
    ISSUE_PANEL_MIXED_CONTENT,
)
from .coordinator import ZigbeeLensDataUpdateCoordinator


def _home_assistant_uses_https(hass: HomeAssistant) -> bool:
    for url in (hass.config.internal_url, hass.config.external_url):
        if url and urlparse(url).scheme == "https":
            return True
    return False


def _core_url_is_http(core_url: str) -> bool:
    return urlparse(core_url).scheme == "http"


def async_manage_repairs(hass: HomeAssistant, coordinator: ZigbeeLensDataUpdateCoordinator) -> None:
    """Create or clear repairs based on coordinator state."""
    if not coordinator.last_update_success or coordinator.data is None:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_CORE_UNREACHABLE,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_CORE_UNREACHABLE,
        )
        return

    ir.async_delete_issue(hass, DOMAIN, ISSUE_CORE_UNREACHABLE)

    data = coordinator.data
    dashboard = data.dashboard
    health = data.health
    config_status = data.config_status

    if not data.collector_connected:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_COLLECTOR_DISCONNECTED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_COLLECTOR_DISCONNECTED,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_COLLECTOR_DISCONNECTED)

    networks = dashboard.get("networks") or []
    configured = config_status.get("configured_networks") or []
    if not networks and not configured:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_NO_NETWORKS,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_NO_NETWORKS,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_NO_NETWORKS)

    device_count = (dashboard.get("health_snapshot") or {}).get("device_count", 0)
    if networks and device_count == 0 and not health.get("mock_mode"):
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_NO_MQTT_DATA,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_NO_MQTT_DATA,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_NO_MQTT_DATA)

    if health.get("mock_mode") or config_status.get("mock_mode"):
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_MOCK_MODE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MOCK_MODE,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_MOCK_MODE)

    entry = getattr(coordinator, "config_entry", None)
    client = getattr(coordinator, "client", None)
    core_url = client.core_url if client else ""
    panel_enabled = True
    if entry is not None:
        core_url = entry.data.get(CONF_CORE_URL, core_url)
        panel_enabled = entry.data.get(CONF_PANEL_ENABLED, True)
    if (
        panel_enabled
        and core_url
        and _home_assistant_uses_https(hass)
        and _core_url_is_http(core_url)
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_PANEL_MIXED_CONTENT,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_PANEL_MIXED_CONTENT,
            translation_placeholders={"core_url": core_url.rstrip("/")},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_PANEL_MIXED_CONTENT)


def async_clear_repairs(hass: HomeAssistant) -> None:
    for issue_id in (
        ISSUE_CORE_UNREACHABLE,
        ISSUE_COLLECTOR_DISCONNECTED,
        ISSUE_NO_NETWORKS,
        ISSUE_NO_MQTT_DATA,
        ISSUE_MOCK_MODE,
        ISSUE_PANEL_MIXED_CONTENT,
    ):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
