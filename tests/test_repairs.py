"""Regression tests for safe, user-confirmed stale-device repair handling."""

import asyncio
from collections.abc import Awaitable, Callable
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
from python_frank_energie import FrankEnergie
from python_frank_energie.frank_energie import FrankEnergieQuery

from custom_components.frank_energie import (
    FrankEnergieEntryData,
    async_remove_config_entry_device,
)
from custom_components.frank_energie.const import (
    DATA_BATTERIES,
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DATA_USER,
    DOMAIN,
    SERVICE_NAME_BATTERIES,
)
from custom_components.frank_energie.coordinator import (
    FrankEnergieBatteryCoordinator,
    FrankEnergieChargerCoordinator,
    FrankEnergieVehicleCoordinator,
)
from custom_components.frank_energie.device_inventory import INVENTORY_SPECS
from custom_components.frank_energie.helpers import register_service_device
from custom_components.frank_energie.repairs import (
    _issue_id,
    async_process_device_inventory,
)

pytestmark = pytest.mark.asyncio

_KINDS = [
    ("charger", "chargers", "charger_brand"),
    ("vehicle", "vehicles", "vehicle_name"),
    ("battery", "batteries", "battery_mode"),
]
_INVENTORIES = [
    ("charger", DATA_ENODE_CHARGERS, "enodeChargers", "charger_brand"),
    ("vehicle", DATA_ENODE_VEHICLES, "enodeVehicles", "vehicle_name"),
    ("battery", DATA_BATTERIES, "smartBatteries", "battery_mode"),
]

type SetupEntry = Callable[[], Awaitable[ConfigEntry[FrankEnergieEntryData]]]
type HardwareCoordinator = (
    FrankEnergieBatteryCoordinator
    | FrankEnergieChargerCoordinator
    | FrankEnergieVehicleCoordinator
)


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
        "select" if kind == "battery" else "sensor",
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


def _enable_inventory_api(
    coordinator: HardwareCoordinator, monkeypatch: pytest.MonkeyPatch
) -> FrankEnergie:
    """Use the real authenticated library methods with synthetic account settings."""
    api = FrankEnergie(auth_token="test-token")
    monkeypatch.setattr(coordinator, "api", api)
    monkeypatch.setattr(coordinator, "_site_reference", "test-site")
    monkeypatch.setitem(
        coordinator.settings_coordinator.data,
        DATA_USER,
        SimpleNamespace(
            smartCharging={"isActivated": True}, smartTrading={"isActivated": True}
        ),
    )
    return api


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
    assert async_process_device_inventory(hass, entry, kind, bad) is None
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


