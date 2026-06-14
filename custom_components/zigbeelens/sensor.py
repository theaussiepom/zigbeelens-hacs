"""Sensor platform for ZigbeeLens."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ZigbeeLensDataUpdateCoordinator
from .entity import ZigbeeLensEntity

SUMMARY_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(key="overall_health", translation_key="overall_health"),
    SensorEntityDescription(key="incident_state", translation_key="incident_state"),
    SensorEntityDescription(key="unavailable_devices", translation_key="unavailable_devices"),
    SensorEntityDescription(
        key="recently_unstable_devices", translation_key="recently_unstable_devices"
    ),
    SensorEntityDescription(key="router_risks", translation_key="router_risks"),
    SensorEntityDescription(key="stale_devices", translation_key="stale_devices"),
    SensorEntityDescription(key="weak_link_devices", translation_key="weak_link_devices"),
    SensorEntityDescription(key="low_battery_devices", translation_key="low_battery_devices"),
    SensorEntityDescription(key="unknown_devices", translation_key="unknown_devices"),
    SensorEntityDescription(key="network_count", translation_key="network_count"),
    SensorEntityDescription(key="device_count", translation_key="device_count"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ZigbeeLensDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    entities: list[SensorEntity] = [
        ZigbeeLensSensor(coordinator, entry.entry_id, description)
        for description in SUMMARY_SENSORS
    ]
    for network in _networks(coordinator):
        network_id = network["id"]
        entities.append(
            ZigbeeLensNetworkSensor(
                coordinator,
                entry.entry_id,
                f"{network_id}_health",
                f"{network.get('name', network_id)} Health",
                "health",
                network_id,
            )
        )
        entities.append(
            ZigbeeLensNetworkSensor(
                coordinator,
                entry.entry_id,
                f"{network_id}_unavailable_devices",
                f"{network.get('name', network_id)} Unavailable Devices",
                "unavailable_devices",
                network_id,
            )
        )
        entities.append(
            ZigbeeLensNetworkSensor(
                coordinator,
                entry.entry_id,
                f"{network_id}_router_risks",
                f"{network.get('name', network_id)} Router Risks",
                "router_risks",
                network_id,
            )
        )
    async_add_entities(entities)


def _networks(coordinator: ZigbeeLensDataUpdateCoordinator) -> list[dict]:
    if coordinator.data is None:
        return []
    return list(coordinator.data.dashboard.get("networks") or [])


class ZigbeeLensSensor(ZigbeeLensEntity, SensorEntity):
    """Global summary sensor."""

    entity_description: SensorEntityDescription

    def __init__(
        self,
        coordinator: ZigbeeLensDataUpdateCoordinator,
        entry_id: str,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, entry_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | None:
        if self.coordinator.data is None:
            return None
        key = self.entity_description.key
        hs = self.health_snapshot
        if key == "overall_health":
            severity = self.dashboard.get("overall_severity", "unknown")
            return _severity_label(severity)
        if key == "incident_state":
            active = int(self.dashboard.get("active_incident_count") or 0)
            watching = int(self.dashboard.get("watching_incident_count") or 0)
            if active > 0:
                return "incident"
            if watching > 0:
                return "watch"
            return "none"
        if key == "unavailable_devices":
            return hs.get("unavailable_count", 0)
        if key == "recently_unstable_devices":
            return len(self.dashboard.get("recently_unstable") or [])
        if key == "router_risks":
            return len(self.dashboard.get("router_risks") or [])
        if key == "stale_devices":
            return len(self.dashboard.get("stale_devices") or [])
        if key == "weak_link_devices":
            return len(self.dashboard.get("weak_links") or [])
        if key == "low_battery_devices":
            return len(self.dashboard.get("low_batteries") or [])
        if key == "unknown_devices":
            return _unknown_device_count(self.dashboard)
        if key == "network_count":
            return hs.get("network_count", len(_networks(self.coordinator)))
        if key == "device_count":
            return hs.get("device_count", 0)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        if self.entity_description.key != "overall_health":
            return {}
        finding = self.dashboard.get("current_finding") or {}
        return {
            "current_finding": finding.get("summary"),
            "confidence": finding.get("confidence"),
            "active_incident_count": self.dashboard.get("active_incident_count"),
            "networks_monitored": self.health_snapshot.get("network_count"),
            "total_devices": self.health_snapshot.get("device_count"),
        }


class ZigbeeLensNetworkSensor(ZigbeeLensEntity, SensorEntity):
    """Per-network summary sensor."""

    def __init__(
        self,
        coordinator: ZigbeeLensDataUpdateCoordinator,
        entry_id: str,
        key: str,
        name: str,
        metric: str,
        network_id: str,
    ) -> None:
        super().__init__(coordinator, entry_id, key)
        self._metric = metric
        self._network_id = network_id
        self._attr_name = name

    def _network(self) -> dict | None:
        for network in _networks(self.coordinator):
            if network.get("id") == self._network_id:
                return network
        return None

    @property
    def native_value(self) -> str | int | None:
        network = self._network()
        if not network:
            return None
        if self._metric == "health":
            health = network.get("health") or {}
            return _severity_label(network.get("incident_state") or health.get("severity", "unknown"))
        if self._metric == "unavailable_devices":
            return network.get("unavailable_count", 0)
        if self._metric == "router_risks":
            return len(
                [
                    r
                    for r in (self.dashboard.get("router_risks") or [])
                    if r.get("network_id") == self._network_id
                ]
            )
        return None


def _severity_label(value: str | None) -> str:
    if not value:
        return "unknown"
    if value in ("healthy", "ok"):
        return "ok"
    if value == "watch":
        return "watch"
    if value in ("incident", "critical"):
        return "incident"
    return str(value)


def _unknown_device_count(dashboard: dict) -> int:
    """Best-effort unknown count from dashboard summary lists only."""
    count = 0
    for bucket in (
        "top_affected_devices",
        "recently_unstable",
        "weak_links",
        "low_batteries",
        "stale_devices",
    ):
        for device in dashboard.get(bucket) or []:
            health = device.get("health") or {}
            if health.get("primary") == "unknown":
                count += 1
    return count
