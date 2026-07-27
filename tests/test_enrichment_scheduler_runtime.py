"""Real Home Assistant ownership tests for enrichment scheduler adapters."""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import inspect
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
import weakref

import pytest

from homeassistant.core import HomeAssistant, is_callback
from homeassistant.helpers import device_registry as dr

from zigbeelens.api import HomeAssistantEnrichmentResult
from zigbeelens.compatibility import EnrichmentContractState
from zigbeelens.enrichment_manager import HomeAssistantEnrichmentManager
from zigbeelens.exceptions import ZigbeeLensConnectionError
from zigbeelens.ha_enrichment import (
    CoreInventoryDevice,
    CoreInventorySnapshot,
    HomeAssistantRegistrySnapshot,
    RegistryCandidate,
    RegistrySnapshotState,
)

IEEE = "0x00124b0001abcdef"
THREAD_SAFETY_MARKERS = (
    "from a thread other than the event loop",
    "non-thread-safe operation invoked on an event loop",
)


class _Entry:
    def __init__(self) -> None:
        self.unload_callbacks: list[Callable[[], Any]] = []

    def async_on_unload(self, callback: Callable[[], Any]) -> None:
        self.unload_callbacks.append(callback)

    def async_start_reauth(
        self,
        _hass: HomeAssistant,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        del data

    async def async_run_unload(self) -> None:
        while self.unload_callbacks:
            result = self.unload_callbacks.pop()()
            if result is not None:
                await result


class _RegistrySource:
    def __init__(self, snapshot: HomeAssistantRegistrySnapshot) -> None:
        self.snapshot = snapshot

    def __call__(self, _hass: HomeAssistant) -> HomeAssistantRegistrySnapshot:
        return self.snapshot


class _ObservedClient:
    def __init__(
        self,
        *,
        publish_outcomes: list[HomeAssistantEnrichmentResult | Exception] | None = None,
    ) -> None:
        self.inventory = CoreInventorySnapshot(
            (CoreInventoryDevice("home", IEEE, "scheduler-lamp"),)
        )
        self.publish_outcomes = list(publish_outcomes or [])
        self.published: list[tuple[Any, ...]] = []
        self.publish_loops: list[asyncio.AbstractEventLoop] = []
        self.max_active_publish = 0
        self._active_publish = 0
        self._changed = asyncio.Condition()
        self.block_on_call: int | None = None
        self.publish_entered = asyncio.Event()
        self.publish_release = asyncio.Event()

    async def async_get_device_inventory(self) -> CoreInventorySnapshot:
        return self.inventory

    async def async_publish_home_assistant_enrichment(
        self,
        devices: tuple[Any, ...],
    ) -> HomeAssistantEnrichmentResult:
        self.publish_loops.append(asyncio.get_running_loop())
        self.published.append(devices)
        call_number = len(self.published)
        self._active_publish += 1
        self.max_active_publish = max(self.max_active_publish, self._active_publish)
        async with self._changed:
            self._changed.notify_all()
        try:
            if self.block_on_call == call_number:
                self.publish_entered.set()
                await self.publish_release.wait()
            if self.publish_outcomes:
                outcome = self.publish_outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            count = len(devices)
            return HomeAssistantEnrichmentResult(
                home_assistant_enrichment_contract_version=1,
                submitted=count,
                matched=count,
                unmatched=0,
                ambiguous=0,
                stored=count,
                last_push_at="2026-07-26T00:00:00+00:00",
            )
        finally:
            self._active_publish -= 1

    async def async_wait_for_publish_count(self, expected: int) -> None:
        async def wait_for_count() -> None:
            async with self._changed:
                await self._changed.wait_for(
                    lambda: len(self.published) >= expected
                )

        await asyncio.wait_for(wait_for_count(), timeout=2)


def _registry_snapshot(
    *,
    name: str = "Scheduler Lamp",
) -> HomeAssistantRegistrySnapshot:
    return HomeAssistantRegistrySnapshot(
        RegistrySnapshotState.COMPLETE,
        (
            RegistryCandidate(
                ieee_address=IEEE,
                ha_device_id="scheduler-device",
                ha_device_name=name,
                area_id="scheduler-area",
                area_name="Scheduler Area",
                entity_id="light.scheduler_lamp",
                original_name="scheduler-lamp",
            ),
        ),
    )


def _plain_hass(tmp_path: Path) -> HomeAssistant:
    return HomeAssistant(str(tmp_path))


def _capture_loop_errors(
    loop: asyncio.AbstractEventLoop,
) -> tuple[list[dict[str, Any]], Callable[[], None]]:
    contexts: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()

    def capture(
        _loop: asyncio.AbstractEventLoop,
        context: dict[str, Any],
    ) -> None:
        contexts.append(context)

    loop.set_exception_handler(capture)

    def restore() -> None:
        loop.set_exception_handler(previous)

    return contexts, restore


async def _timer_barrier(
    loop: asyncio.AbstractEventLoop,
    delay: float,
) -> None:
    complete: asyncio.Future[None] = loop.create_future()
    loop.call_later(delay, complete.set_result, None)
    await asyncio.wait_for(complete, timeout=2)


def _assert_no_thread_safety_errors(
    caplog: pytest.LogCaptureFixture,
    loop_errors: list[dict[str, Any]],
) -> None:
    formatter = logging.Formatter()
    messages = [formatter.format(record).lower() for record in caplog.records]
    messages.extend(
        f"{context.get('message', '')} {context.get('exception', '')}".lower()
        for context in loop_errors
    )
    assert not [
        message
        for message in messages
        if any(marker in message for marker in THREAD_SAFETY_MARKERS)
    ]
    assert loop_errors == []


def _assert_imported_manager_uses_selected_stage() -> None:
    components = Path(os.environ["ZIGBEELENS_HA_TEST_COMPONENTS"]).resolve()
    imported_component = Path(
        inspect.getfile(HomeAssistantEnrichmentManager)
    ).resolve().parent
    assert imported_component == components / "zigbeelens"


def test_thread_safety_guard_rejects_marker_held_only_in_exception_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR)
    try:
        raise RuntimeError(THREAD_SAFETY_MARKERS[0])
    except RuntimeError:
        logging.getLogger("zigbeelens.scheduler-test").exception(
            "generic scheduler failure"
        )

    with pytest.raises(AssertionError):
        _assert_no_thread_safety_errors(caplog, [])


