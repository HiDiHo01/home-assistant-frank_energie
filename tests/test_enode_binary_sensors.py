"""Regression tests for Enode vehicle and charger binary sensor migration."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from pytest_homeassistant.common import MockConfigEntry, MockEntityPlatform

from custom_components.frank_energie.binary_sensor import (
    ENODE_CHARGER_BINARY_SENSOR_DESCRIPTIONS,
    ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS,
    EnodeChargerBinarySensor,
    EnodeVehicleBinarySensor,
    _migrate_legacy_enode_sensor,
)
from custom_components.frank_energie.const import (
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DOMAIN,
)
from custom_components.frank_energie.sensor import (
    ENODE_CHARGER_SENSOR_TYPES,
    ENODE_VEHICLE_SENSOR_TYPES,
)

VEHICLE_FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "can_smart_charge": ("canSmartCharge",),
    "is_reachable": ("isReachable",),
    "is_charging": ("chargeState", "isCharging"),
    "is_fully_charged": ("chargeState", "isFullyCharged"),
    "is_plugged_in": ("chargeState", "isPluggedIn"),
    "smart_charging_enabled": ("chargeSettings", "isSmartChargingEnabled"),
    "solar_charging_enabled": ("chargeSettings", "isSolarChargingEnabled"),
}

CHARGER_FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "can_smart_charge": ("can_smart_charge",),
    "is_plugged_in": ("charge_state", "is_plugged_in"),
    "is_reachable": ("is_reachable",),
    "is_charging": ("charge_state", "is_charging"),
    "is_smart_charging_enabled": ("charge_settings", "is_smart_charging_enabled"),
    "is_solar_charging_enabled": ("charge_settings", "is_solar_charging_enabled"),
}


def _vehicle_data(value: object, field_path: tuple[str, ...]) -> dict:
    """Build a vehicle inventory with the indicated API field."""
    vehicle: dict = {
        "id": "vehicle-test",
        "information": {"brand": "Test", "model": "EV"},
        "chargeState": {},
        "chargeSettings": {},
    }
    target = vehicle
    for field in field_path[:-1]:
        target = target[field]
    target[field_path[-1]] = value
    return vehicle


def _charger_data(value: object, field_path: tuple[str, ...]) -> MagicMock:
    """Build a charger with nullable nested data and an explicit status."""
    charger = MagicMock()
    charger.id = "charger-test"
    charger.information = {"brand": "Test", "model": "Charge"}
    charger.charge_state = SimpleNamespace()
    charger.charge_settings = SimpleNamespace()
    target = charger
    for field in field_path[:-1]:
        target = getattr(target, field)
    setattr(target, field_path[-1], value)
    return charger


def _coordinator(data: dict) -> MagicMock:
    """Create a successful Enode coordinator without network requests."""
    coordinator = MagicMock()
    coordinator.config_entry.entry_id = "test-entry"
    coordinator.last_update_success = True
    coordinator.data = data
    return coordinator


@pytest.mark.parametrize("key", tuple(VEHICLE_FIELD_PATHS))
@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), (None, None), ("false", None), (1, None)],
)
def test_vehicle_binary_states_are_strict(
    key: str, value: object, expected: bool | None
) -> None:
    """Unknown, malformed, and false API states are never conflated."""
    desc = next(d for d in ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS if d.key == key)
    vehicle = _vehicle_data(value, VEHICLE_FIELD_PATHS[key])
    sensor = EnodeVehicleBinarySensor(
        _coordinator({DATA_ENODE_VEHICLES: SimpleNamespace(vehicles=[vehicle])}),
        desc,
        vehicle,
    )
    assert sensor.is_on is expected
    assert sensor.unique_id == f"{DOMAIN}_vehicle-test_{key}"
    assert sensor.available is True
    assert sensor.device_info["identifiers"]


@pytest.mark.parametrize("key", tuple(CHARGER_FIELD_PATHS))
@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), (None, None), ("false", None), (1, None)],
)
def test_charger_binary_states_are_strict(
    key: str, value: object, expected: bool | None
) -> None:
    """Each charger field reports only a verified boolean."""
    desc = next(d for d in ENODE_CHARGER_BINARY_SENSOR_DESCRIPTIONS if d.key == key)
    charger = _charger_data(value, CHARGER_FIELD_PATHS[key])
    sensor = EnodeChargerBinarySensor(
        _coordinator({DATA_ENODE_CHARGERS: SimpleNamespace(chargers=[charger])}),
        desc,
        charger,
    )
    assert sensor.is_on is expected
    assert sensor.unique_id == f"{DOMAIN}_charger-test_{key}"
    assert sensor.available is True


def test_enode_binary_device_classes_and_icons() -> None:
    """Use connectivity/plug/charging classes only for their actual meanings."""
    vehicle = {d.key: d for d in ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS}
    charger = {d.key: d for d in ENODE_CHARGER_BINARY_SENSOR_DESCRIPTIONS}
    assert len(vehicle) == 7
    assert len(charger) == 6
    for descriptions in (vehicle, charger):
        assert descriptions["is_reachable"].device_class == (
            BinarySensorDeviceClass.CONNECTIVITY
        )
        assert descriptions["is_plugged_in"].device_class == (
            BinarySensorDeviceClass.PLUG
        )
        assert descriptions["is_charging"].device_class == (
            BinarySensorDeviceClass.BATTERY_CHARGING
        )
        assert descriptions["can_smart_charge"].device_class is None
        assert all(d.icon and d.icon.startswith("mdi:") for d in descriptions.values())
    assert vehicle["is_fully_charged"].device_class is None
    assert vehicle["smart_charging_enabled"].device_class is None
    assert vehicle["solar_charging_enabled"].device_class is None
    assert vehicle["can_smart_charge"].entity_category == EntityCategory.DIAGNOSTIC
    assert (
        vehicle["smart_charging_enabled"].entity_category == EntityCategory.DIAGNOSTIC
    )
    assert charger["can_smart_charge"].entity_category == EntityCategory.DIAGNOSTIC

    assert not set(vehicle).intersection({d.key for d in ENODE_VEHICLE_SENSOR_TYPES})
    assert not set(charger).intersection({d.key for d in ENODE_CHARGER_SENSOR_TYPES})


def test_all_enode_binary_names_are_localized() -> None:
    """Every moved status has a name in canonical and shipped locale files."""
    root = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    descriptions = (
        *ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS,
        *ENODE_CHARGER_BINARY_SENSOR_DESCRIPTIONS,
    )
    keys = {d.translation_key for d in descriptions}
    for name in (
        "strings.json",
        "en.json",
        "nl.json",
        "nl-BE.json",
        "fr.json",
        "fr-BE.json",
    ):
        file_path = root / ("translations" if name != "strings.json" else "") / name
        data = json.loads(file_path.read_text(encoding="utf-8"))["entity"]
        for key in keys:
            assert key in data["binary_sensor"]
            assert data["binary_sensor"][key]["name"]
            assert key not in data["sensor"]


def test_missing_enode_devices_are_unavailable() -> None:
    """Unavailable hardware must not keep showing stale on/off values."""
    vehicle = _vehicle_data(True, VEHICLE_FIELD_PATHS["is_charging"])
    charger = _charger_data(True, CHARGER_FIELD_PATHS["is_charging"])
    vehicle_coordinator = _coordinator(
        {DATA_ENODE_VEHICLES: SimpleNamespace(vehicles=[vehicle])}
    )
    charger_coordinator = _coordinator(
        {DATA_ENODE_CHARGERS: SimpleNamespace(chargers=[charger])}
    )
    vehicle_sensor = EnodeVehicleBinarySensor(
        vehicle_coordinator,
        next(
            d
            for d in ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS
            if d.key == "is_charging"
        ),
        vehicle,
    )
    charger_sensor = EnodeChargerBinarySensor(
        charger_coordinator,
        next(
            d
            for d in ENODE_CHARGER_BINARY_SENSOR_DESCRIPTIONS
            if d.key == "is_charging"
        ),
        charger,
    )
    assert vehicle_sensor.is_on is True
    assert charger_sensor.is_on is True
    vehicle_coordinator.data = {DATA_ENODE_VEHICLES: SimpleNamespace(vehicles=[])}
    charger_coordinator.data = {DATA_ENODE_CHARGERS: SimpleNamespace(chargers=[])}
    assert vehicle_sensor.available is False
    assert charger_sensor.available is False
    assert vehicle_sensor.is_on is None
    assert charger_sensor.is_on is None


@pytest.mark.asyncio
async def test_binary_vehicle_exposes_home_assistant_states(
    hass: HomeAssistant,
) -> None:
    """A real HA entity platform translates booleans to on/off/unknown."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="test-binary-vehicle")
    entry.add_to_hass(hass)
    vehicle = _vehicle_data(True, VEHICLE_FIELD_PATHS["is_charging"])
    coordinator = _coordinator(
        {DATA_ENODE_VEHICLES: SimpleNamespace(vehicles=[vehicle])}
    )
    coordinator.config_entry = entry
    description = next(
        d for d in ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS if d.key == "is_charging"
    )
    sensor = EnodeVehicleBinarySensor(coordinator, description, vehicle)
    platform = MockEntityPlatform(hass, domain="binary_sensor", platform_name=DOMAIN)
    platform.config_entry = entry
    await platform.async_add_entities([sensor])
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, sensor.unique_id
    )
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get(entity_id).attributes["device_class"] == "battery_charging"

    vehicle["chargeState"]["isCharging"] = False
    sensor.async_write_ha_state()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"

    vehicle["chargeState"]["isCharging"] = None
    sensor.async_write_ha_state()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unknown"


