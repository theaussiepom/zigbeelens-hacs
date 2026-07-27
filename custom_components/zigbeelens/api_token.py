"""HACS-local ZigbeeLens Core API-token grammar (mirrors Core Track 4B).

Kept independent of the Core Python package so the integration remains
installable without monorepo imports.
"""

from __future__ import annotations

import re

API_TOKEN_MIN_LENGTH = 32
API_TOKEN_MAX_LENGTH = 4096

# token68 body with optional '=' padding restricted to the end.
API_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9\-._~+/]+={0,}$")


def _contains_control_characters(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def is_api_token_shape(value: str) -> bool:
    """Return True when *value* matches the shared bearer token grammar."""
    if not (API_TOKEN_MIN_LENGTH <= len(value) <= API_TOKEN_MAX_LENGTH):
        return False
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return API_TOKEN_PATTERN.fullmatch(value) is not None


def validate_core_api_token(value: object) -> str:
    """Validate a non-empty Core API token without echoing rejected input."""
    if not isinstance(value, str):
        raise ValueError("must be a string")
    if value == "":
        raise ValueError("must not be empty")
    if value != value.strip():
        raise ValueError("must not have leading or trailing whitespace")
    if _contains_control_characters(value):
        raise ValueError("must not contain control characters")
    if len(value) < API_TOKEN_MIN_LENGTH:
        raise ValueError(f"must be at least {API_TOKEN_MIN_LENGTH} characters")
    if len(value) > API_TOKEN_MAX_LENGTH:
        raise ValueError(f"must be at most {API_TOKEN_MAX_LENGTH} characters")
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("must be ASCII") from None
    if API_TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError("must be a bearer-compatible token")
    return value


def optional_core_api_token(value: object) -> str:
    """Return a validated token, or ``\"\"`` when no credential is configured."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("must be a string")
    if value == "":
        return ""
    return validate_core_api_token(value)
