"""Repair issues for ZigbeeLens integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .compatibility import (
    CapabilitiesState,
    CoreVersionState,
    DecisionContractState,
    DecisionPayloadState,
    EnrichmentContractState,
    nonneg_int_not_bool,
)
from .const import (
    DOMAIN,
    ISSUE_COLLECTOR_DISCONNECTED,
    ISSUE_CORE_UNREACHABLE,
    ISSUE_DECISION_CONTRACT_INCOMPATIBLE,
    ISSUE_DECISION_CONTRACT_MALFORMED,
    ISSUE_DECISION_CONTRACT_NEWER,
    ISSUE_DECISION_CONTRACT_OLDER,
    ISSUE_DECISION_PAYLOAD_MALFORMED,
    ISSUE_ENRICHMENT_MATCH_INCOMPLETE,
    ISSUE_ENRICHMENT_SYNC_FAILED,
    ISSUE_ENRICHMENT_UNSUPPORTED,
    ISSUE_INCOMPATIBLE_VERSION,
    ISSUE_CORE_VERSION_UNKNOWN,
    ISSUE_MOCK_MODE,
    ISSUE_NO_MQTT_DATA,
    ISSUE_NO_NETWORKS,
)
from .coordinator import ZigbeeLensDataUpdateCoordinator


_COMPATIBILITY_ISSUES = (
    ISSUE_INCOMPATIBLE_VERSION,
    ISSUE_CORE_VERSION_UNKNOWN,
    ISSUE_DECISION_CONTRACT_INCOMPATIBLE,
    ISSUE_DECISION_CONTRACT_OLDER,
    ISSUE_DECISION_CONTRACT_NEWER,
    ISSUE_DECISION_CONTRACT_MALFORMED,
    ISSUE_DECISION_PAYLOAD_MALFORMED,
    ISSUE_ENRICHMENT_UNSUPPORTED,
    ISSUE_ENRICHMENT_SYNC_FAILED,
    ISSUE_ENRICHMENT_MATCH_INCOMPLETE,
)


def _clear_issues(hass: HomeAssistant, issue_ids: tuple[str, ...]) -> None:
    for issue_id in issue_ids:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def async_manage_repairs(
    hass: HomeAssistant,
    coordinator: ZigbeeLensDataUpdateCoordinator,
    enrichment_manager: object | None = None,
) -> None:
    """Create or clear repairs based on coordinator state."""
    if getattr(coordinator, "auth_failed", False):
        # Authentication failures use linked reauth, not an unreachable repair.
        ir.async_delete_issue(hass, DOMAIN, ISSUE_CORE_UNREACHABLE)
        _clear_issues(hass, _COMPATIBILITY_ISSUES)
        return

    if not coordinator.last_update_success or coordinator.data is None:
        _clear_issues(hass, _COMPATIBILITY_ISSUES)
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

    if data.core_version_state is CoreVersionState.INCOMPATIBLE:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_INCOMPATIBLE_VERSION,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_INCOMPATIBLE_VERSION,
            translation_placeholders={"version": data.core_version or "unknown"},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_INCOMPATIBLE_VERSION)

    if data.core_version_state is CoreVersionState.UNKNOWN:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_CORE_VERSION_UNKNOWN,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_CORE_VERSION_UNKNOWN,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_CORE_VERSION_UNKNOWN)

    # Always retire the former catch-all contract issue.
    ir.async_delete_issue(hass, DOMAIN, ISSUE_DECISION_CONTRACT_INCOMPATIBLE)
    decision_issue: str | None = None
    if (
        data.core_version_state is CoreVersionState.COMPATIBLE
        and data.capabilities_state is CapabilitiesState.MALFORMED
    ):
        decision_issue = ISSUE_DECISION_CONTRACT_MALFORMED
    elif (
        data.core_version_state is CoreVersionState.COMPATIBLE
        and data.capabilities_state is CapabilitiesState.ACCEPTED
    ):
        if data.decision_contract_state in {
            DecisionContractState.MISSING,
            DecisionContractState.OLDER,
            DecisionContractState.MISSING_REQUIRED_CAPABILITY,
        }:
            decision_issue = ISSUE_DECISION_CONTRACT_OLDER
        elif data.decision_contract_state is DecisionContractState.NEWER:
            decision_issue = ISSUE_DECISION_CONTRACT_NEWER
        elif data.decision_contract_state is DecisionContractState.MALFORMED:
            decision_issue = ISSUE_DECISION_CONTRACT_MALFORMED
        elif (
            data.decision_contract_state is DecisionContractState.SUPPORTED_EXACT
            and data.decision_payload_state
            in {DecisionPayloadState.MISSING, DecisionPayloadState.MALFORMED}
        ):
            decision_issue = ISSUE_DECISION_PAYLOAD_MALFORMED

    for issue_id in (
        ISSUE_DECISION_CONTRACT_OLDER,
        ISSUE_DECISION_CONTRACT_NEWER,
        ISSUE_DECISION_CONTRACT_MALFORMED,
        ISSUE_DECISION_PAYLOAD_MALFORMED,
    ):
        if issue_id == decision_issue:
            placeholders = None
            if issue_id in {
                ISSUE_DECISION_CONTRACT_OLDER,
                ISSUE_DECISION_CONTRACT_NEWER,
            }:
                placeholders = {
                    "version": (
                        str(data.decision_contract_version)
                        if data.decision_contract_version is not None
                        else "unobserved"
                    )
                }
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=issue_id,
                translation_placeholders=placeholders,
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    manager_state = getattr(enrichment_manager, "diagnostics", None)
    manager_sync_state = (
        str(manager_state.get("sync_state", ""))
        if isinstance(manager_state, dict)
        else ""
    )
    manager_match_state = (
        str(manager_state.get("match_state", ""))
        if isinstance(manager_state, dict)
        else ""
    )
    route_unsupported = manager_sync_state == "failed_contract_unsupported"
    enrichment_issue = route_unsupported or (
        data.enrichment_contract_state
        in {
            EnrichmentContractState.MISSING,
            EnrichmentContractState.UNSUPPORTED,
            EnrichmentContractState.MALFORMED,
        }
    )
    if enrichment_issue and data.core_version_state is CoreVersionState.COMPATIBLE:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_ENRICHMENT_UNSUPPORTED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_ENRICHMENT_UNSUPPORTED,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ENRICHMENT_UNSUPPORTED)

    sync_failed = bool(
        manager_sync_state.startswith("failed")
        and manager_sync_state
        not in {
            "failed_authentication",
            "failed_contract_unsupported",
        }
    )
    if sync_failed:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_ENRICHMENT_SYNC_FAILED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_ENRICHMENT_SYNC_FAILED,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ENRICHMENT_SYNC_FAILED)

    match_incomplete = manager_match_state in {
        "partial_unmatched",
        "partial_ambiguous",
        "no_matches",
        "no_matches_ambiguous",
    }
    if (
        match_incomplete
        and manager_sync_state != "failed_authentication"
        and data.core_version_state is CoreVersionState.COMPATIBLE
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_ENRICHMENT_MATCH_INCOMPLETE,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_ENRICHMENT_MATCH_INCOMPLETE,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ENRICHMENT_MATCH_INCOMPLETE)

    if data.collector_connected is False:
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

    networks = dashboard.get("networks")
    configured = config_status.get("configured_networks")
    operational_inventory_observed = isinstance(networks, list) and isinstance(
        configured,
        list,
    )
    if operational_inventory_observed and not networks and not configured:
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

    device_count = nonneg_int_not_bool(dashboard.get("device_count"))
    if (
        isinstance(networks, list)
        and networks
        and device_count == 0
        and health.get("mock_mode") is False
    ):
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

    if health.get("mock_mode") is True or config_status.get("mock_mode") is True:
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
        ISSUE_INCOMPATIBLE_VERSION,
        ISSUE_DECISION_CONTRACT_INCOMPATIBLE,
        ISSUE_CORE_VERSION_UNKNOWN,
        ISSUE_DECISION_CONTRACT_OLDER,
        ISSUE_DECISION_CONTRACT_NEWER,
        ISSUE_DECISION_CONTRACT_MALFORMED,
        ISSUE_DECISION_PAYLOAD_MALFORMED,
        ISSUE_ENRICHMENT_UNSUPPORTED,
        ISSUE_ENRICHMENT_SYNC_FAILED,
        ISSUE_ENRICHMENT_MATCH_INCOMPLETE,
        ISSUE_COLLECTOR_DISCONNECTED,
        ISSUE_NO_NETWORKS,
        ISSUE_NO_MQTT_DATA,
        ISSUE_MOCK_MODE,
    ):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
