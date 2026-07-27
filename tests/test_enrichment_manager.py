"""Lifecycle, complete-snapshot, retry, and cleanup tests for enrichment."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from zigbeelens.api import HomeAssistantEnrichmentResult
from zigbeelens.compatibility import (
    CapabilitiesState,
    CoreVersionState,
    DecisionContractState,
    DecisionPayloadState,
    EnrichmentContractState,
)
from zigbeelens.const import (
    ISSUE_ENRICHMENT_MATCH_INCOMPLETE,
    ISSUE_ENRICHMENT_SYNC_FAILED,
)
from zigbeelens.enrichment_manager import (
    EnrichmentMatchState,
    EnrichmentSyncState,
    HomeAssistantEnrichmentManager,
)
from zigbeelens.exceptions import (
    ZigbeeLensAuthError,
    ZigbeeLensConnectionError,
    ZigbeeLensInvalidResponseError,
    ZigbeeLensRequestRejectedError,
)
from zigbeelens.ha_enrichment import (
    CoreInventoryDevice,
    CoreInventorySnapshot,
    HomeAssistantRegistrySnapshot,
    RegistryCandidate,
    RegistrySnapshotState,
)
from zigbeelens.repairs import async_manage_repairs

IEEE = "0x00124b0001abcdef"
IEEE_2 = "0x00124b0001abcdee"


@dataclass
class ScheduledAction:
    when: object
    action: object
    cancelled: bool = False
    fired: bool = False


class FakeScheduler:
    def __init__(self):
        self.handles: list[ScheduledAction] = []

    def schedule(self, when, action):
        handle = ScheduledAction(when, action)
        self.handles.append(handle)

        def cancel():
            handle.cancelled = True

        return cancel

    def pending(self) -> list[ScheduledAction]:
        return [
            handle
            for handle in self.handles
            if not handle.cancelled and not handle.fired
        ]

    def fire(self, handle: ScheduledAction):
        assert not handle.cancelled
        assert not handle.fired
        handle.fired = True
        handle.action()


class FakeBus:
    def __init__(self):
        self.listeners: dict[str, list[dict[str, object]]] = {}

    def async_listen(self, event_type, listener):
        registration = {"listener": listener, "active": True}
        self.listeners.setdefault(event_type, []).append(registration)

        def cancel():
            registration["active"] = False

        return cancel

    def fire(self, event_type, data=None):
        for registration in list(self.listeners.get(event_type, [])):
            if registration["active"]:
                registration["listener"](SimpleNamespace(data=data))

    @property
    def active_count(self) -> int:
        return sum(
            bool(registration["active"])
            for registrations in self.listeners.values()
            for registration in registrations
        )


class FakeEntry:
    def __init__(self):
        self.unload_callbacks = []
        self.reauth_calls = 0

    def async_on_unload(self, callback):
        self.unload_callbacks.append(callback)

    def async_start_reauth(self, hass, **kwargs):
        self.reauth_calls += 1

    async def async_run_unload(self):
        while self.unload_callbacks:
            result = self.unload_callbacks.pop()()
            if result is not None:
                await result


class MutableRegistrySource:
    def __init__(self, snapshot: HomeAssistantRegistrySnapshot):
        self.snapshot = snapshot
        self.calls = 0

    def __call__(self, hass):
        self.calls += 1
        return self.snapshot


class FakeClient:
    def __init__(
        self,
        inventory: CoreInventorySnapshot,
        *,
        inventory_outcomes=None,
        publish_outcomes=None,
    ):
        self.inventory = inventory
        self.inventory_outcomes = list(inventory_outcomes or [])
        self.publish_outcomes = list(publish_outcomes or [])
        self.inventory_calls = 0
        self.published = []
        self.accepted = "prior-accepted-snapshot"
        self.active_publish = 0
        self.max_active_publish = 0
        self.block_on_call: int | None = None
        self.publish_entered = asyncio.Event()
        self.publish_release = asyncio.Event()

    async def async_get_device_inventory(self):
        self.inventory_calls += 1
        if self.inventory_outcomes:
            outcome = self.inventory_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return self.inventory

    async def async_publish_home_assistant_enrichment(self, devices):
        self.published.append(devices)
        call_number = len(self.published)
        self.active_publish += 1
        self.max_active_publish = max(
            self.max_active_publish,
            self.active_publish,
        )
        try:
            if self.block_on_call == call_number:
                self.publish_entered.set()
                await self.publish_release.wait()
            if self.publish_outcomes:
                outcome = self.publish_outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                result = outcome
            else:
                result = _result(len(devices))
            self.accepted = devices
            return result
        finally:
            self.active_publish -= 1


class AcceptedCommitWithProjectionFailureClient(FakeClient):
    """Model Core returning its accepted result despite a local projection failure."""

    def __init__(self, inventory: CoreInventorySnapshot):
        super().__init__(inventory)
        self.post_commit_projection_failed = False

    async def async_publish_home_assistant_enrichment(self, devices):
        result = await super().async_publish_home_assistant_enrichment(devices)
        self.post_commit_projection_failed = True
        return result


def _result(
    submitted: int,
    *,
    matched: int | None = None,
    unmatched: int = 0,
    ambiguous: int = 0,
    stored: int | None = None,
) -> HomeAssistantEnrichmentResult:
    matched = submitted - unmatched - ambiguous if matched is None else matched
    stored = matched if stored is None else stored
    return HomeAssistantEnrichmentResult(
        home_assistant_enrichment_contract_version=1,
        submitted=submitted,
        matched=matched,
        unmatched=unmatched,
        ambiguous=ambiguous,
        stored=stored,
        last_push_at="2026-07-23T12:00:00+00:00",
    )


def _candidate(
    *,
    ieee_address: str = IEEE,
    ha_device_id: str = "ha-device-id",
    entity_id: str = "light.reading_lamp",
    original_name: str = "source-lamp",
    name: str = "Reading Lamp",
) -> RegistryCandidate:
    return RegistryCandidate(
        ieee_address=ieee_address,
        ha_device_id=ha_device_id,
        ha_device_name=name,
        area_id="living",
        area_name="Living Room",
        entity_id=entity_id,
        original_name=original_name,
    )


def _registry(
    *,
    name: str = "Reading Lamp",
    state: RegistrySnapshotState = RegistrySnapshotState.COMPLETE,
) -> HomeAssistantRegistrySnapshot:
    if state is RegistrySnapshotState.UNAVAILABLE:
        return HomeAssistantRegistrySnapshot(state)
    return HomeAssistantRegistrySnapshot(
        state,
        (_candidate(name=name),),
    )


def _inventory(network_id: str = "home") -> CoreInventorySnapshot:
    return CoreInventorySnapshot(
        (CoreInventoryDevice(network_id, IEEE, "source-lamp"),)
    )


def _manager(
    source: MutableRegistrySource,
    client: FakeClient,
    *,
    contract_state: EnrichmentContractState = EnrichmentContractState.SUPPORTED,
    on_diagnostics_changed=None,
    now=None,
):
    bus = FakeBus()
    hass = SimpleNamespace(bus=bus)
    entry = FakeEntry()
    later = FakeScheduler()
    interval = FakeScheduler()
    manager = HomeAssistantEnrichmentManager(
        hass,
        entry,
        client,
        capability_provider=lambda: contract_state,
        registry_builder=source,
        later_scheduler=later.schedule,
        interval_scheduler=interval.schedule,
        task_factory=asyncio.create_task,
        now=now or (lambda: "2026-07-23T12:00:01+00:00"),
        on_diagnostics_changed=on_diagnostics_changed,
        debounce_seconds=2,
        retry_delays=(5, 10),
    )
    return manager, bus, entry, later, interval


def _healthy_coordinator():
    return SimpleNamespace(
        auth_failed=False,
        last_update_success=True,
        data=SimpleNamespace(
            health={"mock_mode": False},
            dashboard={"networks": [{"id": "home"}], "device_count": 1},
            config_status={
                "configured_networks": [{"id": "home"}],
                "mock_mode": False,
            },
            core_version="0.1.13",
            core_version_state=CoreVersionState.COMPATIBLE,
            capabilities_state=CapabilitiesState.ACCEPTED,
            decision_contract_version=2,
            decision_contract_state=DecisionContractState.SUPPORTED_EXACT,
            decision_payload_state=DecisionPayloadState.VALID,
            enrichment_contract_state=EnrichmentContractState.SUPPORTED,
            collector_connected=True,
        ),
    )


@pytest.mark.asyncio
async def test_initial_sync_registers_exact_listeners_and_posts_complete_snapshot():
    source = MutableRegistrySource(_registry())
    client = FakeClient(_inventory())
    manager, bus, entry, later, interval = _manager(source, client)

    await manager.async_start()

    assert list(bus.listeners) == [
        dr.EVENT_DEVICE_REGISTRY_UPDATED,
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        ar.EVENT_AREA_REGISTRY_UPDATED,
    ]
    assert bus.active_count == 3
    assert len(entry.unload_callbacks) == 1
    assert len(interval.pending()) == 1
    assert later.pending() == []
    assert len(client.published) == 1
    assert client.published[0][0].network_id == "home"
    assert manager.diagnostics == {
        "sync_state": "successful",
        "match_state": "complete",
        "last_attempt_at": "2026-07-23T12:00:01+00:00",
        "last_success_at": "2026-07-23T12:00:00+00:00",
        "submitted": 1,
        "matched": 1,
        "unmatched": 0,
        "ambiguous": 0,
        "stored": 1,
        "failure_reason": None,
    }
    await manager.async_stop()


@pytest.mark.parametrize(
    "registry",
    [
        HomeAssistantRegistrySnapshot(RegistrySnapshotState.COMPLETE),
        _registry(),
    ],
)
@pytest.mark.asyncio
async def test_complete_empty_or_zero_match_may_post_empty(registry):
    source = MutableRegistrySource(registry)
    client = FakeClient(CoreInventorySnapshot(()))
    manager, _bus, _entry, _later, _interval = _manager(source, client)

    await manager.async_start()

    assert client.published == [()]
    diagnostics = manager.diagnostics
    assert diagnostics["sync_state"] == "successful"
    assert diagnostics["stored"] == 0
    if registry.candidates:
        assert diagnostics["submitted"] == 1
        assert diagnostics["unmatched"] == 1
        assert diagnostics["match_state"] == "no_matches"
    else:
        assert diagnostics["submitted"] == 0
        assert diagnostics["unmatched"] == 0
        assert diagnostics["match_state"] == "no_candidates"
    await manager.async_stop()


@pytest.mark.parametrize(
    ("registry", "inventory", "expected_state", "expected_counts"),
    [
        (
            HomeAssistantRegistrySnapshot(RegistrySnapshotState.COMPLETE),
            CoreInventorySnapshot(()),
            EnrichmentMatchState.NO_CANDIDATES,
            (0, 0, 0, 0, 0),
        ),
        (
            _registry(),
            _inventory(),
            EnrichmentMatchState.COMPLETE,
            (1, 1, 0, 0, 1),
        ),
        (
            HomeAssistantRegistrySnapshot(
                RegistrySnapshotState.COMPLETE,
                (
                    _candidate(),
                    _candidate(
                        ieee_address=IEEE_2,
                        ha_device_id="ha-device-id-2",
                        entity_id="light.second_lamp",
                        original_name="second-lamp",
                    ),
                ),
            ),
            _inventory(),
            EnrichmentMatchState.PARTIAL_UNMATCHED,
            (2, 1, 1, 0, 1),
        ),
        (
            HomeAssistantRegistrySnapshot(
                RegistrySnapshotState.COMPLETE,
                (_candidate(),),
                ambiguous_candidates=1,
            ),
            _inventory(),
            EnrichmentMatchState.PARTIAL_AMBIGUOUS,
            (2, 1, 0, 1, 1),
        ),
        (
            _registry(),
            CoreInventorySnapshot(()),
            EnrichmentMatchState.NO_MATCHES,
            (1, 0, 1, 0, 0),
        ),
        (
            HomeAssistantRegistrySnapshot(
                RegistrySnapshotState.COMPLETE,
                ambiguous_candidates=1,
            ),
            CoreInventorySnapshot(()),
            EnrichmentMatchState.NO_MATCHES_AMBIGUOUS,
            (1, 0, 0, 1, 0),
        ),
    ],
)
@pytest.mark.asyncio
async def test_completed_match_states_keep_exact_aggregate_counts(
    registry,
    inventory,
    expected_state,
    expected_counts,
):
    source = MutableRegistrySource(registry)
    client = FakeClient(inventory)
    manager, _bus, _entry, _later, _interval = _manager(source, client)

    await manager.async_start()

    diagnostics = manager.diagnostics
    observed_counts = tuple(
        diagnostics[key]
        for key in ("submitted", "matched", "unmatched", "ambiguous", "stored")
    )
    assert diagnostics["match_state"] == expected_state.value
    assert observed_counts == expected_counts
    assert diagnostics["submitted"] == (
        diagnostics["matched"] + diagnostics["unmatched"] + diagnostics["ambiguous"]
    )
    assert diagnostics["stored"] == diagnostics["matched"]
    await manager.async_stop()


@pytest.mark.asyncio
async def test_partial_core_acceptance_retries_with_truthful_counts_and_converges():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        publish_outcomes=[
            _result(1, matched=0, unmatched=1, stored=0),
        ],
    )
    transitions = []
    manager, _bus, _entry, later, _interval = _manager(
        source,
        client,
        on_diagnostics_changed=transitions.append,
    )

    await manager.async_start()

    assert manager.diagnostics == {
        "sync_state": "partial_acceptance",
        "match_state": "no_matches",
        "last_attempt_at": "2026-07-23T12:00:01+00:00",
        "last_success_at": "2026-07-23T12:00:00+00:00",
        "submitted": 1,
        "matched": 0,
        "unmatched": 1,
        "ambiguous": 0,
        "stored": 0,
        "failure_reason": "partial_acceptance",
    }
    assert manager._last_fingerprint is None
    assert [handle.when for handle in later.pending()] == [5]

    later.fire(later.pending()[0])
    await manager.async_wait_for_idle()

    assert client.inventory_calls == 2
    assert len(client.published) == 2
    assert client.max_active_publish == 1
    assert manager.diagnostics["sync_state"] == "successful"
    assert manager.diagnostics["match_state"] == "complete"
    assert manager.diagnostics["matched"] == 1
    assert manager.diagnostics["stored"] == 1
    assert later.pending() == []
    assert [item.state for item in transitions] == [
        EnrichmentSyncState.PARTIAL_ACCEPTANCE,
        EnrichmentSyncState.SUCCESSFUL,
    ]
    await manager.async_stop()


@pytest.mark.asyncio
async def test_inconsistent_completed_counts_fail_closed_without_accepting_fingerprint():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        publish_outcomes=[_result(2)],
    )
    manager, _bus, _entry, later, _interval = _manager(source, client)

    await manager.async_start()

    assert manager.diagnostics["sync_state"] == "failed_invalid_response"
    assert manager.diagnostics["match_state"] == "unknown"
    assert manager.diagnostics["failure_reason"] == "inconsistent_result"
    assert manager._last_fingerprint is None
    assert later.pending() == []
    await manager.async_stop()


@pytest.mark.asyncio
async def test_unavailable_registry_never_fetches_inventory_or_posts_empty():
    source = MutableRegistrySource(_registry(state=RegistrySnapshotState.UNAVAILABLE))
    client = FakeClient(_inventory())
    manager, _bus, _entry, later, _interval = _manager(source, client)

    await manager.async_start()

    assert client.inventory_calls == 0
    assert client.published == []
    assert [handle.when for handle in later.pending()] == [5]
    assert manager.diagnostics["sync_state"] == "failed_source_unavailable"
    assert manager.diagnostics["submitted"] is None
    await manager.async_stop()


@pytest.mark.parametrize(
    ("failure", "expected_state", "retry"),
    [
        (
            ZigbeeLensConnectionError("inventory offline"),
            EnrichmentSyncState.INVENTORY_UNAVAILABLE.value,
            True,
        ),
        (
            ZigbeeLensInvalidResponseError("partial inventory"),
            EnrichmentSyncState.INVALID_RESPONSE.value,
            True,
        ),
    ],
)
@pytest.mark.asyncio
async def test_incomplete_inventory_never_posts_replacement(
    failure,
    expected_state,
    retry,
):
    source = MutableRegistrySource(_registry())
    client = FakeClient(_inventory(), inventory_outcomes=[failure])
    manager, _bus, _entry, later, _interval = _manager(source, client)

    await manager.async_start()

    assert client.published == []
    assert client.accepted == "prior-accepted-snapshot"
    assert manager.diagnostics["sync_state"] == expected_state
    assert bool(later.pending()) is retry
    await manager.async_stop()


@pytest.mark.parametrize(
    ("contract", "expected_state", "retry"),
    [
        (
            EnrichmentContractState.UNSUPPORTED,
            "contract_unsupported",
            False,
        ),
        (EnrichmentContractState.MISSING, "contract_missing", False),
        (EnrichmentContractState.MALFORMED, "contract_malformed", False),
        (
            EnrichmentContractState.UNAVAILABLE,
            "failed_contract_unavailable",
            True,
        ),
    ],
)
@pytest.mark.asyncio
async def test_capability_states_are_distinct_and_fail_closed(
    contract,
    expected_state,
    retry,
):
    source = MutableRegistrySource(_registry())
    client = FakeClient(_inventory())
    manager, _bus, _entry, later, _interval = _manager(
        source,
        client,
        contract_state=contract,
    )

    await manager.async_start()

    assert source.calls == 0
    assert client.inventory_calls == 0
    assert client.published == []
    assert manager.diagnostics["sync_state"] == expected_state
    assert bool(later.pending()) is retry
    await manager.async_stop()


@pytest.mark.asyncio
async def test_registry_burst_is_debounced_and_unchanged_payload_is_not_reposted():
    source = MutableRegistrySource(_registry(name="First Name"))
    client = FakeClient(_inventory())
    manager, bus, _entry, later, _interval = _manager(source, client)
    await manager.async_start()

    source.snapshot = _registry(name="Changed Name")
    bus.fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"action": "update"})
    bus.fire(er.EVENT_ENTITY_REGISTRY_UPDATED, {"action": "create"})
    bus.fire(ar.EVENT_AREA_REGISTRY_UPDATED, {"action": "remove"})

    assert len(later.handles) == 3
    assert len(later.pending()) == 1
    later.fire(later.pending()[0])
    await manager.async_wait_for_idle()
    assert len(client.published) == 2
    assert client.published[-1][0].ha_device_name == "Changed Name"

    manager.async_request_sync()
    later.fire(later.pending()[0])
    await manager.async_wait_for_idle()
    assert len(client.published) == 2
    assert manager.diagnostics["sync_state"] == "successful"
    await manager.async_stop()


@pytest.mark.asyncio
async def test_core_inventory_change_changes_exact_network_and_republishes():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory("network-a"),
        inventory_outcomes=[
            _inventory("network-a"),
            _inventory("network-b"),
        ],
    )
    manager, _bus, _entry, later, _interval = _manager(source, client)
    await manager.async_start()

    manager.async_request_sync()
    later.fire(later.pending()[0])
    await manager.async_wait_for_idle()

    assert [published[0].network_id for published in client.published] == [
        "network-a",
        "network-b",
    ]
    await manager.async_stop()


@pytest.mark.asyncio
async def test_no_match_recovers_after_inventory_refresh_without_stale_deduplication():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        inventory_outcomes=[
            CoreInventorySnapshot(()),
            _inventory(),
        ],
    )
    manager, _bus, _entry, later, _interval = _manager(source, client)
    await manager.async_start()

    assert client.published == [()]
    assert manager.diagnostics["match_state"] == "no_matches"

    manager.async_request_sync()
    later.fire(later.pending()[0])
    await manager.async_wait_for_idle()

    assert len(client.published) == 2
    assert client.published[-1][0].ieee_address == IEEE
    assert manager.diagnostics["match_state"] == "complete"
    await manager.async_stop()


@pytest.mark.asyncio
async def test_periodic_reconciliation_forces_republish_for_core_restart_recovery():
    source = MutableRegistrySource(_registry())
    client = FakeClient(_inventory())
    manager, _bus, _entry, _later, interval = _manager(source, client)
    await manager.async_start()

    interval.fire(interval.pending()[0])
    await manager.async_wait_for_idle()

    assert len(client.published) == 2
    await manager.async_stop()


@pytest.mark.asyncio
async def test_transient_publish_failure_retries_without_clearing_or_duplication():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        publish_outcomes=[ZigbeeLensConnectionError("offline")],
    )
    manager, bus, _entry, later, _interval = _manager(source, client)

    await manager.async_start()
    assert client.accepted == "prior-accepted-snapshot"
    assert manager.diagnostics["sync_state"] == "failed_connection"
    assert [handle.when for handle in later.pending()] == [5]

    later.fire(later.pending()[0])
    await manager.async_wait_for_idle()

    assert len(client.published) == 2
    assert client.accepted == client.published[-1]
    assert client.max_active_publish == 1
    assert bus.active_count == 3
    assert manager.diagnostics["sync_state"] == "successful"
    assert later.pending() == []
    await manager.async_stop()


@pytest.mark.asyncio
async def test_accepted_commit_with_projection_failure_is_not_failed_server():
    source = MutableRegistrySource(_registry())
    client = AcceptedCommitWithProjectionFailureClient(_inventory())
    manager, _bus, _entry, later, _interval = _manager(source, client)

    await manager.async_start()

    assert client.post_commit_projection_failed is True
    assert len(client.published) == 1
    assert manager.diagnostics["sync_state"] == "successful"
    assert manager.diagnostics["match_state"] == "complete"
    assert manager._last_fingerprint is not None
    assert later.pending() == []
    await manager.async_stop()


@pytest.mark.asyncio
async def test_registry_failure_and_retry_repair_without_coordinator_refresh():
    source = MutableRegistrySource(_registry(name="Accepted"))
    client = FakeClient(_inventory())
    hass = MagicMock()
    coordinator = _healthy_coordinator()
    manager_ref = {}

    def on_diagnostics_changed(_diagnostics):
        async_manage_repairs(hass, coordinator, manager_ref["manager"])

    manager, bus, _entry, later, _interval = _manager(
        source,
        client,
        on_diagnostics_changed=on_diagnostics_changed,
    )
    manager_ref["manager"] = manager

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ) as delete_issue:
        await manager.async_start()
        create_issue.reset_mock()
        delete_issue.reset_mock()

        source.snapshot = _registry(name="Changed")
        client.publish_outcomes.append(ZigbeeLensConnectionError("offline"))
        bus.fire(dr.EVENT_DEVICE_REGISTRY_UPDATED)
        later.fire(later.pending()[0])
        await manager.async_wait_for_idle()

        assert any(
            call.args[2] == ISSUE_ENRICHMENT_SYNC_FAILED
            for call in create_issue.call_args_list
        )

        delete_issue.reset_mock()
        later.fire(later.pending()[0])
        await manager.async_wait_for_idle()

    assert any(
        call.args[2] == ISSUE_ENRICHMENT_SYNC_FAILED
        for call in delete_issue.call_args_list
    )
    assert manager.diagnostics["sync_state"] == "successful"
    await manager.async_stop()


@pytest.mark.asyncio
async def test_partial_acceptance_repair_clears_on_retry_without_coordinator_refresh():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        publish_outcomes=[_result(1, matched=0, unmatched=1, stored=0)],
    )
    hass = MagicMock()
    coordinator = _healthy_coordinator()
    manager_ref = {}

    def on_diagnostics_changed(_diagnostics):
        async_manage_repairs(hass, coordinator, manager_ref["manager"])

    manager, _bus, _entry, later, _interval = _manager(
        source,
        client,
        on_diagnostics_changed=on_diagnostics_changed,
    )
    manager_ref["manager"] = manager

    with patch("zigbeelens.repairs.ir.async_create_issue") as create_issue, patch(
        "zigbeelens.repairs.ir.async_delete_issue"
    ) as delete_issue:
        await manager.async_start()
        assert any(
            call.args[2] == ISSUE_ENRICHMENT_MATCH_INCOMPLETE
            for call in create_issue.call_args_list
        )

        delete_issue.reset_mock()
        later.fire(later.pending()[0])
        await manager.async_wait_for_idle()

    assert any(
        call.args[2] == ISSUE_ENRICHMENT_MATCH_INCOMPLETE
        for call in delete_issue.call_args_list
    )
    assert manager.diagnostics["match_state"] == "complete"
    await manager.async_stop()


@pytest.mark.asyncio
async def test_rate_limit_is_retried_but_other_payload_rejections_are_not():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        publish_outcomes=[ZigbeeLensRequestRejectedError(429, "rate_limited")],
    )
    manager, _bus, _entry, later, _interval = _manager(source, client)

    await manager.async_start()
    assert manager.diagnostics["failure_reason"] == "rate_limited"
    assert [handle.when for handle in later.pending()] == [5]

    later.fire(later.pending()[0])
    await manager.async_wait_for_idle()

    assert len(client.published) == 2
    assert manager.diagnostics["sync_state"] == "successful"
    await manager.async_stop()


@pytest.mark.asyncio
async def test_retry_backoff_is_capped_and_success_clears_pending_retry():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        publish_outcomes=[
            ZigbeeLensConnectionError("first"),
            ZigbeeLensConnectionError("second"),
            ZigbeeLensConnectionError("third"),
        ],
    )
    manager, _bus, _entry, later, _interval = _manager(source, client)

    await manager.async_start()
    observed_delays = []
    for _ in range(3):
        retry = later.pending()[0]
        observed_delays.append(retry.when)
        later.fire(retry)
        await manager.async_wait_for_idle()

    assert observed_delays == [5, 10, 10]
    assert manager.diagnostics["sync_state"] == "successful"
    assert later.pending() == []
    await manager.async_stop()


@pytest.mark.asyncio
async def test_authentication_failure_starts_linked_reauth_once():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        inventory_outcomes=[
            ZigbeeLensAuthError("secret one"),
            ZigbeeLensAuthError("secret two"),
        ],
    )
    manager, _bus, entry, later, _interval = _manager(source, client)

    await manager.async_start()
    await manager.async_reconcile(force=True)

    assert entry.reauth_calls == 1
    assert client.published == []
    assert later.pending() == []
    assert manager.diagnostics["sync_state"] == "failed_authentication"
    await manager.async_stop()


@pytest.mark.asyncio
async def test_authentication_callback_runs_after_linked_reauth_and_is_identity_free():
    source = MutableRegistrySource(_registry(name="private-device-name"))
    client = FakeClient(
        _inventory(),
        inventory_outcomes=[ZigbeeLensAuthError("private-auth-detail")],
    )
    entry_ref = {}
    transitions = []

    def on_diagnostics_changed(diagnostics):
        transitions.append((diagnostics, entry_ref["entry"].reauth_calls))

    manager, _bus, entry, _later, _interval = _manager(
        source,
        client,
        on_diagnostics_changed=on_diagnostics_changed,
    )
    entry_ref["entry"] = entry

    await manager.async_start()

    assert entry.reauth_calls == 1
    assert len(transitions) == 1
    diagnostics, reauth_calls_at_callback = transitions[0]
    assert diagnostics.state is EnrichmentSyncState.AUTHENTICATION_FAILED
    assert diagnostics.match_state is EnrichmentMatchState.UNKNOWN
    assert reauth_calls_at_callback == 1
    encoded = repr(diagnostics)
    assert "private-device-name" not in encoded
    assert "private-auth-detail" not in encoded
    await manager.async_stop()


@pytest.mark.asyncio
async def test_diagnostics_callback_ignores_timestamp_only_changes_and_stops_on_unload():
    source = MutableRegistrySource(_registry())
    client = FakeClient(_inventory())
    timestamps = iter(
        (
            "2026-07-23T12:00:01+00:00",
            "2026-07-23T12:00:02+00:00",
            "2026-07-23T12:00:03+00:00",
        )
    )
    transitions = []
    manager, _bus, entry, later, _interval = _manager(
        source,
        client,
        on_diagnostics_changed=transitions.append,
        now=lambda: next(timestamps),
    )
    await manager.async_start()
    assert len(transitions) == 1

    manager.async_request_sync()
    later.fire(later.pending()[0])
    await manager.async_wait_for_idle()

    assert len(client.published) == 1
    assert manager.diagnostics["last_attempt_at"] == "2026-07-23T12:00:02+00:00"
    assert len(transitions) == 1

    await entry.async_run_unload()
    await manager.async_reconcile(force=True)
    assert len(transitions) == 1


@pytest.mark.asyncio
async def test_payload_rejection_is_terminal_and_retains_prior_acceptance():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        publish_outcomes=[ZigbeeLensRequestRejectedError(422, "validation")],
    )
    manager, _bus, _entry, later, interval = _manager(source, client)

    await manager.async_start()

    assert client.accepted == "prior-accepted-snapshot"
    assert later.pending() == []
    assert manager.diagnostics["sync_state"] == "failed_request_rejected"
    assert manager.diagnostics["failure_reason"] == "validation"

    # Ordinary coordinator/registry notifications may re-read inventory, but a
    # terminal rejection of the identical payload is not POSTed every poll.
    manager.async_request_sync()
    later.fire(later.pending()[0])
    await manager.async_wait_for_idle()
    assert len(client.published) == 1

    # Bounded periodic reconciliation is forced so a repaired Core route can
    # recover even when the payload itself did not change.
    interval.fire(interval.pending()[0])
    await manager.async_wait_for_idle()
    assert len(client.published) == 2
    assert manager.diagnostics["sync_state"] == "successful"
    await manager.async_stop()


@pytest.mark.asyncio
async def test_missing_exact_post_route_becomes_contract_unsupported():
    source = MutableRegistrySource(_registry())
    client = FakeClient(
        _inventory(),
        publish_outcomes=[ZigbeeLensRequestRejectedError(404, "not_found")],
    )
    manager, _bus, _entry, later, _interval = _manager(source, client)

    await manager.async_start()

    assert client.accepted == "prior-accepted-snapshot"
    assert later.pending() == []
    assert manager.diagnostics["sync_state"] == "failed_contract_unsupported"
    assert manager.diagnostics["failure_reason"] == "not_found"
    await manager.async_stop()


@pytest.mark.asyncio
async def test_normal_reload_never_clears_and_failed_new_sync_preserves_acceptance():
    source = MutableRegistrySource(_registry(name="Accepted Name"))
    client = FakeClient(_inventory())
    first, first_bus, _entry, _later, _interval = _manager(source, client)
    await first.async_start()
    accepted_before_reload = client.accepted
    await first.async_stop()
    assert first_bus.active_count == 0

    source.snapshot = _registry(name="Changed During Reload")
    client.publish_outcomes.append(ZigbeeLensConnectionError("transient"))
    second, second_bus, _entry, _later, _interval = _manager(source, client)
    await second.async_start()

    assert second_bus.active_count == 3
    assert client.accepted == accepted_before_reload
    assert second.diagnostics["sync_state"] == "failed_connection"
    await second.async_stop()


@pytest.mark.asyncio
async def test_concurrent_requests_coalesce_and_never_overlap_posts():
    source = MutableRegistrySource(_registry())
    client = FakeClient(_inventory())
    client.block_on_call = 1
    manager, _bus, _entry, _later, _interval = _manager(source, client)

    first = asyncio.create_task(manager.async_reconcile(force=True))
    await client.publish_entered.wait()
    await manager.async_reconcile(force=True)
    client.publish_release.set()
    await first

    assert client.inventory_calls == 2
    assert len(client.published) == 2
    assert client.max_active_publish == 1
    await manager.async_stop()


@pytest.mark.asyncio
async def test_config_entry_unload_cancels_all_owned_resources_and_inflight_work():
    source = MutableRegistrySource(_registry(name="Initial"))
    client = FakeClient(_inventory())
    manager, bus, entry, later, interval = _manager(source, client)
    await manager.async_start()

    source.snapshot = _registry(name="Changed")
    client.block_on_call = 2
    manager.async_request_sync()
    debounce = later.pending()[0]
    later.fire(debounce)
    await client.publish_entered.wait()

    await entry.async_run_unload()

    assert bus.active_count == 0
    assert all(handle.cancelled or handle.fired for handle in later.handles)
    assert all(handle.cancelled or handle.fired for handle in interval.handles)
    assert client.active_publish == 0
    assert client.accepted == client.published[0]

    scheduled_before = len(later.handles)
    bus.fire(dr.EVENT_DEVICE_REGISTRY_UPDATED, {"different": "shape"})
    manager.async_request_sync()
    assert len(later.handles) == scheduled_before


@pytest.mark.asyncio
async def test_manager_diagnostics_are_identity_free_and_categorical():
    sensitive_name = "name-secret-sentinel"
    source = MutableRegistrySource(_registry(name=sensitive_name))
    client = FakeClient(
        _inventory(),
        publish_outcomes=[ZigbeeLensRequestRejectedError(409, "conflict")],
    )
    manager, _bus, _entry, _later, _interval = _manager(source, client)
    await manager.async_start()

    encoded = repr(manager.diagnostics)
    for secret in (
        sensitive_name,
        "Living Room",
        "living",
        "light.reading_lamp",
        "ha-device-id",
        IEEE,
        "source-lamp",
    ):
        assert secret not in encoded
    assert manager.diagnostics["failure_reason"] == "conflict"
    await manager.async_stop()
