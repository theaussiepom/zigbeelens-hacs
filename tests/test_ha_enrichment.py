"""Production registry extraction and exact Core inventory resolution tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from zigbeelens.ha_enrichment import (
    CoreInventoryDevice,
    CoreInventorySnapshot,
    EnrichmentSnapshotState,
    HomeAssistantEnrichmentDevice,
    HomeAssistantRegistrySnapshot,
    IeeeExtractionState,
    RegistryCandidate,
    RegistrySnapshotState,
    build_home_assistant_registry_snapshot,
    enrichment_fingerprint,
    enrichment_request_payload,
    extract_ieee_address,
    normalize_ieee,
    parse_core_inventory_payload,
    resolve_home_assistant_enrichment,
)


def _device(
    device_id: str,
    *,
    connections=(),
    identifiers=(),
    name: str | None = None,
    name_by_user: str | None = None,
    area_id: str | None = None,
):
    return SimpleNamespace(
        id=device_id,
        connections=set(connections),
        identifiers=set(identifiers),
        name=name,
        name_by_user=name_by_user,
        area_id=area_id,
    )


def _entity(entity_id: str, area_id: str | None = None):
    return SimpleNamespace(entity_id=entity_id, area_id=area_id)


def _candidate(
    ieee_address: str,
    device_id: str,
    *,
    original_name: str | None = None,
    display_name: str | None = None,
    entity_id: str | None = None,
) -> RegistryCandidate:
    return RegistryCandidate(
        ieee_address=ieee_address,
        ha_device_id=device_id,
        ha_device_name=display_name,
        area_id=None,
        area_name=None,
        entity_id=entity_id,
        original_name=original_name,
    )


@pytest.mark.parametrize(
    ("device", "expected"),
    [
        (
            _device(
                "zha",
                connections={("zigbee", "0X00124B0001ABCDEF")},
            ),
            "0x00124b0001abcdef",
        ),
        (
            _device(
                "zha_identifier",
                identifiers={("zha", "00:12:4B:00:01:AB:CD:EF")},
            ),
            "0x00124b0001abcdef",
        ),
        (
            _device(
                "z2m",
                identifiers={("mqtt", "zigbee2mqtt_0x00124B0001ABCDEF_temperature")},
            ),
            "0x00124b0001abcdef",
        ),
        (
            _device(
                "z2m_direct",
                identifiers={("zigbee2mqtt", "0x00124b0001abcdef")},
            ),
            "0x00124b0001abcdef",
        ),
    ],
)
def test_extract_ieee_accepts_only_reviewed_exact_forms(device, expected):
    result = extract_ieee_address(device)
    assert result.state is IeeeExtractionState.EXACT
    assert result.ieee_address == expected


@pytest.mark.parametrize(
    "value",
    [
        "0x00124b0001abcde",
        "0x00124b0001abcdef0",
        "prefix-0x00124b0001abcdef-suffix",
        "00124b0001abcdef",
        " 0x00124b0001abcdef ",
    ],
)
def test_normalize_ieee_rejects_malformed_or_embedded_values(value):
    assert normalize_ieee(value) is None


def test_conflicting_ieee_candidates_are_ambiguous():
    device = _device(
        "conflict",
        connections={("zigbee", "0x00124b0001abcdef")},
        identifiers={("zha", "0x00124b0001abcdee")},
    )
    assert extract_ieee_address(device).state is IeeeExtractionState.AMBIGUOUS


def test_supported_but_malformed_identifier_is_invalid_not_missing():
    device = _device(
        "invalid",
        identifiers={("mqtt", "device-with-00124b0001abcdef-inside")},
    )
    assert extract_ieee_address(device).state is IeeeExtractionState.INVALID


def test_registry_builder_uses_names_areas_and_enabled_entity_order():
    devices = [
        _device(
            "device-area",
            connections={("zigbee", "0x00124b0001abcdef")},
            name="source-lamp",
            name_by_user="  Reading Lamp  ",
            area_id="living",
        ),
        _device(
            "entity-area",
            identifiers={("zha", "00:12:4b:00:01:ab:cd:ee")},
            name="Source Sensor",
        ),
        _device(
            "conflicting-area",
            identifiers={("zigbee2mqtt", "0x00124b0001abcded")},
            name="Conflict",
        ),
        _device(
            "removed-area",
            identifiers={("zigbee", "0x00124b0001abcdec")},
            name="Removed",
            area_id="removed",
        ),
        _device(
            "ambiguous",
            connections={("zigbee", "not-an-ieee")},
        ),
        _device("unrelated", identifiers={("matter", "0x00124b0001abcdeb")}),
    ]
    entities = {
        "device-area": [_entity("sensor.z"), _entity("sensor.a", "kitchen")],
        "entity-area": [
            _entity("sensor.z", "kitchen"),
            _entity("sensor.a", "kitchen"),
            _entity("sensor.no_area"),
        ],
        "conflicting-area": [
            _entity("sensor.one", "living"),
            _entity("sensor.two", "kitchen"),
        ],
        "removed-area": [_entity("sensor.removed", "living")],
    }
    areas = {
        "living": SimpleNamespace(name="Living Room"),
        "kitchen": SimpleNamespace(name="Kitchen"),
    }
    device_registry = SimpleNamespace(
        devices={device.id: device for device in reversed(devices)}
    )
    entity_registry = object()
    area_registry = SimpleNamespace(async_get_area=lambda area_id: areas.get(area_id))

    def entries_for_device(registry, device_id, *, include_disabled_entities):
        assert registry is entity_registry
        assert include_disabled_entities is False
        return entities.get(device_id, [])

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
            side_effect=entries_for_device,
        ),
    ):
        snapshot = build_home_assistant_registry_snapshot(SimpleNamespace())

    assert snapshot.state is RegistrySnapshotState.COMPLETE
    assert snapshot.ambiguous_candidates == 1
    by_id = {candidate.ha_device_id: candidate for candidate in snapshot.candidates}

    assert by_id["device-area"].ha_device_name == "Reading Lamp"
    assert by_id["device-area"].original_name == "source-lamp"
    assert by_id["device-area"].area_id == "living"
    assert by_id["device-area"].area_name == "Living Room"
    assert by_id["device-area"].entity_id == "sensor.a"

    assert by_id["entity-area"].area_id == "kitchen"
    assert by_id["entity-area"].area_name == "Kitchen"
    assert by_id["entity-area"].entity_id == "sensor.a"

    assert by_id["conflicting-area"].area_id is None
    assert by_id["conflicting-area"].area_name is None

    # A removed device-level area is unknown; entity metadata must not silently
    # replace the explicit but now-unresolvable device assignment.
    assert by_id["removed-area"].area_id is None
    assert by_id["removed-area"].area_name is None


def test_any_registry_read_failure_is_explicitly_unavailable():
    with (
        patch(
            "zigbeelens.ha_enrichment.dr.async_get",
            side_effect=RuntimeError("registry not initialized"),
        ),
        patch("zigbeelens.ha_enrichment.er.async_get") as entity_get,
    ):
        snapshot = build_home_assistant_registry_snapshot(SimpleNamespace())

    assert snapshot == HomeAssistantRegistrySnapshot(RegistrySnapshotState.UNAVAILABLE)
    entity_get.assert_not_called()


def test_core_inventory_parser_requires_one_complete_bounded_page():
    payload = {
        "items": [
            {
                "network_id": " beta ",
                "ieee_address": "0X00124B0001ABCDEF",
                "friendly_name": " Lamp ",
                "ignored_projection": {"not": "identity"},
            },
            {
                "network_id": "alpha",
                "ieee_address": "0x00124b0001abcdee",
                "friendly_name": "Sensor",
            },
        ],
        "total": 2,
        "limit": None,
        "next_cursor": None,
    }
    snapshot = parse_core_inventory_payload(payload)
    assert snapshot.devices == (
        CoreInventoryDevice("alpha", "0x00124b0001abcdee", "Sensor"),
        CoreInventoryDevice("beta", "0x00124b0001abcdef", "Lamp"),
    )

    for invalid in (
        {**payload, "total": 3},
        {**payload, "next_cursor": "more"},
        {
            "items": [payload["items"][0], dict(payload["items"][0])],
            "total": 2,
            "next_cursor": None,
        },
        {
            "items": [
                {
                    "network_id": "alpha",
                    "ieee_address": "0x00124b0001abcde",
                    "friendly_name": "bad",
                }
            ],
            "total": 1,
            "next_cursor": None,
        },
    ):
        with pytest.raises(ValueError):
            parse_core_inventory_payload(invalid)


def test_resolver_uses_exact_ieee_and_narrow_original_name_only():
    shared_ieee = "0x00124b0001abcdef"
    unique_ieee = "0x00124b0001abcdee"
    registry = HomeAssistantRegistrySnapshot(
        RegistrySnapshotState.COMPLETE,
        (
            _candidate(
                shared_ieee,
                "ha-selected",
                original_name="source-a",
                display_name="User renamed to source-b",
            ),
            _candidate(
                shared_ieee,
                "ha-ambiguous",
                original_name="does-not-match",
            ),
            _candidate(
                unique_ieee,
                "ha-unique",
                original_name="irrelevant",
            ),
            _candidate(
                "0x00124b0001abcded",
                "ha-unmatched",
            ),
        ),
    )
    inventory = CoreInventorySnapshot(
        (
            CoreInventoryDevice("network-b", shared_ieee, "source-b"),
            CoreInventoryDevice("network-a", shared_ieee, "source-a"),
            CoreInventoryDevice("network-c", unique_ieee, "Unique"),
        )
    )

    result = resolve_home_assistant_enrichment(registry, inventory)
    assert result.state is EnrichmentSnapshotState.COMPLETE_NONEMPTY
    assert result.submitted_candidates == 4
    assert result.unmatched == 1
    assert result.ambiguous == 1
    assert [
        (row.network_id, row.ieee_address, row.ha_device_id) for row in result.devices
    ] == [
        ("network-a", shared_ieee, "ha-selected"),
        ("network-c", unique_ieee, "ha-unique"),
    ]
    assert result.devices[0].ha_device_name == "User renamed to source-b"
    assert result.fingerprint == enrichment_fingerprint(result.devices)

    reversed_result = resolve_home_assistant_enrichment(
        HomeAssistantRegistrySnapshot(
            RegistrySnapshotState.COMPLETE,
            tuple(reversed(registry.candidates)),
        ),
        CoreInventorySnapshot(tuple(reversed(inventory.devices))),
    )
    assert reversed_result == result


def test_duplicate_ha_targets_are_all_ambiguous_not_first_row():
    ieee = "0x00124b0001abcdef"
    result = resolve_home_assistant_enrichment(
        HomeAssistantRegistrySnapshot(
            RegistrySnapshotState.COMPLETE,
            (
                _candidate(ieee, "ha-b"),
                _candidate(ieee, "ha-a"),
            ),
        ),
        CoreInventorySnapshot((CoreInventoryDevice("network", ieee, "Lamp"),)),
    )
    assert result.state is EnrichmentSnapshotState.COMPLETE_EMPTY
    assert result.devices == ()
    assert result.ambiguous == 2


@pytest.mark.parametrize("conflict_field", ["ha_device_id", "entity_id"])
def test_conflicting_ha_or_entity_ownership_is_omitted_as_ambiguous(
    conflict_field,
):
    first_ieee = "0x00124b0001abcdef"
    second_ieee = "0x00124b0001abcdee"
    candidates = (
        _candidate(
            first_ieee,
            "shared-ha" if conflict_field == "ha_device_id" else "ha-a",
            entity_id=(
                "sensor.shared" if conflict_field == "entity_id" else "sensor.a"
            ),
        ),
        _candidate(
            second_ieee,
            "shared-ha" if conflict_field == "ha_device_id" else "ha-b",
            entity_id=(
                "sensor.shared" if conflict_field == "entity_id" else "sensor.b"
            ),
        ),
    )
    result = resolve_home_assistant_enrichment(
        HomeAssistantRegistrySnapshot(
            RegistrySnapshotState.COMPLETE,
            candidates,
        ),
        CoreInventorySnapshot(
            (
                CoreInventoryDevice("network", first_ieee, "First"),
                CoreInventoryDevice("network", second_ieee, "Second"),
            )
        ),
    )

    assert result.state is EnrichmentSnapshotState.COMPLETE_EMPTY
    assert result.devices == ()
    assert result.ambiguous == 2


def test_complete_empty_is_distinct_from_unavailable_and_is_fingerprintable():
    complete = resolve_home_assistant_enrichment(
        HomeAssistantRegistrySnapshot(RegistrySnapshotState.COMPLETE),
        CoreInventorySnapshot(()),
    )
    unavailable = resolve_home_assistant_enrichment(
        HomeAssistantRegistrySnapshot(RegistrySnapshotState.UNAVAILABLE),
        CoreInventorySnapshot(()),
    )

    assert complete.state is EnrichmentSnapshotState.COMPLETE_EMPTY
    assert complete.complete is True
    assert complete.fingerprint
    assert enrichment_request_payload(complete.devices)["devices"] == []

    assert unavailable.state is EnrichmentSnapshotState.UNAVAILABLE
    assert unavailable.complete is False
    assert unavailable.fingerprint is None


def test_request_payload_is_canonical_strict_and_deterministic():
    rows = (
        HomeAssistantEnrichmentDevice(
            network_id=" beta ",
            ieee_address="0X00124B0001ABCDEF",
            ha_device_id=" ha-b ",
            ha_device_name=" ",
            area_id=" kitchen ",
        ),
        HomeAssistantEnrichmentDevice(
            network_id="alpha",
            ieee_address="0x00124b0001abcdee",
            ha_device_id="ha-a",
        ),
    )
    assert enrichment_request_payload(rows) == {
        "home_assistant_enrichment_contract_version": 1,
        "devices": [
            {
                "network_id": "alpha",
                "ieee_address": "0x00124b0001abcdee",
                "ha_device_id": "ha-a",
                "ha_device_name": None,
                "area_id": None,
                "area_name": None,
                "entity_id": None,
            },
            {
                "network_id": "beta",
                "ieee_address": "0x00124b0001abcdef",
                "ha_device_id": "ha-b",
                "ha_device_name": None,
                "area_id": "kitchen",
                "area_name": None,
                "entity_id": None,
            },
        ],
    }

    malformed = (
        HomeAssistantEnrichmentDevice(
            network_id="network",
            ieee_address="00:12:4b:00:01:ab:cd:ef",
            ha_device_id="ha",
        ),
    )
    with pytest.raises(ValueError):
        enrichment_request_payload(malformed)

    duplicate = (
        HomeAssistantEnrichmentDevice(
            network_id=" network ",
            ieee_address="0X00124B0001ABCDEF",
            ha_device_id="ha-1",
        ),
        HomeAssistantEnrichmentDevice(
            network_id="network",
            ieee_address="0x00124b0001abcdef",
            ha_device_id="ha-2",
        ),
    )
    with pytest.raises(ValueError):
        enrichment_request_payload(duplicate)

    conflicting_devices = (
        HomeAssistantEnrichmentDevice(
            network_id="network-a",
            ieee_address="0x00124b0001abcdef",
            ha_device_id=" same-ha-device ",
        ),
        HomeAssistantEnrichmentDevice(
            network_id="network-b",
            ieee_address="0x00124b0001abcdee",
            ha_device_id="same-ha-device",
        ),
    )
    with pytest.raises(ValueError):
        enrichment_request_payload(conflicting_devices)

    conflicting_entities = (
        HomeAssistantEnrichmentDevice(
            network_id="network-a",
            ieee_address="0x00124b0001abcdef",
            ha_device_id="ha-a",
            entity_id=" sensor.shared ",
        ),
        HomeAssistantEnrichmentDevice(
            network_id="network-b",
            ieee_address="0x00124b0001abcdee",
            ha_device_id="ha-b",
            entity_id="sensor.shared",
        ),
    )
    with pytest.raises(ValueError):
        enrichment_request_payload(conflicting_entities)

    oversized = tuple(
        HomeAssistantEnrichmentDevice(
            network_id=f"network-{index}",
            ieee_address="0x00124b0001abcdef",
            ha_device_id=f"ha-{index}",
        )
        for index in range(5001)
    )
    with pytest.raises(ValueError):
        enrichment_request_payload(oversized)
