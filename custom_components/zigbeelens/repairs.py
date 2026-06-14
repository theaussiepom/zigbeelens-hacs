"""Repair issues for ZigbeeLens integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    DOMAIN,
    ISSUE_COLLECTOR_DISCONNECTED,
    ISSUE_CORE_UNREACHABLE,
    ISSUE_MOCK_MODE,
    ISSUE_NO_MQTT_DATA,
    ISSUE_NO_NETWORKS,
)
from .coordinator import ZigbeeLensDataUpdateCoordinator


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


def async_clear_repairs(hass: HomeAssistant) -> None:
    for issue_id in (
        ISSUE_CORE_UNREACHABLE,
        ISSUE_COLLECTOR_DISCONNECTED,
        ISSUE_NO_NETWORKS,
        ISSUE_NO_MQTT_DATA,
        ISSUE_MOCK_MODE,
    ):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
