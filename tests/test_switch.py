import pytest
from unittest.mock import MagicMock

from custom_components.frank_energie.const import (
    DOMAIN,
    DATA_ENODE_VEHICLES,
)
from custom_components.frank_energie.switch import (
    FrankEnergieEnodeSmartChargingSwitch,
)


def test_enode_smart_charging_switch_properties(
    mock_coordinator, mock_config_entry, create_mock_vehicle
):
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
    mock_vehicle = create_mock_vehicle(
        vehicle_id=vehicle_id, brand="Tesla", model="Model 3"
    )
    mock_vehicle.charge_settings = None

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
    mock_coordinator, mock_config_entry, create_mock_vehicle
):
    """Test turn_on and turn_off actions."""
    vehicle_id = "vehicle_1"
    mock_vehicle = create_mock_vehicle(
        vehicle_id=vehicle_id,
        brand=None,
        model=None,
        charge_settings_kwargs={"is_smart_charging_enabled": False},
    )

    mock_vehicles = MagicMock()
    mock_vehicles.vehicles = [mock_vehicle]
    mock_coordinator.data = {DATA_ENODE_VEHICLES: mock_vehicles}

    def mock_optimistic_update(vehicle_id, enabled):
        mock_vehicle.charge_settings.is_smart_charging_enabled = enabled

    mock_coordinator.update_vehicle_smart_charging_optimistic = mock_optimistic_update

    switch = FrankEnergieEnodeSmartChargingSwitch(
        mock_coordinator, mock_config_entry, vehicle_id
    )

    # Test turn on success
    mock_coordinator.api.enode_update_vehicle_charge_settings.return_value = True
    await switch.async_turn_on()
    mock_coordinator.api.enode_update_vehicle_charge_settings.assert_called_once()
    mock_coordinator.async_request_refresh.assert_called_once()
    # Verify optimistic update succeeded
    assert mock_vehicle.charge_settings.is_smart_charging_enabled is True

    # Verify input_data had isSmartChargingEnabled = True
    call_arg = mock_coordinator.api.enode_update_vehicle_charge_settings.call_args[0][0]
    assert call_arg["isSmartChargingEnabled"] is True
    assert call_arg["id"] == "set_123"

    # Reset mock
    mock_coordinator.api.enode_update_vehicle_charge_settings.reset_mock()
    mock_coordinator.async_request_refresh.reset_mock()

    # Test turn off success
    mock_vehicle.charge_settings.is_smart_charging_enabled = (
        True  # Simulate switch state change
    )
    await switch.async_turn_off()
    mock_coordinator.api.enode_update_vehicle_charge_settings.assert_called_once()
    mock_coordinator.async_request_refresh.assert_called_once()
    # Verify optimistic update succeeded
    assert mock_vehicle.charge_settings.is_smart_charging_enabled is False

    call_arg = mock_coordinator.api.enode_update_vehicle_charge_settings.call_args[0][0]
    assert call_arg["isSmartChargingEnabled"] is False

    # Reset mock
    mock_coordinator.api.enode_update_vehicle_charge_settings.reset_mock()
    mock_coordinator.async_request_refresh.reset_mock()

    # Test turn on failure
    mock_coordinator.api.enode_update_vehicle_charge_settings.return_value = False
    await switch.async_turn_on()
    mock_coordinator.api.enode_update_vehicle_charge_settings.assert_called_once()
    mock_coordinator.async_request_refresh.assert_not_called()
