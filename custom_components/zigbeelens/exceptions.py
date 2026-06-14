"""ZigbeeLens integration exceptions."""

from __future__ import annotations


class ZigbeeLensError(Exception):
    """Base ZigbeeLens integration error."""


class ZigbeeLensApiError(ZigbeeLensError):
    """Unexpected API failure."""


class ZigbeeLensConnectionError(ZigbeeLensApiError):
    """Core is not reachable."""


class ZigbeeLensAuthError(ZigbeeLensApiError):
    """Authentication failed (reserved for future auth)."""


class ZigbeeLensInvalidResponseError(ZigbeeLensApiError):
    """Response was not valid ZigbeeLens JSON."""
