"""Core version and decision-contract compatibility helpers for the HACS companion.

Track 5 requires exact decision contract v2. Missing, older, newer, or malformed
contracts disable companion decision display — never fall back to Health/Lens.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

# Must match apps/core/.../api/summary.py DECISION_CONTRACT_VERSION.
DECISION_CONTRACT_VERSION = 2

# Exact versions this HACS package understands. Do not treat newer as compatible.
SUPPORTED_DECISION_CONTRACT_VERSIONS = frozenset({2})

REQUIRED_COMPANION_CAPABILITIES = frozenset(
    {
        "shared_decisions",
        "companion_decision_summary",
        "decision_only_diagnostic_payloads",
        "report_contract_v3",
        "decision_mqtt_summary",
    }
)

REQUIRED_COMPANION_DECISION_SURFACES = frozenset(
    {
        "dashboard_decision_summary",
        "dashboard_investigation_priorities",
        "dashboard_data_coverage_warnings",
        "network_decision_badges",
        "device_decision_badges",
    }
)

KNOWN_DECISION_STATUSES = frozenset(
    {
        "informational",
        "no_notable_change",
        "changed",
        "watch",
        "worth_reviewing",
        "review_first",
        "improve_data_coverage",
        "data_unavailable",
    }
)

DECISION_STATUS_ORDER = (
    "review_first",
    "worth_reviewing",
    "improve_data_coverage",
    "watch",
    "changed",
    "informational",
    "no_notable_change",
    "data_unavailable",
)

KNOWN_DECISION_PRIORITIES = frozenset(
    {
        "none",
        "low",
        "medium",
        "high",
    }
)

DECISION_PRIORITY_ORDER = (
    "high",
    "medium",
    "low",
    "none",
)

KNOWN_COVERAGE_LABEL_CODES = frozenset(
    {
        "availability_tracking_off",
        "availability_history_building",
        "availability_status_unknown",
        "availability_available",
        "route_hints_unavailable",
        "ha_areas_not_linked",
        "snapshot_stale",
        "battery_history_sparse",
        "battery_history_available",
        "lqi_history_sparse",
        "lqi_history_available",
        "last_seen_available",
        "last_seen_unknown",
        "last_payload_available",
        "last_payload_unknown",
        "topology_history_available",
        "topology_history_sparse",
        "topology_history_not_observed",
        "ha_area_linked",
    }
)

KNOWN_AVAILABILITIES = frozenset({"online", "offline", "unknown"})

# Absolute minimum Core this integration expects for basic operational use.
MIN_CORE_VERSION = (0, 1, 0)

HOME_ASSISTANT_ENRICHMENT_CONTRACT_VERSION = 1

_SEMVER_PRERELEASE_IDENTIFIER = (
    r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
)
_CORE_VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)"
    r"\.(?P<minor>0|[1-9][0-9]*)"
    r"\.(?P<patch>0|[1-9][0-9]*)"
    rf"(?:-{_SEMVER_PRERELEASE_IDENTIFIER}"
    rf"(?:\.{_SEMVER_PRERELEASE_IDENTIFIER})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class CoreVersionState(StrEnum):
    """Compatibility classification for the observed Core version."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


class CapabilitiesState(StrEnum):
    """Availability and integrity of the capabilities response."""

    ACCEPTED = "accepted"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"


class DecisionContractState(StrEnum):
    """Compatibility classification for the companion Decision contract."""

    SUPPORTED_EXACT = "supported_exact"
    MISSING = "missing"
    OLDER = "older"
    NEWER = "newer"
    MALFORMED = "malformed"
    MISSING_REQUIRED_CAPABILITY = "missing_required_capability"


class DecisionPayloadState(StrEnum):
    """Integrity state for exact-contract Dashboard Decision data."""

    VALID = "valid"
    MISSING = "missing"
    MALFORMED = "malformed"


