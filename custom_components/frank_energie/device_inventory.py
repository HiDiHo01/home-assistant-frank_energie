"""Identify registered Frank Energie hardware and validate live inventories."""

import re
from dataclasses import dataclass
from hashlib import sha256
from json import dumps
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SERVICE_NAME_BATTERIES

_TYPED_ID_PREFIX = "hardware_v1:"


@dataclass(frozen=True, slots=True)
class InventorySpec:
    """Map a hardware kind to its coordinator and validated provider inventory."""

    coordinator_attribute: str
    collection_key: str
    markers: tuple[tuple[str, str], ...]


INVENTORY_SPECS: Final[dict[str, InventorySpec]] = {
    "battery": InventorySpec(
        "battery_coordinator",
        "batteries",
        (
            ("select", "battery_mode"),
            ("select", "battery_strategy"),
            ("number", "consumption_threshold_price"),
        ),
    ),
    "charger": InventorySpec(
        "charger_coordinator",
        "chargers",
        (("sensor", "charger_brand"),),
    ),
    "vehicle": InventorySpec(
        "vehicle_coordinator",
        "vehicles",
        (("sensor", "vehicle_name"),),
    ),
    "pv": InventorySpec(
        "pv_coordinator",
        "systems",
        (("sensor", "operational_status"), ("sensor", "steering_status")),
    ),
    "hvac": InventorySpec(
        "smart_hvac_coordinator",
        "devices",
        (),
    ),
}

def _typed_device_marker(entry_id: str, kind: str, identifier: str) -> str:
    """Create an entry-scoped, opaque category marker from an existing provider ID."""
    digest = sha256(identifier.encode()).hexdigest()
    return f"{_TYPED_ID_PREFIX}{entry_id}:{kind}:{digest}"


def inventory_device_identifiers(
    entry_id: str, kind: str, identifier: str
) -> set[tuple[str, str]]:
    """Keep the original registry identifier plus a durable physical kind."""
    if kind not in INVENTORY_SPECS:
        raise ValueError(f"Unsupported Frank Energie hardware category: {kind}")
    return {
        (DOMAIN, identifier),
        (DOMAIN, _typed_device_marker(entry_id, kind, identifier)),
    }


@dataclass(frozen=True, slots=True)
class RegistryDeviceInventory:
    """Represent verified opaque device IDs for a normalized API inventory."""

    devices: list[dict[str, str]]


def smart_hvac_device_identifier(
    entry_id: str, site_reference: str, device_id: str
) -> str:
    """Return the existing site-scoped, opaque Smart Heating registry ID."""
    site_device = dumps((site_reference, device_id))
    digest = sha256(site_device.encode()).hexdigest()
    return f"{entry_id}_smart_hvac_device_{digest}"


def _has_hvac_marker(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry, identifier: str
) -> bool:
    """Recognize actual heating equipment from its scoped registry identity."""
    prefix = f"{entry.entry_id}_smart_hvac_device_"
    if re.fullmatch(rf"{re.escape(prefix)}[0-9a-f]{{64}}", identifier) is None:
        return False
    sensor_prefix = f"{entry.entry_id}_smart_hvac_equipment_"
    return any(
        entity.config_entry_id == entry.entry_id
        and entity.platform == DOMAIN
        and entity.domain == "sensor"
        and entity.unique_id.startswith(sensor_prefix)
        and entity.unique_id.endswith("_smart_hvac_equipment")
        for entity in er.async_entries_for_device(
            er.async_get(hass), device.id, include_disabled_entities=True
        )
    )


def _has_pv_panel_group_marker(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry, identifier: str
) -> bool:
    """Recognize PV hardware whose only remaining entity is a panel group."""
    prefix = f"{DOMAIN}_{identifier}_panel_group_"
    return any(
        entity.config_entry_id == entry.entry_id
        and entity.platform == DOMAIN
        and entity.domain == "sensor"
        and entity.unique_id.startswith(prefix)
        for entity in er.async_entries_for_device(
            er.async_get(hass), device.id, include_disabled_entities=True
        )
    )


def _has_battery_sensor(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry, identifier: str
) -> bool:
    """Recognize a battery whose control entities were never registered."""
    if device.via_device_id is None:
        return False
    parent = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_{SERVICE_NAME_BATTERIES}"), entry.entry_id
    )
    if parent is None or parent.id != device.via_device_id:
        return False

    prefix = re.escape(f"{entry.unique_id}.{identifier}_smart_battery_")
    marker = re.compile(rf"{prefix}[0-9]+_id")
    return any(
        entity.config_entry_id == entry.entry_id
        and entity.platform == DOMAIN
        and entity.domain == "sensor"
        and marker.fullmatch(entity.unique_id) is not None
        for entity in er.async_entries_for_device(
            er.async_get(hass), device.id, include_disabled_entities=True
        )
    )


