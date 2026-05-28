import pytest
from unittest.mock import MagicMock
from homeassistant.config_entries import ConfigEntry

from custom_components.frank_energie.const import (
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
    DATA_BATTERY_DETAILS,
    DATA_PV_SYSTEMS,
    DATA_PV_SUMMARY,
)
from custom_components.frank_energie.switch import (
    FrankEnergieSmartChargingSwitch,
    FrankEnergieSmartTradingSwitch,
    FrankEnergieSmartFeedInSwitch,
    FrankEnergieBatteryTradingSwitch,
    FrankEnergiePvSteeringSwitch,
)
from python_frank_energie.models import (
    UserSmartFeedInStatus,
    SmartBatteryDetails,
    SmartPvSystems,
    SmartPvSystem,
    SmartPvSystemSummary,
)


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.data = {}
    return coordinator


@pytest.fixture
def mock_config_entry():
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    return entry


def test_smart_charging_switch(mock_coordinator, mock_config_entry):
    """Test the smart charging switch is_on property."""
    switch = FrankEnergieSmartChargingSwitch(mock_coordinator, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert switch.is_on is None

    # Test when active (dict payload)
    mock_coordinator.data = {
        DATA_USER: MagicMock(smartCharging={"isActivated": True})
    }
    assert switch.is_on is True

    # Test when inactive (dict payload)
    mock_coordinator.data = {
        DATA_USER: MagicMock(smartCharging={"isActivated": False})
    }
    assert switch.is_on is False

    # Test with object attribute
    mock_user = MagicMock()
    mock_user.smartCharging = MagicMock()
    mock_user.smartCharging.isActivated = True
    mock_coordinator.data = {DATA_USER: mock_user}
    assert switch.is_on is True


def test_smart_trading_switch(mock_coordinator, mock_config_entry):
    """Test the smart trading switch is_on property."""
    switch = FrankEnergieSmartTradingSwitch(mock_coordinator, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert switch.is_on is None

    # Test when active (dict payload)
    mock_coordinator.data = {
        DATA_USER: MagicMock(smartTrading={"isActivated": True})
    }
    assert switch.is_on is True

    # Test when inactive (dict payload)
    mock_coordinator.data = {
        DATA_USER: MagicMock(smartTrading={"isActivated": False})
    }
    assert switch.is_on is False


def test_smart_feed_in_switch(mock_coordinator, mock_config_entry):
    """Test the smart feed-in switch is_on property."""
    switch = FrankEnergieSmartFeedInSwitch(mock_coordinator, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert switch.is_on is None

    # Test when active (dict payload)
    mock_coordinator.data = {
        DATA_USER_SMART_FEED_IN: {"isActivated": True}
    }
    assert switch.is_on is True

    # Test when inactive (object payload)
    mock_feed_in = MagicMock(spec=UserSmartFeedInStatus)
    mock_feed_in.is_activated = False
    mock_coordinator.data = {DATA_USER_SMART_FEED_IN: mock_feed_in}
    assert switch.is_on is False


def test_battery_trading_switch(mock_coordinator, mock_config_entry):
    """Test the battery self-consumption trading switch is_on property."""
    battery_id = "test_battery_id"
    switch = FrankEnergieBatteryTradingSwitch(mock_coordinator, mock_config_entry, battery_id)

    # Test when details are missing
    mock_coordinator.data = {}
    assert switch.is_on is None

    # Test when details have our battery, with setting active
    mock_detail = MagicMock(spec=SmartBatteryDetails)
    mock_detail.smart_battery = MagicMock()
    mock_detail.smart_battery.id = battery_id
    mock_detail.smart_battery.settings = MagicMock()
    mock_detail.smart_battery.settings.self_consumption_trading_allowed = True

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_detail]}
    assert switch.is_on is True

    # Test when details have a different battery id
    mock_detail.smart_battery.id = "other_battery"
    assert switch.is_on is None


def test_pv_steering_switch(mock_coordinator, mock_config_entry):
    """Test the PV steering switch is_on property."""
    system_id = "pv_sys_1"
    
    # Mock systems_obj for constructor
    mock_system = MagicMock(spec=SmartPvSystem)
    mock_system.id = system_id
    mock_system.brand = "SolarEdge"
    mock_system.model = "SE3000"
    mock_system.display_name = "Tuin PV"
    mock_system.inverter_serial_numbers = ["SE123456"]
    
    mock_systems = MagicMock(spec=SmartPvSystems)
    mock_systems.systems = [mock_system]
    mock_coordinator.data = {DATA_PV_SYSTEMS: mock_systems}

    switch = FrankEnergiePvSteeringSwitch(mock_coordinator, mock_config_entry, system_id)

    # Test when summary says active
    mock_summary = MagicMock(spec=SmartPvSystemSummary)
    mock_summary.steering_status = "ACTIVE"
    mock_coordinator.data = {
        DATA_PV_SYSTEMS: mock_systems,
        DATA_PV_SUMMARY: {system_id: mock_summary}
    }
    assert switch.is_on is True

    # Test when summary says stopped, but system list says active
    mock_summary.steering_status = "STOPPED"
    mock_system.steering_status = "ACTIVE"
    assert switch.is_on is False

    # Test fallback to systems_obj when summary has no steering_status
    mock_summary.steering_status = None
    mock_system.steering_status = "STEERING"
    assert switch.is_on is True

    mock_system.steering_status = "INACTIVE"
    assert switch.is_on is False


@pytest.mark.asyncio
async def test_switch_actions(mock_coordinator, mock_config_entry):
    """Test that turn_on and turn_off methods run without exceptions (logging warnings)."""
    switches = [
        FrankEnergieSmartChargingSwitch(mock_coordinator, mock_config_entry),
        FrankEnergieSmartTradingSwitch(mock_coordinator, mock_config_entry),
        FrankEnergieSmartFeedInSwitch(mock_coordinator, mock_config_entry),
        FrankEnergieBatteryTradingSwitch(mock_coordinator, mock_config_entry, "bat1"),
        FrankEnergiePvSteeringSwitch(mock_coordinator, mock_config_entry, "pv1"),
    ]

    for switch in switches:
        await switch.async_turn_on()
        await switch.async_turn_off()
