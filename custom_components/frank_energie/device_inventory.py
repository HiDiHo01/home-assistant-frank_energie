"""Identify registered Frank Energie hardware and validate live inventories."""

import re
from dataclasses import dataclass
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, SERVICE_NAME_BATTERIES


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
}


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
    return kind == "battery" and _has_battery_sensor(hass, entry, device, identifier)


@callback
def async_identify_inventory_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: dr.DeviceEntry,
    *,
    expected_kind: str | None = None,
) -> tuple[str, str] | None:
    """Return the unique hardware kind and ID owned by this config entry."""
    if device.config_entry_id != entry.entry_id or device.entry_type is not None:
        return None
    identifiers = {
        identifier
        for domain, identifier in device.identifiers
        if domain == DOMAIN and identifier
    }
    if len(identifiers) != 1:
        return None
    identifier = next(iter(identifiers))

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
