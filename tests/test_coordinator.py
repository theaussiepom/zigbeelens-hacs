"""Coordinator tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from zigbeelens.api import ZigbeeLensApiClient
from zigbeelens.compatibility import (
    CapabilitiesState,
    CoreVersionState,
    DecisionContractState,
    DecisionPayloadState,
    EnrichmentContractState,
)
from zigbeelens.coordinator import ZigbeeLensDataUpdateCoordinator
from zigbeelens.exceptions import (
    ZigbeeLensAuthError,
    ZigbeeLensConnectionError,
    ZigbeeLensInvalidResponseError,
    ZigbeeLensRequestRejectedError,
)


def _capabilities(*, version: object = 2) -> dict:
    return {
        "product": "zigbeelens",
        "version": "0.1.13",
        "decision_contract_version": version,
        "home_assistant_enrichment_contract_version": 1,
        "capabilities": {
            "home_assistant_enrichment": True,
            "shared_decisions": True,
            "companion_decision_summary": True,
            "decision_only_diagnostic_payloads": True,
            "report_contract_v3": True,
            "decision_mqtt_summary": True,
            "legacy_health_lens_payloads": False,
        },
        "decision_surfaces": {
            "dashboard_decision_summary": True,
            "dashboard_investigation_priorities": True,
            "dashboard_data_coverage_warnings": True,
            "network_decision_badges": True,
            "device_decision_badges": True,
        },
    }


@pytest.fixture
def mock_client(sample_health, sample_dashboard, sample_config_status):
    client = MagicMock(spec=ZigbeeLensApiClient)
    client.async_get_health = AsyncMock(return_value=sample_health)
    client.async_get_dashboard = AsyncMock(return_value=sample_dashboard)
    client.async_get_config_status = AsyncMock(return_value=sample_config_status)
    client.async_get_capabilities = AsyncMock(return_value=_capabilities())
    client.core_url = "http://localhost:8377"
    return client


def _bare_coordinator(mock_client) -> ZigbeeLensDataUpdateCoordinator:
    coordinator = ZigbeeLensDataUpdateCoordinator.__new__(ZigbeeLensDataUpdateCoordinator)
    coordinator.client = mock_client
    coordinator.last_update_success = False
    coordinator.last_exception = None
    coordinator.auth_failed = False
    return coordinator


@pytest.mark.asyncio
async def test_coordinator_first_refresh_success(mock_client):
    coordinator = _bare_coordinator(mock_client)
    data = await coordinator._async_update_data()
    assert data.core_version == "0.1.0"
    assert data.collector_connected is True
    assert coordinator.last_update_success is True
    assert data.shared_decisions_available is True
    assert data.decision_contract_version == 2
    assert data.core_version_compatible is True
    assert data.core_version_state is CoreVersionState.COMPATIBLE
    assert data.capabilities_state is CapabilitiesState.ACCEPTED
    assert data.decision_contract_state is DecisionContractState.SUPPORTED_EXACT
    assert data.decision_payload_state is DecisionPayloadState.VALID
    assert data.enrichment_contract_state is EnrichmentContractState.SUPPORTED


@pytest.mark.asyncio
async def test_coordinator_rejects_contract_version_1(mock_client):
    mock_client.async_get_capabilities = AsyncMock(return_value=_capabilities(version=1))
    coordinator = _bare_coordinator(mock_client)
    data = await coordinator._async_update_data()
    assert coordinator.last_update_success is True
    assert data.shared_decisions_available is False
    assert data.decision_contract_version == 1
    assert data.decision_contract_state is DecisionContractState.OLDER
    assert data.core_version_compatible is True
    assert coordinator.auth_failed is False


@pytest.mark.asyncio
async def test_coordinator_rejects_newer_contract(mock_client):
    mock_client.async_get_capabilities = AsyncMock(return_value=_capabilities(version=3))
    coordinator = _bare_coordinator(mock_client)
    data = await coordinator._async_update_data()
    assert data.shared_decisions_available is False
    assert data.decision_contract_version == 3
    assert data.decision_contract_state is DecisionContractState.NEWER
    assert coordinator.auth_failed is False


@pytest.mark.asyncio
async def test_coordinator_tolerates_missing_capabilities(mock_client):
    mock_client.async_get_capabilities = AsyncMock(
        side_effect=ZigbeeLensInvalidResponseError("missing")
    )
    coordinator = _bare_coordinator(mock_client)
    data = await coordinator._async_update_data()
    assert data.shared_decisions_available is False
    assert data.decision_contract_version is None
    assert data.capabilities_state is CapabilitiesState.MALFORMED
    assert data.decision_contract_state is DecisionContractState.MALFORMED
    assert coordinator.last_update_success is True


@pytest.mark.asyncio
async def test_coordinator_classifies_missing_capabilities_route_as_unavailable(
    mock_client,
):
    mock_client.async_get_capabilities = AsyncMock(
        side_effect=ZigbeeLensRequestRejectedError(404, "not_found")
    )
    coordinator = _bare_coordinator(mock_client)

    data = await coordinator._async_update_data()

    assert data.capabilities_state is CapabilitiesState.UNAVAILABLE
    assert data.decision_contract_state is DecisionContractState.MISSING
    assert data.enrichment_contract_state is EnrichmentContractState.UNAVAILABLE
    assert data.shared_decisions_available is False


@pytest.mark.asyncio
async def test_coordinator_gates_decisions_when_core_incompatible(
    mock_client, sample_health
):
    sample_health = dict(sample_health)
    sample_health["version"] = "0.0.1"
    mock_client.async_get_health = AsyncMock(return_value=sample_health)
    coordinator = _bare_coordinator(mock_client)
    data = await coordinator._async_update_data()
    assert data.core_version_compatible is False
    assert data.core_version_state is CoreVersionState.INCOMPATIBLE
    assert data.shared_decisions_available is False
    assert data.decision_contract_version == 2


@pytest.mark.asyncio
async def test_coordinator_rejects_malformed_dashboard_decision_surfaces(mock_client):
    dashboard = dict(mock_client.async_get_dashboard.return_value)
    dashboard.pop("decision_summary", None)
    mock_client.async_get_dashboard = AsyncMock(return_value=dashboard)
    coordinator = _bare_coordinator(mock_client)
    data = await coordinator._async_update_data()
    assert coordinator.last_update_success is True
    assert data.decision_contract_version == 2
    assert data.shared_decisions_available is False
    assert data.core_version_compatible is True
    assert data.decision_contract_state is DecisionContractState.SUPPORTED_EXACT
    assert data.decision_payload_state is DecisionPayloadState.MALFORMED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_dashboard", "expected_state"),
    [
        (None, DecisionPayloadState.MISSING),
        ([], DecisionPayloadState.MALFORMED),
    ],
)
async def test_coordinator_classifies_non_object_dashboard_without_unreachable(
    mock_client,
    raw_dashboard,
    expected_state,
):
    mock_client.async_get_dashboard = AsyncMock(return_value=raw_dashboard)
    coordinator = _bare_coordinator(mock_client)

    data = await coordinator._async_update_data()

    assert coordinator.last_update_success is True
    assert data.dashboard == {}
    assert data.decision_contract_state is DecisionContractState.SUPPORTED_EXACT
    assert data.decision_payload_state is expected_state
    assert data.shared_decisions_available is False


@pytest.mark.asyncio
async def test_coordinator_classifies_invalid_dashboard_json_as_payload_malformed(
    mock_client,
):
    mock_client.async_get_dashboard = AsyncMock(
        side_effect=ZigbeeLensInvalidResponseError("Invalid JSON from Core")
    )
    coordinator = _bare_coordinator(mock_client)

    data = await coordinator._async_update_data()

    assert coordinator.last_update_success is True
    assert data.decision_contract_state is DecisionContractState.SUPPORTED_EXACT
    assert data.decision_payload_state is DecisionPayloadState.MALFORMED
    assert data.shared_decisions_available is False


@pytest.mark.asyncio
async def test_coordinator_accepts_valid_empty_decision_lists(mock_client):
    dashboard = dict(mock_client.async_get_dashboard.return_value)
    dashboard["investigation_priorities"] = []
    dashboard["data_coverage_warnings"] = []
    mock_client.async_get_dashboard = AsyncMock(return_value=dashboard)
    coordinator = _bare_coordinator(mock_client)
    data = await coordinator._async_update_data()
    assert data.shared_decisions_available is True


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "", " ", "not-a-version", True])
async def test_coordinator_missing_or_malformed_core_version_is_unknown(
    mock_client,
    sample_health,
    version,
):
    health = dict(sample_health)
    health["version"] = version
    mock_client.async_get_health = AsyncMock(return_value=health)
    coordinator = _bare_coordinator(mock_client)

    data = await coordinator._async_update_data()

    assert data.core_version_state is CoreVersionState.UNKNOWN
    assert data.core_version_compatible is None
    assert data.core_version is None
    assert data.shared_decisions_available is False


@pytest.mark.asyncio
async def test_coordinator_absent_core_version_is_unknown(mock_client, sample_health):
    health = dict(sample_health)
    health.pop("version")
    mock_client.async_get_health = AsyncMock(return_value=health)
    coordinator = _bare_coordinator(mock_client)

    data = await coordinator._async_update_data()

    assert data.core_version is None
    assert data.core_version_state is CoreVersionState.UNKNOWN
    assert data.core_version_compatible is None
    assert data.shared_decisions_available is False


@pytest.mark.asyncio
async def test_coordinator_auth_error_still_raises_reauth(mock_client):
    mock_client.async_get_health = AsyncMock(side_effect=ZigbeeLensAuthError("401"))
    coordinator = _bare_coordinator(mock_client)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    assert coordinator.auth_failed is True


@pytest.mark.asyncio
async def test_auth_transition_notifies_after_an_existing_failed_refresh(mock_client):
    mock_client.async_get_health = AsyncMock(side_effect=ZigbeeLensAuthError("401"))
    coordinator = _bare_coordinator(mock_client)
    coordinator._listeners = {"repair-listener": MagicMock()}
    coordinator.async_update_listeners = MagicMock()
    coordinator.last_update_success = False
    coordinator.last_exception = "Cannot connect to ZigbeeLens Core"

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    coordinator.async_update_listeners.assert_called_once_with()
    assert coordinator.auth_failed is True
    assert coordinator.last_exception == "Authentication required"


@pytest.mark.asyncio
async def test_auth_transition_from_success_leaves_notification_to_coordinator(
    mock_client,
):
    mock_client.async_get_health = AsyncMock(side_effect=ZigbeeLensAuthError("401"))
    coordinator = _bare_coordinator(mock_client)
    coordinator._listeners = {"repair-listener": MagicMock()}
    coordinator.async_update_listeners = MagicMock()
    coordinator.last_update_success = True

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()

    coordinator.async_update_listeners.assert_not_called()


@pytest.mark.asyncio
async def test_coordinator_connection_error_is_update_failed(mock_client):
    mock_client.async_get_health = AsyncMock(side_effect=ZigbeeLensConnectionError("down"))
    coordinator = _bare_coordinator(mock_client)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert coordinator.auth_failed is False