def _has_inventory_marker(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dr.DeviceEntry,
    identifier: str,
    kind: str,
) -> bool:
    """Look up exact registry markers by platform and unique ID."""
    if kind == "hvac":
        return _has_hvac_marker(hass, entry, device, identifier)

    registry = er.async_get(hass)
    for platform, marker in INVENTORY_SPECS[kind].markers:
        entity_id = registry.async_get_entity_id(
            platform, DOMAIN, f"{DOMAIN}_{identifier}_{marker}"
        )
        if (
            entity_id is not None
            and (entity := registry.async_get(entity_id)) is not None
            and entity.config_entry_id == entry.entry_id
            and entity.device_id == device.id
        ):
            return True
    if kind == "battery":
        return _has_battery_sensor(hass, entry, device, identifier)
    if kind == "pv":
        return _has_pv_panel_group_marker(hass, entry, device, identifier)
    return False


@callback
def async_identify_inventory_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dr.DeviceEntry,
    *,
    expected_kind: str | None = None,
) -> tuple[str, str] | None:
    """Identify hardware via its persisted provider ID and scoped category.

    Preexisting untagged devices retain the historical entity-marker fallback
    until a valid inventory establishes their category.
    """
    if device.config_entry_id != entry.entry_id or device.entry_type is not None:
        return None

    identifiers = {
        identifier
        for domain, identifier in device.identifiers
        if domain == DOMAIN and identifier
    }
    legacy_ids = {
        identifier
        for identifier in identifiers
        if not identifier.startswith(_TYPED_ID_PREFIX)
    }
    if len(legacy_ids) != 1:
        return None
    identifier = next(iter(legacy_ids))
    typed_markers = identifiers - legacy_ids
    if typed_markers:
        matching_kinds = [
            kind
            for kind in INVENTORY_SPECS
            if _typed_device_marker(entry.entry_id, kind, identifier)
            in typed_markers
        ]
        if (
            len(typed_markers) != 1
            or len(matching_kinds) != 1
            or (expected_kind is not None and expected_kind != matching_kinds[0])
        ):
            return None
        return matching_kinds[0], identifier

    if expected_kind is not None and not _has_inventory_marker(
        hass, entry, device, identifier, expected_kind
    ):
        return None

    matching_kinds = [
        kind
        for kind in INVENTORY_SPECS
        if kind == expected_kind
        or _has_inventory_marker(hass, entry, device, identifier, kind)
    ]
    return (matching_kinds[0], identifier) if len(matching_kinds) == 1 else None


@callback
def async_persist_inventory_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dr.DeviceEntry,
    kind: str,
    inventory_ids: set[str],
) -> tuple[str, str] | None:
    """Persist a hardware category only when the original provider ID is proved.

    An unmarked legacy device may be recovered without any sensor only when
    its sole registry ID is present in the complete live provider inventory.
    Conflicting, cross-entry and service-device identities remain protected.
    """
    identified = async_identify_inventory_device(
        hass, entry, device, expected_kind=kind
    )
    if identified is None:
        if device.config_entry_id != entry.entry_id or device.entry_type is not None:
            return None
        own_ids = {
            identifier
            for domain, identifier in device.identifiers
            if domain == DOMAIN and identifier
        }
        if len(own_ids) != 1:
            return None
        identifier = next(iter(own_ids))
        if identifier.startswith(_TYPED_ID_PREFIX) or identifier not in inventory_ids:
            return None
        identified = kind, identifier

    kind, identifier = identified
    expected_identifiers = inventory_device_identifiers(
        entry.entry_id, kind, identifier
    )
    if expected_identifiers.issubset(device.identifiers):
        return identified

    registry = dr.async_get(hass)
    updated = registry.async_update_device(
        device.id, new_identifiers=set(device.identifiers) | expected_identifiers
    )
    if updated is None or async_identify_inventory_device(
        hass, entry, updated, expected_kind=kind
    ) != identified:
        return None
    return identified

def validated_inventory_ids(collection_key: str, inventory: object) -> set[str] | None:
    """Return unique inventory IDs, or None when the response is incomplete."""
    collection = getattr(inventory, collection_key, None)
    if not isinstance(collection, list):
        return None

    identifiers: set[str] = set()
    for item in collection:
        item_id = (
            item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        )
        if (
            not isinstance(item_id, str)
            or not item_id.strip()
            or item_id in identifiers
        ):
            return None
        identifiers.add(item_id)
    return identifiers
