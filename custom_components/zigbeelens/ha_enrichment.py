"""Pure Home Assistant registry extraction for Core-local enrichment.

The registry snapshot is complete only when all three official Home Assistant
registries can be read.  Identity is derived exclusively from reviewed Zigbee
identifier/connection forms and resolved against an exact Core inventory.
Home Assistant user names are display metadata and never identity evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

HOME_ASSISTANT_ENRICHMENT_CONTRACT_VERSION = 1
MAX_ENRICHMENT_DEVICES = 5000
MAX_REGISTRY_DEVICES = 20_000
MAX_TEXT_LENGTH = 255
MAX_NETWORK_ID_LENGTH = 128

_IEEE_RE = re.compile(r"^0x(?P<hex>[0-9a-f]{16})$", re.IGNORECASE)
_COLON_IEEE_RE = re.compile(r"^(?:[0-9a-f]{2}:){7}[0-9a-f]{2}$", re.IGNORECASE)
_MQTT_ZIGBEE2MQTT_RE = re.compile(
    r"^zigbee2mqtt[/_.:-](?P<ieee>0x[0-9a-f]{16})"
    r"(?:$|[/_.:-][A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$)",
    re.IGNORECASE,
)
_DIRECT_IDENTIFIER_DOMAINS = frozenset({"zha", "zigbee", "zigbee2mqtt"})
_MQTT_IDENTIFIER_DOMAINS = frozenset({"mqtt", "zigbee2mqtt"})


class IeeeExtractionState(str, Enum):
    """Outcome of exact IEEE extraction from one HA device."""

    MISSING = "missing"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    EXACT = "exact"


class RegistrySnapshotState(str, Enum):
    """Availability of a complete HA registry read."""

    UNAVAILABLE = "unavailable"
    COMPLETE = "complete"


class EnrichmentSnapshotState(str, Enum):
    """Whether a complete replacement is empty or contains resolved rows."""

    UNAVAILABLE = "unavailable"
    COMPLETE_EMPTY = "complete_empty"
    COMPLETE_NONEMPTY = "complete_nonempty"


@dataclass(frozen=True, slots=True)
class IeeeExtraction:
    state: IeeeExtractionState
    ieee_address: str | None = None


@dataclass(frozen=True, slots=True)
class RegistryCandidate:
    """One HA device with exactly one reviewed Zigbee IEEE identity."""

    ieee_address: str
    ha_device_id: str
    ha_device_name: str | None
    area_id: str | None
    area_name: str | None
    entity_id: str | None
    # Original registry name is narrow disambiguation evidence. name_by_user is
    # deliberately not retained here because a user rename is never identity.
    original_name: str | None


@dataclass(frozen=True, slots=True)
class HomeAssistantRegistrySnapshot:
    state: RegistrySnapshotState
    candidates: tuple[RegistryCandidate, ...] = ()
    ambiguous_candidates: int = 0

    @property
    def submitted_candidates(self) -> int:
        return len(self.candidates) + self.ambiguous_candidates


@dataclass(frozen=True, slots=True)
class CoreInventoryDevice:
    network_id: str
    ieee_address: str
    friendly_name: str


@dataclass(frozen=True, slots=True)
class CoreInventorySnapshot:
    devices: tuple[CoreInventoryDevice, ...]


@dataclass(frozen=True, slots=True)
class HomeAssistantEnrichmentDevice:
    network_id: str
    ieee_address: str
    ha_device_id: str
    ha_device_name: str | None = None
    area_id: str | None = None
    area_name: str | None = None
    entity_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        """Return the exact Core contract-v1 device shape."""
        return {
            "network_id": self.network_id,
            "ieee_address": self.ieee_address,
            "ha_device_id": self.ha_device_id,
            "ha_device_name": self.ha_device_name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "entity_id": self.entity_id,
        }


@dataclass(frozen=True, slots=True)
class EnrichmentBuildResult:
    state: EnrichmentSnapshotState
    devices: tuple[HomeAssistantEnrichmentDevice, ...] = ()
    submitted_candidates: int = 0
    unmatched: int = 0
    ambiguous: int = 0
    fingerprint: str | None = None

    @property
    def complete(self) -> bool:
        return self.state is not EnrichmentSnapshotState.UNAVAILABLE


def _optional_text(value: object, *, max_length: int = MAX_TEXT_LENGTH) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        return None
    return normalized


def _required_text(value: object, *, max_length: int = MAX_TEXT_LENGTH) -> str:
    normalized = _optional_text(value, max_length=max_length)
    if normalized is None:
        raise ValueError("required registry identifier is unavailable")
    return normalized


def normalize_ieee(value: object) -> str | None:
    """Normalize one exact reviewed IEEE spelling, without substring matching."""
    if not isinstance(value, str) or value != value.strip():
        return None
    direct = _IEEE_RE.fullmatch(value)
    if direct is not None:
        return f"0x{direct.group('hex').lower()}"
    if _COLON_IEEE_RE.fullmatch(value) is not None:
        return "0x" + value.replace(":", "").lower()
    return None


def _mqtt_ieee(value: object) -> str | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    match = _MQTT_ZIGBEE2MQTT_RE.fullmatch(value)
    return normalize_ieee(match.group("ieee")) if match is not None else None


def extract_ieee_address(device: object) -> IeeeExtraction:
    """Extract exactly one unambiguous IEEE from reviewed HA registry fields."""
    candidates: set[str] = set()
    saw_supported_source = False

    for raw in getattr(device, "connections", ()) or ():
        if not isinstance(raw, (tuple, list)) or len(raw) != 2:
            continue
        kind, value = raw
        if str(kind).lower() != str(dr.CONNECTION_ZIGBEE).lower():
            continue
        saw_supported_source = True
        if (normalized := normalize_ieee(value)) is not None:
            candidates.add(normalized)

    for raw in getattr(device, "identifiers", ()) or ():
        if not isinstance(raw, (tuple, list)) or len(raw) != 2:
            continue
        domain, value = raw
        normalized_domain = str(domain).strip().lower()
        normalized: str | None = None
        if normalized_domain in _DIRECT_IDENTIFIER_DOMAINS:
            saw_supported_source = True
            normalized = normalize_ieee(value)
        if normalized is None and normalized_domain in _MQTT_IDENTIFIER_DOMAINS:
            saw_supported_source = True
            normalized = _mqtt_ieee(value)
        if normalized is not None:
            candidates.add(normalized)

    if len(candidates) > 1:
        return IeeeExtraction(IeeeExtractionState.AMBIGUOUS)
    if len(candidates) == 1:
        return IeeeExtraction(IeeeExtractionState.EXACT, next(iter(candidates)))
    if saw_supported_source:
        return IeeeExtraction(IeeeExtractionState.INVALID)
    return IeeeExtraction(IeeeExtractionState.MISSING)


def _resolved_area(
    device: object,
    enabled_entities: Sequence[object],
    area_registry: object,
) -> tuple[str | None, str | None]:
    device_area_id = _optional_text(getattr(device, "area_id", None))
    if device_area_id is not None:
        area = area_registry.async_get_area(device_area_id)
        if area is None:
            return None, None
        return device_area_id, _optional_text(getattr(area, "name", None))

    entity_area_ids = {
        area_id
        for entity in enabled_entities
        if (area_id := _optional_text(getattr(entity, "area_id", None))) is not None
    }
    if len(entity_area_ids) != 1:
        return None, None
    area_id = next(iter(entity_area_ids))
    area = area_registry.async_get_area(area_id)
    if area is None:
        return None, None
    return area_id, _optional_text(getattr(area, "name", None))


def _representative_entity(enabled_entities: Sequence[object]) -> str | None:
    entity_ids = sorted(
        entity_id
        for entity in enabled_entities
        if (entity_id := _optional_text(getattr(entity, "entity_id", None))) is not None
    )
    return entity_ids[0] if entity_ids else None


def build_home_assistant_registry_snapshot(
    hass: HomeAssistant,
) -> HomeAssistantRegistrySnapshot:
    """Read one complete snapshot using official HA registry APIs.

    Any registry access failure makes the whole source unavailable.  Callers
    must not turn that state into an empty replacement.
    """
    try:
        device_registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)
        area_registry = ar.async_get(hass)
        devices = list(device_registry.devices.values())
        if len(devices) > MAX_REGISTRY_DEVICES:
            return HomeAssistantRegistrySnapshot(RegistrySnapshotState.UNAVAILABLE)

        candidates: list[RegistryCandidate] = []
        ambiguous = 0
        for device in sorted(devices, key=lambda item: str(getattr(item, "id", ""))):
            extraction = extract_ieee_address(device)
            if extraction.state is IeeeExtractionState.MISSING:
                continue
            if extraction.state is not IeeeExtractionState.EXACT:
                ambiguous += 1
                continue

            enabled_entities = er.async_entries_for_device(
                entity_registry,
                _required_text(getattr(device, "id", None)),
                include_disabled_entities=False,
            )
            area_id, area_name = _resolved_area(
                device,
                enabled_entities,
                area_registry,
            )
            original_name = _optional_text(getattr(device, "name", None))
            display_name = (
                _optional_text(getattr(device, "name_by_user", None)) or original_name
            )
            ieee_address = extraction.ieee_address
            if ieee_address is None:
                raise ValueError("exact IEEE extraction omitted its value")
            candidates.append(
                RegistryCandidate(
                    ieee_address=ieee_address,
                    ha_device_id=_required_text(getattr(device, "id", None)),
                    ha_device_name=display_name,
                    area_id=area_id,
                    area_name=area_name,
                    entity_id=_representative_entity(enabled_entities),
                    original_name=original_name,
                )
            )
            if len(candidates) + ambiguous > MAX_ENRICHMENT_DEVICES:
                return HomeAssistantRegistrySnapshot(RegistrySnapshotState.UNAVAILABLE)
    except Exception:  # Registry getter/view failures mean an incomplete source.
        return HomeAssistantRegistrySnapshot(RegistrySnapshotState.UNAVAILABLE)

    return HomeAssistantRegistrySnapshot(
        RegistrySnapshotState.COMPLETE,
        tuple(
            sorted(candidates, key=lambda item: (item.ieee_address, item.ha_device_id))
        ),
        ambiguous,
    )


def parse_core_inventory_payload(payload: Mapping[str, Any]) -> CoreInventorySnapshot:
    """Validate one complete bounded `/api/v1/devices` inventory response."""
    items = payload.get("items")
    total = payload.get("total")
    if not isinstance(items, list):
        raise ValueError("Core inventory items are unavailable")
    if isinstance(total, bool) or type(total) is not int or total != len(items):
        raise ValueError("Core inventory is incomplete")
    if payload.get("next_cursor") is not None or len(items) > MAX_ENRICHMENT_DEVICES:
        raise ValueError("Core inventory is incomplete")

    devices: list[CoreInventoryDevice] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("Core inventory row is malformed")
        network_id = _required_text(
            item.get("network_id"),
            max_length=MAX_NETWORK_ID_LENGTH,
        )
        ieee_address = normalize_ieee(item.get("ieee_address"))
        friendly_name = _required_text(item.get("friendly_name"))
        if ieee_address is None:
            raise ValueError("Core inventory IEEE is malformed")
        identity = (network_id, ieee_address)
        if identity in seen:
            raise ValueError("Core inventory contains a duplicate identity")
        seen.add(identity)
        devices.append(CoreInventoryDevice(network_id, ieee_address, friendly_name))

    return CoreInventorySnapshot(
        tuple(sorted(devices, key=lambda item: (item.network_id, item.ieee_address)))
    )


def resolve_home_assistant_enrichment(
    registry: HomeAssistantRegistrySnapshot,
    inventory: CoreInventorySnapshot,
) -> EnrichmentBuildResult:
    """Resolve HA candidates to exact Core identities without order dependence."""
    if registry.state is RegistrySnapshotState.UNAVAILABLE:
        return EnrichmentBuildResult(EnrichmentSnapshotState.UNAVAILABLE)

    by_ieee: dict[str, list[CoreInventoryDevice]] = {}
    for device in inventory.devices:
        by_ieee.setdefault(device.ieee_address, []).append(device)

    resolved_groups: dict[
        tuple[str, str],
        list[tuple[RegistryCandidate, CoreInventoryDevice]],
    ] = {}
    unmatched = 0
    ambiguous = registry.ambiguous_candidates

    for candidate in registry.candidates:
        core_rows = by_ieee.get(candidate.ieee_address, [])
        selected: CoreInventoryDevice | None = None
        if len(core_rows) == 1:
            selected = core_rows[0]
        elif len(core_rows) > 1 and candidate.original_name is not None:
            original_matches = [
                row for row in core_rows if row.friendly_name == candidate.original_name
            ]
            if len(original_matches) == 1:
                selected = original_matches[0]
        if selected is None:
            if core_rows:
                ambiguous += 1
            else:
                unmatched += 1
            continue
        resolved_groups.setdefault(
            (selected.network_id, selected.ieee_address),
            [],
        ).append((candidate, selected))

    rows: list[HomeAssistantEnrichmentDevice] = []
    for identity in sorted(resolved_groups):
        resolved = resolved_groups[identity]
        if len(resolved) != 1:
            ambiguous += len(resolved)
            continue
        candidate, selected = resolved[0]
        rows.append(
            HomeAssistantEnrichmentDevice(
                network_id=selected.network_id,
                ieee_address=selected.ieee_address,
                ha_device_id=candidate.ha_device_id,
                ha_device_name=candidate.ha_device_name,
                area_id=candidate.area_id,
                area_name=candidate.area_name,
                entity_id=candidate.entity_id,
            )
        )

    ha_device_id_counts: dict[str, int] = {}
    entity_id_counts: dict[str, int] = {}
    for row in rows:
        ha_device_id_counts[row.ha_device_id] = (
            ha_device_id_counts.get(row.ha_device_id, 0) + 1
        )
        if row.entity_id is not None:
            entity_id_counts[row.entity_id] = entity_id_counts.get(row.entity_id, 0) + 1
    unconflicted_rows = [
        row
        for row in rows
        if ha_device_id_counts[row.ha_device_id] == 1
        and (row.entity_id is None or entity_id_counts[row.entity_id] == 1)
    ]
    ambiguous += len(rows) - len(unconflicted_rows)
    rows = unconflicted_rows

    state = (
        EnrichmentSnapshotState.COMPLETE_NONEMPTY
        if rows
        else EnrichmentSnapshotState.COMPLETE_EMPTY
    )
    resolved_devices = tuple(rows)
    return EnrichmentBuildResult(
        state=state,
        devices=resolved_devices,
        submitted_candidates=registry.submitted_candidates,
        unmatched=unmatched,
        ambiguous=ambiguous,
        fingerprint=enrichment_fingerprint(resolved_devices),
    )


def _enrichment_device_dict(
    device: HomeAssistantEnrichmentDevice,
) -> dict[str, str | None]:
    if not isinstance(device, HomeAssistantEnrichmentDevice):
        raise ValueError("Home Assistant enrichment row has the wrong type")

    network_id = _required_text(
        device.network_id,
        max_length=MAX_NETWORK_ID_LENGTH,
    )
    raw_ieee = _required_text(device.ieee_address, max_length=18)
    direct_ieee = _IEEE_RE.fullmatch(raw_ieee)
    if direct_ieee is None:
        raise ValueError("Home Assistant enrichment IEEE is malformed")
    ha_device_id = _required_text(device.ha_device_id)

    def optional_contract_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Home Assistant enrichment text is malformed")
        normalized = value.strip()
        if len(normalized) > MAX_TEXT_LENGTH:
            raise ValueError("Home Assistant enrichment text is too long")
        return normalized or None

    return {
        "network_id": network_id,
        "ieee_address": f"0x{direct_ieee.group('hex').lower()}",
        "ha_device_id": ha_device_id,
        "ha_device_name": optional_contract_text(device.ha_device_name),
        "area_id": optional_contract_text(device.area_id),
        "area_name": optional_contract_text(device.area_name),
        "entity_id": optional_contract_text(device.entity_id),
    }


def enrichment_request_payload(
    devices: Iterable[HomeAssistantEnrichmentDevice],
) -> dict[str, object]:
    """Build the one allowed Core-local mutation request."""
    rows = sorted(
        (_enrichment_device_dict(device) for device in devices),
        key=lambda item: (item["network_id"] or "", item["ieee_address"] or ""),
    )
    if len(rows) > MAX_ENRICHMENT_DEVICES:
        raise ValueError("Home Assistant enrichment payload is too large")
    identities = {(row["network_id"], row["ieee_address"]) for row in rows}
    if len(identities) != len(rows):
        raise ValueError("Home Assistant enrichment payload has duplicate identities")
    ha_device_ids = {row["ha_device_id"] for row in rows}
    if len(ha_device_ids) != len(rows):
        raise ValueError("Home Assistant enrichment payload has conflicting devices")
    entity_ids = [row["entity_id"] for row in rows if row["entity_id"] is not None]
    if len(set(entity_ids)) != len(entity_ids):
        raise ValueError("Home Assistant enrichment payload has conflicting entities")
    return {
        "home_assistant_enrichment_contract_version": (
            HOME_ASSISTANT_ENRICHMENT_CONTRACT_VERSION
        ),
        "devices": rows,
    }


def enrichment_fingerprint(
    devices: Iterable[HomeAssistantEnrichmentDevice],
) -> str:
    """Return a safe deterministic digest; never retain metadata in diagnostics."""
    encoded = json.dumps(
        enrichment_request_payload(devices),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
