"""DataUpdateCoordinator for ZigbeeLens Core."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZigbeeLensApiClient
from .compatibility import (
    CapabilitiesState,
    CoreVersionState,
    DecisionContractState,
    DecisionPayloadState,
    EnrichmentContractState,
    classify_core_version,
    classify_decision_contract,
    classify_decision_payload,
    classify_enrichment_contract,
    decision_contract_version,
)
from .const import DOMAIN
from .exceptions import (
    ZigbeeLensApiError,
    ZigbeeLensAuthError,
    ZigbeeLensInvalidResponseError,
    ZigbeeLensRequestRejectedError,
    ZigbeeLensServerError,
)

_LOGGER = logging.getLogger(__name__)

_AUTH_REQUIRED = "Authentication required"
_AUTH_FAILED_MESSAGE = "Core credentials need to be updated"


@dataclass
class ZigbeeLensCoordinatorData:
    """Coordinator snapshot used by entities and repairs."""

    health: dict[str, Any]
    dashboard: dict[str, Any]
    config_status: dict[str, Any]
    core_version: str | None
    collector_connected: bool | None
    last_update_success: bool
    last_exception: str | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    capabilities_state: CapabilitiesState = CapabilitiesState.UNAVAILABLE
    decision_contract_version: int | None = None
    decision_contract_state: DecisionContractState = DecisionContractState.MISSING
    decision_payload_state: DecisionPayloadState = DecisionPayloadState.MISSING
    enrichment_contract_state: EnrichmentContractState = (
        EnrichmentContractState.UNAVAILABLE
    )
    core_version_state: CoreVersionState = CoreVersionState.UNKNOWN
    shared_decisions_available: bool = False
    core_version_compatible: bool | None = None


class ZigbeeLensDataUpdateCoordinator(DataUpdateCoordinator[ZigbeeLensCoordinatorData]):
    """Fetch summary data from ZigbeeLens Core."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: ZigbeeLensApiClient,
        scan_interval: int,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            config_entry=config_entry,
        )
        self.client = client
        self.last_update_success = False
        self.last_exception: str | None = None
        self.auth_failed = False

    def _raise_auth_failed(self) -> None:
        needs_terminal_notification = not self.last_update_success
        self.last_update_success = False
        self.last_exception = _AUTH_REQUIRED
        self.auth_failed = True
        # DataUpdateCoordinator suppresses a second consecutive failed update.
        # Authentication is a distinct terminal transition, so notify existing
        # listeners now to retire any stale unreachable/compatibility repairs.
        if needs_terminal_notification and getattr(self, "_listeners", None):
            self.async_update_listeners()
        raise ConfigEntryAuthFailed(_AUTH_FAILED_MESSAGE) from None

    async def _async_update_data(self) -> ZigbeeLensCoordinatorData:
        try:
            health = await self.client.async_get_health()
        except ZigbeeLensAuthError:
            self._raise_auth_failed()
        except ZigbeeLensApiError as err:
            self.last_update_success = False
            self.last_exception = str(err)
            self.auth_failed = False
            raise UpdateFailed(str(err)) from None

        try:
            raw_dashboard = await self.client.async_get_dashboard()
        except ZigbeeLensAuthError:
            self._raise_auth_failed()
        except (ZigbeeLensRequestRejectedError, ZigbeeLensServerError) as err:
            self.last_update_success = False
            self.last_exception = str(err)
            self.auth_failed = False
            raise UpdateFailed(str(err)) from None
        except ZigbeeLensInvalidResponseError as err:
            # A reachable 200 response with invalid JSON/content/shape is a
            # Decision payload-integrity failure, not a Core outage.
            raw_dashboard = []
            _LOGGER.debug(
                "Core Dashboard payload is malformed (%s)",
                type(err).__name__,
            )
        except ZigbeeLensApiError as err:
            self.last_update_success = False
            self.last_exception = str(err)
            self.auth_failed = False
            raise UpdateFailed(str(err)) from None

        try:
            config_status = await self.client.async_get_config_status()
        except ZigbeeLensAuthError:
            self._raise_auth_failed()
        except ZigbeeLensApiError as err:
            self.last_update_success = False
            self.last_exception = str(err)
            self.auth_failed = False
            raise UpdateFailed(str(err)) from None

        capabilities: dict[str, Any] = {}
        capabilities_state = CapabilitiesState.UNAVAILABLE
        try:
            capabilities = await self.client.async_get_capabilities()
            capabilities_state = CapabilitiesState.ACCEPTED
        except ZigbeeLensAuthError:
            self._raise_auth_failed()
        except ZigbeeLensRequestRejectedError as err:
            # A missing/forbidden capabilities route is unavailable, not a
            # syntactically accepted-but-malformed capability document.
            _LOGGER.debug("Core capabilities request rejected: %s", err)
        except ZigbeeLensInvalidResponseError as err:
            capabilities_state = CapabilitiesState.MALFORMED
            _LOGGER.debug("Core capabilities unavailable: %s", err)
        except ZigbeeLensApiError as err:
            _LOGGER.debug("Core capabilities fetch failed: %s", err)

        collector = health.get("collector") or {}
        raw_collector_connected = (
            collector.get("connected") if isinstance(collector, dict) else None
        )
        connected = (
            raw_collector_connected
            if isinstance(raw_collector_connected, bool)
            else None
        )
        raw_core_version = health.get("version")
        observed_core_version = (
            raw_core_version if isinstance(raw_core_version, str) else None
        )
        version_state = classify_core_version(observed_core_version)
        # Never project malformed version text into panel/entity display.
        core_version = (
            observed_core_version
            if version_state is not CoreVersionState.UNKNOWN
            else None
        )
        contract_state = classify_decision_contract(
            capabilities,
            capabilities_state,
        )
        payload_state = classify_decision_payload(raw_dashboard)
        # Downstream entities consume a mapping, but the typed state retains
        # whether the reachable endpoint returned missing/malformed JSON.
        dashboard = raw_dashboard if isinstance(raw_dashboard, dict) else {}
        enrichment_state = classify_enrichment_contract(
            capabilities,
            capabilities_state,
        )
        version_compatible = {
            CoreVersionState.COMPATIBLE: True,
            CoreVersionState.INCOMPATIBLE: False,
            CoreVersionState.UNKNOWN: None,
        }[version_state]
        self.last_update_success = True
        self.last_exception = None
        self.auth_failed = False

        return ZigbeeLensCoordinatorData(
            health=health,
            dashboard=dashboard,
            config_status=config_status,
            core_version=core_version,
            collector_connected=connected,
            last_update_success=True,
            last_exception=None,
            capabilities=capabilities,
            capabilities_state=capabilities_state,
            decision_contract_version=decision_contract_version(capabilities),
            decision_contract_state=contract_state,
            decision_payload_state=payload_state,
            enrichment_contract_state=enrichment_state,
            core_version_state=version_state,
            shared_decisions_available=(
                version_state is CoreVersionState.COMPATIBLE
                and contract_state is DecisionContractState.SUPPORTED_EXACT
                and payload_state is DecisionPayloadState.VALID
            ),
            core_version_compatible=version_compatible,
        )
