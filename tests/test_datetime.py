from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock

from custom_components.frank_energie.const import (
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
)
from custom_components.frank_energie.datetime import (
    FrankEnergieVehicleDeadlineEntity,
    FrankEnergieChargerDeadlineEntity,
)


def test_vehicle_deadline_properties(
    mock_coordinator, mock_config_entry, create_mock_vehicle
):
    """Test properties of the vehicle deadline entity."""
    vehicle_id = "veh_123"

    # Test when data is missing
    mock_coordinator.data = {}
    entity = FrankEnergieVehicleDeadlineEntity(
        mock_coordinator, mock_config_entry, vehicle_id
    )
    assert entity.native_value is None

    # Test when vehicle exists with settings
    test_dt = datetime(2026, 5, 28, 7, 0, tzinfo=timezone.utc)
    mock_vehicle = create_mock_vehicle(
        vehicle_id=vehicle_id, brand="Audi", model="e-tron"
    )
    mock_vehicle.charge_settings.calculated_deadline = test_dt

    mock_vehicles = MagicMock()
    mock_vehicles.vehicles = [mock_vehicle]
    mock_coordinator.data = {DATA_ENODE_VEHICLES: mock_vehicles}

    entity = FrankEnergieVehicleDeadlineEntity(
        mock_coordinator, mock_config_entry, vehicle_id
    )
    assert entity.native_value == test_dt
    assert entity.device_info["manufacturer"] == "Audi"
    assert entity.device_info["model"] == "e-tron"


@pytest.mark.asyncio
async def test_vehicle_deadline_set_value(
    mock_coordinator, mock_config_entry, create_mock_vehicle
):
    """Test setting value for the vehicle deadline entity."""
    vehicle_id = "veh_123"
    mock_vehicle = create_mock_vehicle(
        vehicle_id=vehicle_id,
        brand=None,
        model=None,
        charge_settings_kwargs={"is_smart_charging_enabled": True},
    )

    mock_vehicles = MagicMock()
    mock_vehicles.vehicles = [mock_vehicle]
    mock_coordinator.data = {DATA_ENODE_VEHICLES: mock_vehicles}

    entity = FrankEnergieVehicleDeadlineEntity(
        mock_coordinator, mock_config_entry, vehicle_id
    )

    mock_coordinator.async_update_enode_charge_settings.return_value = True

    new_deadline = datetime(2026, 5, 29, 8, 30, tzinfo=timezone.utc)
    await entity.async_set_value(new_deadline)

    mock_coordinator.async_update_enode_charge_settings.assert_called_once_with(
        vehicle_id, True, {"deadline": new_deadline.isoformat()}
    )
    mock_coordinator.async_request_refresh.assert_called_once()


def test_charger_deadline_properties(
    mock_coordinator, mock_config_entry, create_mock_charger
):
    """Test properties of the charger deadline entity."""
    charger_id = "chg_123"

    # Test when data is missing
    mock_coordinator.data = {}
    entity = FrankEnergieChargerDeadlineEntity(
        mock_coordinator, mock_config_entry, charger_id
    )
    assert entity.native_value is None

    # Test when charger exists with settings
    test_dt = datetime(2026, 5, 28, 13, 15, tzinfo=timezone.utc)
    mock_charger = create_mock_charger(
        charger_id=charger_id, brand="Wallbox", model="Copper"
    )
    mock_charger.charge_settings.calculated_deadline = test_dt

    mock_chargers = MagicMock()
    mock_chargers.chargers = [mock_charger]
    mock_coordinator.data = {DATA_ENODE_CHARGERS: mock_chargers}

    entity = FrankEnergieChargerDeadlineEntity(
        mock_coordinator, mock_config_entry, charger_id
    )
    assert entity.native_value == test_dt
    assert entity.device_info["manufacturer"] == "Wallbox"
    assert entity.device_info["model"] == "Copper"


