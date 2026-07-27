"""HACS Core origin validation agrees with Core grammar on shared vectors."""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zigbeelens.core_origin import InvalidCoreOrigin, canonicalize_core_origin
from zigbeelens.exceptions import ZigbeeLensInvalidResponseError

TEST_ROOT = Path(__file__).resolve().parent
MONOREPO_CORE_VECTORS = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "tests"
    / "fixtures"
    / "http_origin_vectors.json"
)
PACKAGED_CORE_VECTORS = TEST_ROOT / "fixtures" / "http_origin_vectors.json"
CORE_VECTORS = (
    MONOREPO_CORE_VECTORS
    if MONOREPO_CORE_VECTORS.is_file()
    else PACKAGED_CORE_VECTORS
)
VECTORS = json.loads(CORE_VECTORS.read_text(encoding="utf-8"))
SENTINEL_ORIGIN = "http://[credential-sentinel]"


@pytest.mark.parametrize("case", VECTORS, ids=[c["input"][:40] for c in VECTORS])
def test_hacs_matches_shared_vectors(case: dict) -> None:
    raw = case["input"]
    if case.get("reject"):
        with pytest.raises(InvalidCoreOrigin) as exc_info:
            canonicalize_core_origin(raw)
        assert "pass" not in str(exc_info.value)
        assert "token=" not in str(exc_info.value)
        assert "credential-sentinel" not in str(exc_info.value)
    else:
        assert canonicalize_core_origin(raw) == case["canonical"]


def test_normalize_rejects_userinfo_and_path():
    from zigbeelens.config_flow import _normalize_core_url

    with pytest.raises(ValueError):
        _normalize_core_url("https://user:secret@host.example")
    with pytest.raises(ValueError):
        _normalize_core_url("https://host.example/api")


def test_invalid_origin_exception_chain_hides_sentinel():
    from zigbeelens.api import ZigbeeLensApiClient
    from zigbeelens.config_flow import _normalize_core_url

    with pytest.raises(InvalidCoreOrigin) as exc_info:
        canonicalize_core_origin(SENTINEL_ORIGIN)
    exc = exc_info.value
    rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert "credential-sentinel" not in str(exc)
    assert "credential-sentinel" not in repr(exc)
    assert "credential-sentinel" not in rendered
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True

    with pytest.raises(ValueError) as norm_info:
        _normalize_core_url(SENTINEL_ORIGIN)
    assert norm_info.value.__cause__ is None
    assert norm_info.value.__suppress_context__ is True
    assert "credential-sentinel" not in str(norm_info.value)
    assert "credential-sentinel" not in "".join(
        traceback.format_exception(
            type(norm_info.value), norm_info.value, norm_info.value.__traceback__
        )
    )

    with pytest.raises(ZigbeeLensInvalidResponseError) as api_info:
        ZigbeeLensApiClient(MagicMock(), SENTINEL_ORIGIN)
    assert api_info.value.__cause__ is None
    assert api_info.value.__suppress_context__ is True
    assert "credential-sentinel" not in str(api_info.value)
    assert "credential-sentinel" not in "".join(
        traceback.format_exception(
            type(api_info.value), api_info.value, api_info.value.__traceback__
        )
    )


@pytest.mark.asyncio
async def test_setup_entry_invalid_url_hides_sentinel(monkeypatch):
    from homeassistant.exceptions import ConfigEntryError

    from zigbeelens import async_setup_entry

    hass = MagicMock()
    entry = MagicMock()
    entry.data = {
        "core_url": SENTINEL_ORIGIN,
        "verify_ssl": False,
        "panel_enabled": True,
    }
    entry.options = {}
    monkeypatch.setattr(
        "zigbeelens.async_get_clientsession", lambda *_a, **_k: MagicMock()
    )
    with pytest.raises(ConfigEntryError) as setup_info:
        await async_setup_entry(hass, entry)
    assert setup_info.value.__cause__ is None
    assert setup_info.value.__suppress_context__ is True
    assert "credential-sentinel" not in str(setup_info.value)
    assert "credential-sentinel" not in "".join(
        traceback.format_exception(
            type(setup_info.value),
            setup_info.value,
            setup_info.value.__traceback__,
        )
    )
