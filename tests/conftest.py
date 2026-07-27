"""Shared fixtures for ZigbeeLens HA integration tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "custom_components"
if str(COMPONENTS) not in sys.path:
    sys.path.insert(0, str(COMPONENTS))

from zigbeelens.coordinator import ZigbeeLensCoordinatorData, ZigbeeLensDataUpdateCoordinator
from zigbeelens.compatibility import (
    CapabilitiesState,
    CoreVersionState,
    DecisionContractState,
    DecisionPayloadState,
    EnrichmentContractState,
)


@pytest.fixture
def sample_health():
    return {
        "status": "ok",
        "version": "0.1.0",
        "uptime_seconds": 120,
        "config_loaded": True,
        "mock_mode": False,
        "database": "ok",
        "migration_version": 5,
        "collector": {
            "enabled": True,
            "connected": True,
            "subscribed_topics_count": 4,
            "last_message_at": "2026-06-14T12:00:00+00:00",
            "last_error": None,
        },
    }


@pytest.fixture
def sample_dashboard():
    return {
        "generated_at": "2026-06-14T12:00:00+00:00",
        "active_incident_count": 1,
        "watching_incident_count": 0,
        "network_count": 1,
        "device_count": 10,
        "unavailable_device_count": 4,
        "decision_summary": {
            "subject_count": 6,
            "overall_status": "review_first",
            "highest_priority": "high",
            "status_counts": {
                "review_first": 2,
                "worth_reviewing": 1,
                "watch": 3,
            },
            "priority_counts": {"high": 2, "medium": 1, "low": 3},
            "coverage_warning_count": 1,
        },
        "networks": [
            {
                "id": "home",
                "name": "Home",
                "base_topic": "zigbee2mqtt",
                "bridge_state": "online",
                "device_count": 10,
                "unavailable_count": 4,
                "active_incident_count": 1,
                "active_incident_severity": "incident",
                "decision": {
                    "status": "review_first",
                    "priority": "high",
                    "headline_code": "network_review_first",
                    "coverage_label_codes": [],
                },
                "decision_summary": {
                    "subject_count": 2,
                    "overall_status": "review_first",
                    "highest_priority": "high",
                    "status_counts": {"review_first": 2},
                    "priority_counts": {"high": 2},
                    "coverage_warning_count": 1,
                },
            }
        ],
        "router_risks": [
            {
                "network_id": "home",
                "ieee_address": "0x00124b0000000001",
                "friendly_name": "router",
                "availability": "online",
                "linkquality": 100,
                "last_seen": "2026-06-14T12:00:00+00:00",
                "possibly_dependent_devices": 2,
                "correlated_affected_devices": 1,
                "risk": {},
            }
        ],
        "recent_timeline": [],
        "investigation_priorities": [],
        "data_coverage_warnings": [
            {
                "id": "w1",
                "network_id": "home",
                "dimension": "availability",
                "state": "limited",
                "label_code": "availability_history_building",
                "scope_type": "network",
                "params": {},
            }
        ],
    }


@pytest.fixture
def sample_config_status():
    return {
        "version": "0.1.0",
        "uptime_seconds": 120,
        "mqtt_connected": True,
        "mqtt_server": "mqtt://broker:1883",
        "configured_networks": [{"id": "home", "name": "Home", "base_topic": "zigbee2mqtt"}],
        "storage_path": "/data/zigbeelens.sqlite",
        "storage_ready": True,
        "retention_days": 7,
        "features": {"mqtt_collector": True},
        "data_mode": "live",
        "mock_mode": False,
        "security": {
            "mode": "local",
            "loopback_bind": True,
            "api_token_configured": False,
            "session_secret_configured": False,
            "legacy_mutation_guard_enabled": False,
        },
    }


@pytest.fixture
def mock_coordinator(sample_health, sample_dashboard, sample_config_status):
    """Coordinator stub with populated data — avoids HA frame setup in unit tests."""
    coordinator = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coordinator.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version="0.1.0",
        collector_connected=True,
        last_update_success=True,
        capabilities_state=CapabilitiesState.ACCEPTED,
        decision_contract_version=2,
        decision_contract_state=DecisionContractState.SUPPORTED_EXACT,
        decision_payload_state=DecisionPayloadState.VALID,
        enrichment_contract_state=EnrichmentContractState.SUPPORTED,
        core_version_state=CoreVersionState.COMPATIBLE,
        shared_decisions_available=True,
        core_version_compatible=True,
    )
    coordinator.last_update_success = True
    coordinator.last_exception = None
    coordinator.auth_failed = False
    coordinator.client = MagicMock(core_url="http://localhost:8377")
    return coordinator