class EnrichmentContractState(StrEnum):
    """Compatibility classification for Core-local HA enrichment."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    MISSING = "missing"
    MALFORMED = "malformed"
    UNAVAILABLE = "unavailable"


def parse_core_version(version: object) -> tuple[int, ...] | None:
    """Parse a strict dotted Core version without coercing malformed values."""
    if (
        not isinstance(version, str)
        or not version
        or len(version) > 64
        or version != version.strip()
    ):
        return None
    match = _CORE_VERSION_RE.fullmatch(version)
    if match is None:
        return None
    return tuple(
        int(match.group(name))
        for name in (
            "major",
            "minor",
            "patch",
        )
    )


def classify_core_version(
    version: object,
    *,
    minimum: tuple[int, ...] = MIN_CORE_VERSION,
) -> CoreVersionState:
    """Classify Core version, failing closed when it is absent or malformed."""
    parsed = parse_core_version(version)
    if parsed is None:
        return CoreVersionState.UNKNOWN
    if parsed < minimum:
        return CoreVersionState.INCOMPATIBLE
    return CoreVersionState.COMPATIBLE


def core_version_compatible(
    version: object,
    *,
    minimum: tuple[int, ...] = MIN_CORE_VERSION,
) -> bool:
    """Derived compatibility boolean; unknown versions are never compatible."""
    return classify_core_version(version, minimum=minimum) is CoreVersionState.COMPATIBLE


def nonneg_int_not_bool(value: Any) -> int | None:
    """Return a non-negative int or None when missing, bool, float, or invalid."""
    if value is None or isinstance(value, bool):
        return None
    if type(value) is not int:
        return None
    if value < 0:
        return None
    return value


def decision_contract_version(capabilities: dict[str, Any] | None) -> int | None:
    """Return an observed strict contract integer, or None when unobserved."""
    if not isinstance(capabilities, dict):
        return None
    raw = capabilities.get("decision_contract_version")
    if type(raw) is not int or raw < 0:
        return None
    return raw


def classify_decision_contract(
    capabilities: dict[str, Any] | None,
    capabilities_state: CapabilitiesState = CapabilitiesState.ACCEPTED,
) -> DecisionContractState:
    """Classify the exact companion Decision contract without sentinel versions."""
    if capabilities_state is CapabilitiesState.MALFORMED:
        return DecisionContractState.MALFORMED
    if capabilities_state is CapabilitiesState.UNAVAILABLE:
        return DecisionContractState.MISSING
    if not isinstance(capabilities, dict):
        return DecisionContractState.MALFORMED
    if "decision_contract_version" not in capabilities:
        return DecisionContractState.MISSING
    raw_version = capabilities.get("decision_contract_version")
    if type(raw_version) is not int or raw_version < 0:
        return DecisionContractState.MALFORMED
    if raw_version < DECISION_CONTRACT_VERSION:
        return DecisionContractState.OLDER
    if raw_version > DECISION_CONTRACT_VERSION:
        return DecisionContractState.NEWER
    caps = capabilities.get("capabilities")
    if caps is None:
        return DecisionContractState.MISSING_REQUIRED_CAPABILITY
    if not isinstance(caps, dict):
        return DecisionContractState.MALFORMED
    for name in REQUIRED_COMPANION_CAPABILITIES:
        if name not in caps or caps[name] is False:
            return DecisionContractState.MISSING_REQUIRED_CAPABILITY
        if caps[name] is not True:
            return DecisionContractState.MALFORMED
    # Explicit negative fact — missing/null/string/0 must not pass.
    if (
        "legacy_health_lens_payloads" not in caps
        or caps["legacy_health_lens_payloads"] is True
    ):
        return DecisionContractState.MISSING_REQUIRED_CAPABILITY
    if caps["legacy_health_lens_payloads"] is not False:
        return DecisionContractState.MALFORMED
    surfaces = capabilities.get("decision_surfaces")
    if surfaces is None:
        return DecisionContractState.MISSING_REQUIRED_CAPABILITY
    if not isinstance(surfaces, dict):
        return DecisionContractState.MALFORMED
    for surface in REQUIRED_COMPANION_DECISION_SURFACES:
        if surface not in surfaces or surfaces[surface] is False:
            return DecisionContractState.MISSING_REQUIRED_CAPABILITY
        if surfaces[surface] is not True:
            return DecisionContractState.MALFORMED
    return DecisionContractState.SUPPORTED_EXACT


def supports_companion_decisions(capabilities: dict[str, Any] | None) -> bool:
    """Derived gate: True only for the exact supported companion contract."""
    return (
        classify_decision_contract(capabilities)
        is DecisionContractState.SUPPORTED_EXACT
    )


def classify_enrichment_contract(
    capabilities: dict[str, Any] | None,
    capabilities_state: CapabilitiesState = CapabilitiesState.ACCEPTED,
) -> EnrichmentContractState:
    """Classify Core's exact Home Assistant enrichment contract."""
    if capabilities_state is CapabilitiesState.UNAVAILABLE:
        return EnrichmentContractState.UNAVAILABLE
    if capabilities_state is CapabilitiesState.MALFORMED:
        return EnrichmentContractState.MALFORMED
    if not isinstance(capabilities, dict):
        return EnrichmentContractState.MALFORMED
    caps = capabilities.get("capabilities")
    if not isinstance(caps, dict):
        return EnrichmentContractState.MALFORMED
    if "home_assistant_enrichment" not in caps:
        return EnrichmentContractState.MISSING
    if caps.get("home_assistant_enrichment") is False:
        return EnrichmentContractState.UNSUPPORTED
    if caps.get("home_assistant_enrichment") is not True:
        return EnrichmentContractState.MALFORMED
    raw = capabilities.get("home_assistant_enrichment_contract_version")
    if type(raw) is not int or isinstance(raw, bool):
        return EnrichmentContractState.MALFORMED
    if raw != HOME_ASSISTANT_ENRICHMENT_CONTRACT_VERSION:
        return EnrichmentContractState.UNSUPPORTED
    return EnrichmentContractState.SUPPORTED


