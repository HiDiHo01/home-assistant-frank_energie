"""Regression tests for safe, user-confirmed stale-device repair handling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.frank_energie import (
    FrankEnergieEntryData,
    async_remove_config_entry_device,
)
from custom_components.frank_energie.const import DATA_ENODE_CHARGERS, DOMAIN
from custom_components.frank_energie.repairs import (
    _issue_id,
    async_process_device_inventory,
)

pytestmark = pytest.mark.asyncio

_KINDS = [
    ("charger", "chargers", "charger_brand"),
    ("vehicle", "vehicles", "vehicle_name"),
    ("battery", "batteries", "battery_status"),
]


def _register_device(
    hass: HomeAssistant,
    entry: ConfigEntry[FrankEnergieEntryData],
    kind: str,
    marker: str,
) -> tuple[dr.DeviceEntry, er.RegistryEntry]:
    """Register a device and its historical marker without a live entity."""
    identifier = f"stale-{kind}"
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, identifier)},
        model="SmartBattery" if kind == "battery" else None,
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{DOMAIN}_{identifier}_{marker}",
        config_entry=entry,
        device_id=device.id,
    )
    return device, entity


def _inventory(collection_key: str, identifiers: list[object]) -> SimpleNamespace:
    """Build a complete mock inventory of API device models."""
    return SimpleNamespace(**{collection_key: identifiers})


def _repair_exists(hass: HomeAssistant, issue_id: str) -> bool:
    """Check the actual Home Assistant issue registry."""
    return (DOMAIN, issue_id) in ir.async_get(hass).issues


@pytest.mark.parametrize(("kind", "collection_key", "marker"), _KINDS)
async def test_stale_device_requires_two_fresh_inventories(
    hass: HomeAssistant,
    frank_energie_setup,
    kind: str,
    collection_key: str,
    marker: str,
) -> None:
    """Raise one persistent repair after two valid absences and clear on return."""
    entry = await frank_energie_setup()
    device, entity = _register_device(hass, entry, kind, marker)
    issue_id = _issue_id(kind, entry.entry_id, device.id)
    missing = _inventory(collection_key, [])

    async_process_device_inventory(hass, entry, kind, missing)
    assert not _repair_exists(hass, issue_id)
    assert entry.runtime_data.stale_inventory_epochs[kind] == 1

    async_process_device_inventory(hass, entry, kind, missing)
    assert _repair_exists(hass, issue_id)
    issue = ir.async_get(hass).issues[DOMAIN, issue_id]
    assert issue.is_fixable
    assert issue.is_persistent
    assert issue.config_entry_id == entry.entry_id
    assert issue.data == {
        "entry_id": entry.entry_id,
        "device_id": device.id,
        "kind": kind,
    }
    assert er.async_get(hass).async_get(entity.entity_id) is not None

    member = (
        {"id": f"stale-{kind}"}
        if kind == "vehicle"
        else SimpleNamespace(id=f"stale-{kind}")
    )
    async_process_device_inventory(
        hass, entry, kind, _inventory(collection_key, [member])
    )
    assert not _repair_exists(hass, issue_id)
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.parametrize(("kind", "collection_key", "marker"), _KINDS)
@pytest.mark.parametrize("invalid_case", ["missing", "none", "invalid_id", "duplicate"])
async def test_invalid_inventory_never_creates_or_clears_repair(
    hass: HomeAssistant,
    frank_energie_setup,
    kind: str,
    collection_key: str,
    marker: str,
    invalid_case: str,
) -> None:
    """Skipped, partial, malformed, and duplicate inventories preserve repairs."""
    entry = await frank_energie_setup()
    device, _ = _register_device(hass, entry, kind, marker)
    issue_id = _issue_id(kind, entry.entry_id, device.id)
    valid_empty = _inventory(collection_key, [])
    async_process_device_inventory(hass, entry, kind, valid_empty)
    async_process_device_inventory(hass, entry, kind, valid_empty)
    assert _repair_exists(hass, issue_id)

    bad = {
        "missing": SimpleNamespace(),
        "none": None,
        "invalid_id": _inventory(collection_key, [SimpleNamespace(id=None)]),
        "duplicate": _inventory(
            collection_key,
            [SimpleNamespace(id="duplicate"), SimpleNamespace(id="duplicate")],
        ),
    }[invalid_case]
    epoch = entry.runtime_data.stale_inventory_epochs[kind]
    async_process_device_inventory(hass, entry, kind, bad)
    assert entry.runtime_data.stale_inventory_epochs[kind] == epoch
    assert _repair_exists(hass, issue_id)
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.parametrize(("kind", "collection_key", "marker"), _KINDS)
async def test_disabled_registry_entity_does_not_hide_stale_device(
    hass: HomeAssistant,
    frank_energie_setup,
    kind: str,
    collection_key: str,
    marker: str,
) -> None:
    """Disabled markers still identify devices missing from the account."""
    entry = await frank_energie_setup()
    device, entity = _register_device(hass, entry, kind, marker)
    er.async_get(hass).async_update_entity(
        entity.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )

    empty = _inventory(collection_key, [])
    async_process_device_inventory(hass, entry, kind, empty)
    async_process_device_inventory(hass, entry, kind, empty)
    assert _repair_exists(hass, _issue_id(kind, entry.entry_id, device.id))


async def test_unknown_device_never_gets_repair(
    hass: HomeAssistant, frank_energie_setup
) -> None:
    """A device without a recognized marker must not be proposed for deletion."""
    entry = await frank_energie_setup()
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "unknown-device")},
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        "unknown-device_power",
        config_entry=entry,
        device_id=device.id,
    )
    empty = _inventory("chargers", [])
    async_process_device_inventory(hass, entry, "charger", empty)
    async_process_device_inventory(hass, entry, "charger", empty)
    assert not _repair_exists(hass, _issue_id("charger", entry.entry_id, device.id))


@pytest.mark.parametrize("current_state", ["missing", "present", "fetch_failed"])
async def test_repair_flow_revalidates_before_removing_device(
    hass: HomeAssistant,
    frank_energie_setup,
    current_state: str,
) -> None:
    """Exercise Home Assistant's real repair flow with a fresh-fetch guard."""
    assert await async_setup_component(hass, "repairs", {})
    entry = await frank_energie_setup()
    device, entity = _register_device(hass, entry, "charger", "charger_brand")
    issue_id = _issue_id("charger", entry.entry_id, device.id)
    empty = _inventory("chargers", [])
    async_process_device_inventory(hass, entry, "charger", empty)
    async_process_device_inventory(hass, entry, "charger", empty)
    assert _repair_exists(hass, issue_id)

    coordinator = entry.runtime_data.charger_coordinator

    async def _mock_fresh_refresh() -> None:
        """Update the coordinator exactly as a newly completed API fetch would."""
        if current_state == "fetch_failed":
            coordinator.data[DATA_ENODE_CHARGERS] = None
            return
        members = (
            [SimpleNamespace(id="stale-charger")] if current_state == "present" else []
        )
        inventory = _inventory("chargers", members)
        coordinator.data[DATA_ENODE_CHARGERS] = inventory
        coordinator.last_update_success = True
        async_process_device_inventory(hass, entry, "charger", inventory)

    manager = repairs_flow_manager(hass)
    assert manager is not None
    start = await manager.async_init(DOMAIN, context={"issue_id": issue_id})
    assert start["type"] is FlowResultType.FORM
    assert start["step_id"] == "confirm"

    with patch.object(
        coordinator,
        "async_refresh",
        new_callable=AsyncMock,
        side_effect=_mock_fresh_refresh,
    ) as refresh:
        result = await manager.async_configure(start["flow_id"], user_input={})

    refresh.assert_awaited_once()
    await hass.async_block_till_done()
    expected_deleted = current_state == "missing"
    assert (dr.async_get(hass).async_get(device.id) is None) is expected_deleted
    assert (er.async_get(hass).async_get(entity.entity_id) is None) is expected_deleted
    if expected_deleted:
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert not _repair_exists(hass, issue_id)
    else:
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == (
            "cannot_verify" if current_state == "fetch_failed" else "device_restored"
        )


