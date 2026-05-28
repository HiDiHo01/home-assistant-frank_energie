import pytest
from unittest.mock import AsyncMock, MagicMock
from homeassistant.config_entries import ConfigEntry

from custom_components.frank_energie.const import (
    DOMAIN,
    DATA_ENODE_VEHICLES,
)
from custom_components.frank_energie.switch import (
    FrankEnergieEnodeSmartChargingSwitch,
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


def test_enode_smart_charging_switch_properties(mock_coordinator, mock_config_entry):
    """Test properties of the Enode smart charging switch."""
    vehicle_id = "vehicle_1"

    # Test when data is missing
    mock_coordinator.data = {}
    switch = FrankEnergieEnodeSmartChargingSwitch(
        mock_coordinator, mock_config_entry, vehicle_id
    )
    assert switch.is_on is None
    assert switch.unique_id == f"{DOMAIN}_{vehicle_id}_enode_smart_charging"

    # Test with vehicle matching ID but without charge settings
    mock_vehicle = MagicMock()
    mock_vehicle.id = vehicle_id
    mock_vehicle.charge_settings = None
    mock_vehicle.information = MagicMock()
    mock_vehicle.information.brand = "Tesla"
    mock_vehicle.information.model = "Model 3"

    mock_vehicles = MagicMock()
    mock_vehicles.vehicles = [mock_vehicle]
    mock_coordinator.data = {DATA_ENODE_VEHICLES: mock_vehicles}

    switch = FrankEnergieEnodeSmartChargingSwitch(
        mock_coordinator, mock_config_entry, vehicle_id
    )
    assert switch.is_on is None
    assert switch.device_info["identifiers"] == {(DOMAIN, vehicle_id)}
    assert switch.device_info["manufacturer"] == "Tesla"
    assert switch.device_info["model"] == "Model 3"
    assert switch.device_info["name"] == "Tesla Model 3"

    # Test with vehicle matching ID and with charge settings
    mock_settings = MagicMock()
    mock_settings.is_smart_charging_enabled = True
    mock_vehicle.charge_settings = mock_settings
    assert switch.is_on is True

    # Test when disabled
    mock_settings.is_smart_charging_enabled = False
    assert switch.is_on is False


@pytest.mark.asyncio
async def test_enode_smart_charging_switch_actions(
    mock_coordinator, mock_config_entry
):
    """Test turn_on and turn_off actions."""
    vehicle_id = "vehicle_1"
    mock_vehicle = MagicMock()
    mock_vehicle.id = vehicle_id
    mock_vehicle.information = None
    mock_vehicle.charge_settings = MagicMock()

    mock_vehicles = MagicMock()
    mock_vehicles.vehicles = [mock_vehicle]
    mock_coordinator.data = {DATA_ENODE_VEHICLES: mock_vehicles}

    switch = FrankEnergieEnodeSmartChargingSwitch(
        mock_coordinator, mock_config_entry, vehicle_id
    )

    # Test turn on success
    mock_coordinator.api.enode_enable_smart_charging.return_value = True
    await switch.async_turn_on()
    mock_coordinator.api.enode_enable_smart_charging.assert_called_once()
    mock_coordinator.async_request_refresh.assert_called_once()

    # Test turn off success
    mock_coordinator.api.enode_disable_smart_charging.return_value = True
    await switch.async_turn_off()
    mock_coordinator.api.enode_disable_smart_charging.assert_called_once()
    assert mock_coordinator.async_request_refresh.call_count == 2