def validate_decision_count_summary(summary: Any) -> bool:
    """True when a DecisionCountSummary dict is a pure subject-count fold."""
    if not isinstance(summary, dict):
        return False
    subject_count = nonneg_int_not_bool(summary.get("subject_count"))
    if subject_count is None:
        return False
    overall = summary.get("overall_status")
    if not isinstance(overall, str) or overall not in KNOWN_DECISION_STATUSES:
        return False
    highest = summary.get("highest_priority")
    if not isinstance(highest, str) or highest not in KNOWN_DECISION_PRIORITIES:
        return False
    coverage = nonneg_int_not_bool(summary.get("coverage_warning_count"))
    if coverage is None:
        return False
    status_counts = summary.get("status_counts")
    if not isinstance(status_counts, dict):
        return False
    priority_counts = summary.get("priority_counts")
    if not isinstance(priority_counts, dict):
        return False

    status_total = 0
    for key, count in status_counts.items():
        if key not in KNOWN_DECISION_STATUSES:
            return False
        parsed = nonneg_int_not_bool(count)
        if parsed is None:
            return False
        status_total += parsed

    priority_total = 0
    for key, count in priority_counts.items():
        if key not in KNOWN_DECISION_PRIORITIES:
            return False
        parsed = nonneg_int_not_bool(count)
        if parsed is None:
            return False
        priority_total += parsed

    if subject_count == 0:
        return (
            not status_counts
            and not priority_counts
            and overall == "data_unavailable"
            and highest == "none"
        )

    if status_total != subject_count or priority_total != subject_count:
        return False

    expected_overall = "data_unavailable"
    for status in DECISION_STATUS_ORDER:
        parsed = nonneg_int_not_bool(status_counts.get(status))
        if parsed is not None and parsed > 0:
            expected_overall = status
            break
    if overall != expected_overall:
        return False

    expected_priority = "none"
    for priority in DECISION_PRIORITY_ORDER:
        parsed = nonneg_int_not_bool(priority_counts.get(priority))
        if parsed is not None and parsed > 0:
            expected_priority = priority
            break
    return highest == expected_priority


def validate_decision_badge(badge: Any) -> bool:
    """True when a compact decision badge dict is structurally valid."""
    if not isinstance(badge, dict):
        return False
    status = badge.get("status")
    if not isinstance(status, str) or status not in KNOWN_DECISION_STATUSES:
        return False
    priority = badge.get("priority")
    if not isinstance(priority, str) or priority not in KNOWN_DECISION_PRIORITIES:
        return False
    headline = badge.get("headline_code")
    if not isinstance(headline, str) or not headline.strip():
        return False
    if "coverage_label_codes" not in badge:
        return False
    codes = badge.get("coverage_label_codes")
    if not isinstance(codes, list):
        return False
    for code in codes:
        if not isinstance(code, str) or code not in KNOWN_COVERAGE_LABEL_CODES:
            return False
    return True


def _validate_dashboard_factual_fields(dashboard: dict[str, Any]) -> bool:
    if not _valid_timestamp(dashboard.get("generated_at")):
        return False
    for field in (
        "active_incident_count",
        "watching_incident_count",
        "network_count",
        "device_count",
        "unavailable_device_count",
    ):
        if nonneg_int_not_bool(dashboard.get(field)) is None:
            return False
    return validate_router_risks(dashboard.get("router_risks")) and isinstance(
        dashboard.get("recent_timeline"), list
    )