async def test_entry_scoped_issue_preserves_ignored_state(
    hass: HomeAssistant, frank_energie_setup
) -> None:
    """Let Core own the issue lifecycle without losing user dismissals."""
    entry = await frank_energie_setup()
    device, _ = _register_device(hass, entry, "charger", "charger_brand")
    issue_id = _issue_id("charger", entry.entry_id, device.id)
    empty = _inventory("chargers", [])
    async_process_device_inventory(hass, entry, "charger", empty)
    async_process_device_inventory(hass, entry, "charger", empty)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.config_entry_id == entry.entry_id

    ir.async_ignore_issue(hass, DOMAIN, issue_id, True)
    async_process_device_inventory(hass, entry, "charger", empty)
    dismissed_issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert dismissed_issue is not None
    assert dismissed_issue.dismissed_version is not None


async def test_unidentifiable_device_clears_unfixable_repair(
    hass: HomeAssistant, frank_energie_setup
) -> None:
    """Drop the issue, but not the device, when its type can no longer be proved."""
    entry = await frank_energie_setup()
    device, entity = _register_device(hass, entry, "charger", "charger_brand")
    empty = _inventory("chargers", [])
    async_process_device_inventory(hass, entry, "charger", empty)
    async_process_device_inventory(hass, entry, "charger", empty)
    issue_id = _issue_id("charger", entry.entry_id, device.id)
    assert _repair_exists(hass, issue_id)

    er.async_get(hass).async_remove(entity.entity_id)
    async_process_device_inventory(hass, entry, "charger", empty)

    assert not _repair_exists(hass, issue_id)
    assert dr.async_get(hass).async_get(device.id) is not None


async def test_device_removal_rejects_duplicate_inventory_ids(
    hass: HomeAssistant, frank_energie_setup
) -> None:
    """A malformed complete inventory cannot authorize deletion."""
    entry = await frank_energie_setup()
    device, _ = _register_device(hass, entry, "charger", "charger_brand")
    coordinator = entry.runtime_data.charger_coordinator
    coordinator.last_update_success = True
    coordinator.data = {
        DATA_ENODE_CHARGERS: _inventory(
            "chargers",
            [SimpleNamespace(id="other"), SimpleNamespace(id="other")],
        )
    }

    assert not await async_remove_config_entry_device(hass, entry, device)
    assert dr.async_get(hass).async_get(device.id) is not None
