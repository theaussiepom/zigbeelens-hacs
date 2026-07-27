"""Async client for ZigbeeLens Core."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, ContentTypeError
from aiohttp.client_exceptions import ClientConnectorError, ClientSSLError

from .api_token import optional_core_api_token
from .const import API_TIMEOUT
from .core_origin import InvalidCoreOrigin, canonicalize_core_origin
from .exceptions import (
    ZigbeeLensAuthError,
    ZigbeeLensConnectionError,
    ZigbeeLensHttpError,
    ZigbeeLensInvalidResponseError,
    ZigbeeLensRequestRejectedError,
    ZigbeeLensServerError,
)
from .ha_enrichment import (
    HOME_ASSISTANT_ENRICHMENT_CONTRACT_VERSION,
    CoreInventorySnapshot,
    HomeAssistantEnrichmentDevice,
    enrichment_request_payload,
    parse_core_inventory_payload,
)

_LOGGER = logging.getLogger(__name__)

_AUTH_REQUIRED = "Authentication required"
_INVALID_CORE_URL = "Invalid ZigbeeLens Core URL"
_INVALID_RESPONSE = "Invalid response from ZigbeeLens Core"
_NOT_ZIGBEELENS = "Endpoint is not ZigbeeLens Core"


@dataclass(frozen=True, slots=True)
class HomeAssistantEnrichmentResult:
    """Validated factual result from the exact Core enrichment mutation."""

    home_assistant_enrichment_contract_version: int
    submitted: int
    matched: int
    unmatched: int
    ambiguous: int
    stored: int
    last_push_at: str


class ZigbeeLensApiClient:
    """Bounded HTTP client for ZigbeeLens Core.

    When ``api_token`` is non-empty, protected requests send
    ``Authorization: Bearer <token>``. Public product discovery never
    includes Authorization. The only mutation methods are the exact
    Home Assistant enrichment publish and clear routes.
    """

    __slots__ = ("_api_token", "_core_origin", "_session", "_verify_ssl")

    def __init__(
        self,
        session: ClientSession,
        core_url: str,
        *,
        verify_ssl: bool = False,
        api_token: str = "",
    ) -> None:
        self._session = session
        try:
            origin = canonicalize_core_origin(core_url)
        except InvalidCoreOrigin:
            raise ZigbeeLensInvalidResponseError(_INVALID_CORE_URL) from None
        try:
            token = optional_core_api_token(api_token)
        except ValueError:
            raise ZigbeeLensAuthError(_AUTH_REQUIRED) from None
        self._core_origin = origin
        self._verify_ssl = verify_ssl
        self._api_token = token

    @property
    def core_url(self) -> str:
        return self._core_origin

    @property
    def api_token_configured(self) -> bool:
        return bool(self._api_token)

    def __repr__(self) -> str:
        return (
            f"ZigbeeLensApiClient(core_url={self._core_origin!r}, "
            f"verify_ssl={self._verify_ssl!r}, "
            f"api_token_configured={self.api_token_configured!r})"
        )

    def api_url(self, path: str) -> str:
        """Join a fixed internal path onto the canonical origin (no urljoin authority swap)."""
        normalized = path.lstrip("/")
        if not normalized or normalized.startswith("//") or "://" in normalized:
            raise ZigbeeLensInvalidResponseError("Invalid ZigbeeLens API path")
        return f"{self._core_origin}/{normalized}"

    def _protected_headers(self) -> dict[str, str] | None:
        if not self._api_token:
            return None
        return {"Authorization": f"Bearer {self._api_token}"}

    @staticmethod
    def _http_category(status: int) -> str:
        return {
            400: "bad_request",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "validation",
            429: "rate_limited",
        }.get(status, "request_rejected")

    @classmethod
    async def _decode_json_response(
        cls,
        response: Any,
        *,
        protected: bool,
        require_object: bool = True,
    ) -> Any:
        status = response.status
        if 300 <= status < 400:
            raise ZigbeeLensInvalidResponseError(_INVALID_RESPONSE) from None
        if status == 401:
            if protected:
                raise ZigbeeLensAuthError(_AUTH_REQUIRED) from None
            raise ZigbeeLensInvalidResponseError(_INVALID_RESPONSE) from None
        if 400 <= status < 500:
            raise ZigbeeLensRequestRejectedError(
                status,
                cls._http_category(status),
            ) from None
        if status >= 500:
            raise ZigbeeLensServerError(status, "server_error") from None
        try:
            payload = await response.json()
        except (ContentTypeError, ValueError):
            raise ZigbeeLensInvalidResponseError("Invalid JSON from Core") from None
        if require_object and not isinstance(payload, dict):
            raise ZigbeeLensInvalidResponseError("Expected JSON object from Core")
        return payload

    async def _request_json(
        self,
        path: str,
        *,
        protected: bool = True,
        require_object: bool = True,
    ) -> Any:
        url = self.api_url(path)
        timeout = ClientTimeout(total=API_TIMEOUT)
        headers = self._protected_headers() if protected else None

        try:
            async with self._session.get(
                url,
                timeout=timeout,
                ssl=self._verify_ssl,
                headers=headers,
                allow_redirects=False,
            ) as response:
                payload = await self._decode_json_response(
                    response,
                    protected=protected,
                    require_object=require_object,
                )
        except ZigbeeLensAuthError:
            raise
        except (ZigbeeLensHttpError, ZigbeeLensInvalidResponseError):
            raise
        except ClientSSLError:
            raise ZigbeeLensConnectionError("SSL error connecting to Core") from None
        except ClientConnectorError:
            raise ZigbeeLensConnectionError(
                "Cannot connect to ZigbeeLens Core"
            ) from None
        except ClientError:
            raise ZigbeeLensConnectionError(
                "Connection error talking to Core"
            ) from None
        except TimeoutError:
            raise ZigbeeLensConnectionError("Timed out connecting to Core") from None

        return payload

    @staticmethod
    def _validate_health(payload: dict[str, Any]) -> None:
        # Version is intentionally optional here so the coordinator can
        # classify an absent or malformed observation as Unknown instead of
        # misreporting a reachable Core as unreachable.
        if "status" not in payload:
            raise ZigbeeLensInvalidResponseError(
                "Health response missing required fields"
            )

    async def async_get_health(self) -> dict[str, Any]:
        payload = await self._request_json("api/health", protected=True)
        self._validate_health(payload)
        return payload

    async def async_get_dashboard(self) -> object:
        # The coordinator's typed Decision classifier owns payload integrity.
        # Returning malformed JSON values keeps reachable distinct from
        # unreachable and enables the payload-specific repair.
        return await self._request_json(
            "api/dashboard",
            protected=True,
            require_object=False,
        )

    async def async_get_config_status(self) -> dict[str, Any]:
        payload = await self._request_json("api/config/status", protected=True)
        if "version" not in payload:
            raise ZigbeeLensInvalidResponseError("Config status missing version")
        return payload

    @staticmethod
    def _validate_capabilities(payload: dict[str, Any]) -> None:
        if payload.get("product") != "zigbeelens":
            raise ZigbeeLensInvalidResponseError(
                "Capabilities response missing product"
            )
        if "capabilities" not in payload or not isinstance(
            payload["capabilities"], dict
        ):
            raise ZigbeeLensInvalidResponseError(
                "Capabilities response missing capabilities"
            )

    async def async_get_capabilities(self) -> dict[str, Any]:
        payload = await self._request_json("api/capabilities", protected=True)
        self._validate_capabilities(payload)
        return payload

    async def async_get_version(self) -> dict[str, Any]:
        """Public product probe — never sends Authorization."""
        return await self._request_json("api/version", protected=False)

    async def async_get_device_inventory(self) -> CoreInventorySnapshot:
        """Fetch one complete bounded Core inventory through the preferred API."""
        payload = await self._request_json("api/v1/devices", protected=True)
        try:
            return parse_core_inventory_payload(payload)
        except ValueError:
            raise ZigbeeLensInvalidResponseError(
                "Invalid ZigbeeLens Core device inventory"
            ) from None

    @staticmethod
    def _validate_enrichment_result(
        payload: dict[str, Any],
        *,
        expected_submitted: int,
    ) -> HomeAssistantEnrichmentResult:
        expected_keys = {
            "home_assistant_enrichment_contract_version",
            "submitted",
            "matched",
            "unmatched",
            "ambiguous",
            "stored",
            "last_push_at",
        }
        if set(payload) != expected_keys:
            raise ZigbeeLensInvalidResponseError(
                "Invalid Home Assistant enrichment response"
            )
        contract_version = payload["home_assistant_enrichment_contract_version"]
        if (
            isinstance(contract_version, bool)
            or type(contract_version) is not int
            or contract_version != HOME_ASSISTANT_ENRICHMENT_CONTRACT_VERSION
        ):
            raise ZigbeeLensInvalidResponseError(
                "Invalid Home Assistant enrichment response"
            )
        counts: dict[str, int] = {}
        for name in ("submitted", "matched", "unmatched", "ambiguous", "stored"):
            value = payload[name]
            if isinstance(value, bool) or type(value) is not int or value < 0:
                raise ZigbeeLensInvalidResponseError(
                    "Invalid Home Assistant enrichment response"
                )
            counts[name] = value
        if (
            counts["submitted"] != expected_submitted
            or counts["submitted"]
            != counts["matched"] + counts["unmatched"] + counts["ambiguous"]
            or counts["stored"] != counts["matched"]
        ):
            raise ZigbeeLensInvalidResponseError(
                "Invalid Home Assistant enrichment response"
            )
        last_push_at = payload["last_push_at"]
        if (
            not isinstance(last_push_at, str)
            or not last_push_at.strip()
            or last_push_at != last_push_at.strip()
            or len(last_push_at) > 64
        ):
            raise ZigbeeLensInvalidResponseError(
                "Invalid Home Assistant enrichment response"
            )
        try:
            parsed_last_push_at = datetime.fromisoformat(last_push_at)
        except ValueError:
            raise ZigbeeLensInvalidResponseError(
                "Invalid Home Assistant enrichment response"
            ) from None
        if (
            parsed_last_push_at.tzinfo is None
            or parsed_last_push_at.utcoffset() is None
        ):
            raise ZigbeeLensInvalidResponseError(
                "Invalid Home Assistant enrichment response"
            )
        return HomeAssistantEnrichmentResult(
            home_assistant_enrichment_contract_version=contract_version,
            submitted=counts["submitted"],
            matched=counts["matched"],
            unmatched=counts["unmatched"],
            ambiguous=counts["ambiguous"],
            stored=counts["stored"],
            last_push_at=last_push_at,
        )

    async def async_publish_home_assistant_enrichment(
        self,
        devices: tuple[HomeAssistantEnrichmentDevice, ...],
    ) -> HomeAssistantEnrichmentResult:
        """Publish one complete snapshot to the sole allowed POST route."""
        if type(devices) is not tuple:
            raise ZigbeeLensInvalidResponseError(
                "Invalid Home Assistant enrichment request"
            )
        try:
            payload = enrichment_request_payload(devices)
        except (TypeError, ValueError):
            raise ZigbeeLensInvalidResponseError(
                "Invalid Home Assistant enrichment request"
            ) from None
        url = self.api_url("api/v1/enrichment/homeassistant")
        timeout = ClientTimeout(total=API_TIMEOUT)
        try:
            async with self._session.post(
                url,
                json=payload,
                timeout=timeout,
                ssl=self._verify_ssl,
                headers=self._protected_headers(),
                allow_redirects=False,
            ) as response:
                result_payload = await self._decode_json_response(
                    response,
                    protected=True,
                )
        except ZigbeeLensAuthError:
            raise
        except (ZigbeeLensHttpError, ZigbeeLensInvalidResponseError):
            raise
        except ClientSSLError:
            raise ZigbeeLensConnectionError("SSL error connecting to Core") from None
        except ClientConnectorError:
            raise ZigbeeLensConnectionError(
                "Cannot connect to ZigbeeLens Core"
            ) from None
        except ClientError:
            raise ZigbeeLensConnectionError(
                "Connection error talking to Core"
            ) from None
        except TimeoutError:
            raise ZigbeeLensConnectionError("Timed out connecting to Core") from None
        return self._validate_enrichment_result(
            result_payload,
            expected_submitted=len(devices),
        )

    async def async_clear_home_assistant_enrichment(self) -> None:
        """Clear enrichment only through the exact optional removal route."""
        url = self.api_url("api/v1/enrichment/homeassistant")
        timeout = ClientTimeout(total=API_TIMEOUT)
        try:
            async with self._session.delete(
                url,
                timeout=timeout,
                ssl=self._verify_ssl,
                headers=self._protected_headers(),
                allow_redirects=False,
            ) as response:
                payload = await self._decode_json_response(
                    response,
                    protected=True,
                )
        except ZigbeeLensAuthError:
            raise
        except (ZigbeeLensHttpError, ZigbeeLensInvalidResponseError):
            raise
        except ClientSSLError:
            raise ZigbeeLensConnectionError("SSL error connecting to Core") from None
        except ClientConnectorError:
            raise ZigbeeLensConnectionError(
                "Cannot connect to ZigbeeLens Core"
            ) from None
        except ClientError:
            raise ZigbeeLensConnectionError(
                "Connection error talking to Core"
            ) from None
        except TimeoutError:
            raise ZigbeeLensConnectionError("Timed out connecting to Core") from None
        if set(payload) != {"cleared"} or payload.get("cleared") is not True:
            raise ZigbeeLensInvalidResponseError(
                "Invalid Home Assistant enrichment clear response"
            )

    async def async_validate_core(self) -> dict[str, Any]:
        """Prove ZigbeeLens product identity publicly, then probe protected health."""
        version = await self.async_get_version()
        if version.get("name") != "zigbeelens-core":
            raise ZigbeeLensInvalidResponseError(_NOT_ZIGBEELENS)
        ver = version.get("version")
        if not isinstance(ver, str) or not ver.strip():
            raise ZigbeeLensInvalidResponseError(_INVALID_RESPONSE)
        return await self.async_get_health()