def _validate_network_decision_badges(networks: Any) -> bool:
    if not isinstance(networks, list):
        return False
    for net in networks:
        if not isinstance(net, dict):
            return False
        for field in ("id", "name", "bridge_state"):
            if not _nonempty_text_field(net, field):
                return False
        if not validate_decision_badge(net.get("decision")):
            return False
        if not validate_decision_count_summary(net.get("decision_summary")):
            return False
        for field in ("device_count", "unavailable_count", "active_incident_count"):
            if nonneg_int_not_bool(net.get(field)) is None:
                return False
    return True


def _nonempty_text_field(item: dict[str, Any], key: str) -> bool:
    value = item.get(key)
    return isinstance(value, str) and bool(value.strip())


def _valid_timestamp(value: Any) -> bool:
    """Return True only for a bounded, timezone-aware ISO timestamp."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 64
        or value != value.strip()
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_router_risks(value: Any) -> bool:
    """Validate the RouterRisk fields consumed as factual entity counts."""
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not all(
            _nonempty_text_field(item, key)
            for key in ("network_id", "ieee_address", "friendly_name")
        ):
            return False
        ieee_address = item["ieee_address"]
        if re.fullmatch(r"0x[0-9a-f]{16}", ieee_address) is None:
            return False
        if item.get("availability") not in KNOWN_AVAILABILITIES:
            return False
        if nonneg_int_not_bool(item.get("correlated_affected_devices")) is None:
            return False
        if not isinstance(item.get("risk"), dict):
            return False
        for optional_count in ("linkquality", "possibly_dependent_devices"):
            raw_count = item.get(optional_count)
            if raw_count is not None and nonneg_int_not_bool(raw_count) is None:
                return False
        last_seen = item.get("last_seen")
        if last_seen is not None and not _valid_timestamp(last_seen):
            return False
    return True


def _validate_investigation_priorities(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    required_text = (
        "id",
        "network_id",
        "card_type",
        "priority",
        "action_group",
        "title",
        "summary",
    )
    for item in value:
        if not isinstance(item, dict):
            return False
        if not all(_nonempty_text_field(item, key) for key in required_text):
            return False
        score = item.get("score")
        if isinstance(score, bool) or type(score) is not int:
            return False
        device_ieees = item.get("device_ieees")
        if not isinstance(device_ieees, list) or not all(
            isinstance(ieee, str) and bool(ieee.strip()) for ieee in device_ieees
        ):
            return False
        latest_evidence = item.get("latest_supporting_evidence_at")
        if latest_evidence is not None and not _valid_timestamp(latest_evidence):
            return False
    return True


def _validate_data_coverage_warnings(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    required_text = (
        "id",
        "network_id",
        "dimension",
        "state",
        "label_code",
        "scope_type",
    )
    for item in value:
        if not isinstance(item, dict):
            return False
        if not all(_nonempty_text_field(item, key) for key in required_text):
            return False
        if not isinstance(item.get("params"), dict):
            return False
    return True


def dashboard_decision_payload_valid(dashboard: dict[str, Any] | None) -> bool:
    """True when Dashboard advertises a valid contract-v2 decision payload."""
    if not isinstance(dashboard, dict):
        return False
    if not _validate_investigation_priorities(
        dashboard.get("investigation_priorities")
    ):
        return False
    if not _validate_data_coverage_warnings(
        dashboard.get("data_coverage_warnings")
    ):
        return False
    if not validate_decision_count_summary(dashboard.get("decision_summary")):
        return False
    if not _validate_dashboard_factual_fields(dashboard):
        return False
    networks = dashboard.get("networks")
    if not _validate_network_decision_badges(networks):
        return False
    network_count = nonneg_int_not_bool(dashboard.get("network_count"))
    return isinstance(networks, list) and network_count == len(networks)


def classify_decision_payload(dashboard: object) -> DecisionPayloadState:
    """Classify Dashboard Decision integrity independently of contract support."""
    if dashboard is None:
        return DecisionPayloadState.MISSING
    if not isinstance(dashboard, dict):
        return DecisionPayloadState.MALFORMED
    if dashboard_decision_payload_valid(dashboard):
        return DecisionPayloadState.VALID
    return DecisionPayloadState.MALFORMED
