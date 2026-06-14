"""DataUpdateCoordinator for ZigbeeLens Core."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZigbeeLensApiClient
from .const import DOMAIN
from .exceptions import ZigbeeLensApiError

_LOGGER = logging.getLogger(__name__)


@dataclass
class ZigbeeLensCoordinatorData:
    """Coordinator snapshot used by entities and repairs."""

    health: dict[str, Any]
    dashboard: dict[str, Any]
    config_status: dict[str, Any]
    core_version: str
    collector_connected: bool
    last_update_success: bool
    last_exception: str | None = None


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

    async def _async_update_data(self) -> ZigbeeLensCoordinatorData:
        try:
            health = await self.client.async_get_health()
            dashboard = await self.client.async_get_dashboard()
            config_status = await self.client.async_get_config_status()
        except ZigbeeLensApiError as err:
            self.last_update_success = False
            self.last_exception = str(err)
            raise UpdateFailed(str(err)) from err

        collector = health.get("collector") or {}
        connected = bool(collector.get("connected"))
        self.last_update_success = True
        self.last_exception = None

        return ZigbeeLensCoordinatorData(
            health=health,
            dashboard=dashboard,
            config_status=config_status,
            core_version=str(health.get("version", "")),
            collector_connected=connected,
            last_update_success=True,
            last_exception=None,
        )
