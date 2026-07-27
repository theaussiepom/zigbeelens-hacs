"""Disposable real-registry probe for the production debounce scheduler."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from datetime import timedelta
import gc
from importlib.metadata import version
import inspect
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
import weakref

from homeassistant import loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import frame

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


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


async def _load_registries(hass: HomeAssistant) -> None:
    await ar.async_load(hass)
    setup_device_registry = getattr(dr, "async_setup", None)
    if setup_device_registry is not None:
        setup_device_registry(hass)
    await dr.async_load(hass)
    await er.async_load(hass)


async def _timer_barrier(
    loop: asyncio.AbstractEventLoop,
    delay: float,
) -> None:
    complete: asyncio.Future[None] = loop.create_future()
    loop.call_later(delay, complete.set_result, None)
    await asyncio.wait_for(complete, timeout=2)


async def _run(args: argparse.Namespace) -> dict[str, bool | int | str]:
    args.config_dir.mkdir(parents=True, exist_ok=True)
    hass = HomeAssistant(str(args.config_dir))
    loader.async_setup(hass)
    setup_frame = getattr(frame, "async_setup", None)
    if setup_frame is not None:
        setup_frame(hass)
    config_entry = SimpleNamespace(domain="mqtt", title="", disabled_by=None)
    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _entry_id: config_entry
    )
    await _load_registries(hass)

    area_registry = ar.async_get(hass)
    areas = {
        "kitchen": area_registry.async_create("Kitchen"),
        "study": area_registry.async_create("Study"),
    }
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id="scheduler-entry",
        connections={(dr.CONNECTION_ZIGBEE, IEEE)},
        name="Scheduler Lamp",
    )
    er.async_get(hass).async_get_or_create(
        "light",
        "zigbee2mqtt",
        "scheduler-lamp",
        device_id=device.id,
        suggested_object_id="scheduler_lamp",
    )

    sys.path.insert(0, str(args.components))
    from zigbeelens.api import HomeAssistantEnrichmentResult
    from zigbeelens.compatibility import EnrichmentContractState
    from zigbeelens.enrichment_manager import HomeAssistantEnrichmentManager
    from zigbeelens.ha_enrichment import CoreInventoryDevice, CoreInventorySnapshot

    component_path_verified = (
        Path(inspect.getfile(HomeAssistantEnrichmentManager)).resolve().parent
        == (args.components / "zigbeelens").resolve()
    )
    if not component_path_verified:
        raise RuntimeError("component path mismatch")

    class ObservedClient:
        def __init__(self) -> None:
            self.inventory = CoreInventorySnapshot(
                (CoreInventoryDevice("home", IEEE, "scheduler-lamp"),)
            )
            self.published: list[tuple[Any, ...]] = []
            self.publish_loops: list[asyncio.AbstractEventLoop] = []
            self.changed = asyncio.Condition()

        async def async_get_device_inventory(self) -> CoreInventorySnapshot:
            return self.inventory

        async def async_publish_home_assistant_enrichment(
            self,
            devices: tuple[Any, ...],
        ) -> HomeAssistantEnrichmentResult:
            self.publish_loops.append(asyncio.get_running_loop())
            self.published.append(devices)
            async with self.changed:
                self.changed.notify_all()
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

        async def async_wait_for_publish_count(self, expected: int) -> None:
            async def wait_for_count() -> None:
                async with self.changed:
                    await self.changed.wait_for(
                        lambda: len(self.published) >= expected
                    )

            await asyncio.wait_for(wait_for_count(), timeout=2)

    log_capture = _LogCapture()
    logging.getLogger().addHandler(log_capture)
    loop_errors: list[dict[str, Any]] = []
    previous_exception_handler = hass.loop.get_exception_handler()

    def capture_loop_error(
        loop: asyncio.AbstractEventLoop,
        context: dict[str, Any],
    ) -> None:
        loop_errors.append(context)
        if previous_exception_handler is not None:
            previous_exception_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    hass.loop.set_exception_handler(capture_loop_error)
    entry = _Entry()
    client = ObservedClient()
    diagnostics: list[object] = []
    manager = HomeAssistantEnrichmentManager(
        hass,
        entry,
        client,
        capability_provider=lambda: EnrichmentContractState.SUPPORTED,
        on_diagnostics_changed=diagnostics.append,
        debounce_seconds=0.1,
        reconciliation_interval=timedelta(days=1),
    )
    manager_ref = weakref.ref(manager)
    debounce_loops: list[asyncio.AbstractEventLoop] = []

    def observed_run_debounced() -> None:
        debounce_loops.append(asyncio.get_running_loop())
        current_manager = manager_ref()
        if current_manager is None:
            raise RuntimeError("manager disappeared before debounce")
        HomeAssistantEnrichmentManager._run_debounced(current_manager)

    manager._run_debounced = observed_run_debounced
    try:
        await manager.async_start()
        device = device_registry.async_update_device(
            device.id,
            name_by_user="Intermediate Name",
        )
        device = device_registry.async_update_device(
            device.id,
            area_id=areas["study"].id,
        )
        device = device_registry.async_update_device(
            device.id,
            name_by_user="Final Name",
        )
        await client.async_wait_for_publish_count(2)
        await manager.async_wait_for_idle()

        if len(client.published) != 2:
            raise RuntimeError("registry burst was not coalesced")
        if client.published[-1][0].ha_device_name != "Final Name":
            raise RuntimeError("latest registry name was not published")
        if client.published[-1][0].area_name != "Study":
            raise RuntimeError("latest registry area was not published")

        device_registry.async_update_device(
            device.id,
            name_by_user="Cancelled Name",
        )
        await hass.async_block_till_done()
        if manager._debounce_cancel is None:
            raise RuntimeError("pending debounce was not owned")
        publish_count = len(client.published)
        diagnostics_count = len(diagnostics)

        await entry.async_run_unload()
        await _timer_barrier(hass.loop, 0.2)
        device_registry.async_update_device(
            device.id,
            name_by_user="Post-stop Name",
        )
        await _timer_barrier(hass.loop, 0.2)

        thread_safety_errors = sum(
            any(marker in message.lower() for marker in THREAD_SAFETY_MARKERS)
            for message in (
                *log_capture.messages,
                *(
                    f"{context.get('message', '')} {context.get('exception', '')}"
                    for context in loop_errors
                ),
            )
        )
        if loop_errors or thread_safety_errors:
            raise RuntimeError("Home Assistant scheduler safety error")
        if len(client.published) != publish_count:
            raise RuntimeError("publish occurred after stop")
        if len(diagnostics) != diagnostics_count:
            raise RuntimeError("diagnostics callback occurred after stop")
        return {
            "coalesced_publish_count": len(client.published),
            "component_path_verified": component_path_verified,
            "debounce_on_hass_loop": debounce_loops == [hass.loop],
            "diagnostics_state": str(manager.diagnostics["sync_state"]),
            "manager_tasks_after_stop": len(manager._tasks),
            "post_stop_publish_count": len(client.published),
            "publish_on_hass_loop": all(
                loop is hass.loop for loop in client.publish_loops
            ),
            "thread_safety_errors": thread_safety_errors,
        }
    finally:
        await manager.async_stop()
        hass.loop.set_exception_handler(previous_exception_handler)
        logging.getLogger().removeHandler(log_capture)
        await hass.async_stop(force=True)
        pending_tasks = [
            task
            for task in asyncio.all_tasks(hass.loop)
            if task is not asyncio.current_task() and not task.done()
        ]
        if pending_tasks:
            raise RuntimeError("tasks remained alive after Home Assistant stop")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--components", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    args = parser.parse_args()
    result: dict[str, bool | int | str] | None = None
    exit_code = 0
    try:
        result = asyncio.run(_run(args))
    except Exception as err:
        print(
            f"real registry scheduler probe failed: {type(err).__name__}",
            file=sys.stderr,
        )
        exit_code = 1
    finally:
        if (
            sys.platform == "darwin"
            and sys.version_info[:2] == (3, 14)
            and version("homeassistant") in {"2026.6.3", "2026.7.3"}
        ):
            # HA 2026.7.3's attrs-based registry classes can crash macOS
            # CPython 3.14 during module-finalization GC after a clean stop.
            # Collect first, then freeze only after all scheduler assertions,
            # the explicit task-liveness check, and HA cleanup.
            gc.collect()
            gc.freeze()
    if result is not None:
        print(
            "ZIGBEELENS_SCHEDULER_PROBE="
            + json.dumps(result, sort_keys=True, separators=(",", ":"))
        )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
