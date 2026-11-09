"""Validate complete Frank Energie hardware inventories before acting on absence."""


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
