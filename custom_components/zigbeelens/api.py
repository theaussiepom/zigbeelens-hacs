"""Async client for ZigbeeLens Core."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from aiohttp import ClientError, ClientSession, ClientTimeout, ContentTypeError
from aiohttp.client_exceptions import ClientConnectorError, ClientSSLError

from .const import API_TIMEOUT
from .exceptions import (
    ZigbeeLensConnectionError,
    ZigbeeLensInvalidResponseError,
)

_LOGGER = logging.getLogger(__name__)


class ZigbeeLensApiClient:
    """Read-only HTTP client for ZigbeeLens Core."""

    def __init__(
        self,
        session: ClientSession,
        core_url: str,
        *,
        verify_ssl: bool = False,
    ) -> None:
        self._session = session
        self._core_url = core_url.rstrip("/") + "/"
        self._verify_ssl = verify_ssl

    @property
    def core_url(self) -> str:
        return self._core_url.rstrip("/")

    def api_url(self, path: str) -> str:
        normalized = path.lstrip("/")
        return urljoin(self._core_url, normalized)

    async def _request_json(self, path: str) -> dict[str, Any]:
        url = self.api_url(path)
        timeout = ClientTimeout(total=API_TIMEOUT)
        try:
            async with self._session.get(
                url,
                timeout=timeout,
                ssl=self._verify_ssl,
            ) as response:
                if response.status >= 400:
                    raise ZigbeeLensInvalidResponseError(
                        f"ZigbeeLens Core returned HTTP {response.status}"
                    )
                try:
                    payload = await response.json()
                except (ContentTypeError, ValueError) as err:
                    raise ZigbeeLensInvalidResponseError("Invalid JSON from Core") from err
        except ClientSSLError as err:
            raise ZigbeeLensConnectionError("SSL error connecting to Core") from err
        except ClientConnectorError as err:
            raise ZigbeeLensConnectionError("Cannot connect to ZigbeeLens Core") from err
        except ClientError as err:
            raise ZigbeeLensConnectionError("Connection error talking to Core") from err
        except TimeoutError as err:
            raise ZigbeeLensConnectionError("Timed out connecting to Core") from err

        if not isinstance(payload, dict):
            raise ZigbeeLensInvalidResponseError("Expected JSON object from Core")
        return payload

    @staticmethod
    def _validate_health(payload: dict[str, Any]) -> None:
        for key in ("status", "version"):
            if key not in payload:
                raise ZigbeeLensInvalidResponseError("Health response missing required fields")

    @staticmethod
    def _validate_dashboard(payload: dict[str, Any]) -> None:
        if "overall_severity" not in payload or "health_snapshot" not in payload:
            raise ZigbeeLensInvalidResponseError("Dashboard response missing required fields")

    async def async_get_health(self) -> dict[str, Any]:
        payload = await self._request_json("api/health")
        self._validate_health(payload)
        return payload

    async def async_get_dashboard(self) -> dict[str, Any]:
        payload = await self._request_json("api/dashboard")
        self._validate_dashboard(payload)
        return payload

    async def async_get_config_status(self) -> dict[str, Any]:
        payload = await self._request_json("api/config/status")
        if "version" not in payload:
            raise ZigbeeLensInvalidResponseError("Config status missing version")
        return payload

    async def async_get_version(self) -> dict[str, Any]:
        return await self._request_json("api/version")

    async def async_validate_core(self) -> dict[str, Any]:
        """Validate Core is reachable and looks like ZigbeeLens."""
        health = await self.async_get_health()
        version = await self.async_get_version()
        if version.get("name") not in (None, "zigbeelens-core"):
            # Allow missing name for forward compatibility; reject wrong products.
            name = str(version.get("name", ""))
            if name and "zigbeelens" not in name.lower():
                raise ZigbeeLensInvalidResponseError("Endpoint is not ZigbeeLens Core")
        return health
