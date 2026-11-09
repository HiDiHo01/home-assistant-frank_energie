"""Validate complete Frank Energie hardware inventories before acting on absence."""

from dataclasses import dataclass
from typing import Final

from .const import DATA_BATTERIES, DATA_ENODE_CHARGERS, DATA_ENODE_VEHICLES


@dataclass(frozen=True, slots=True)
class InventorySpec:
    """Map a hardware kind to its coordinator and validated provider inventory."""

    coordinator_attribute: str
    data_key: str
    collection_key: str
    markers: tuple[tuple[str, str], ...]


INVENTORY_SPECS: Final[dict[str, InventorySpec]] = {
    "battery": InventorySpec(
        "battery_coordinator",
        DATA_BATTERIES,
        "batteries",
        (
            ("select", "battery_mode"),
            ("select", "battery_strategy"),
            ("number", "consumption_threshold_price"),
        ),
    ),
    "charger": InventorySpec(
        "charger_coordinator", DATA_ENODE_CHARGERS, "chargers",
        (("sensor", "charger_brand"),),
    ),
    "vehicle": InventorySpec(
        "vehicle_coordinator", DATA_ENODE_VEHICLES, "vehicles",
        (("sensor", "vehicle_name"),),
    ),
}


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
        if not isinstance(item_id, str) or not item_id or item_id in identifiers:
            return None
        identifiers.add(item_id)
    return identifiers
