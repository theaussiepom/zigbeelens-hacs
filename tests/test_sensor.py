"""Sensor platform unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from zigbeelens.const import DOMAIN
from zigbeelens.sensor import (
    ZigbeeLensNetworkSensor,
    ZigbeeLensSensor,
    async_setup_entry,
)
from homeassistant.components.sensor import SensorEntityDescription


def test_overall_decision_sensor(mock_coordinator):
    sensor = ZigbeeLensSensor(
        mock_coordinator,
        "entry1",
        SensorEntityDescription(key="overall_decision", translation_key="overall_decision"),
    )
    assert sensor.unique_id == "entry1_overall_decision"
    assert sensor.native_value == "review_first"
    assert sensor.available is True
    attrs = sensor.extra_state_attributes
    assert attrs["status_counts"]["review_first"] == 2
    assert attrs["highest_priority"] == "high"
    assert attrs["core_version_state"] == "compatible"
    assert attrs["decision_contract_version"] == 2
    assert attrs["decision_contract_state"] == "supported_exact"
    assert attrs["decision_payload_state"] == "valid"


def test_decision_sensors_unavailable_without_contract(mock_coordinator):
    mock_coordinator.data.shared_decisions_available = False
    sensor = ZigbeeLensSensor(
        mock_coordinator,
        "entry1",
        SensorEntityDescription(key="overall_decision", translation_key="overall_decision"),
    )
    assert sensor.available is False
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["decision_contract_state"] == "supported_exact"


def test_review_first_count(mock_coordinator):
    sensor = ZigbeeLensSensor(
        mock_coordinator,
        "entry1",
        SensorEntityDescription(
            key="review_first_devices", translation_key="review_first_devices"
        ),
    )
    assert sensor.unique_id == "entry1_review_first_devices"
    assert sensor.native_value == 2


def test_factual_unavailable_devices(mock_coordinator):
    sensor = ZigbeeLensSensor(
        mock_coordinator,
        "entry1",
        SensorEntityDescription(
            key="unavailable_devices", translation_key="unavailable_devices"
        ),
    )
    assert sensor.unique_id == "entry1_unavailable_devices"
    assert sensor.native_value == 4


def test_factual_entities_unknown_when_field_missing(mock_coordinator):
    dashboard = dict(mock_coordinator.data.dashboard)
    dashboard.pop("unavailable_device_count", None)
    mock_coordinator.data.dashboard = dashboard
    sensor = ZigbeeLensSensor(
        mock_coordinator,
        "entry1",
        SensorEntityDescription(
            key="unavailable_devices", translation_key="unavailable_devices"
        ),
    )
    assert sensor.native_value is None


def test_incident_state_none_when_counts_missing(mock_coordinator):
    dashboard = dict(mock_coordinator.data.dashboard)
    dashboard.pop("active_incident_count", None)
    mock_coordinator.data.dashboard = dashboard
    sensor = ZigbeeLensSensor(
        mock_coordinator,
        "entry1",
        SensorEntityDescription(key="incident_state", translation_key="incident_state"),
    )
    assert sensor.native_value is None


def test_network_count_none_without_valid_field(mock_coordinator):
    dashboard = dict(mock_coordinator.data.dashboard)
    dashboard.pop("network_count", None)
    mock_coordinator.data.dashboard = dashboard
    sensor = ZigbeeLensSensor(
        mock_coordinator,
        "entry1",
        SensorEntityDescription(key="network_count", translation_key="network_count"),
    )
    assert sensor.native_value is None


def test_network_decision_sensor(mock_coordinator):
    sensor = ZigbeeLensNetworkSensor(
        mock_coordinator,
        "entry1",
        "home_decision",
        "Home Decision",
        "decision",
        "home",
    )
    assert sensor.unique_id == "entry1_home_decision"
    assert sensor.native_value == "review_first"


def test_malformed_router_risk_items_are_unknown_globally_and_per_network(
    mock_coordinator,
):
    mock_coordinator.data.dashboard = {
        **mock_coordinator.data.dashboard,
        "router_risks": ["malformed"],
    }
    mock_coordinator.data.shared_decisions_available = False
    global_sensor = ZigbeeLensSensor(
        mock_coordinator,
        "entry1",
        SensorEntityDescription(key="router_risks", translation_key="router_risks"),
    )
    network_sensor = ZigbeeLensNetworkSensor(
        mock_coordinator,
        "entry1",
        "home_router_risks",
        "Home Router Risks",
        "router_risks",
        "home",
    )

    assert global_sensor.available is False
    assert global_sensor.native_value is None
    assert network_sensor.available is False
    assert network_sensor.native_value is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_networks",
    ["not-a-list", {"id": "home"}, [None, "bad", {"name": "missing id"}]],
)
async def test_sensor_setup_survives_malformed_network_rows(
    mock_coordinator,
    malformed_networks,
):
    mock_coordinator.data.dashboard = {
        **mock_coordinator.data.dashboard,
        "networks": malformed_networks,
    }
    hass = MagicMock()
    entry = MagicMock(entry_id="entry1")
    hass.data = {DOMAIN: {"entry1": {"coordinator": mock_coordinator}}}
    added = MagicMock()

    await async_setup_entry(hass, entry, added)

    entities = added.call_args.args[0]
    assert len(entities) == 10


def test_superseded_health_unique_ids_not_reused():
    """New decision entities must not reuse overall_health unique IDs."""
    assert "overall_health" not in {
        "overall_decision",
        "review_first_devices",
        "worth_reviewing_devices",
        "coverage_warning_count",
        "watch_devices",
    }
