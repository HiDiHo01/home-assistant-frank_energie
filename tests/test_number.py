import pytest
from unittest.mock import MagicMock

from custom_components.frank_energie.const import DATA_BATTERY_DETAILS
from custom_components.frank_energie.number import FrankEnergieBatteryThresholdNumber


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
