"""ZigbeeLens integration exceptions."""

from __future__ import annotations


class ZigbeeLensError(Exception):
    """Base ZigbeeLens integration error."""


class ZigbeeLensApiError(ZigbeeLensError):
    """Unexpected API failure."""


class ZigbeeLensConnectionError(ZigbeeLensApiError):
    """Core is not reachable."""


class ZigbeeLensAuthError(ZigbeeLensApiError):
    """Core rejected the HACS bearer credential (HTTP 401 on a protected route)."""


class ZigbeeLensInvalidResponseError(ZigbeeLensApiError):
    """Response was not valid ZigbeeLens JSON."""


class ZigbeeLensHttpError(ZigbeeLensInvalidResponseError):
    """Safe categorical HTTP failure without response-body or request data."""

    __slots__ = ("category", "status_code")

    def __init__(self, status_code: int, category: str) -> None:
        self.status_code = status_code
        self.category = category
        super().__init__(
            f"ZigbeeLens Core request failed ({category}, HTTP {status_code})"
        )


class ZigbeeLensRequestRejectedError(ZigbeeLensHttpError):
    """Core rejected a request with a non-authentication 4xx response."""


class ZigbeeLensServerError(ZigbeeLensHttpError):
    """Core returned a transient server-side failure."""
