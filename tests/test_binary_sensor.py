import pytest
from unittest.mock import MagicMock
from homeassistant.config_entries import ConfigEntry

from custom_components.frank_energie.const import (
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
)
from custom_components.frank_energie.binary_sensor import (
    FrankEnergieSmartChargingBinarySensor,
    FrankEnergieSmartTradingBinarySensor,
    FrankEnergieSmartFeedInBinarySensor,
    FrankEnergieSmartHvacBinarySensor,
    _build_dynamic_smart_batteries_descriptions,
    FrankEnergieBinarySensor,
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


def test_battery_self_consumption_trading_binary_sensor(mock_coordinator, mock_config_entry):
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
