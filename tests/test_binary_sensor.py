from unittest.mock import MagicMock

from custom_components.frank_energie.const import (
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
    DATA_PV_SYSTEMS,
)
from custom_components.frank_energie.binary_sensor import (
    FrankEnergieSmartChargingBinarySensor,
    FrankEnergieSmartTradingBinarySensor,
    FrankEnergieSmartFeedInBinarySensor,
    FrankEnergieSmartHvacBinarySensor,
    _build_dynamic_smart_batteries_descriptions,
    FrankEnergieBinarySensor,
    FrankEnergieSmartPvSystemsSensor,
)


def test_smart_charging_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart charging binary sensor."""
    sensor = FrankEnergieSmartChargingBinarySensor(mock_coordinator, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert sensor.is_on is None

    # Test when active (dict payload inside mock object)
    mock_user = MagicMock()
    mock_user.smartCharging = {"isActivated": True}
    mock_coordinator.data = {DATA_USER: mock_user}
    assert sensor.is_on is True

    # Test when inactive (dict payload inside mock object)
    mock_user.smartCharging = {"isActivated": False}
    assert sensor.is_on is False

    # Test with object attributes
    mock_user.smartCharging = MagicMock()
    mock_user.smartCharging.isActivated = True
    assert sensor.is_on is True

    mock_user.smartCharging.isActivated = False
    assert sensor.is_on is False


def test_smart_trading_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart trading binary sensor."""
    sensor = FrankEnergieSmartTradingBinarySensor(mock_coordinator, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert sensor.is_on is None

    # Test when active (dict payload inside mock object)
    mock_user = MagicMock()
    mock_user.smartTrading = {"isActivated": True}
    mock_coordinator.data = {DATA_USER: mock_user}
    assert sensor.is_on is True

    # Test when inactive (dict payload inside mock object)
    mock_user.smartTrading = {"isActivated": False}
    assert sensor.is_on is False

    # Test with object attributes
    mock_user.smartTrading = MagicMock()
    mock_user.smartTrading.isActivated = True
    assert sensor.is_on is True


def test_smart_feed_in_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart feed-in binary sensor."""
    sensor = FrankEnergieSmartFeedInBinarySensor(mock_coordinator, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert sensor.is_on is None

    # Test when active (dict payload)
    mock_coordinator.data = {DATA_USER_SMART_FEED_IN: {"isActivated": True}}
    assert sensor.is_on is True

    # Test when inactive (dict payload)
    mock_coordinator.data = {DATA_USER_SMART_FEED_IN: {"isActivated": False}}
    assert sensor.is_on is False

    # Test with object attributes
    mock_feed_in = MagicMock()
    mock_feed_in.is_activated = True
    mock_coordinator.data = {DATA_USER_SMART_FEED_IN: mock_feed_in}
    assert sensor.is_on is True

    mock_feed_in.is_activated = False
    assert sensor.is_on is False


def test_smart_hvac_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart HVAC binary sensor."""
    sensor = FrankEnergieSmartHvacBinarySensor(mock_coordinator, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert sensor.is_on is None

    # Test when active (dict payload inside mock object)
    mock_user = MagicMock()
    mock_user.smartHvac = {"isActivated": True}
    mock_coordinator.data = {DATA_USER: mock_user}
    assert sensor.is_on is True

    # Test when inactive (dict payload inside mock object)
    mock_user.smartHvac = {"isActivated": False}
    assert sensor.is_on is False

    # Test with object attributes
    mock_user.smartHvac = MagicMock()
    mock_user.smartHvac.isActivated = True
    assert sensor.is_on is True

    # Test when smartHvac is None
    mock_user.smartHvac = None
    assert sensor.is_on is None


def test_battery_self_consumption_trading_binary_sensor(
    mock_coordinator, mock_config_entry
):
    """Test battery self-consumption trading binary sensor builds and evaluates properly."""
    mock_battery = MagicMock()
    mock_battery.id = "bat_123"
    mock_battery.brand = "Sunsynk"
    mock_battery.settings = MagicMock()
    mock_battery.settings.self_consumption_trading_allowed = True

    descriptions = _build_dynamic_smart_batteries_descriptions([mock_battery])
    assert len(descriptions) == 1
    desc = descriptions[0]

    assert desc.child_device_id == "bat_123"
    assert desc.child_device_name == "Sunsynk Battery"
    assert desc.child_device_manufacturer == "Sunsynk"

    sensor = FrankEnergieBinarySensor(mock_coordinator, desc, mock_config_entry)
    # Test value_fn evaluation
    mock_coordinator.data = {}
    assert sensor.is_on is True


def test_smart_pv_systems_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart PV systems binary sensor."""
    sensor = FrankEnergieSmartPvSystemsSensor(mock_coordinator, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert sensor.available is False
    assert sensor.is_on is False

    # Test when PV systems are present
    mock_pv_system = MagicMock()
    mock_pv_system.id = "pv_123"
    mock_pv_system.display_name = "My Solar Panels"
    mock_pv_system.brand = "Solis"
    mock_pv_system.model = "S5"
    mock_pv_system.onboarding_status = "CONNECTED"

    mock_pv = MagicMock()
    mock_pv.systems = [mock_pv_system]
    mock_coordinator.data = {DATA_PV_SYSTEMS: mock_pv}

    assert sensor.available is True
    assert sensor.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["system_count"] == 1
    assert attrs["systems"][0]["id"] == "pv_123"
    assert attrs["systems"][0]["status"] == "CONNECTED"
