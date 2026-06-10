import pytest
from unittest.mock import MagicMock

from custom_components.frank_energie.const import DATA_BATTERY_DETAILS
from custom_components.frank_energie.select import (
    FrankEnergieBatteryModeSelect,
    FrankEnergieBatteryStrategySelect,
)


def test_battery_mode_select_properties(mock_coordinator, mock_config_entry):
    """Test properties of battery mode select entity."""
    battery_id = "bat_123"
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = battery_id
    mock_battery.smart_battery.brand = "Sessy"
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.battery_mode = "SELF_CONSUMPTION_MIX"

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}

    entity = FrankEnergieBatteryModeSelect(
        mock_coordinator, mock_config_entry, battery_id
    )
    assert entity.current_option == "self_consumption_mix"
    assert entity.device_info["manufacturer"] == "Sessy"
    assert entity.device_info["model"] == "Smart Battery"


@pytest.mark.asyncio
async def test_battery_mode_select_action(mock_coordinator, mock_config_entry):
    """Test battery mode selection action."""
    battery_id = "bat_123"
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = battery_id
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.battery_mode = "SELF_CONSUMPTION_MIX"

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}
    mock_coordinator.api.smart_battery_update_settings.return_value = True

    entity = FrankEnergieBatteryModeSelect(
        mock_coordinator, mock_config_entry, battery_id
    )
    await entity.async_select_option("trading")

    mock_coordinator.api.smart_battery_update_settings.assert_called_once_with(
        battery_id, {"batteryMode": "TRADING"}
    )
    mock_coordinator.async_request_refresh.assert_called_once()


def test_battery_strategy_select_properties(mock_coordinator, mock_config_entry):
    """Test properties of battery strategy select entity."""
    battery_id = "bat_123"
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = battery_id
    mock_battery.smart_battery.brand = "Sessy"
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.imbalance_trading_strategy = "AGGRESSIVE"
    mock_battery.smart_battery.settings.battery_mode = "TRADING"

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}

    entity = FrankEnergieBatteryStrategySelect(
        mock_coordinator, mock_config_entry, battery_id
    )
    assert entity.current_option == "aggressive"
    assert entity.available is True


@pytest.mark.asyncio
async def test_battery_strategy_select_action(mock_coordinator, mock_config_entry):
    """Test battery strategy selection action."""
    battery_id = "bat_123"
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = battery_id
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.imbalance_trading_strategy = "BALANCED"
    mock_battery.smart_battery.settings.battery_mode = "TRADING"

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}
    mock_coordinator.api.smart_battery_update_settings.return_value = True

    entity = FrankEnergieBatteryStrategySelect(
        mock_coordinator, mock_config_entry, battery_id
    )
    await entity.async_select_option("aggressive")

    mock_coordinator.api.smart_battery_update_settings.assert_called_once_with(
        battery_id, {"imbalanceTradingStrategy": "AGGRESSIVE"}
    )
    mock_coordinator.async_request_refresh.assert_called_once()


def test_battery_strategy_select_availability(mock_coordinator, mock_config_entry):
    """Test availability logic of battery strategy select entity based on battery mode."""
    battery_id = "bat_123"
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = battery_id
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.battery_mode = "SELF_CONSUMPTION_MIX"

    entity = FrankEnergieBatteryStrategySelect(
        mock_coordinator, mock_config_entry, battery_id
    )

    # Unavailable when no battery details exist
    mock_coordinator.data = {}
    assert entity.available is False

    # Unavailable when battery mode is not TRADING
    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}
    assert entity.available is False

    # Available when battery mode is TRADING
    mock_battery.smart_battery.settings.battery_mode = "TRADING"
    assert entity.available is True
