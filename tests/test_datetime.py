from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock
from homeassistant.config_entries import ConfigEntry

from custom_components.frank_energie.const import (
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
)
from custom_components.frank_energie.datetime import (
    FrankEnergieVehicleDeadlineEntity,
    FrankEnergieChargerDeadlineEntity,
)


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.data = {}
    coordinator.api = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.fixture
def mock_config_entry():
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    return entry


def test_vehicle_deadline_properties(mock_coordinator, mock_config_entry):
    """Test properties of the vehicle deadline entity."""
    vehicle_id = "veh_123"

    # Test when data is missing
    mock_coordinator.data = {}
    entity = FrankEnergieVehicleDeadlineEntity(
        mock_coordinator, mock_config_entry, vehicle_id
    )
    assert entity.native_value is None

    # Test when vehicle exists with settings
    mock_vehicle = MagicMock()
    mock_vehicle.id = vehicle_id
    mock_vehicle.information.brand = "Audi"
    mock_vehicle.information.model = "e-tron"
    mock_vehicle.charge_settings = MagicMock()

    test_dt = datetime(2026, 5, 28, 7, 0, tzinfo=timezone.utc)
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
async def test_vehicle_deadline_set_value(mock_coordinator, mock_config_entry):
    """Test setting value for the vehicle deadline entity."""
    vehicle_id = "veh_123"
    mock_vehicle = MagicMock()
    mock_vehicle.id = vehicle_id
    mock_vehicle.information = None
    mock_vehicle.charge_settings = MagicMock()
    mock_vehicle.charge_settings.id = "set_123"
    mock_vehicle.charge_settings.is_smart_charging_enabled = True
    mock_vehicle.charge_settings.is_solar_charging_enabled = False
    mock_vehicle.charge_settings.min_charge_limit = 20
    mock_vehicle.charge_settings.max_charge_limit = 80
    mock_vehicle.charge_settings.hour_monday = 420
    mock_vehicle.charge_settings.hour_tuesday = 420
    mock_vehicle.charge_settings.hour_wednesday = 420
    mock_vehicle.charge_settings.hour_thursday = 420
    mock_vehicle.charge_settings.hour_friday = 420
    mock_vehicle.charge_settings.hour_saturday = 420
    mock_vehicle.charge_settings.hour_sunday = 420

    mock_vehicles = MagicMock()
    mock_vehicles.vehicles = [mock_vehicle]
    mock_coordinator.data = {DATA_ENODE_VEHICLES: mock_vehicles}

    entity = FrankEnergieVehicleDeadlineEntity(
        mock_coordinator, mock_config_entry, vehicle_id
    )

    mock_coordinator.api.enode_update_vehicle_charge_settings.return_value = True

    new_deadline = datetime(2026, 5, 29, 8, 30, tzinfo=timezone.utc)
    await entity.async_set_value(new_deadline)

    expected_payload = {
        "id": "set_123",
        "deadline": new_deadline.isoformat(),
        "isSmartChargingEnabled": True,
        "isSolarChargingEnabled": False,
        "minChargeLimit": 20,
        "maxChargeLimit": 80,
        "hourMonday": 420,
        "hourTuesday": 420,
        "hourWednesday": 420,
        "hourThursday": 420,
        "hourFriday": 420,
        "hourSaturday": 420,
        "hourSunday": 420,
    }

    mock_coordinator.api.enode_update_vehicle_charge_settings.assert_called_once_with(
        expected_payload
    )
    mock_coordinator.async_request_refresh.assert_called_once()


def test_charger_deadline_properties(mock_coordinator, mock_config_entry):
    """Test properties of the charger deadline entity."""
    charger_id = "chg_123"

    # Test when data is missing
    mock_coordinator.data = {}
    entity = FrankEnergieChargerDeadlineEntity(
        mock_coordinator, mock_config_entry, charger_id
    )
    assert entity.native_value is None

    # Test when charger exists with settings
    mock_charger = MagicMock()
    mock_charger.id = charger_id
    mock_charger.information = {"brand": "Wallbox", "model": "Copper"}
    mock_charger.charge_settings = MagicMock()

    test_dt = datetime(2026, 5, 28, 13, 15, tzinfo=timezone.utc)
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
async def test_charger_deadline_set_value(mock_coordinator, mock_config_entry):
    """Test setting value for the charger deadline entity."""
    charger_id = "chg_123"
    mock_charger = MagicMock()
    mock_charger.id = charger_id
    mock_charger.information = None
    mock_charger.charge_settings = MagicMock()
    mock_charger.charge_settings.id = "set_456"
    mock_charger.charge_settings.is_smart_charging_enabled = False
    mock_charger.charge_settings.is_solar_charging_enabled = True
    mock_charger.charge_settings.min_charge_limit = 10
    mock_charger.charge_settings.max_charge_limit = 90
    mock_charger.charge_settings.hour_monday = 480
    mock_charger.charge_settings.hour_tuesday = 480
    mock_charger.charge_settings.hour_wednesday = 480
    mock_charger.charge_settings.hour_thursday = 480
    mock_charger.charge_settings.hour_friday = 480
    mock_charger.charge_settings.hour_saturday = 480
    mock_charger.charge_settings.hour_sunday = 480

    mock_chargers = MagicMock()
    mock_chargers.chargers = [mock_charger]
    mock_coordinator.data = {DATA_ENODE_CHARGERS: mock_chargers}

    entity = FrankEnergieChargerDeadlineEntity(
        mock_coordinator, mock_config_entry, charger_id
    )

    mock_coordinator.api.enode_update_charger_charge_settings.return_value = True

    new_deadline = datetime(2026, 5, 29, 7, 0, tzinfo=timezone.utc)
    await entity.async_set_value(new_deadline)

    expected_payload = {
        "id": "set_456",
        "deadline": new_deadline.isoformat(),
        "isSmartChargingEnabled": False,
        "isSolarChargingEnabled": True,
        "minChargeLimit": 10,
        "maxChargeLimit": 90,
        "hourMonday": 480,
        "hourTuesday": 480,
        "hourWednesday": 480,
        "hourThursday": 480,
        "hourFriday": 480,
        "hourSaturday": 480,
        "hourSunday": 480,
    }

    mock_coordinator.api.enode_update_charger_charge_settings.assert_called_once_with(
        expected_payload
    )
    mock_coordinator.async_request_refresh.assert_called_once()