def test_default_debounce_registry_event_runs_on_hass_loop_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    _assert_imported_manager_uses_selected_stage()
    components = Path(os.environ["ZIGBEELENS_HA_TEST_COMPONENTS"]).resolve()
    staged_root = components.parent
    source_commit = os.environ["ZIGBEELENS_HA_TEST_SOURCE_COMMIT"]
    assert (staged_root / "SOURCE_COMMIT").read_text(encoding="utf-8").strip() == (
        source_commit
    )
    manifest = json.loads(
        (components / "zigbeelens" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["version"] == "0.1.14"
    probe = Path(__file__).with_name("enrichment_scheduler_probe.py")
    environment = {
        **os.environ,
        "PYTHONASYNCIODEBUG": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        (
            sys.executable,
            str(probe),
            "--components",
            str(components),
            "--config-dir",
            str(tmp_path / "home-assistant"),
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
    )
    assert result.returncode == 0, (
        f"real registry scheduler probe failed ({result.returncode}): "
        f"{result.stderr[-2000:]}"
    )
    marker = "ZIGBEELENS_SCHEDULER_PROBE="
    payload_lines = [
        line.removeprefix(marker)
        for line in result.stdout.splitlines()
        if line.startswith(marker)
    ]
    assert len(payload_lines) == 1
    payload = json.loads(payload_lines[0])
    assert payload == {
        "coalesced_publish_count": 2,
        "component_path_verified": True,
        "debounce_on_hass_loop": True,
        "diagnostics_state": "successful",
        "manager_tasks_after_stop": 0,
        "post_stop_publish_count": 2,
        "publish_on_hass_loop": True,
        "thread_safety_errors": 0,
    }


@pytest.mark.asyncio
async def test_default_retry_runs_on_hass_loop_and_stop_cancels_pending_retry(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _assert_imported_manager_uses_selected_stage()
    caplog.set_level(logging.WARNING)
    hass = _plain_hass(tmp_path)
    loop_errors, restore_loop_handler = _capture_loop_errors(hass.loop)
    entry = _Entry()
    source = _RegistrySource(_registry_snapshot())
    client = _ObservedClient(
        publish_outcomes=[ZigbeeLensConnectionError("transient")]
    )
    failure_count = 0
    second_failure = asyncio.Event()

    def diagnostics_changed(diagnostics: object) -> None:
        nonlocal failure_count
        if getattr(diagnostics, "state", None).value == "failed_connection":
            failure_count += 1
            if failure_count == 2:
                second_failure.set()

    manager = HomeAssistantEnrichmentManager(
        hass,
        entry,
        client,
        capability_provider=lambda: EnrichmentContractState.SUPPORTED,
        registry_builder=source,
        on_diagnostics_changed=diagnostics_changed,
        debounce_seconds=0.01,
        retry_delays=(0.1,),
        reconciliation_interval=timedelta(days=1),
    )
    retry_loops: list[asyncio.AbstractEventLoop] = []
    manager_ref = weakref.ref(manager)

    def observed_run_retry() -> None:
        retry_loops.append(asyncio.get_running_loop())
        current_manager = manager_ref()
        assert current_manager is not None
        HomeAssistantEnrichmentManager._run_retry(current_manager)

    manager._run_retry = observed_run_retry
    try:
        await manager.async_start()
        assert manager.diagnostics["sync_state"] == "failed_connection"
        await client.async_wait_for_publish_count(2)
        await manager.async_wait_for_idle()

        assert retry_loops == [hass.loop]
        assert client.publish_loops == [hass.loop, hass.loop]
        assert manager.diagnostics["sync_state"] == "successful"
        assert manager._retry_cancel is None
        assert client.max_active_publish == 1

        client.publish_outcomes.append(ZigbeeLensConnectionError("transient"))
        source.snapshot = _registry_snapshot(name="Retry Cancellation")
        hass.bus.async_fire(
            dr.EVENT_DEVICE_REGISTRY_UPDATED,
            {"action": "update"},
        )
        await asyncio.wait_for(second_failure.wait(), timeout=2)
        assert manager._retry_cancel is not None
        publish_count = len(client.published)
        retry_count = len(retry_loops)
        failure_count_before_stop = failure_count

        await entry.async_run_unload()
        await _timer_barrier(hass.loop, 0.2)

        assert len(client.published) == publish_count
        assert len(retry_loops) == retry_count
        assert failure_count == failure_count_before_stop
        assert manager._retry_cancel is None
        assert manager._tasks == set()
        assert client.max_active_publish == 1
    finally:
        await manager.async_stop()
        await hass.async_stop(force=True)
        restore_loop_handler()

    _assert_no_thread_safety_errors(caplog, loop_errors)


@pytest.mark.asyncio
async def test_default_periodic_reconciliation_runs_on_hass_loop_without_overlap_and_stops(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _assert_imported_manager_uses_selected_stage()
    caplog.set_level(logging.WARNING)
    hass = _plain_hass(tmp_path)
    loop_errors, restore_loop_handler = _capture_loop_errors(hass.loop)
    entry = _Entry()
    source = _RegistrySource(_registry_snapshot())
    client = _ObservedClient()
    client.block_on_call = 2
    diagnostics: list[object] = []
    interval_seconds = 0.05
    manager = HomeAssistantEnrichmentManager(
        hass,
        entry,
        client,
        capability_provider=lambda: EnrichmentContractState.SUPPORTED,
        registry_builder=source,
        on_diagnostics_changed=diagnostics.append,
        debounce_seconds=1,
        reconciliation_interval=timedelta(seconds=interval_seconds),
    )
    periodic_loops: list[asyncio.AbstractEventLoop] = []
    second_interval = asyncio.Event()
    manager_ref = weakref.ref(manager)

    def observed_periodic() -> None:
        periodic_loops.append(asyncio.get_running_loop())
        if len(periodic_loops) == 2:
            second_interval.set()
        current_manager = manager_ref()
        assert current_manager is not None
        HomeAssistantEnrichmentManager._handle_periodic_reconciliation(
            current_manager
        )

    manager._handle_periodic_reconciliation = observed_periodic
    try:
        await manager.async_start()
        await asyncio.wait_for(client.publish_entered.wait(), timeout=2)
        await asyncio.wait_for(second_interval.wait(), timeout=2)

        client.publish_release.set()
        await client.async_wait_for_publish_count(3)
        await manager.async_wait_for_idle()
        assert periodic_loops[:2] == [hass.loop, hass.loop]
        assert client.publish_loops[:3] == [hass.loop, hass.loop, hass.loop]
        assert client.max_active_publish == 1
        assert manager.diagnostics["sync_state"] == "successful"

        publish_count = len(client.published)
        periodic_count = len(periodic_loops)
        diagnostics_count = len(diagnostics)
        await entry.async_run_unload()
        await _timer_barrier(hass.loop, interval_seconds * 3)

        assert len(client.published) == publish_count
        assert len(periodic_loops) == periodic_count
        assert len(diagnostics) == diagnostics_count
        assert manager._periodic_cancel is None
        assert manager._tasks == set()
    finally:
        client.publish_release.set()
        await manager.async_stop()
        await hass.async_stop(force=True)
        restore_loop_handler()

    _assert_no_thread_safety_errors(caplog, loop_errors)


def _assert_adapter_ast_contract(
    method: Callable[..., object],
    *,
    helper_name: str,
    callback_argument: int,
) -> None:
    _assert_adapter_source_ast_contract(
        textwrap.dedent(inspect.getsource(method)),
        helper_name=helper_name,
        callback_argument=callback_argument,
    )


def _assert_adapter_source_ast_contract(
    source: str,
    *,
    helper_name: str,
    callback_argument: int,
) -> None:
    tree = ast.parse(source)
    method_node = tree.body[0]
    assert isinstance(method_node, ast.FunctionDef)
    assert not any(isinstance(node, ast.Lambda) for node in ast.walk(method_node))
    local_functions = [
        node for node in method_node.body if isinstance(node, ast.FunctionDef)
    ]
    assert len(local_functions) == 1
    local_callback = local_functions[0]
    assert [
        decorator.id
        for decorator in local_callback.decorator_list
        if isinstance(decorator, ast.Name)
    ] == ["callback"]
    assert len(local_callback.body) == 1
    action_statement = local_callback.body[0]
    assert isinstance(action_statement, ast.Expr)
    assert isinstance(action_statement.value, ast.Call)
    assert isinstance(action_statement.value.func, ast.Name)
    assert action_statement.value.func.id == "action"
    assert action_statement.value.args == []
    helper_calls = [
        node
        for node in ast.walk(method_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == helper_name
    ]
    assert len(helper_calls) == 1
    callback_node = helper_calls[0].args[callback_argument]
    assert isinstance(callback_node, ast.Name)
    assert callback_node.id == local_callback.name


@pytest.mark.parametrize(
    ("source", "helper_name", "callback_argument"),
    [
        (
            """
def adapter(self, delay, action):
    return async_call_later(self._hass, delay, lambda _now: action())
""",
            "async_call_later",
            2,
        ),
        (
            """
def adapter(self, delay, action):
    def run_action(_now):
        action()
    return async_call_later(self._hass, delay, run_action)
""",
            "async_call_later",
            2,
        ),
        (
            """
def adapter(self, interval, action):
    return async_track_time_interval(
        self._hass, lambda _now: action(), interval
    )
""",
            "async_track_time_interval",
            1,
        ),
        (
            """
def adapter(self, interval, action):
    def run_action(_now):
        action()
    return async_track_time_interval(self._hass, run_action, interval)
""",
            "async_track_time_interval",
            1,
        ),
    ],
    ids=(
        "later-lambda",
        "later-unclassified-local",
        "interval-lambda",
        "interval-unclassified-local",
    ),
)
def test_scheduler_adapter_guard_rejects_executor_classified_shapes(
    source: str,
    helper_name: str,
    callback_argument: int,
) -> None:
    with pytest.raises(AssertionError):
        _assert_adapter_source_ast_contract(
            textwrap.dedent(source),
            helper_name=helper_name,
            callback_argument=callback_argument,
        )


def test_default_scheduler_adapters_require_explicit_ha_callbacks() -> None:
    _assert_adapter_ast_contract(
        HomeAssistantEnrichmentManager._default_later_scheduler,
        helper_name="async_call_later",
        callback_argument=2,
    )
    _assert_adapter_ast_contract(
        HomeAssistantEnrichmentManager._default_interval_scheduler,
        helper_name="async_track_time_interval",
        callback_argument=1,
    )

    manager = HomeAssistantEnrichmentManager(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        capability_provider=lambda: EnrichmentContractState.SUPPORTED,
    )
    scheduled: dict[str, Callable[[datetime], None]] = {}
    actions: list[str] = []

    def capture_later(
        _hass: object,
        _delay: float,
        action: Callable[[datetime], None],
    ) -> Callable[[], None]:
        scheduled["later"] = action
        return lambda: None

    def capture_interval(
        _hass: object,
        action: Callable[[datetime], None],
        _interval: timedelta,
    ) -> Callable[[], None]:
        scheduled["interval"] = action
        return lambda: None

    with patch(
        "zigbeelens.enrichment_manager.async_call_later",
        side_effect=capture_later,
    ), patch(
        "zigbeelens.enrichment_manager.async_track_time_interval",
        side_effect=capture_interval,
    ):
        manager._default_later_scheduler(1, lambda: actions.append("later"))
        manager._default_interval_scheduler(
            timedelta(seconds=1),
            lambda: actions.append("interval"),
        )

    assert is_callback(scheduled["later"])
    assert is_callback(scheduled["interval"])
    now = datetime.now(UTC)
    scheduled["later"](now)
    scheduled["interval"](now)
    assert actions == ["later", "interval"]
