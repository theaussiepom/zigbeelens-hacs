"""Canonical HTTP/HTTPS Core origin validation for the HACS companion."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

import idna

MAX_ORIGIN_LENGTH = 2048
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}

_IPV4_STRICT = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$"
)
_IPV4_NUMBER_LABEL = re.compile(r"^(?:[0-9]+|0[xX][0-9A-Fa-f]+)$")


class InvalidCoreOrigin(ValueError):
    """Raised when a Core URL is not a canonical HTTP(S) origin."""

    def __init__(self, reason: str = "invalid_url") -> None:
        # Never embed rejected input — it may contain credentials.
        super().__init__(reason)


def _has_forbidden_characters(value: str) -> bool:
    if "\\" in value:
        return True
    return any(unicodedata.category(ch) == "Cc" for ch in value)


def _ends_in_ipv4_number(hostname: str) -> bool:
    if not hostname:
        return False
    label = hostname.rsplit(".", 1)[-1]
    return _IPV4_NUMBER_LABEL.fullmatch(label) is not None


def _strict_ipv4(hostname: str) -> str:
    if _IPV4_STRICT.fullmatch(hostname) is None:
        raise InvalidCoreOrigin("invalid_url")
    try:
        return str(ipaddress.IPv4Address(hostname))
    except ValueError:
        raise InvalidCoreOrigin("invalid_url") from None


def _validate_ascii_hostname(hostname: str) -> None:
    if not hostname:
        raise InvalidCoreOrigin("invalid_url")
    if hostname.endswith(".") or hostname.startswith("."):
        raise InvalidCoreOrigin("invalid_url")
    if ".." in hostname:
        raise InvalidCoreOrigin("invalid_url")
    if any(ch.isspace() for ch in hostname):
        raise InvalidCoreOrigin("invalid_url")
    if any(ch in hostname for ch in ("'", '"', ";", ",", "\\")):
        raise InvalidCoreOrigin("invalid_url")
    if _ends_in_ipv4_number(hostname) and _IPV4_STRICT.fullmatch(hostname) is None:
        raise InvalidCoreOrigin("invalid_url")


def _normalize_hostname(hostname: str, *, bracketed: bool) -> str:
    if not hostname:
        raise InvalidCoreOrigin("invalid_url")
    if "*" in hostname:
        raise InvalidCoreOrigin("invalid_url")
    if hostname.endswith("."):
        raise InvalidCoreOrigin("invalid_url")
    if "%" in hostname:
        raise InvalidCoreOrigin("invalid_url")
    if any(ch.isspace() for ch in hostname):
        raise InvalidCoreOrigin("invalid_url")
    if any(ch in hostname for ch in ("'", '"', ";", ",", "\\")):
        raise InvalidCoreOrigin("invalid_url")

    if bracketed:
        try:
            return ipaddress.IPv6Address(hostname).compressed
        except ValueError:
            raise InvalidCoreOrigin("invalid_url") from None

    if ":" in hostname:
        try:
            return ipaddress.IPv6Address(hostname).compressed
        except ValueError:
            raise InvalidCoreOrigin("invalid_url") from None

    if _ends_in_ipv4_number(hostname):
        return _strict_ipv4(hostname)

    try:
        ascii_host = idna.encode(
            hostname, uts46=True, transitional=False, std3_rules=True
        ).decode("ascii")
    except (idna.IDNAError, UnicodeError, ValueError):
        raise InvalidCoreOrigin("invalid_url") from None

    _validate_ascii_hostname(ascii_host)
    return ascii_host


def canonicalize_core_origin(value: str) -> str:
    """Return a canonical ``scheme://host[:port]`` Core origin.

    Rejects credentials, non-root paths, query, fragment, wildcards, and
    non-HTTP schemes. Does not resolve DNS. A single trailing ``/`` is the only
    path form that normalizes away. Hostnames use the same CSP-safe grammar as
    Core (strict IPv4/IPv6 or IDNA STD3 DNS).
    """
    if not isinstance(value, str):
        raise InvalidCoreOrigin("invalid_url")
    if not value:
        raise InvalidCoreOrigin("invalid_url")
    if value != value.strip():
        raise InvalidCoreOrigin("invalid_url")
    if len(value) > MAX_ORIGIN_LENGTH:
        raise InvalidCoreOrigin("invalid_url")
    if _has_forbidden_characters(value):
        raise InvalidCoreOrigin("invalid_url")
    if value.lower() == "null":
        raise InvalidCoreOrigin("invalid_url")
    if value.startswith("//"):
        raise InvalidCoreOrigin("invalid_url")

    try:
        parts = urlsplit(value)
    except ValueError:
        raise InvalidCoreOrigin("invalid_url") from None

    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise InvalidCoreOrigin("invalid_url")
    if parts.username is not None or parts.password is not None:
        raise InvalidCoreOrigin("invalid_url")
    if "@" in (parts.netloc or ""):
        raise InvalidCoreOrigin("invalid_url")

    netloc = parts.netloc or ""
    bracketed = netloc.startswith("[")
    if bracketed and "]" not in netloc:
        raise InvalidCoreOrigin("invalid_url")

    hostname = parts.hostname
    if hostname is None or hostname == "":
        raise InvalidCoreOrigin("invalid_url")

    path = parts.path or ""
    if path not in {"", "/"}:
        raise InvalidCoreOrigin("invalid_url")
    if parts.query or parts.fragment:
        raise InvalidCoreOrigin("invalid_url")
    if ";" in path:
        raise InvalidCoreOrigin("invalid_url")

    try:
        port = parts.port
    except ValueError:
        raise InvalidCoreOrigin("invalid_url") from None
    if port is not None and (port < 1 or port > 65535):
        raise InvalidCoreOrigin("invalid_url")

    host = _normalize_hostname(hostname, bracketed=bracketed)
    if ":" in host:
        host_fmt = f"[{host}]"
    else:
        host_fmt = host

    if port is None or port == _DEFAULT_PORTS[scheme]:
        origin = f"{scheme}://{host_fmt}"
    else:
        origin = f"{scheme}://{host_fmt}:{port}"

    if any(ch.isspace() for ch in origin):
        raise InvalidCoreOrigin("invalid_url")
    return origin
