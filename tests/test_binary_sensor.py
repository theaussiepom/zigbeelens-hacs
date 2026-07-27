"""Binary sensor tests."""

from __future__ import annotations

from zigbeelens.binary_sensor import ZigbeeLensBinarySensor, BINARY_SENSORS
from zigbeelens.coordinator import ZigbeeLensCoordinatorData


def test_core_connected_binary_sensor(mock_coordinator):
    sensor = ZigbeeLensBinarySensor(mock_coordinator, "entry1", BINARY_SENSORS[1])
    assert sensor.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["core_url"] == "http://localhost:8377"
    assert attrs["core_version_state"] == "compatible"
    assert attrs["capabilities_state"] == "accepted"
    assert attrs["decision_contract_version"] == 2
    assert attrs["decision_contract_state"] == "supported_exact"
    assert attrs["decision_payload_state"] == "valid"
    assert attrs["enrichment_contract_state"] == "supported"


def test_active_incident_off_when_zero(mock_coordinator, sample_health, sample_config_status):
    dashboard = dict(mock_coordinator.data.dashboard)
    dashboard["active_incident_count"] = 0
    mock_coordinator.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=dashboard,
        config_status=sample_config_status,
        core_version="0.1.0",
        collector_connected=True,
        last_update_success=True,
    )
    sensor = ZigbeeLensBinarySensor(mock_coordinator, "entry1", BINARY_SENSORS[0])
    assert sensor.is_on is False


def test_active_incident_none_when_count_missing(mock_coordinator, sample_health, sample_config_status):
    dashboard = dict(mock_coordinator.data.dashboard)
    dashboard.pop("active_incident_count", None)
    mock_coordinator.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=dashboard,
        config_status=sample_config_status,
        core_version="0.1.0",
        collector_connected=True,
        last_update_success=True,
    )
    sensor = ZigbeeLensBinarySensor(mock_coordinator, "entry1", BINARY_SENSORS[0])
    assert sensor.is_on is None


def test_active_incident_omits_decision_attrs_when_decisions_unavailable(
    mock_coordinator, sample_health, sample_config_status
):
    dashboard = dict(mock_coordinator.data.dashboard)
    dashboard["active_incident_count"] = 1
    dashboard["watching_incident_count"] = True  # malformed — must be omitted
    mock_coordinator.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=dashboard,
        config_status=sample_config_status,
        core_version="0.1.0",
        collector_connected=True,
        last_update_success=True,
        shared_decisions_available=False,
    )
    sensor = ZigbeeLensBinarySensor(mock_coordinator, "entry1", BINARY_SENSORS[0])
    attrs = sensor.extra_state_attributes
    assert attrs["active_incident_count"] == 1
    assert "watching_incident_count" not in attrs
    assert "overall_decision_status" not in attrs


def test_active_incident_includes_validated_decision_status_when_available(
    mock_coordinator, sample_health, sample_config_status
):
    mock_coordinator.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=mock_coordinator.data.dashboard,
        config_status=sample_config_status,
        core_version="0.1.0",
        collector_connected=True,
        last_update_success=True,
        shared_decisions_available=True,
    )
    sensor = ZigbeeLensBinarySensor(mock_coordinator, "entry1", BINARY_SENSORS[0])
    attrs = sensor.extra_state_attributes
    assert attrs["overall_decision_status"] == "review_first"
    assert attrs["active_incident_count"] == 1
    assert attrs["watching_incident_count"] == 0


def test_mqtt_collector_attributes_survive_malformed_collector(mock_coordinator):
    mock_coordinator.data.health = {
        **mock_coordinator.data.health,
        "collector": ["not", "an", "object"],
    }
    sensor = ZigbeeLensBinarySensor(mock_coordinator, "entry1", BINARY_SENSORS[2])

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "last_message_at": None,
        "subscribed_topics_count": None,
        "last_error": None,
    }
