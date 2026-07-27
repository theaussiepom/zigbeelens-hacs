"""Diagnostics for ZigbeeLens integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONF_API_TOKEN, DOMAIN
from .compatibility import (
    CapabilitiesState,
    CoreVersionState,
    DecisionContractState,
    DecisionPayloadState,
    EnrichmentContractState,
)
from .coordinator import ZigbeeLensDataUpdateCoordinator
from .core_origin import InvalidCoreOrigin, canonicalize_core_origin
from .enrichment_manager import (
    ENRICHMENT_FAILURE_REASONS,
    EnrichmentMatchState,
    EnrichmentSyncState,
)


def _redact_url(url: str) -> str:
    """Redact userinfo from MQTT/broker URLs (not used for Core URL projection)."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    if "@" in rest:
        _, _, hostpart = rest.rpartition("@")
        return f"{scheme}://[redacted]@{hostpart}"
    return url


def _diagnostic_core_url(raw: object) -> str:
    """Project Core URL for diagnostics: canonical origin or fixed invalid marker."""
    if not isinstance(raw, str) or not raw:
        return "[invalid]"
    try:
        return canonicalize_core_origin(raw)
    except InvalidCoreOrigin:
        return "[invalid]"


def _safe_category(value: object, allowed: frozenset[str]) -> str:
    """Return a bounded known category without forwarding arbitrary text."""
    return value if isinstance(value, str) and value in allowed else "unknown"


def _safe_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        return None
    return value


def _safe_timestamp(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > 64
    ):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return value


def _collector_diagnostics(value: object) -> dict[str, Any]:
    """Project only reviewed categorical/count/timestamp collector facts."""
    collector = value if isinstance(value, dict) else {}
    enabled = collector.get("enabled")
    connected = collector.get("connected")
    return {
        "enabled": enabled if isinstance(enabled, bool) else None,
        "connected": connected if isinstance(connected, bool) else None,
        "subscribed_topics_count": _safe_nonnegative_int(
            collector.get("subscribed_topics_count")
        ),
        "last_message_at": _safe_timestamp(collector.get("last_message_at")),
        "last_error": "[redacted]" if collector.get("last_error") else None,
    }


def _enrichment_diagnostics(runtime: dict[str, Any]) -> dict[str, Any]:
    """Return an allowlisted, identity-free manager diagnostic summary."""
    default = {
        "sync_state": EnrichmentSyncState.NEVER_ATTEMPTED.value,
        "match_state": EnrichmentMatchState.UNKNOWN.value,
        "last_attempt_at": None,
        "last_success_at": None,
        "submitted": None,
        "matched": None,
        "unmatched": None,
        "ambiguous": None,
        "stored": None,
        "failure_reason": None,
    }
    manager = runtime.get("enrichment_manager")
    raw = getattr(manager, "diagnostics", None)
    if not isinstance(raw, dict):
        return default
    failure_reason = raw.get("failure_reason")
    return {
        "sync_state": _safe_category(
            raw.get("sync_state"),
            frozenset(state.value for state in EnrichmentSyncState),
        ),
        "match_state": _safe_category(
            raw.get("match_state"),
            frozenset(state.value for state in EnrichmentMatchState),
        ),
        "last_attempt_at": _safe_timestamp(raw.get("last_attempt_at")),
        "last_success_at": _safe_timestamp(raw.get("last_success_at")),
        "submitted": _safe_nonnegative_int(raw.get("submitted")),
        "matched": _safe_nonnegative_int(raw.get("matched")),
        "unmatched": _safe_nonnegative_int(raw.get("unmatched")),
        "ambiguous": _safe_nonnegative_int(raw.get("ambiguous")),
        "stored": _safe_nonnegative_int(raw.get("stored")),
        "failure_reason": (
            failure_reason
            if isinstance(failure_reason, str)
            and failure_reason in ENRICHMENT_FAILURE_REASONS
            else "unknown"
            if failure_reason is not None
            else None
        ),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator: ZigbeeLensDataUpdateCoordinator | None = runtime.get("coordinator")
    data = coordinator.data if coordinator and coordinator.data else None

    health = dict(data.health) if data else {}
    config_status = dict(data.config_status) if data else {}
    if "mqtt_server" in config_status:
        config_status["mqtt_server"] = _redact_url(str(config_status["mqtt_server"]))

    collector = _collector_diagnostics(health.get("collector"))

    registry = er.async_get(hass)
    entity_count = len(er.async_entries_for_config_entry(registry, entry.entry_id))

    token = entry.data.get(CONF_API_TOKEN, "")
    api_token_configured = isinstance(token, str) and bool(token)
    last_error_category: str | None = None
    if coordinator:
        if getattr(coordinator, "auth_failed", False):
            last_error_category = "authentication"
        elif not coordinator.last_update_success:
            last_error_category = "unreachable"

    return {
        "integration_version": entry.version,
        "core_url": _diagnostic_core_url(entry.data.get("core_url", "")),
        "api_token_configured": api_token_configured,
        "core_reachable": bool(coordinator and coordinator.last_update_success),
        "core_version": data.core_version if data else None,
        "core_version_state": (
            data.core_version_state.value if data else CoreVersionState.UNKNOWN.value
        ),
        "capabilities_state": (
            data.capabilities_state.value
            if data
            else CapabilitiesState.UNAVAILABLE.value
        ),
        "decision_contract_version": data.decision_contract_version if data else None,
        "decision_contract_state": (
            data.decision_contract_state.value
            if data
            else DecisionContractState.MISSING.value
        ),
        "decision_payload_state": (
            data.decision_payload_state.value
            if data
            else DecisionPayloadState.MISSING.value
        ),
        "enrichment_contract_state": (
            data.enrichment_contract_state.value
            if data
            else EnrichmentContractState.UNAVAILABLE.value
        ),
        "shared_decisions_available": data.shared_decisions_available if data else False,
        "core_version_compatible": data.core_version_compatible if data else None,
        "last_update_success": coordinator.last_update_success if coordinator else False,
        "last_error_category": last_error_category,
        "collector_connected": data.collector_connected if data else None,
        "home_assistant_enrichment": _enrichment_diagnostics(runtime),
        "health": {
            "status": _safe_category(
                health.get("status"),
                frozenset({"ok", "degraded", "error"}),
            ),
            # Use the coordinator's strict observation, never attacker-controlled
            # malformed health text that failed version parsing.
            "version": data.core_version if data else None,
            "mock_mode": (
                health.get("mock_mode")
                if isinstance(health.get("mock_mode"), bool)
                else None
            ),
            "database": _safe_category(
                health.get("database"),
                frozenset({"ok", "ready", "unavailable", "error"}),
            ),
            "migration_version": _safe_nonnegative_int(
                health.get("migration_version")
            ),
            "collector": collector,
        },
        "config_status": {
            "data_mode": _safe_category(
                config_status.get("data_mode"),
                frozenset({"live", "mock"}),
            ),
            "mock_mode": (
                config_status.get("mock_mode")
                if isinstance(config_status.get("mock_mode"), bool)
                else None
            ),
            "mqtt_connected": (
                config_status.get("mqtt_connected")
                if isinstance(config_status.get("mqtt_connected"), bool)
                else None
            ),
            "configured_networks": (
                len(config_status["configured_networks"])
                if isinstance(config_status.get("configured_networks"), list)
                else None
            ),
            "storage_ready": (
                config_status.get("storage_ready")
                if isinstance(config_status.get("storage_ready"), bool)
                else None
            ),
        },
        "entity_count": entity_count,
    }
