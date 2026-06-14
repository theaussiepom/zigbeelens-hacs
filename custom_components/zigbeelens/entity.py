"""Shared entity helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ZigbeeLensDataUpdateCoordinator


def device_info(entry_id: str, core_version: str | None) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        manufacturer=MANUFACTURER,
        name="ZigbeeLens",
        model="ZigbeeLens Core",
        sw_version=core_version,
    )


class ZigbeeLensEntity(CoordinatorEntity[ZigbeeLensDataUpdateCoordinator]):
    """Base entity for ZigbeeLens summary entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZigbeeLensDataUpdateCoordinator,
        entry_id: str,
        description_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._description_key = description_key
        self._attr_unique_id = f"{entry_id}_{description_key}"
        self._attr_device_info = device_info(entry_id, coordinator.data.core_version if coordinator.data else None)

    @property
    def dashboard(self) -> dict[str, Any]:
        assert self.coordinator.data is not None
        return self.coordinator.data.dashboard

    @property
    def health(self) -> dict[str, Any]:
        assert self.coordinator.data is not None
        return self.coordinator.data.health

    @property
    def config_status(self) -> dict[str, Any]:
        assert self.coordinator.data is not None
        return self.coordinator.data.config_status

    @property
    def health_snapshot(self) -> dict[str, Any]:
        return self.dashboard.get("health_snapshot") or {}
