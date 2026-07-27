"""Diagnostics and repairs tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from zigbeelens.compatibility import (
    CapabilitiesState,
    CoreVersionState,
    DecisionContractState,
    DecisionPayloadState,
    EnrichmentContractState,
)
from zigbeelens.const import (
    DOMAIN,
    ISSUE_COLLECTOR_DISCONNECTED,
    ISSUE_CORE_UNREACHABLE,
    ISSUE_CORE_VERSION_UNKNOWN,
    ISSUE_DECISION_CONTRACT_MALFORMED,
    ISSUE_DECISION_CONTRACT_NEWER,
    ISSUE_DECISION_CONTRACT_OLDER,
    ISSUE_DECISION_PAYLOAD_MALFORMED,
    ISSUE_ENRICHMENT_MATCH_INCOMPLETE,
    ISSUE_ENRICHMENT_SYNC_FAILED,
    ISSUE_ENRICHMENT_UNSUPPORTED,
    ISSUE_INCOMPATIBLE_VERSION,
    ISSUE_MOCK_MODE,
    ISSUE_NO_MQTT_DATA,
    ISSUE_NO_NETWORKS,
)
from zigbeelens.coordinator import ZigbeeLensCoordinatorData, ZigbeeLensDataUpdateCoordinator
from zigbeelens.diagnostics import async_get_config_entry_diagnostics
from zigbeelens.repairs import async_manage_repairs, async_clear_repairs


@pytest.fixture
def hass_with_coordinator(mock_coordinator):
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.version = 1
    entry.data = {"core_url": "http://user:secret@localhost:8377"}
    hass.data = {"zigbeelens": {"entry1": {"coordinator": mock_coordinator}}}

    with patch("zigbeelens.diagnostics.er.async_get") as mock_er_get:
        mock_registry = MagicMock()
        mock_er_get.return_value = mock_registry
        with patch(
            "zigbeelens.diagnostics.er.async_entries_for_config_entry",
            return_value=[MagicMock(), MagicMock()],
        ):
            yield hass, entry


@pytest.mark.asyncio
async def test_diagnostics_redacts_secrets(hass_with_coordinator):
    hass, entry = hass_with_coordinator
    payload = await async_get_config_entry_diagnostics(hass, entry)
    assert "secret" not in str(payload)
    assert "user:secret" not in str(payload)
    assert payload["core_url"] == "[invalid]"
    assert payload["core_version"] == "0.1.0"
    assert payload["entity_count"] == 2
    assert "devices" not in payload
    assert "decision_contract_version" in payload
    assert "shared_decisions_available" in payload
    assert "core_version_compatible" in payload
    assert payload["core_version_state"] == "compatible"
    assert payload["decision_contract_state"] == "supported_exact"
    assert payload["decision_payload_state"] == "valid"
    assert payload["enrichment_contract_state"] == "supported"
    assert "capabilities" not in payload
    assert "investigation_priorities" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "http://user:password@localhost:8377",
        "https://host.example?token=leak-token",
        "https://host.example?api_key=leak-key",
        "https://host.example#access_token=leak-frag",
        "http://[::1",
        "https://host.example/path",
        "http://host\x01.example",
    ],
)
async def test_diagnostics_invalid_core_url_fail_closed(raw):
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.version = 1
    entry.data = {"core_url": raw}
    coordinator = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coordinator.data = None
    coordinator.last_update_success = False
    coordinator.last_exception = None
    coordinator.auth_failed = False
    hass.data = {"zigbeelens": {"entry1": {"coordinator": coordinator}}}
    with patch("zigbeelens.diagnostics.er.async_get") as mock_er_get:
        mock_er_get.return_value = MagicMock()
        with patch(
            "zigbeelens.diagnostics.er.async_entries_for_config_entry",
            return_value=[],
        ):
            payload = await async_get_config_entry_diagnostics(hass, entry)
    assert payload["core_url"] == "[invalid]"
    blob = str(payload)
    for sentinel in (
        "password",
        "leak-token",
        "leak-key",
        "leak-frag",
        "user:",
        "/path",
    ):
        assert sentinel not in blob


@pytest.mark.asyncio
async def test_diagnostics_preserves_unknown_compatibility():
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.version = 1
    entry.data = {"core_url": "http://localhost:8377"}
    coordinator = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coordinator.data = None
    coordinator.last_update_success = False
    coordinator.last_exception = None
    coordinator.auth_failed = False
    hass.data = {"zigbeelens": {"entry1": {"coordinator": coordinator}}}

    with patch("zigbeelens.diagnostics.er.async_get") as mock_er_get:
        mock_er_get.return_value = MagicMock()
        with patch(
            "zigbeelens.diagnostics.er.async_entries_for_config_entry",
            return_value=[],
        ):
            payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["decision_contract_version"] is None
    assert payload["core_version_state"] == "unknown"
    assert payload["decision_contract_state"] == "missing"
    assert payload["shared_decisions_available"] is False
    assert payload["core_version_compatible"] is None


def test_repairs_core_unreachable():
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.last_update_success = False
    coord.data = None
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue:
        async_manage_repairs(hass, coord)
        assert any(
            call.args[2] == ISSUE_CORE_UNREACHABLE for call in create_issue.call_args_list
        )


def test_repairs_auth_failed_skips_unreachable():
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.last_update_success = False
    coord.data = None
    coord.auth_failed = True
    coord.last_exception = "Authentication required"

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ) as delete_issue:
        async_manage_repairs(hass, coord)
    assert not any(
        call.args[2] == ISSUE_CORE_UNREACHABLE for call in create_issue.call_args_list
    )
    assert any(
        call.args[2] == ISSUE_CORE_UNREACHABLE for call in delete_issue.call_args_list
    )


@pytest.mark.asyncio
async def test_diagnostics_hides_api_token_sentinel():
    import json

    sentinel = "zl-hacs-diag-sentinel-token-aaaaaaa"
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.version = 1
    entry.data = {
        "core_url": "http://localhost:8377",
        "api_token": sentinel,
    }
    coordinator = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coordinator.data = None
    coordinator.last_update_success = False
    coordinator.last_exception = "Authentication required"
    coordinator.auth_failed = True
    hass.data = {"zigbeelens": {"entry1": {"coordinator": coordinator}}}

    with patch("zigbeelens.diagnostics.er.async_get") as mock_er_get:
        mock_er_get.return_value = MagicMock()
        with patch(
            "zigbeelens.diagnostics.er.async_entries_for_config_entry",
            return_value=[],
        ):
            payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["api_token_configured"] is True
    assert payload["last_error_category"] == "authentication"
    blob = json.dumps(payload)
    assert sentinel not in blob
    assert sentinel not in str(payload)
    assert "Authorization" not in blob
    assert "api_token" not in payload


@pytest.mark.asyncio
async def test_diagnostics_never_projects_malformed_raw_core_version(
    sample_health,
    sample_dashboard,
    sample_config_status,
):
    raw_version = "malformed-version-secret"
    health = dict(sample_health)
    health["version"] = raw_version
    collector_sentinel = "Kitchen Lamp light.secret 0x00124b0001abcdef"
    health["collector"] = {
        "enabled": True,
        "connected": True,
        "subscribed_topics_count": 4,
        "last_message_at": collector_sentinel,
        "last_error": collector_sentinel,
        "unreviewed_registry_payload": collector_sentinel,
    }
    coordinator = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coordinator.data = ZigbeeLensCoordinatorData(
        health=health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version=None,
        collector_connected=True,
        last_update_success=True,
        core_version_state=CoreVersionState.UNKNOWN,
        core_version_compatible=None,
    )
    coordinator.last_update_success = True
    coordinator.auth_failed = False
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.version = 2
    entry.data = {"core_url": "http://localhost:8377", "api_token": ""}
    hass.data = {DOMAIN: {"entry1": {"coordinator": coordinator}}}

    with patch("zigbeelens.diagnostics.er.async_get", return_value=MagicMock()), patch(
        "zigbeelens.diagnostics.er.async_entries_for_config_entry",
        return_value=[],
    ):
        payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["core_version"] is None
    assert payload["health"]["version"] is None
    assert raw_version not in str(payload)
    assert collector_sentinel not in str(payload)
    assert payload["health"]["collector"] == {
        "enabled": True,
        "connected": True,
        "subscribed_topics_count": 4,
        "last_message_at": None,
        "last_error": "[redacted]",
    }


@pytest.mark.asyncio
async def test_enrichment_diagnostics_sanitize_every_projected_field(
    sample_health,
    sample_dashboard,
    sample_config_status,
):
    sentinel = "Kitchen Lamp 0x00124b0001abcdef light.private ha-device-private"
    coordinator = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coordinator.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version="0.1.13",
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
    coordinator.auth_failed = False
    manager = MagicMock(
        diagnostics={
            "sync_state": sentinel,
            "match_state": sentinel,
            "last_attempt_at": sentinel,
            "last_success_at": sentinel,
            "submitted": sentinel,
            "matched": sentinel,
            "unmatched": sentinel,
            "ambiguous": sentinel,
            "stored": sentinel,
            "failure_reason": sentinel,
            "unreviewed_identity": sentinel,
        }
    )
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.version = 2
    entry.data = {"core_url": "http://localhost:8377", "api_token": ""}
    hass.data = {
        DOMAIN: {
            "entry1": {
                "coordinator": coordinator,
                "enrichment_manager": manager,
            }
        }
    }

    with (
        patch(
            "zigbeelens.diagnostics.er.async_get",
            return_value=MagicMock(),
        ),
        patch(
            "zigbeelens.diagnostics.er.async_entries_for_config_entry",
            return_value=[],
        ),
    ):
        payload = await async_get_config_entry_diagnostics(hass, entry)

    enrichment = payload["home_assistant_enrichment"]
    assert enrichment == {
        "sync_state": "unknown",
        "match_state": "unknown",
        "last_attempt_at": None,
        "last_success_at": None,
        "submitted": None,
        "matched": None,
        "unmatched": None,
        "ambiguous": None,
        "stored": None,
        "failure_reason": "unknown",
    }
    assert sentinel not in repr(payload)


def test_repairs_collector_disconnected(sample_health, sample_dashboard, sample_config_status):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    sample_health = dict(sample_health)
    sample_health["collector"] = {"connected": False}
    coord.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version="0.1.0",
        collector_connected=False,
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
    coord.last_update_success = True
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ):
        async_manage_repairs(hass, coord)
        assert any(
            call.args[2] == ISSUE_COLLECTOR_DISCONNECTED for call in create_issue.call_args_list
        )


def test_repairs_mock_mode(sample_health, sample_dashboard, sample_config_status):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    sample_health = dict(sample_health)
    sample_health["mock_mode"] = True
    coord.data = ZigbeeLensCoordinatorData(
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
    coord.last_update_success = True
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ):
        async_manage_repairs(hass, coord)
        assert any(call.args[2] == ISSUE_MOCK_MODE for call in create_issue.call_args_list)


def test_repairs_incompatible_core_version(sample_health, sample_dashboard, sample_config_status):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version="0.0.1",
        collector_connected=True,
        last_update_success=True,
        core_version_state=CoreVersionState.INCOMPATIBLE,
        core_version_compatible=False,
    )
    coord.last_update_success = True
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ):
        async_manage_repairs(hass, coord)
        assert any(call.args[2] == ISSUE_INCOMPATIBLE_VERSION for call in create_issue.call_args_list)


def test_unsupported_decision_contract_does_not_create_version_repair(
    sample_health, sample_dashboard, sample_config_status
):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version="0.1.13",
        collector_connected=True,
        last_update_success=True,
        capabilities_state=CapabilitiesState.ACCEPTED,
        decision_contract_version=2,
        decision_contract_state=DecisionContractState.MISSING_REQUIRED_CAPABILITY,
        decision_payload_state=DecisionPayloadState.VALID,
        enrichment_contract_state=EnrichmentContractState.SUPPORTED,
        core_version_state=CoreVersionState.COMPATIBLE,
        shared_decisions_available=False,
        core_version_compatible=True,
    )
    coord.last_update_success = True
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ):
        async_manage_repairs(hass, coord)
        assert not any(
            call.args[2] == ISSUE_INCOMPATIBLE_VERSION for call in create_issue.call_args_list
        )


@pytest.mark.parametrize(
    ("contract_state", "payload_state", "version", "expected_issue"),
    [
        (
            DecisionContractState.OLDER,
            DecisionPayloadState.VALID,
            1,
            ISSUE_DECISION_CONTRACT_OLDER,
        ),
        (
            DecisionContractState.NEWER,
            DecisionPayloadState.VALID,
            3,
            ISSUE_DECISION_CONTRACT_NEWER,
        ),
        (
            DecisionContractState.SUPPORTED_EXACT,
            DecisionPayloadState.MALFORMED,
            2,
            ISSUE_DECISION_PAYLOAD_MALFORMED,
        ),
        (
            DecisionContractState.MALFORMED,
            DecisionPayloadState.VALID,
            2,
            ISSUE_DECISION_CONTRACT_MALFORMED,
        ),
    ],
)
def test_repairs_distinguish_contract_and_payload_failures(
    sample_health,
    sample_dashboard,
    sample_config_status,
    contract_state,
    payload_state,
    version,
    expected_issue,
):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version="0.1.13",
        collector_connected=True,
        last_update_success=True,
        capabilities_state=CapabilitiesState.ACCEPTED,
        decision_contract_version=version,
        decision_contract_state=contract_state,
        decision_payload_state=payload_state,
        enrichment_contract_state=EnrichmentContractState.SUPPORTED,
        core_version_state=CoreVersionState.COMPATIBLE,
        shared_decisions_available=False,
        core_version_compatible=True,
    )
    coord.last_update_success = True
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ):
        async_manage_repairs(hass, coord)

    created = {call.args[2] for call in create_issue.call_args_list}
    assert expected_issue in created
    assert (
        created
        & {
            ISSUE_DECISION_CONTRACT_OLDER,
            ISSUE_DECISION_CONTRACT_NEWER,
            ISSUE_DECISION_CONTRACT_MALFORMED,
            ISSUE_DECISION_PAYLOAD_MALFORMED,
        }
    ) == {expected_issue}


def test_repairs_unknown_core_version_is_not_compatible(
    sample_health, sample_dashboard, sample_config_status
):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version=None,
        collector_connected=True,
        last_update_success=True,
        capabilities_state=CapabilitiesState.ACCEPTED,
        decision_contract_version=2,
        decision_contract_state=DecisionContractState.SUPPORTED_EXACT,
        decision_payload_state=DecisionPayloadState.VALID,
        enrichment_contract_state=EnrichmentContractState.SUPPORTED,
        core_version_state=CoreVersionState.UNKNOWN,
        shared_decisions_available=False,
        core_version_compatible=None,
    )
    coord.last_update_success = True
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ):
        async_manage_repairs(hass, coord)

    created = {call.args[2] for call in create_issue.call_args_list}
    assert ISSUE_CORE_VERSION_UNKNOWN in created
    assert ISSUE_INCOMPATIBLE_VERSION not in created
    assert ISSUE_DECISION_CONTRACT_OLDER not in created


def test_repairs_capabilities_outage_does_not_claim_core_is_older(
    sample_health, sample_dashboard, sample_config_status
):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version="0.1.13",
        collector_connected=True,
        last_update_success=True,
        capabilities_state=CapabilitiesState.UNAVAILABLE,
        decision_contract_version=None,
        decision_contract_state=DecisionContractState.MISSING,
        decision_payload_state=DecisionPayloadState.VALID,
        enrichment_contract_state=EnrichmentContractState.UNAVAILABLE,
        core_version_state=CoreVersionState.COMPATIBLE,
        shared_decisions_available=False,
        core_version_compatible=True,
    )
    coord.last_update_success = True
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ):
        async_manage_repairs(hass, coord)

    created = {call.args[2] for call in create_issue.call_args_list}
    assert ISSUE_DECISION_CONTRACT_OLDER not in created
    assert ISSUE_DECISION_CONTRACT_NEWER not in created
    assert ISSUE_DECISION_PAYLOAD_MALFORMED not in created


def test_repairs_do_not_coerce_malformed_operational_fields_to_empty_or_zero(
    sample_health,
):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.data = ZigbeeLensCoordinatorData(
        health={**sample_health, "collector": {}, "mock_mode": "false"},
        dashboard={"networks": "not-a-list", "device_count": "not-a-count"},
        config_status={"configured_networks": None, "mock_mode": "false"},
        core_version="0.1.13",
        collector_connected=None,
        last_update_success=True,
        capabilities_state=CapabilitiesState.ACCEPTED,
        decision_contract_version=2,
        decision_contract_state=DecisionContractState.SUPPORTED_EXACT,
        decision_payload_state=DecisionPayloadState.MALFORMED,
        enrichment_contract_state=EnrichmentContractState.SUPPORTED,
        core_version_state=CoreVersionState.COMPATIBLE,
        shared_decisions_available=False,
        core_version_compatible=True,
    )
    coord.last_update_success = True
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ):
        async_manage_repairs(hass, coord)

    created = {call.args[2] for call in create_issue.call_args_list}
    assert ISSUE_DECISION_PAYLOAD_MALFORMED in created
    assert ISSUE_NO_NETWORKS not in created
    assert ISSUE_NO_MQTT_DATA not in created
    assert ISSUE_COLLECTOR_DISCONNECTED not in created
    assert ISSUE_MOCK_MODE not in created


def test_payload_repair_is_deleted_after_valid_recovery(
    sample_health,
    sample_dashboard,
    sample_config_status,
):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard={},
        config_status=sample_config_status,
        core_version="0.1.13",
        collector_connected=True,
        last_update_success=True,
        capabilities_state=CapabilitiesState.ACCEPTED,
        decision_contract_version=2,
        decision_contract_state=DecisionContractState.SUPPORTED_EXACT,
        decision_payload_state=DecisionPayloadState.MALFORMED,
        enrichment_contract_state=EnrichmentContractState.SUPPORTED,
        core_version_state=CoreVersionState.COMPATIBLE,
        shared_decisions_available=False,
        core_version_compatible=True,
    )
    coord.last_update_success = True
    coord.auth_failed = False

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ) as delete_issue:
        async_manage_repairs(hass, coord)
        assert any(
            call.args[2] == ISSUE_DECISION_PAYLOAD_MALFORMED
            for call in create_issue.call_args_list
        )

        coord.data.dashboard = sample_dashboard
        coord.data.decision_payload_state = DecisionPayloadState.VALID
        coord.data.shared_decisions_available = True
        async_manage_repairs(hass, coord)

    assert any(
        call.args[2] == ISSUE_DECISION_PAYLOAD_MALFORMED
        for call in delete_issue.call_args_list
    )


def _enrichment_ready_coordinator(
    sample_health,
    sample_dashboard,
    sample_config_status,
):
    coordinator = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coordinator.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version="0.1.13",
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
    coordinator.auth_failed = False
    return coordinator


@pytest.mark.parametrize(
    ("sync_state", "expected", "excluded"),
    [
        (
            "failed_authentication",
            set(),
            {ISSUE_ENRICHMENT_SYNC_FAILED, ISSUE_ENRICHMENT_UNSUPPORTED},
        ),
        (
            "failed_contract_unsupported",
            {ISSUE_ENRICHMENT_UNSUPPORTED},
            {ISSUE_ENRICHMENT_SYNC_FAILED},
        ),
        (
            "failed_request_rejected",
            {ISSUE_ENRICHMENT_SYNC_FAILED},
            {ISSUE_ENRICHMENT_UNSUPPORTED},
        ),
    ],
)
def test_enrichment_repairs_distinguish_auth_route_and_terminal_sync_failure(
    sample_health,
    sample_dashboard,
    sample_config_status,
    sync_state,
    expected,
    excluded,
):
    hass = MagicMock()
    coord = MagicMock(spec=ZigbeeLensDataUpdateCoordinator)
    coord.data = ZigbeeLensCoordinatorData(
        health=sample_health,
        dashboard=sample_dashboard,
        config_status=sample_config_status,
        core_version="0.1.13",
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
    coord.last_update_success = True
    coord.auth_failed = False
    manager = MagicMock(
        diagnostics={
            "sync_state": sync_state,
            "match_state": (
                "partial_unmatched"
                if sync_state == "failed_authentication"
                else "complete"
            ),
            "failure_reason": "categorical-only",
        }
    )

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ):
        async_manage_repairs(hass, coord, manager)

    created = {call.args[2] for call in create_issue.call_args_list}
    assert expected <= created
    assert not (excluded & created)
    if sync_state == "failed_authentication":
        assert ISSUE_ENRICHMENT_MATCH_INCOMPLETE not in created


@pytest.mark.parametrize(
    ("match_state", "counts", "expect_issue"),
    [
        ("no_candidates", (0, 0, 0, 0, 0), False),
        ("complete", (1, 1, 0, 0, 1), False),
        ("partial_unmatched", (2, 1, 1, 0, 1), True),
        ("partial_ambiguous", (2, 1, 0, 1, 1), True),
        ("no_matches", (1, 0, 1, 0, 0), True),
        ("no_matches_ambiguous", (1, 0, 0, 1, 0), True),
    ],
)
def test_enrichment_match_repairs_use_only_categorical_identity_free_state(
    sample_health,
    sample_dashboard,
    sample_config_status,
    match_state,
    counts,
    expect_issue,
):
    coord = _enrichment_ready_coordinator(
        sample_health,
        sample_dashboard,
        sample_config_status,
    )
    submitted, matched, unmatched, ambiguous, stored = counts
    manager = MagicMock(
        diagnostics={
            "sync_state": "successful",
            "match_state": match_state,
            "submitted": submitted,
            "matched": matched,
            "unmatched": unmatched,
            "ambiguous": ambiguous,
            "stored": stored,
            "failure_reason": None,
        }
    )

    with (
        patch("zigbeelens.repairs.ir.async_create_issue") as create_issue,
        patch("zigbeelens.repairs.ir.async_delete_issue"),
    ):
        async_manage_repairs(MagicMock(), coord, manager)

    matching_calls = [
        call
        for call in create_issue.call_args_list
        if call.args[2] == ISSUE_ENRICHMENT_MATCH_INCOMPLETE
    ]
    assert bool(matching_calls) is expect_issue
    for call in matching_calls:
        assert "translation_placeholders" not in call.kwargs
        encoded = repr(call)
        for identity in (
            "0x00124b0001abcdef",
            "light.private",
            "ha-device-private",
            "Kitchen Lamp",
        ):
            assert identity not in encoded


def test_enrichment_match_repair_clears_immediately_after_full_recovery(
    sample_health,
    sample_dashboard,
    sample_config_status,
):
    coord = _enrichment_ready_coordinator(
        sample_health,
        sample_dashboard,
        sample_config_status,
    )
    manager = MagicMock(
        diagnostics={
            "sync_state": "successful",
            "match_state": "no_matches",
            "submitted": 1,
            "matched": 0,
            "unmatched": 1,
            "ambiguous": 0,
            "stored": 0,
            "failure_reason": None,
        }
    )

    with (
        patch("zigbeelens.repairs.ir.async_create_issue") as create_issue,
        patch("zigbeelens.repairs.ir.async_delete_issue") as delete_issue,
    ):
        async_manage_repairs(MagicMock(), coord, manager)
        assert any(
            call.args[2] == ISSUE_ENRICHMENT_MATCH_INCOMPLETE
            for call in create_issue.call_args_list
        )

        manager.diagnostics = {
            **manager.diagnostics,
            "match_state": "complete",
            "matched": 1,
            "unmatched": 0,
            "stored": 1,
        }
        delete_issue.reset_mock()
        async_manage_repairs(MagicMock(), coord, manager)

    assert any(
        call.args[2] == ISSUE_ENRICHMENT_MATCH_INCOMPLETE
        for call in delete_issue.call_args_list
    )


def test_clear_repairs():
    hass = MagicMock()
    with patch("zigbeelens.repairs.ir.async_delete_issue") as delete_issue:
        async_clear_repairs(hass)
        assert delete_issue.call_count >= 6
