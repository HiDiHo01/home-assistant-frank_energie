import pytest
from unittest.mock import MagicMock, AsyncMock

from custom_components.frank_energie.const import (
    DATA_BATTERY_DETAILS,
    DATA_ENODE_VEHICLES,
    DATA_ENODE_CHARGERS,
)
from custom_components.frank_energie.number import (
    FrankEnergieBatteryThresholdNumber,
    FrankEnergieEnodeChargeLimitNumber,
)


def test_battery_threshold_number_properties(mock_coordinator, mock_config_entry):
    """Test properties of battery threshold number entity."""
    battery_id = "bat_123"
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = battery_id
    mock_battery.smart_battery.brand = "Sessy"
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.self_consumption_trading_threshold_price = 0.25
    mock_battery.smart_battery.settings.battery_mode = "SELF_CONSUMPTION_MIX"

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}

    entity = FrankEnergieBatteryThresholdNumber(
        mock_coordinator, mock_config_entry, battery_id
    )
    assert entity.native_value == 0.25
    assert entity.available is True
    assert entity.native_min_value == 0.20
    assert entity.native_max_value == 0.40
    assert entity.native_step == 0.05
    assert entity.native_unit_of_measurement == "€/kWh"
    assert entity.device_info["manufacturer"] == "Sessy"
    assert entity.device_info["model"] == "Smart Battery"


@pytest.mark.asyncio
async def test_battery_threshold_number_action(mock_coordinator, mock_config_entry):
    """Test battery threshold number update action."""
    battery_id = "bat_123"
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = battery_id
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.self_consumption_trading_threshold_price = 0.25
    mock_battery.smart_battery.settings.battery_mode = "SELF_CONSUMPTION_MIX"

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}
    mock_coordinator.api.smart_battery_update_settings.return_value = True

    entity = FrankEnergieBatteryThresholdNumber(
        mock_coordinator, mock_config_entry, battery_id
    )
    await entity.async_set_native_value(0.35)

    mock_coordinator.api.smart_battery_update_settings.assert_called_once_with(
        battery_id, {"selfConsumptionTradingThresholdPrice": 0.35}
    )
    mock_coordinator.async_request_refresh.assert_called_once()


def test_battery_threshold_number_availability(mock_coordinator, mock_config_entry):
    """Test availability logic of battery threshold number entity based on battery mode."""
    battery_id = "bat_123"
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = battery_id
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.self_consumption_trading_threshold_price = 0.25

    entity = FrankEnergieBatteryThresholdNumber(
        mock_coordinator, mock_config_entry, battery_id
    )

    # Unavailable when no battery details exist
    mock_coordinator.data = {}
    assert entity.available is False

    # Unavailable when battery mode is not SELF_CONSUMPTION_MIX
    mock_battery.smart_battery.settings.battery_mode = "TRADING"
    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}
    assert entity.available is False

    # Available when battery mode is SELF_CONSUMPTION_MIX
    mock_battery.smart_battery.settings.battery_mode = "SELF_CONSUMPTION_MIX"
    assert entity.available is True


def test_enode_charge_limit_number_properties(mock_coordinator):
    """Test properties of enode charge limit number entity."""
    vehicle_id = "veh_123"
    mock_vehicle = MagicMock()
    mock_vehicle.id = vehicle_id
    mock_vehicle.information = MagicMock()
    mock_vehicle.information.brand = "Tesla"
    mock_vehicle.information.model = "Model 3"
    mock_vehicle.charge_settings = MagicMock()
    mock_vehicle.charge_settings.min_charge_limit = 20
    mock_vehicle.charge_settings.max_charge_limit = 80

    mock_coordinator.data = {DATA_ENODE_VEHICLES: MagicMock(vehicles=[mock_vehicle])}

    entity = FrankEnergieEnodeChargeLimitNumber(
        mock_coordinator, vehicle_id, "maxChargeLimit", "max_charge_limit", True
    )
    assert entity.native_value == 80
    assert entity.icon == "mdi:battery-charging-80"
    assert entity.native_min_value == 50
    assert entity.native_max_value == 100
    assert entity.native_step == 5
    assert entity.native_unit_of_measurement == "%"
    assert entity.device_info["manufacturer"] == "Tesla"
    assert entity.device_info["model"] == "Model 3"