@pytest.mark.asyncio
async def test_charger_deadline_set_value(
    mock_coordinator, mock_config_entry, create_mock_charger
):
    """Test setting value for the charger deadline entity."""
    charger_id = "chg_123"
    mock_charger = create_mock_charger(
        charger_id=charger_id,
        brand=None,
        model=None,
        charge_settings_kwargs={
            "id": "set_456",
            "is_solar_charging_enabled": True,
            "min_charge_limit": 10,
            "max_charge_limit": 90,
            "hour_monday": 480,
            "hour_tuesday": 480,
            "hour_wednesday": 480,
            "hour_thursday": 480,
            "hour_friday": 480,
            "hour_saturday": 480,
            "hour_sunday": 480,
        },
    )

    mock_chargers = MagicMock()
    mock_chargers.chargers = [mock_charger]
    mock_coordinator.data = {DATA_ENODE_CHARGERS: mock_chargers}

    entity = FrankEnergieChargerDeadlineEntity(
        mock_coordinator, mock_config_entry, charger_id
    )

    mock_coordinator.async_update_enode_charge_settings.return_value = True

    new_deadline = datetime(2026, 5, 29, 7, 0, tzinfo=timezone.utc)
    await entity.async_set_value(new_deadline)

    mock_coordinator.async_update_enode_charge_settings.assert_called_once_with(
        charger_id, False, {"deadline": new_deadline.isoformat()}
    )
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_vehicle_deadline_set_value_failure(
    mock_coordinator, mock_config_entry, create_mock_vehicle
):
    """Test vehicle deadline set_value handles API failure by not refreshing."""
    vehicle_id = "veh_123"
    mock_vehicle = create_mock_vehicle(
        vehicle_id=vehicle_id,
        brand=None,
        model=None,
        charge_settings_kwargs={"is_smart_charging_enabled": True},
    )

    mock_vehicles = MagicMock()
    mock_vehicles.vehicles = [mock_vehicle]
    mock_coordinator.data = {DATA_ENODE_VEHICLES: mock_vehicles}

    entity = FrankEnergieVehicleDeadlineEntity(
        mock_coordinator, mock_config_entry, vehicle_id
    )

    mock_coordinator.async_update_enode_charge_settings.return_value = False

    new_deadline = datetime(2026, 5, 29, 7, 0, tzinfo=timezone.utc)
    await entity.async_set_value(new_deadline)

    mock_coordinator.async_update_enode_charge_settings.assert_called_once()
    mock_coordinator.async_request_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_charger_deadline_set_value_failure(
    mock_coordinator, mock_config_entry, create_mock_charger
):
    """Test charger deadline set_value handles API failure by not refreshing."""
    charger_id = "chg_123"
    mock_charger = create_mock_charger(
        charger_id=charger_id,
        brand=None,
        model=None,
        charge_settings_kwargs={
            "id": "set_456",
            "is_solar_charging_enabled": True,
            "min_charge_limit": 10,
            "max_charge_limit": 90,
            "hour_monday": 480,
            "hour_tuesday": 480,
            "hour_wednesday": 480,
            "hour_thursday": 480,
            "hour_friday": 480,
            "hour_saturday": 480,
            "hour_sunday": 480,
        },
    )

    mock_chargers = MagicMock()
    mock_chargers.chargers = [mock_charger]
    mock_coordinator.data = {DATA_ENODE_CHARGERS: mock_chargers}

    entity = FrankEnergieChargerDeadlineEntity(
        mock_coordinator, mock_config_entry, charger_id
    )

    mock_coordinator.async_update_enode_charge_settings.return_value = False

    new_deadline = datetime(2026, 5, 29, 7, 0, tzinfo=timezone.utc)
    await entity.async_set_value(new_deadline)

    mock_coordinator.async_update_enode_charge_settings.assert_called_once()
    mock_coordinator.async_request_refresh.assert_not_called()
