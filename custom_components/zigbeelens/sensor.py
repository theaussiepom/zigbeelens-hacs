"""Sensor platform for ZigbeeLens."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .compatibility import nonneg_int_not_bool, validate_router_risks
from .const import DOMAIN
from .coordinator import ZigbeeLensDataUpdateCoordinator
from .entity import ZigbeeLensEntity

# Factual operational sensors keep stable unique IDs.
# Decision sensors use new explicit IDs — never reuse overall_health / Lens IDs.
SUMMARY_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(key="overall_decision", translation_key="overall_decision"),
    SensorEntityDescription(key="review_first_devices", translation_key="review_first_devices"),
    SensorEntityDescription(
        key="worth_reviewing_devices", translation_key="worth_reviewing_devices"
    ),
    SensorEntityDescription(
        key="coverage_warning_count", translation_key="coverage_warning_count"
    ),
    SensorEntityDescription(key="watch_devices", translation_key="watch_devices"),
    SensorEntityDescription(key="incident_state", translation_key="incident_state"),
    SensorEntityDescription(key="unavailable_devices", translation_key="unavailable_devices"),
    SensorEntityDescription(key="router_risks", translation_key="router_risks"),
    SensorEntityDescription(key="network_count", translation_key="network_count"),
    SensorEntityDescription(key="device_count", translation_key="device_count"),
)

_DECISION_COUNT_KEYS = frozenset(
    {
        "overall_decision",
        "review_first_devices",
        "worth_reviewing_devices",
        "coverage_warning_count",
        "watch_devices",
        "router_risks",
    }
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
                f"{network_id}_decision",
                f"{network.get('name', network_id)} Decision",
                "decision",
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
    networks = coordinator.data.dashboard.get("networks")
    if not isinstance(networks, list):
        return []
    return [
        network
        for network in networks
        if isinstance(network, dict)
        and isinstance(network.get("id"), str)
        and bool(network["id"].strip())
    ]


def _decision_summary(dashboard: dict) -> dict:
    summary = dashboard.get("decision_summary")
    return summary if isinstance(summary, dict) else {}


def _status_count(dashboard: dict, status: str) -> int | None:
    counts = _decision_summary(dashboard).get("status_counts")
    if not isinstance(counts, dict):
        return None
    raw = counts.get(status)
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
    def available(self) -> bool:
        if not super().available:
            return False
        if self.entity_description.key in _DECISION_COUNT_KEYS:
            data = self.coordinator.data
            return bool(data and data.shared_decisions_available)
        return True

    @property
    def native_value(self) -> str | int | None:
        if self.coordinator.data is None:
            return None
        key = self.entity_description.key
        dashboard = self.dashboard
        if key in _DECISION_COUNT_KEYS and not self.coordinator.data.shared_decisions_available:
            return None
        if key == "overall_decision":
            status = _decision_summary(dashboard).get("overall_status")
            return str(status) if status else None
        if key == "review_first_devices":
            return _status_count(dashboard, "review_first")
        if key == "worth_reviewing_devices":
            return _status_count(dashboard, "worth_reviewing")
        if key == "watch_devices":
            return _status_count(dashboard, "watch")
        if key == "coverage_warning_count":
            summary = _decision_summary(dashboard)
            if "coverage_warning_count" in summary:
                try:
                    return int(summary["coverage_warning_count"])
                except (TypeError, ValueError):
                    return None
            warnings = dashboard.get("data_coverage_warnings")
            return len(warnings) if isinstance(warnings, list) else None
        if key == "incident_state":
            if "active_incident_count" not in dashboard:
                return None
            active = nonneg_int_not_bool(dashboard.get("active_incident_count"))
            if active is None:
                return None
            if "watching_incident_count" not in dashboard:
                return None
            watching = nonneg_int_not_bool(dashboard.get("watching_incident_count"))
            if watching is None:
                return None
            if active > 0:
                return "incident"
            if watching > 0:
                return "watch"
            return "none"
        if key == "unavailable_devices":
            if "unavailable_device_count" not in dashboard:
                return None
            return nonneg_int_not_bool(dashboard.get("unavailable_device_count"))
        if key == "router_risks":
            risks = dashboard.get("router_risks")
            if not validate_router_risks(risks):
                return None
            return len(risks)
        if key == "network_count":
            if "network_count" not in dashboard:
                return None
            return nonneg_int_not_bool(dashboard.get("network_count"))
        if key == "device_count":
            if "device_count" not in dashboard:
                return None
            return nonneg_int_not_bool(dashboard.get("device_count"))
        return None

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        if self.entity_description.key != "overall_decision":
            return {}
        data = self.coordinator.data
        attrs = {
            "core_version_state": data.core_version_state.value,
            "capabilities_state": data.capabilities_state.value,
            "decision_contract_version": data.decision_contract_version,
            "decision_contract_state": data.decision_contract_state.value,
            "decision_payload_state": data.decision_payload_state.value,
            "enrichment_contract_state": data.enrichment_contract_state.value,
        }
        if not self.coordinator.data.shared_decisions_available:
            return attrs
        summary = _decision_summary(self.dashboard)
        attrs.update(
            {
                "highest_priority": summary.get("highest_priority"),
                "status_counts": summary.get("status_counts") or {},
                "priority_counts": summary.get("priority_counts") or {},
                "coverage_warning_count": summary.get("coverage_warning_count"),
                "active_incident_count": self.dashboard.get("active_incident_count"),
                "subject_count": summary.get("subject_count"),
            }
        )
        return attrs


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
    def available(self) -> bool:
        if not super().available:
            return False
        if self._metric in {"decision", "router_risks"}:
            data = self.coordinator.data
            return bool(data and data.shared_decisions_available)
        return True

    @property
    def native_value(self) -> str | int | None:
        network = self._network()
        if not network:
            return None
        if self._metric == "decision":
            if not self.coordinator.data or not self.coordinator.data.shared_decisions_available:
                return None
            decision = network.get("decision") or {}
            if isinstance(decision, dict) and decision.get("status"):
                return str(decision["status"])
            summary = network.get("decision_summary") or {}
            if isinstance(summary, dict) and summary.get("overall_status"):
                return str(summary["overall_status"])
            return None
        if self._metric == "unavailable_devices":
            if "unavailable_count" not in network:
                return None
            return nonneg_int_not_bool(network.get("unavailable_count"))
        if self._metric == "router_risks":
            risks = self.dashboard.get("router_risks")
            if (
                not self.coordinator.data
                or not self.coordinator.data.shared_decisions_available
                or not validate_router_risks(risks)
            ):
                return None
            return len(
                [
                    r
                    for r in risks
                    if isinstance(r, dict) and r.get("network_id") == self._network_id
                ]
            )
        return None
