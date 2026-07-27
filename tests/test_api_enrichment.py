"""Exact HA enrichment API methods and mutation-boundary tests."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import zigbeelens.api as api_module
from zigbeelens.api import ZigbeeLensApiClient
from zigbeelens.compatibility import EnrichmentContractState
from zigbeelens.enrichment_manager import HomeAssistantEnrichmentManager
from zigbeelens.exceptions import (
    ZigbeeLensAuthError,
    ZigbeeLensInvalidResponseError,
    ZigbeeLensRequestRejectedError,
    ZigbeeLensServerError,
)
from zigbeelens.ha_enrichment import (
    HomeAssistantEnrichmentDevice,
    RegistrySnapshotState,
    build_home_assistant_registry_snapshot,
    resolve_home_assistant_enrichment,
)

TOKEN = "publisher-token-sentinel-should-never-leak"
BODY_SECRET = "body-secret-device-name-should-never-leak"
IEEE = "0x00124b0001abcdef"


class FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, **responses):
        self.responses = {
            method: list(method_responses)
            for method, method_responses in responses.items()
        }
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def _request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses[method].pop(0)

    def get(self, url: str, **kwargs):
        return self._request("get", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self._request("post", url, **kwargs)

    def delete(self, url: str, **kwargs):
        return self._request("delete", url, **kwargs)


def _row(**changes) -> HomeAssistantEnrichmentDevice:
    values = {
        "network_id": "home",
        "ieee_address": IEEE,
        "ha_device_id": "ha-device-id",
        "ha_device_name": "Reading Lamp",
        "area_id": "living",
        "area_name": "Living Room",
        "entity_id": "light.reading_lamp",
    }
    values.update(changes)
    return HomeAssistantEnrichmentDevice(**values)


def _result_payload(*, submitted: int = 1):
    return {
        "home_assistant_enrichment_contract_version": 1,
        "submitted": submitted,
        "matched": submitted,
        "unmatched": 0,
        "ambiguous": 0,
        "stored": submitted,
        "last_push_at": "2026-07-23T12:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_health_allows_unobserved_version_for_unknown_classification():
    session = FakeSession(get=[FakeResponse(200, {"status": "ok"})])
    client = ZigbeeLensApiClient(session, "http://core.local")

    payload = await client.async_get_health()

    assert payload == {"status": "ok"}


@pytest.mark.asyncio
async def test_dashboard_integrity_is_deferred_to_typed_coordinator_state():
    session = FakeSession(get=[FakeResponse(200, {"malformed": True})])
    client = ZigbeeLensApiClient(session, "http://core.local")

    payload = await client.async_get_dashboard()

    assert payload == {"malformed": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, [], ["not", "a", "dashboard"]])
async def test_non_object_dashboard_reaches_typed_coordinator_boundary(raw):
    session = FakeSession(get=[FakeResponse(200, raw)])
    client = ZigbeeLensApiClient(session, "http://core.local")

    payload = await client.async_get_dashboard()

    assert payload == raw


@pytest.mark.asyncio
async def test_dashboard_invalid_json_remains_a_categorical_payload_error():
    session = FakeSession(get=[FakeResponse(200, ValueError(BODY_SECRET))])
    client = ZigbeeLensApiClient(session, "http://core.local")

    with pytest.raises(
        ZigbeeLensInvalidResponseError,
        match="Invalid JSON from Core",
    ) as raised:
        await client.async_get_dashboard()

    assert BODY_SECRET not in str(raised.value)


@pytest.mark.asyncio
async def test_inventory_get_uses_exact_preferred_protected_route():
    session = FakeSession(
        get=[
            FakeResponse(
                200,
                {
                    "items": [
                        {
                            "network_id": "home",
                            "ieee_address": IEEE,
                            "friendly_name": "source-lamp",
                        }
                    ],
                    "total": 1,
                    "limit": None,
                    "next_cursor": None,
                },
            )
        ]
    )
    client = ZigbeeLensApiClient(
        session,
        "http://core.local:8377",
        api_token=TOKEN,
    )

    inventory = await client.async_get_device_inventory()

    assert inventory.devices[0].network_id == "home"
    assert session.calls == [
        (
            "get",
            "http://core.local:8377/api/v1/devices",
            {
                "timeout": session.calls[0][2]["timeout"],
                "ssl": False,
                "headers": {"Authorization": f"Bearer {TOKEN}"},
                "allow_redirects": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_publish_uses_exact_route_request_and_validated_response():
    session = FakeSession(post=[FakeResponse(200, _result_payload())])
    client = ZigbeeLensApiClient(
        session,
        "https://core.example",
        verify_ssl=True,
        api_token=TOKEN,
    )

    result = await client.async_publish_home_assistant_enrichment((_row(),))

    assert result.submitted == 1
    assert result.stored == 1
    method, url, kwargs = session.calls[0]
    assert method == "post"
    assert url == "https://core.example/api/v1/enrichment/homeassistant"
    assert kwargs["allow_redirects"] is False
    assert kwargs["ssl"] is True
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert kwargs["json"] == {
        "home_assistant_enrichment_contract_version": 1,
        "devices": [
            {
                "network_id": "home",
                "ieee_address": IEEE,
                "ha_device_id": "ha-device-id",
                "ha_device_name": "Reading Lamp",
                "area_id": "living",
                "area_name": "Living Room",
                "entity_id": "light.reading_lamp",
            }
        ],
    }
    assert TOKEN not in repr(client)


@pytest.mark.asyncio
async def test_publish_preserves_valid_partial_core_counts():
    partial = {
        **_result_payload(),
        "matched": 0,
        "unmatched": 1,
        "stored": 0,
    }
    session = FakeSession(post=[FakeResponse(200, partial)])
    client = ZigbeeLensApiClient(session, "http://core.local")

    result = await client.async_publish_home_assistant_enrichment((_row(),))

    assert (
        result.submitted,
        result.matched,
        result.unmatched,
        result.ambiguous,
        result.stored,
    ) == (1, 0, 1, 0, 0)


@pytest.mark.asyncio
async def test_production_registry_fixture_reaches_exact_post_shape():
    """HA-side cross-app proof: official registries → resolver → API request."""
    device = SimpleNamespace(
        id="ha-device-id",
        connections={("zigbee", IEEE)},
        identifiers=set(),
        name="source-lamp",
        name_by_user="Reading Lamp",
        area_id="living",
    )
    device_registry = SimpleNamespace(devices={device.id: device})
    entity_registry = object()
    area_registry = SimpleNamespace(
        async_get_area=lambda area_id: (
            SimpleNamespace(name="Living Room") if area_id == "living" else None
        )
    )
    with (
        patch(
            "zigbeelens.ha_enrichment.dr.async_get",
            return_value=device_registry,
        ),
        patch(
            "zigbeelens.ha_enrichment.er.async_get",
            return_value=entity_registry,
        ),
        patch(
            "zigbeelens.ha_enrichment.ar.async_get",
            return_value=area_registry,
        ),
        patch(
            "zigbeelens.ha_enrichment.er.async_entries_for_device",
            return_value=[
                SimpleNamespace(
                    entity_id="light.reading_lamp",
                    area_id=None,
                )
            ],
        ),
    ):
        registry = build_home_assistant_registry_snapshot(SimpleNamespace())

    inventory_session = FakeSession(
        get=[
            FakeResponse(
                200,
                {
                    "items": [
                        {
                            "network_id": "home",
                            "ieee_address": IEEE,
                            "friendly_name": "source-lamp",
                        }
                    ],
                    "total": 1,
                    "next_cursor": None,
                },
            )
        ],
        post=[FakeResponse(200, _result_payload())],
    )
    client = ZigbeeLensApiClient(inventory_session, "http://core.local")
    inventory = await client.async_get_device_inventory()
    build = resolve_home_assistant_enrichment(registry, inventory)
    await client.async_publish_home_assistant_enrichment(build.devices)

    assert registry.state is RegistrySnapshotState.COMPLETE
    posted = inventory_session.calls[1][2]["json"]
    assert posted["devices"] == [
        {
            "network_id": "home",
            "ieee_address": IEEE,
            "ha_device_id": "ha-device-id",
            "ha_device_name": "Reading Lamp",
            "area_id": "living",
            "area_name": "Living Room",
            "entity_id": "light.reading_lamp",
        }
    ]


@pytest.mark.asyncio
async def test_production_manager_publishes_registry_rename_area_and_removal():
    """Production HA registry builder → manager → strict API client."""
    device = SimpleNamespace(
        id="ha-device-id",
        connections={("zigbee", IEEE)},
        identifiers=set(),
        name="source-lamp",
        name_by_user="Reading Lamp",
        area_id="living",
    )
    device_registry = SimpleNamespace(devices={device.id: device})
    entity_registry = object()
    area_names = {
        "living": "Living Room",
        "office": "Office",
    }
    area_registry = SimpleNamespace(
        async_get_area=lambda area_id: (
            SimpleNamespace(name=area_names[area_id]) if area_id in area_names else None
        )
    )
    inventory_payload = {
        "items": [
            {
                "network_id": "home",
                "ieee_address": IEEE,
                "friendly_name": "source-lamp",
            }
        ],
        "total": 1,
        "next_cursor": None,
    }
    session = FakeSession(
        get=[
            FakeResponse(200, inventory_payload),
            FakeResponse(200, inventory_payload),
            FakeResponse(200, inventory_payload),
        ],
        post=[
            FakeResponse(200, _result_payload()),
            FakeResponse(200, _result_payload()),
            FakeResponse(200, _result_payload()),
        ],
    )
    client = ZigbeeLensApiClient(session, "http://core.local")
    hass = SimpleNamespace()
    hass.bus = SimpleNamespace(async_listen=lambda *_args: lambda: None)
    entry = SimpleNamespace(
        async_on_unload=lambda _callback: None,
        async_start_reauth=lambda *_args, **_kwargs: None,
    )
    manager = HomeAssistantEnrichmentManager(
        hass,
        entry,
        client,
        capability_provider=lambda: EnrichmentContractState.SUPPORTED,
        later_scheduler=lambda _delay, _action: lambda: None,
        interval_scheduler=lambda _interval, _action: lambda: None,
    )

    with (
        patch(
            "zigbeelens.ha_enrichment.dr.async_get",
            return_value=device_registry,
        ),
        patch(
            "zigbeelens.ha_enrichment.er.async_get",
            return_value=entity_registry,
        ),
        patch(
            "zigbeelens.ha_enrichment.ar.async_get",
            return_value=area_registry,
        ),
        patch(
            "zigbeelens.ha_enrichment.er.async_entries_for_device",
            return_value=[
                SimpleNamespace(
                    entity_id="light.reading_lamp",
                    area_id=None,
                )
            ],
        ),
    ):
        await manager.async_start()

        device.name_by_user = "Desk Lamp"
        device.area_id = "office"
        await manager.async_reconcile()

        device.name = None
        device.name_by_user = None
        device.area_id = None
        await manager.async_reconcile()

    posted_rows = [
        kwargs["json"]["devices"][0]
        for method, _url, kwargs in session.calls
        if method == "post"
    ]
    assert [(row["ha_device_name"], row["area_name"]) for row in posted_rows] == [
        ("Reading Lamp", "Living Room"),
        ("Desk Lamp", "Office"),
        (None, None),
    ]
    assert all(row["network_id"] == "home" for row in posted_rows)
    assert manager.diagnostics["sync_state"] == "successful"
    assert manager.diagnostics["match_state"] == "complete"
    await manager.async_stop()


@pytest.mark.asyncio
async def test_complete_empty_publish_is_an_explicit_valid_post():
    session = FakeSession(post=[FakeResponse(200, _result_payload(submitted=0))])
    client = ZigbeeLensApiClient(session, "http://core.local")

    result = await client.async_publish_home_assistant_enrichment(())

    assert result.stored == 0
    assert session.calls[0][2]["json"] == {
        "home_assistant_enrichment_contract_version": 1,
        "devices": [],
    }


@pytest.mark.asyncio
async def test_optional_clear_uses_only_exact_delete_route():
    session = FakeSession(delete=[FakeResponse(200, {"cleared": True})])
    client = ZigbeeLensApiClient(
        session,
        "http://core.local",
        api_token=TOKEN,
    )

    assert await client.async_clear_home_assistant_enrichment() is None
    method, url, kwargs = session.calls[0]
    assert method == "delete"
    assert url == "http://core.local/api/v1/enrichment/homeassistant"
    assert "json" not in kwargs
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"cleared": False},
        {"cleared": True, "extra": True},
        {},
    ],
)
@pytest.mark.asyncio
async def test_clear_rejects_any_non_exact_success(payload):
    session = FakeSession(delete=[FakeResponse(200, payload)])
    client = ZigbeeLensApiClient(session, "http://core.local")

    with pytest.raises(ZigbeeLensInvalidResponseError):
        await client.async_clear_home_assistant_enrichment()


@pytest.mark.parametrize(
    ("status", "exception_type", "category"),
    [
        (401, ZigbeeLensAuthError, None),
        (403, ZigbeeLensRequestRejectedError, "forbidden"),
        (404, ZigbeeLensRequestRejectedError, "not_found"),
        (409, ZigbeeLensRequestRejectedError, "conflict"),
        (422, ZigbeeLensRequestRejectedError, "validation"),
        (500, ZigbeeLensServerError, "server_error"),
    ],
)
@pytest.mark.asyncio
async def test_http_failures_are_categorical_and_never_include_payload(
    status,
    exception_type,
    category,
):
    session = FakeSession(
        post=[FakeResponse(status, {"detail": BODY_SECRET, "token": TOKEN})]
    )
    client = ZigbeeLensApiClient(
        session,
        "http://core.local",
        api_token=TOKEN,
    )

    with pytest.raises(exception_type) as exc_info:
        await client.async_publish_home_assistant_enrichment((_row(),))

    assert BODY_SECRET not in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)
    if category is not None:
        assert exc_info.value.category == category
        assert exc_info.value.status_code == status


@pytest.mark.asyncio
async def test_redirect_is_rejected_without_following():
    session = FakeSession(
        post=[FakeResponse(307, {"location": "https://evil.example"})]
    )
    client = ZigbeeLensApiClient(session, "http://core.local", api_token=TOKEN)

    with pytest.raises(ZigbeeLensInvalidResponseError):
        await client.async_publish_home_assistant_enrichment((_row(),))

    assert session.calls[0][2]["allow_redirects"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {**_result_payload(), "extra": True},
        {key: value for key, value in _result_payload().items() if key != "stored"},
        {**_result_payload(), "submitted": True},
        {**_result_payload(), "matched": 0, "stored": 0},
        {**_result_payload(), "home_assistant_enrichment_contract_version": 2},
        {**_result_payload(), "last_push_at": ""},
        {**_result_payload(), "last_push_at": "2026-07-23T12:00:00"},
        {**_result_payload(), "last_push_at": "not-a-timestamp"},
        {
            **_result_payload(),
            "last_push_at": " 2026-07-23T12:00:00+00:00 ",
        },
    ],
)
@pytest.mark.asyncio
async def test_publish_rejects_malformed_or_inconsistent_success(payload):
    session = FakeSession(post=[FakeResponse(200, payload)])
    client = ZigbeeLensApiClient(session, "http://core.local")
    with pytest.raises(ZigbeeLensInvalidResponseError):
        await client.async_publish_home_assistant_enrichment((_row(),))


@pytest.mark.asyncio
async def test_malformed_request_is_rejected_before_http():
    session = FakeSession(post=[])
    client = ZigbeeLensApiClient(session, "http://core.local")

    with pytest.raises(ZigbeeLensInvalidResponseError):
        await client.async_publish_home_assistant_enrichment(
            (_row(ieee_address="0x00124b0001abcde"),)
        )
    with pytest.raises(ZigbeeLensInvalidResponseError):
        await client.async_publish_home_assistant_enrichment(
            (_row(area_name={"nested": BODY_SECRET}),)
        )
    with pytest.raises(ZigbeeLensInvalidResponseError):
        await client.async_publish_home_assistant_enrichment([_row()])

    assert session.calls == []


def test_api_source_has_no_arbitrary_mutation_primitive():
    """AST proof that production writes are confined to the two exact methods."""
    source_path = Path(api_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    mutation_calls: set[tuple[str, str]] = set()

    for function in (
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    ):
        for child in ast.walk(function):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in {"post", "put", "patch", "delete"}
            ):
                mutation_calls.add((function.name, child.func.attr))

        if not function.name.startswith("_"):
            argument_names = {
                argument.arg
                for argument in (*function.args.posonlyargs, *function.args.args)
            }
            assert "method" not in argument_names
            assert "path" not in argument_names

    assert mutation_calls == {
        ("async_publish_home_assistant_enrichment", "post"),
        ("async_clear_home_assistant_enrichment", "delete"),
    }
