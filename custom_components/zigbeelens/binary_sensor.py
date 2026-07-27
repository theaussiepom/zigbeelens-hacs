"""Binary sensor platform for ZigbeeLens."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry

from .compatibility import nonneg_int_not_bool, validate_decision_count_summary
from .const import DOMAIN
from .coordinator import ZigbeeLensDataUpdateCoordinator
from .entity import ZigbeeLensEntity

BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="active_incident",
        translation_key="active_incident",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    BinarySensorEntityDescription(
        key="core_connected",
        translation_key="core_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
    BinarySensorEntityDescription(
        key="mqtt_collector_connected",
        translation_key="mqtt_collector_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZigbeeLensDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities(
        ZigbeeLensBinarySensor(coordinator, entry.entry_id, description)
        for description in BINARY_SENSORS
    )


class ZigbeeLensBinarySensor(ZigbeeLensEntity, BinarySensorEntity):
    """ZigbeeLens summary binary sensor."""

    entity_description: BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: ZigbeeLensDataUpdateCoordinator,
        entry_id: str,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return False
        key = self.entity_description.key
        if key == "active_incident":
            if "active_incident_count" not in self.dashboard:
                return None
            active = nonneg_int_not_bool(self.dashboard.get("active_incident_count"))
            if active is None:
                return None
            return active > 0
        if key == "core_connected":
            return self.coordinator.last_update_success
        if key == "mqtt_collector_connected":
            return self.coordinator.data.collector_connected
        return None

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        key = self.entity_description.key
        if key == "active_incident":
            attrs: dict = {}
            active = nonneg_int_not_bool(self.dashboard.get("active_incident_count"))
            watching = nonneg_int_not_bool(self.dashboard.get("watching_incident_count"))
            if active is not None:
                attrs["active_incident_count"] = active
            if watching is not None:
                attrs["watching_incident_count"] = watching
            # Decision attributes only when companion decisions are available.
            if self.coordinator.data.shared_decisions_available:
                summary = self.dashboard.get("decision_summary")
                if validate_decision_count_summary(summary):
                    attrs["overall_decision_status"] = summary["overall_status"]
            return attrs
        if key == "core_connected":
            data = self.coordinator.data
            return {
                "core_url": self.coordinator.client.core_url,
                "core_version": data.core_version,
                "core_version_state": data.core_version_state.value,
                "capabilities_state": data.capabilities_state.value,
                "decision_contract_version": data.decision_contract_version,
                "decision_contract_state": data.decision_contract_state.value,
                "decision_payload_state": data.decision_payload_state.value,
                "enrichment_contract_state": data.enrichment_contract_state.value,
                "last_update_success": self.coordinator.last_update_success,
                "collector_connected": data.collector_connected,
            }
        if key == "mqtt_collector_connected":
            raw_collector = self.health.get("collector")
            collector = raw_collector if isinstance(raw_collector, dict) else {}
            last_error = collector.get("last_error")
            return {
                "last_message_at": collector.get("last_message_at"),
                "subscribed_topics_count": collector.get("subscribed_topics_count"),
                "last_error": "[redacted]" if last_error else None,
            }
        return {}