@pytest.mark.parametrize(
    "current_state", ["missing", "present", "renamed", "maintenance", "marker_removed"]
)
async def test_repair_flow_revalidates_before_removing_device(
    hass: HomeAssistant,
    frank_energie_setup: SetupEntry,
    monkeypatch: pytest.MonkeyPatch,
    current_state: str,
) -> None:
    """Verify against a fresh API result during the refresh cooldown."""
    assert await async_setup_component(hass, "repairs", {})
    entry = await frank_energie_setup()
    device, entity = _register_device(hass, entry, "charger", "charger_brand")
    issue_id = _issue_id("charger", entry.entry_id, device.id)

    coordinator = entry.runtime_data.charger_coordinator
    api = _enable_inventory_api(coordinator, monkeypatch)
    empty: dict[str, object] = {"data": {"enodeChargers": []}}
    query = AsyncMock(return_value=empty)
    monkeypatch.setattr(api, "_query", query)
    await coordinator.async_refresh()
    await coordinator.async_request_refresh()
    await coordinator.async_request_refresh()
    assert query.await_count == 2
    assert _repair_exists(hass, issue_id)

    finish_poll = asyncio.Event()

    async def _inventory_response(_: FrankEnergieQuery) -> dict[str, object]:
        """Simulate live provider responses and changes while a request is running."""
        if current_state == "maintenance":
            await finish_poll.wait()
        if current_state == "renamed":
            dr.async_get(hass).async_update_device(device.id, name_by_user="My charger")
        if current_state == "present":
            return {
                "data": {
                    "enodeChargers": [
                        {
                            "id": "stale-charger",
                            "canSmartCharge": False,
                            "chargeSettings": {
                                "id": "test-settings",
                                "isSmartChargingEnabled": False,
                                "maxChargeLimit": 80,
                                "minChargeLimit": 20,
                                **{
                                    f"hour{day}": 7
                                    for day in (
                                        "Monday",
                                        "Tuesday",
                                        "Wednesday",
                                        "Thursday",
                                        "Friday",
                                        "Saturday",
                                        "Sunday",
                                    )
                                },
                            },
                            "chargeState": {"isCharging": False, "isPluggedIn": False},
                            "information": {},
                            "interventions": [],
                            "isReachable": False,
                            "lastSeen": None,
                        }
                    ]
                }
            }
        return empty

    manager = repairs_flow_manager(hass)
    assert manager is not None
    start = await manager.async_init(DOMAIN, context={"issue_id": issue_id})
    assert start["type"] is FlowResultType.FORM
    assert start["step_id"] == "confirm"

    query.side_effect = _inventory_response
    if current_state == "marker_removed":
        er.async_get(hass).async_remove(entity.entity_id)
    if current_state == "maintenance":
        # A poll already in progress can succeed even if the confirmation's own
        # refresh has to skip its fetch because maintenance has since begun.
        poll = hass.async_create_task(coordinator.async_refresh(), eager_start=True)
        monkeypatch.setattr(coordinator, "_should_skip_api_calls", lambda _: True)
        confirm = hass.async_create_task(
            manager.async_configure(start["flow_id"], user_input={}), eager_start=True
        )
        finish_poll.set()
        await poll
        result = await confirm
    else:
        result = await manager.async_configure(start["flow_id"], user_input={})
    await hass.async_block_till_done()

    expected_deleted = current_state in {"missing", "renamed"}
    expected_query_count = 2 if current_state == "marker_removed" else 3
    assert query.await_count == expected_query_count
    assert (dr.async_get(hass).async_get(device.id) is None) is expected_deleted
    assert (er.async_get(hass).async_get(entity.entity_id) is None) is (
        expected_deleted or current_state == "marker_removed"
    )
    assert _repair_exists(hass, issue_id) is (current_state == "maintenance")
    if expected_deleted:
        assert result["type"] is FlowResultType.CREATE_ENTRY
    else:
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == (
            "device_restored" if current_state == "present" else "cannot_verify"
        )
        assert not await async_remove_config_entry_device(hass, entry, device)


@pytest.mark.parametrize(("kind", "data_key", "api_field", "marker"), _INVENTORIES)
@pytest.mark.parametrize("failure", ["timeout", "graphql_error"])
async def test_provider_failure_does_not_authorize_device_removal(
    hass: HomeAssistant,
    frank_energie_setup: SetupEntry,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    data_key: str,
    api_field: str,
    marker: str,
    failure: str,
) -> None:
    """Preserve an existing repair and device through repeated real library failures."""
    assert await async_setup_component(hass, "repairs", {})
    entry = await frank_energie_setup()
    device, entity = _register_device(hass, entry, kind, marker)
    device_registry = dr.async_get(hass)
    saved_device = device_registry.async_update_device(
        device.id, name_by_user="Keep me"
    )
    entity_registry = er.async_get(hass)
    saved_entity = entity_registry.async_update_entity(
        entity.entity_id, disabled_by=er.RegistryEntryDisabler.USER, name="Keep entity"
    )
    issue_id = _issue_id(kind, entry.entry_id, device.id)
    coordinator: HardwareCoordinator = getattr(
        entry.runtime_data, INVENTORY_SPECS[kind].coordinator_attribute
    )
    api = _enable_inventory_api(coordinator, monkeypatch)
    query = AsyncMock(return_value={"data": {api_field: []}})
    monkeypatch.setattr(api, "_query", query)
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert _repair_exists(hass, issue_id)
    verified_data = coordinator.data[data_key]

    if failure == "timeout":
        query.side_effect = TimeoutError
    else:
        query.return_value = {
            "data": {api_field: []},
            "errors": [{"message": "Synthetic provider error"}],
        }
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.data[data_key] is verified_data
    assert not await async_remove_config_entry_device(hass, entry, saved_device)

    manager = repairs_flow_manager(hass)
    assert manager is not None
    start = await manager.async_init(DOMAIN, context={"issue_id": issue_id})
    result = await manager.async_configure(start["flow_id"], user_input={})

    assert query.await_count == 5
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_verify"
    assert _repair_exists(hass, issue_id)
    assert device_registry.async_get(device.id) == saved_device
    assert entity_registry.async_get(entity.entity_id) == saved_entity


