"""Lifecycle owner for Home Assistant → ZigbeeLens enrichment snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
import logging
from typing import Any, Protocol

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .api import HomeAssistantEnrichmentResult
from .compatibility import EnrichmentContractState
from .exceptions import (
    ZigbeeLensAuthError,
    ZigbeeLensConnectionError,
    ZigbeeLensInvalidResponseError,
    ZigbeeLensRequestRejectedError,
    ZigbeeLensServerError,
)
from .ha_enrichment import (
    EnrichmentBuildResult,
    EnrichmentSnapshotState,
    CoreInventorySnapshot,
    HomeAssistantEnrichmentDevice,
    HomeAssistantRegistrySnapshot,
    RegistrySnapshotState,
    build_home_assistant_registry_snapshot,
    resolve_home_assistant_enrichment,
)

CancelCallback = Callable[[], None]
LaterScheduler = Callable[[float, Callable[[], None]], CancelCallback]
IntervalScheduler = Callable[[timedelta, Callable[[], None]], CancelCallback]
TaskFactory = Callable[
    [Coroutine[Any, Any, None]],
    asyncio.Future[None],
]
RegistryBuilder = Callable[[HomeAssistant], HomeAssistantRegistrySnapshot]

_LOGGER = logging.getLogger(__name__)


class EnrichmentSyncState(str, Enum):
    """Safe diagnostics state for the publisher lifecycle."""

    NEVER_ATTEMPTED = "never_attempted"
    SUCCESSFUL = "successful"
    SOURCE_UNAVAILABLE = "failed_source_unavailable"
    INVENTORY_UNAVAILABLE = "failed_inventory_unavailable"
    CONTRACT_UNSUPPORTED = "contract_unsupported"
    ROUTE_UNSUPPORTED = "failed_contract_unsupported"
    CONTRACT_MISSING = "contract_missing"
    CONTRACT_UNAVAILABLE = "failed_contract_unavailable"
    CONTRACT_MALFORMED = "contract_malformed"
    AUTHENTICATION_FAILED = "failed_authentication"
    CONNECTION_FAILED = "failed_connection"
    SERVER_FAILED = "failed_server"
    REQUEST_REJECTED = "failed_request_rejected"
    INVALID_RESPONSE = "failed_invalid_response"
    PARTIAL_ACCEPTANCE = "partial_acceptance"


class EnrichmentMatchState(str, Enum):
    """Identity-free coverage outcome for the latest completed snapshot."""

    UNKNOWN = "unknown"
    NO_CANDIDATES = "no_candidates"
    COMPLETE = "complete"
    PARTIAL_UNMATCHED = "partial_unmatched"
    PARTIAL_AMBIGUOUS = "partial_ambiguous"
    NO_MATCHES = "no_matches"
    NO_MATCHES_AMBIGUOUS = "no_matches_ambiguous"


ENRICHMENT_FAILURE_REASONS = frozenset(
    {
        "authentication",
        "bad_request",
        "conflict",
        "connection",
        "fingerprint_unavailable",
        "forbidden",
        "inconsistent_result",
        "inventory_connection",
        "inventory_invalid_response",
        "malformed",
        "missing",
        "not_found",
        "partial_acceptance",
        "rate_limited",
        "registry_unavailable",
        "request_rejected",
        "server_error",
        "unavailable",
        "unknown",
        "unsupported",
        "validation",
        "invalid_response",
    }
)


@dataclass(frozen=True, slots=True)
class _CompletedEnrichmentOutcome:
    """Exact aggregate of one builder result and one accepted Core result."""

    submitted: int
    matched: int
    unmatched: int
    ambiguous: int
    stored: int
    match_state: EnrichmentMatchState
    fully_converged: bool


@dataclass(frozen=True, slots=True)
class EnrichmentManagerDiagnostics:
    """Non-identifying manager facts safe to expose through diagnostics."""

    state: EnrichmentSyncState
    match_state: EnrichmentMatchState
    last_attempt_at: str | None
    last_success_at: str | None
    submitted: int | None
    matched: int | None
    unmatched: int | None
    ambiguous: int | None
    stored: int | None
    failure_reason: str | None

    def as_dict(self) -> dict[str, str | int | None]:
        """Return the exact identity-free shape consumed by diagnostics/repairs."""
        return {
            "sync_state": self.state.value,
            "match_state": self.match_state.value,
            "last_attempt_at": self.last_attempt_at,
            "last_success_at": self.last_success_at,
            "submitted": self.submitted,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "ambiguous": self.ambiguous,
            "stored": self.stored,
            "failure_reason": self.failure_reason,
        }


DiagnosticsChangedCallback = Callable[[EnrichmentManagerDiagnostics], None]


def _strict_nonnegative_count(value: object) -> int:
    """Return one exact non-negative count without bool coercion."""
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise ValueError("enrichment result count is invalid")
    return value


def _safe_timestamp(value: object) -> str | None:
    """Keep only a bounded timezone-aware timestamp in callback diagnostics."""
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


def _match_state(
    *,
    submitted: int,
    ambiguous: int,
    stored: int,
) -> EnrichmentMatchState:
    """Classify aggregate coverage without retaining any registry identity."""
    if submitted == 0:
        return EnrichmentMatchState.NO_CANDIDATES
    if stored == submitted:
        return EnrichmentMatchState.COMPLETE
    if stored == 0:
        return (
            EnrichmentMatchState.NO_MATCHES_AMBIGUOUS
            if ambiguous
            else EnrichmentMatchState.NO_MATCHES
        )
    if ambiguous:
        return EnrichmentMatchState.PARTIAL_AMBIGUOUS
    return EnrichmentMatchState.PARTIAL_UNMATCHED


def _completed_outcome(
    build: EnrichmentBuildResult,
    result: HomeAssistantEnrichmentResult,
) -> _CompletedEnrichmentOutcome:
    """Aggregate builder and Core-owned counts, failing closed on inconsistency."""
    posted = len(build.devices)
    server_submitted = _strict_nonnegative_count(result.submitted)
    server_matched = _strict_nonnegative_count(result.matched)
    server_unmatched = _strict_nonnegative_count(result.unmatched)
    server_ambiguous = _strict_nonnegative_count(result.ambiguous)
    server_stored = _strict_nonnegative_count(result.stored)
    if _safe_timestamp(result.last_push_at) is None:
        raise ValueError("Core enrichment timestamp is invalid")
    if (
        server_submitted != posted
        or server_submitted != server_matched + server_unmatched + server_ambiguous
        or server_stored != server_matched
    ):
        raise ValueError("Core enrichment result is inconsistent")

    submitted = _strict_nonnegative_count(build.submitted_candidates)
    builder_unmatched = _strict_nonnegative_count(build.unmatched)
    builder_ambiguous = _strict_nonnegative_count(build.ambiguous)
    matched = server_matched
    unmatched = builder_unmatched + server_unmatched
    ambiguous = builder_ambiguous + server_ambiguous
    stored = server_stored
    if submitted != matched + unmatched + ambiguous or stored != matched:
        raise ValueError("aggregate enrichment result is inconsistent")

    fully_converged = (
        server_submitted == posted
        and server_matched == server_submitted
        and server_unmatched == 0
        and server_ambiguous == 0
        and server_stored == server_matched
    )
    return _CompletedEnrichmentOutcome(
        submitted=submitted,
        matched=matched,
        unmatched=unmatched,
        ambiguous=ambiguous,
        stored=stored,
        match_state=_match_state(
            submitted=submitted,
            ambiguous=ambiguous,
            stored=stored,
        ),
        fully_converged=fully_converged,
    )


class EnrichmentApiClient(Protocol):
    async def async_get_device_inventory(self) -> CoreInventorySnapshot: ...

    async def async_publish_home_assistant_enrichment(
        self,
        devices: tuple[HomeAssistantEnrichmentDevice, ...],
    ) -> HomeAssistantEnrichmentResult: ...


class EnrichmentConfigEntry(Protocol):
    def async_on_unload(
        self,
        func: Callable[[], Coroutine[Any, Any, None] | None],
    ) -> None: ...

    def async_start_reauth(
        self,
        hass: HomeAssistant,
        *,
        data: dict[str, Any] | None = None,
    ) -> None: ...


class HomeAssistantEnrichmentManager:
    """Own registry events, coalescing, retries, reconciliation, and cleanup."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: EnrichmentConfigEntry,
        client: EnrichmentApiClient,
        *,
        capability_provider: Callable[[], EnrichmentContractState | bool | None],
        registry_builder: RegistryBuilder = build_home_assistant_registry_snapshot,
        later_scheduler: LaterScheduler | None = None,
        interval_scheduler: IntervalScheduler | None = None,
        task_factory: TaskFactory | None = None,
        now: Callable[[], str] | None = None,
        on_diagnostics_changed: DiagnosticsChangedCallback | None = None,
        debounce_seconds: float = 2.0,
        retry_delays: tuple[float, ...] = (15.0, 60.0, 300.0),
        reconciliation_interval: timedelta = timedelta(minutes=15),
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._client = client
        self._capability_provider = capability_provider
        self._registry_builder = registry_builder
        self._later_scheduler = later_scheduler or self._default_later_scheduler
        self._interval_scheduler = (
            interval_scheduler or self._default_interval_scheduler
        )
        self._task_factory = task_factory or self._default_task_factory
        self._now = now or self._default_now
        self._on_diagnostics_changed = on_diagnostics_changed
        self._debounce_seconds = debounce_seconds
        self._retry_delays = retry_delays
        self._reconciliation_interval = reconciliation_interval

        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Future[None]] = set()
        self._unsubscribers: list[CancelCallback] = []
        self._debounce_cancel: CancelCallback | None = None
        self._retry_cancel: CancelCallback | None = None
        self._periodic_cancel: CancelCallback | None = None
        self._retry_index = 0
        self._pending = False
        self._pending_force = False
        self._started = False
        self._stopped = False
        self._reauth_started = False
        self._last_fingerprint: str | None = None
        self._last_full_result: HomeAssistantEnrichmentResult | None = None
        self._terminal_failure_fingerprint: str | None = None
        self._diagnostics = EnrichmentManagerDiagnostics(
            state=EnrichmentSyncState.NEVER_ATTEMPTED,
            match_state=EnrichmentMatchState.UNKNOWN,
            last_attempt_at=None,
            last_success_at=None,
            submitted=None,
            matched=None,
            unmatched=None,
            ambiguous=None,
            stored=None,
            failure_reason=None,
        )

    @staticmethod
    def _default_now() -> str:
        return datetime.now(UTC).isoformat()

    def _default_later_scheduler(
        self,
        delay: float,
        action: Callable[[], None],
    ) -> CancelCallback:
        @callback
        def run_action(_now: datetime) -> None:
            action()

        return async_call_later(self._hass, delay, run_action)

    def _default_interval_scheduler(
        self,
        interval: timedelta,
        action: Callable[[], None],
    ) -> CancelCallback:
        @callback
        def run_action(_now: datetime) -> None:
            action()

        return async_track_time_interval(
            self._hass,
            run_action,
            interval,
        )

    def _default_task_factory(
        self,
        coro: Coroutine[Any, Any, None],
    ) -> asyncio.Future[None]:
        return self._hass.async_create_task(coro)

    @property
    def diagnostics(self) -> dict[str, str | int | None]:
        """Return a fresh allowlisted mapping with no registry identities."""
        return self._diagnostics.as_dict()

    async def async_start(self) -> None:
        """Register lifecycle owners once and perform the initial sync."""
        if self._started:
            return
        self._started = True
        self._stopped = False
        self._unsubscribers.extend(
            (
                self._hass.bus.async_listen(
                    dr.EVENT_DEVICE_REGISTRY_UPDATED,
                    self._handle_registry_event,
                ),
                self._hass.bus.async_listen(
                    er.EVENT_ENTITY_REGISTRY_UPDATED,
                    self._handle_registry_event,
                ),
                self._hass.bus.async_listen(
                    ar.EVENT_AREA_REGISTRY_UPDATED,
                    self._handle_registry_event,
                ),
            )
        )
        self._periodic_cancel = self._interval_scheduler(
            self._reconciliation_interval,
            self._handle_periodic_reconciliation,
        )
        self._entry.async_on_unload(self.async_stop)
        await self.async_reconcile()

    async def async_stop(self) -> None:
        """Cancel every listener, timer, retry, and in-flight manager task."""
        if self._stopped:
            return
        self._stopped = True
        for cancel in (
            self._debounce_cancel,
            self._retry_cancel,
            self._periodic_cancel,
            *self._unsubscribers,
        ):
            if cancel is not None:
                cancel()
        self._debounce_cancel = None
        self._retry_cancel = None
        self._periodic_cancel = None
        self._unsubscribers.clear()
        self._on_diagnostics_changed = None

        current = asyncio.current_task()
        pending = [
            task for task in self._tasks if task is not current and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()

    @callback
    def _handle_registry_event(self, _event: Event) -> None:
        # Event payloads differ by HA release/action. Every registry update is
        # simply a signal to rebuild from the complete official registries.
        self.async_request_sync()

    @callback
    def _handle_periodic_reconciliation(self) -> None:
        # Force a bounded periodic re-publish so accepted metadata recovers
        # after a Core restart even when neither registry nor inventory changed.
        self._spawn(self.async_reconcile(force=True))

    @callback
    def async_request_sync(self) -> None:
        """Debounce and coalesce a registry event burst."""
        if self._stopped:
            return
        self._cancel_retry()
        if self._debounce_cancel is not None:
            self._debounce_cancel()
        self._debounce_cancel = self._later_scheduler(
            self._debounce_seconds,
            self._run_debounced,
        )

    @callback
    def _run_debounced(self) -> None:
        self._debounce_cancel = None
        self._spawn(self.async_reconcile())

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        if self._stopped:
            coro.close()
            return
        task = self._task_factory(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def async_wait_for_idle(self) -> None:
        """Test seam: wait for currently spawned work without sleeping."""
        pending = [task for task in self._tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending)

    def _contract_state(self) -> EnrichmentContractState:
        try:
            raw = self._capability_provider()
        except Exception:
            return EnrichmentContractState.UNAVAILABLE
        if isinstance(raw, EnrichmentContractState):
            return raw
        if raw is True:
            return EnrichmentContractState.SUPPORTED
        if raw is False:
            return EnrichmentContractState.UNSUPPORTED
        return EnrichmentContractState.UNAVAILABLE

    def _set_diagnostics(
        self,
        state: EnrichmentSyncState,
        *,
        outcome: _CompletedEnrichmentOutcome | None = None,
        last_success_at: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        previous = self._diagnostics
        safe_failure_reason = (
            (
                failure_reason
                if failure_reason in ENRICHMENT_FAILURE_REASONS
                else "unknown"
            )
            if failure_reason is not None
            else None
        )
        next_diagnostics = EnrichmentManagerDiagnostics(
            state=state,
            match_state=(
                outcome.match_state
                if outcome is not None
                else EnrichmentMatchState.UNKNOWN
            ),
            last_attempt_at=_safe_timestamp(self._now()),
            last_success_at=(
                _safe_timestamp(last_success_at)
                if last_success_at is not None
                else previous.last_success_at
            ),
            submitted=outcome.submitted if outcome is not None else None,
            matched=outcome.matched if outcome is not None else None,
            unmatched=outcome.unmatched if outcome is not None else None,
            ambiguous=outcome.ambiguous if outcome is not None else None,
            stored=outcome.stored if outcome is not None else None,
            failure_reason=safe_failure_reason,
        )
        self._diagnostics = next_diagnostics
        previous_semantics = (
            previous.state,
            previous.match_state,
            previous.submitted,
            previous.matched,
            previous.unmatched,
            previous.ambiguous,
            previous.stored,
            previous.failure_reason,
        )
        next_semantics = (
            next_diagnostics.state,
            next_diagnostics.match_state,
            next_diagnostics.submitted,
            next_diagnostics.matched,
            next_diagnostics.unmatched,
            next_diagnostics.ambiguous,
            next_diagnostics.stored,
            next_diagnostics.failure_reason,
        )
        if (
            self._stopped
            or self._on_diagnostics_changed is None
            or next_semantics == previous_semantics
        ):
            return
        try:
            self._on_diagnostics_changed(next_diagnostics)
        except Exception:
            _LOGGER.error("Home Assistant enrichment diagnostics callback failed")

    async def async_reconcile(self, *, force: bool = False) -> None:
        """Build and publish at most one snapshot concurrently."""
        if self._stopped:
            return
        if self._lock.locked():
            self._pending = True
            self._pending_force = self._pending_force or force
            return

        async with self._lock:
            requested_force = force
            while not self._stopped:
                self._pending = False
                self._pending_force = False
                await self._async_reconcile_once(force=requested_force)
                if not self._pending:
                    break
                requested_force = self._pending_force

    async def _async_reconcile_once(self, *, force: bool) -> None:
        contract_state = self._contract_state()
        if contract_state is not EnrichmentContractState.SUPPORTED:
            # A capability transition is external Core state, so a later return
            # to supported must get one fresh attempt even for identical rows.
            self._last_fingerprint = None
            self._last_full_result = None
            self._terminal_failure_fingerprint = None
            state = {
                EnrichmentContractState.UNSUPPORTED: (
                    EnrichmentSyncState.CONTRACT_UNSUPPORTED
                ),
                EnrichmentContractState.MISSING: EnrichmentSyncState.CONTRACT_MISSING,
                EnrichmentContractState.UNAVAILABLE: (
                    EnrichmentSyncState.CONTRACT_UNAVAILABLE
                ),
                EnrichmentContractState.MALFORMED: (
                    EnrichmentSyncState.CONTRACT_MALFORMED
                ),
            }[contract_state]
            self._set_diagnostics(
                state,
                failure_reason=contract_state.value,
            )
            if contract_state is EnrichmentContractState.UNAVAILABLE:
                self._schedule_retry()
            else:
                self._cancel_retry()
            return

        registry = self._registry_builder(self._hass)
        if registry.state is RegistrySnapshotState.UNAVAILABLE:
            self._set_diagnostics(
                EnrichmentSyncState.SOURCE_UNAVAILABLE,
                failure_reason="registry_unavailable",
            )
            self._schedule_retry()
            return

        try:
            inventory = await self._client.async_get_device_inventory()
        except ZigbeeLensAuthError:
            self._cancel_retry()
            self._start_reauth_once()
            self._set_diagnostics(
                EnrichmentSyncState.AUTHENTICATION_FAILED,
                failure_reason="authentication",
            )
            return
        except ZigbeeLensConnectionError:
            self._set_diagnostics(
                EnrichmentSyncState.INVENTORY_UNAVAILABLE,
                failure_reason="inventory_connection",
            )
            self._schedule_retry()
            return
        except ZigbeeLensServerError as err:
            self._set_diagnostics(
                EnrichmentSyncState.SERVER_FAILED,
                failure_reason=err.category,
            )
            self._schedule_retry()
            return
        except ZigbeeLensRequestRejectedError as err:
            self._set_diagnostics(
                EnrichmentSyncState.REQUEST_REJECTED,
                failure_reason=err.category,
            )
            if err.status_code == 429:
                self._schedule_retry()
            else:
                self._cancel_retry()
            return
        except ZigbeeLensInvalidResponseError:
            self._set_diagnostics(
                EnrichmentSyncState.INVALID_RESPONSE,
                failure_reason="inventory_invalid_response",
            )
            self._schedule_retry()
            return

        build = resolve_home_assistant_enrichment(registry, inventory)
        if build.state is EnrichmentSnapshotState.UNAVAILABLE:
            self._set_diagnostics(
                EnrichmentSyncState.SOURCE_UNAVAILABLE,
                failure_reason="registry_unavailable",
            )
            self._schedule_retry()
            return
        fingerprint = build.fingerprint
        if fingerprint is None:
            self._set_diagnostics(
                EnrichmentSyncState.INVALID_RESPONSE,
                failure_reason="fingerprint_unavailable",
            )
            self._cancel_retry()
            return
        if (
            not force
            and fingerprint == self._last_fingerprint
            and self._last_full_result is not None
        ):
            try:
                outcome = _completed_outcome(build, self._last_full_result)
            except ValueError:
                self._last_fingerprint = None
                self._last_full_result = None
            else:
                self._set_diagnostics(
                    EnrichmentSyncState.SUCCESSFUL,
                    outcome=outcome,
                )
                return
        if not force and fingerprint == self._terminal_failure_fingerprint:
            previous = self._diagnostics
            self._set_diagnostics(
                previous.state,
                failure_reason=previous.failure_reason,
            )
            return

        try:
            result = await self._client.async_publish_home_assistant_enrichment(
                build.devices
            )
        except ZigbeeLensAuthError:
            self._cancel_retry()
            self._start_reauth_once()
            self._set_diagnostics(
                EnrichmentSyncState.AUTHENTICATION_FAILED,
                failure_reason="authentication",
            )
            return
        except ZigbeeLensConnectionError:
            self._set_diagnostics(
                EnrichmentSyncState.CONNECTION_FAILED,
                failure_reason="connection",
            )
            self._schedule_retry()
            return
        except ZigbeeLensServerError as err:
            self._set_diagnostics(
                EnrichmentSyncState.SERVER_FAILED,
                failure_reason=err.category,
            )
            self._schedule_retry()
            return
        except ZigbeeLensRequestRejectedError as err:
            state = (
                EnrichmentSyncState.ROUTE_UNSUPPORTED
                if err.status_code == 404
                else EnrichmentSyncState.REQUEST_REJECTED
            )
            self._set_diagnostics(
                state,
                failure_reason=err.category,
            )
            if err.status_code == 429:
                self._schedule_retry()
            else:
                self._terminal_failure_fingerprint = fingerprint
                self._cancel_retry()
            return
        except ZigbeeLensInvalidResponseError:
            self._last_fingerprint = None
            self._last_full_result = None
            self._set_diagnostics(
                EnrichmentSyncState.INVALID_RESPONSE,
                failure_reason="invalid_response",
            )
            self._terminal_failure_fingerprint = fingerprint
            self._cancel_retry()
            return

        try:
            outcome = _completed_outcome(build, result)
        except ValueError:
            self._last_fingerprint = None
            self._last_full_result = None
            self._set_diagnostics(
                EnrichmentSyncState.INVALID_RESPONSE,
                failure_reason="inconsistent_result",
            )
            self._terminal_failure_fingerprint = fingerprint
            self._cancel_retry()
            return

        self._terminal_failure_fingerprint = None
        if not outcome.fully_converged:
            # Core accepted a replacement but did not retain every posted exact
            # identity. Any prior accepted fingerprint no longer describes Core.
            self._last_fingerprint = None
            self._last_full_result = None
            self._set_diagnostics(
                EnrichmentSyncState.PARTIAL_ACCEPTANCE,
                outcome=outcome,
                last_success_at=result.last_push_at,
                failure_reason="partial_acceptance",
            )
            self._schedule_retry()
            return

        self._last_fingerprint = fingerprint
        self._last_full_result = result
        self._retry_index = 0
        self._cancel_retry()
        self._set_diagnostics(
            EnrichmentSyncState.SUCCESSFUL,
            outcome=outcome,
            last_success_at=result.last_push_at,
        )

    def _cancel_retry(self) -> None:
        if self._retry_cancel is None:
            return
        self._retry_cancel()
        self._retry_cancel = None

    def _schedule_retry(self) -> None:
        if self._stopped or not self._retry_delays or self._retry_cancel is not None:
            return
        index = min(self._retry_index, len(self._retry_delays) - 1)
        delay = self._retry_delays[index]
        self._retry_index = min(self._retry_index + 1, len(self._retry_delays) - 1)
        self._retry_cancel = self._later_scheduler(delay, self._run_retry)

    @callback
    def _run_retry(self) -> None:
        self._retry_cancel = None
        self._spawn(self.async_reconcile(force=True))

    def _start_reauth_once(self) -> None:
        if self._reauth_started:
            return
        self._reauth_started = True
        self._entry.async_start_reauth(self._hass)