@pytest.mark.asyncio
async def test_enode_charge_limit_number_action(mock_coordinator):
    """Test enode charge limit number update action."""
    charger_id = "charger_123"
    charge_settings_id = "cs_123"
    mock_charger = MagicMock()
    mock_charger.id = charger_id
    mock_charger.charge_settings = MagicMock()
    mock_charger.charge_settings.id = charge_settings_id
    mock_charger.charge_settings.initial_charge = 10

    mock_coordinator.data = {DATA_ENODE_CHARGERS: MagicMock(chargers=[mock_charger])}

    async def mock_update(dev_id, is_veh, mutations):
        if "initialCharge" in mutations:
            mock_charger.charge_settings.initial_charge = mutations["initialCharge"]
        return True

    mock_coordinator.async_update_enode_charge_settings = AsyncMock(
        side_effect=mock_update
    )

    entity = FrankEnergieEnodeChargeLimitNumber(
        mock_coordinator, charger_id, "initialCharge", "initial_charge", False
    )

    # Verify invalid service values are rejected before any coordinator call is made
    for invalid_val in (-1, 101, 50.9):
        with pytest.raises(ValueError):
            await entity.async_set_native_value(invalid_val)
        mock_coordinator.async_update_enode_charge_settings.assert_not_called()

    await entity.async_set_native_value(25.0)

    mock_coordinator.async_update_enode_charge_settings.assert_called_once_with(
        charger_id, False, {"initialCharge": 25.0}
    )
    assert mock_charger.charge_settings.initial_charge == 25.0


@pytest.mark.asyncio
async def test_enode_charge_limit_validation(mock_coordinator):
    """Test validation of values set on Enode charge limit entities."""
    charger_id = "charger_123"
    mock_charger = MagicMock()
    mock_charger.id = charger_id
    mock_charger.charge_settings = MagicMock()
    mock_charger.charge_settings.initial_charge = 10
    mock_charger.charge_settings.max_charge_limit = 80

    mock_coordinator.data = {DATA_ENODE_CHARGERS: MagicMock(chargers=[mock_charger])}
    mock_coordinator.async_update_enode_charge_settings = AsyncMock(return_value=True)

    # Entity for initialCharge
    initial_charge_entity = FrankEnergieEnodeChargeLimitNumber(
        mock_coordinator, charger_id, "initialCharge", "initial_charge", False
    )

    # Test out of bounds (0-100) for initialCharge
    with pytest.raises(ValueError, match="out of range"):
        await initial_charge_entity.async_set_native_value(105.0)
    with pytest.raises(ValueError, match="out of range"):
        await initial_charge_entity.async_set_native_value(-5.0)

    # Test step mismatch (multiple of 5) for initialCharge
    with pytest.raises(ValueError, match="must be a multiple of"):
        await initial_charge_entity.async_set_native_value(23.0)

    # Entity for maxChargeLimit (requires integer, min 50, max 100, step 5)
    max_limit_entity = FrankEnergieEnodeChargeLimitNumber(
        mock_coordinator, charger_id, "maxChargeLimit", "max_charge_limit", False
    )

    # Test out of bounds for maxChargeLimit
    with pytest.raises(ValueError, match="out of range"):
        await max_limit_entity.async_set_native_value(45.0)

    # Test step mismatch for float value (82.5 % 5 != 0) before truncation
    with pytest.raises(ValueError, match="must be a multiple of"):
        await max_limit_entity.async_set_native_value(82.5)

    # Valid integer setting
    await max_limit_entity.async_set_native_value(85.0)
    mock_coordinator.async_update_enode_charge_settings.assert_called_once_with(
        charger_id, False, {"maxChargeLimit": 85}
    )