@pytest.mark.parametrize(("kind", "data_key", "api_field", "marker"), _INVENTORIES)
async def test_repairs_failure_does_not_interrupt_hardware_polling(
    hass: HomeAssistant,
    frank_energie_setup: SetupEntry,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    data_key: str,
    api_field: str,
    marker: str,
) -> None:
    """Keep fresh coordinator data available if issue processing raises."""
    entry = await frank_energie_setup()
    device, _ = _register_device(hass, entry, kind, marker)
    coordinator: HardwareCoordinator = getattr(
        entry.runtime_data, INVENTORY_SPECS[kind].coordinator_attribute
    )
    api = _enable_inventory_api(coordinator, monkeypatch)
    query = AsyncMock(return_value={"data": {api_field: []}})
    monkeypatch.setattr(api, "_query", query)
    with patch.object(ir, "async_refresh_issues", side_effect=RuntimeError):
        await coordinator.async_refresh()

    query.assert_awaited_once()
    assert coordinator.last_update_success
    inventory = coordinator.data[data_key]
    assert getattr(inventory, INVENTORY_SPECS[kind].collection_key) == []
    assert dr.async_get(hass).async_get(device.id) is not None


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
    inventory = _inventory(
        "chargers", [SimpleNamespace(id="other"), SimpleNamespace(id="other")]
    )
    coordinator.data[DATA_ENODE_CHARGERS] = inventory
    coordinator.verified_inventory_ids = async_process_device_inventory(
        hass, entry, "charger", inventory
    )

    assert not await async_remove_config_entry_device(hass, entry, device)
    assert dr.async_get(hass).async_get(device.id) is not None


@pytest.mark.parametrize(
    "battery_model", ["SmartBattery", "Smart Battery", "Batteries", None]
)
async def test_battery_classification_uses_stable_entity_marker(
    hass: HomeAssistant, frank_energie_setup, battery_model: str | None
) -> None:
    """Recognize battery devices regardless of the mutable registry model."""
    entry = await frank_energie_setup()
    device, mode = _register_device(hass, entry, "battery", "battery_mode")
    entity_registry = er.async_get(hass)
    entity_registry.async_update_entity(
        mode.entity_id, disabled_by=er.RegistryEntryDisabler.USER
    )
    entity_registry.async_get_or_create(
        "number",
        DOMAIN,
        f"{DOMAIN}_stale-battery_consumption_threshold_price",
        config_entry=entry,
        device_id=device.id,
    )
    registry = dr.async_get(hass)
    updated_device = registry.async_update_device(device.id, model=battery_model)
    empty = _inventory("batteries", [])
    async_process_device_inventory(hass, entry, "battery", empty)
    coordinator = entry.runtime_data.battery_coordinator
    coordinator.verified_inventory_ids = async_process_device_inventory(
        hass, entry, "battery", empty
    )
    assert _repair_exists(hass, _issue_id("battery", entry.entry_id, device.id))
    assert await async_remove_config_entry_device(hass, entry, updated_device)


async def test_battery_identified_by_disabled_sensor_without_controls(
    hass: HomeAssistant, frank_energie_setup: SetupEntry
) -> None:
    """Identify a historical battery registered with only sensor entities."""
    entry = await frank_energie_setup()
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "stale-battery")},
        via_device_id=register_service_device(hass, entry, SERVICE_NAME_BATTERIES),
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.unique_id}.stale-battery_smart_battery_2_id",
        config_entry=entry,
        device_id=device.id,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    empty = _inventory("batteries", [])
    async_process_device_inventory(hass, entry, "battery", empty)
    entry.runtime_data.battery_coordinator.verified_inventory_ids = (
        async_process_device_inventory(hass, entry, "battery", empty)
    )

    assert _repair_exists(hass, _issue_id("battery", entry.entry_id, device.id))
    assert await async_remove_config_entry_device(hass, entry, device)


async def test_unrelated_device_does_not_scan_entity_registry(
    hass: HomeAssistant, frank_energie_setup
) -> None:
    """Unrelated service or hardware devices are filtered before entity scans."""
    entry = await frank_energie_setup()
    _register_device(hass, entry, "vehicle", "vehicle_name")
    with patch.object(
        er,
        "async_entries_for_device",
        side_effect=AssertionError("Must filter by marker before scanning"),
    ):
        async_process_device_inventory(
            hass, entry, "battery", _inventory("batteries", [])
        )
