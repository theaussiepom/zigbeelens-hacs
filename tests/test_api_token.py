"""HACS API-token grammar — mirrored against Core Track 4B vectors."""

from __future__ import annotations

import base64
import os
import traceback

import pytest

from zigbeelens.api_token import (
    API_TOKEN_MAX_LENGTH,
    API_TOKEN_MIN_LENGTH,
    is_api_token_shape,
    optional_core_api_token,
    validate_core_api_token,
)

VALID_ALNUM = "a" * 32
VALID_BASE64 = base64.b64encode(os.urandom(36)).decode("ascii")
VALID_URLSAFE = "Ab0-_~." + ("x" * 25)
VALID_DOT_TILDE = ("z" * 30) + ".~"


def _assert_secret_absent(exc: BaseException, secret: str) -> None:
    assert secret not in str(exc)
    assert secret not in repr(exc)
    rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert secret not in rendered


@pytest.mark.parametrize(
    "token",
    [VALID_ALNUM, VALID_BASE64, VALID_URLSAFE, VALID_DOT_TILDE],
)
def test_accepted_api_tokens(token: str):
    assert is_api_token_shape(token)
    assert validate_core_api_token(token) == token
    assert optional_core_api_token(token) == token


def test_blank_optional_means_no_credential():
    assert optional_core_api_token("") == ""
    assert optional_core_api_token(None) == ""


@pytest.mark.parametrize(
    "token",
    [
        "has space inside____________",
        "a" * 31,
        "token,with,commas,,,,,,,,,,,",
        "token:with:colons:::::::::::",
        '"quoted-token-value-here!!!!!"',
        "hash#inside#################",
        "emoji-👍-not-ascii-xxxxxxxxx",
        "pad=middle=stillmore========",
        "a" * (API_TOKEN_MAX_LENGTH + 1),
        "tab\tinsidexxxxxxxxxxxxxxxxx",
        "newline\ninsidexxxxxxxxxxxxxxx",
        "  " + ("a" * 32),
        ("a" * 32) + "  ",
    ],
)
def test_rejected_api_tokens_without_echo(token: str):
    assert not is_api_token_shape(token) or token != token.strip()
    with pytest.raises(ValueError) as exc_info:
        validate_core_api_token(token)
    _assert_secret_absent(exc_info.value, token)
    with pytest.raises(ValueError) as exc_info:
        optional_core_api_token(token)
    _assert_secret_absent(exc_info.value, token)


def test_min_max_bounds():
    assert API_TOKEN_MIN_LENGTH == 32
    assert API_TOKEN_MAX_LENGTH == 4096
    assert is_api_token_shape("a" * 32)
    assert is_api_token_shape("a" * 4096)
    assert not is_api_token_shape("a" * 31)
    assert not is_api_token_shape("a" * 4097)