@pytest.mark.asyncio
async def test_migration_preserves_user_disable_and_is_idempotent(
    hass: HomeAssistant,
) -> None:
    """Copy an old sensor's user choice before removing its own registry entry."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="binary-migration")
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    unique_id = f"{DOMAIN}_vehicle-test_is_charging"
    old = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        config_entry=entry,
        disabled_by=RegistryEntryDisabler.USER,
        suggested_object_id="custom_vehicle_charging",
    )
    registry.async_update_entity(old.entity_id, icon="mdi:my-user-icon")
    description = next(
        d for d in ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS if d.key == "is_charging"
    )
    _migrate_legacy_enode_sensor(hass, entry, unique_id, description)
    new_id = registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id)
    assert new_id == "binary_sensor.custom_vehicle_charging"
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None
    migrated = registry.async_get(new_id)
    assert migrated.disabled_by == RegistryEntryDisabler.USER
    assert migrated.icon == "mdi:my-user-icon"
    _migrate_legacy_enode_sensor(hass, entry, unique_id, description)
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id) == new_id


@pytest.mark.asyncio
async def test_migration_never_removes_another_entry(hass: HomeAssistant) -> None:
    """Prevent cross-config-entry cleanup even with a colliding provider ID."""
    owner = MockConfigEntry(domain=DOMAIN, unique_id="owner")
    foreign = MockConfigEntry(domain=DOMAIN, unique_id="foreign")
    owner.add_to_hass(hass)
    foreign.add_to_hass(hass)
    registry = er.async_get(hass)
    unique_id = f"{DOMAIN}_vehicle-test_is_charging"
    old = registry.async_get_or_create("sensor", DOMAIN, unique_id, config_entry=owner)
    description = next(
        d for d in ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS if d.key == "is_charging"
    )
    _migrate_legacy_enode_sensor(hass, foreign, unique_id, description)
    assert registry.async_get(old.entity_id) is not None
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id) is None
