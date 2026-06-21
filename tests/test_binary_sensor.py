from unittest.mock import MagicMock

from custom_components.frank_energie.const import (
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
    DATA_PV_SYSTEMS,
)
from custom_components.frank_energie.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    _build_battery_descriptions,
    FrankEnergieBinarySensor,
)


def test_smart_charging_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart charging binary sensor."""
    # smartChargingisActivated is index 0
    desc = BINARY_SENSOR_DESCRIPTIONS[0]
    sensor = FrankEnergieBinarySensor(mock_coordinator, desc, mock_config_entry)

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
    mock_coordinator.data = {DATA_USER: mock_user}
    assert sensor.is_on is False

    # Test with object attributes
    mock_user.smartCharging = MagicMock()
    mock_user.smartCharging.isActivated = True
    assert sensor.is_on is True

    mock_user.smartCharging.isActivated = False
    assert sensor.is_on is False


def test_smart_trading_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart trading binary sensor."""
    # smartTradingisActivated is index 1
    desc = BINARY_SENSOR_DESCRIPTIONS[1]
    sensor = FrankEnergieBinarySensor(mock_coordinator, desc, mock_config_entry)

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
    mock_coordinator.data = {DATA_USER: mock_user}
    assert sensor.is_on is False

    # Test with object attributes
    mock_user.smartTrading = MagicMock()
    mock_user.smartTrading.isActivated = True
    assert sensor.is_on is True


def test_smart_feed_in_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart feed-in binary sensor."""
    # smart_feed_in is index 2
    desc = BINARY_SENSOR_DESCRIPTIONS[2]
    sensor = FrankEnergieBinarySensor(mock_coordinator, desc, mock_config_entry)

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
    class MockFeedIn:
        def __init__(self, is_activated: bool) -> None:
            self.is_activated = is_activated

    mock_feed_in = MockFeedIn(True)
    mock_coordinator.data = {DATA_USER_SMART_FEED_IN: mock_feed_in}
    assert sensor.is_on is True

    mock_feed_in.is_activated = False
    assert sensor.is_on is False


def test_smart_hvac_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart HVAC binary sensor."""
    # smart_hvac is index 3
    desc = BINARY_SENSOR_DESCRIPTIONS[3]
    sensor = FrankEnergieBinarySensor(mock_coordinator, desc, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert sensor.is_on is None
    assert sensor.available is False

    # Test when active (dict payload inside mock object)
    mock_user = MagicMock()
    mock_user.smartHvac = {"isActivated": True}
    mock_coordinator.data = {DATA_USER: mock_user}
    assert sensor.is_on is True
    assert sensor.available is True

    # Test when inactive (dict payload inside mock object)
    mock_user.smartHvac = {"isActivated": False}
    mock_coordinator.data = {DATA_USER: mock_user}
    assert sensor.is_on is False
    assert sensor.available is True

    # Test with object attributes
    mock_user.smartHvac = MagicMock()
    mock_user.smartHvac.isActivated = True
    assert sensor.is_on is True
    assert sensor.available is True

    # Test when smartHvac is None
    mock_user.smartHvac = None
    assert sensor.is_on is None
    assert sensor.available is False

    # Test attributes (dict payload)
    mock_user.smartHvac = {
        "isActivated": True,
        "isAvailableInCountry": True,
        "userCreatedAt": "2026-06-20T17:00:00Z",
        "userId": "test-user-id",
    }
    mock_coordinator.data = {DATA_USER: mock_user}
    attrs = sensor.extra_state_attributes
    assert attrs["available_in_country"] is True
    assert attrs["user_created_at"] == "2026-06-20T17:00:00Z"
    assert attrs["user_id"] == "test-user-id"

    # Test attributes (object payload)
    mock_user.smartHvac = MagicMock()
    mock_user.smartHvac.isAvailableInCountry = True
    mock_user.smartHvac.userCreatedAt = "2026-06-20T17:00:00Z"
    mock_user.smartHvac.userId = "test-user-id"
    mock_coordinator.data = {DATA_USER: mock_user}
    attrs = sensor.extra_state_attributes
    assert attrs["available_in_country"] is True
    assert attrs["user_created_at"] == "2026-06-20T17:00:00Z"
    assert attrs["user_id"] == "test-user-id"


def test_battery_self_consumption_trading_binary_sensor(
    mock_coordinator, mock_config_entry
):
    """Test battery self-consumption trading binary sensor builds and evaluates properly."""
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = "bat_123"
    mock_battery.smart_battery.brand = "Sunsynk"
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.self_consumption_trading_allowed = True

    mock_coordinator.data = {"smart_battery_details": [mock_battery]}

    descriptions = _build_battery_descriptions(mock_coordinator.data)
    assert len(descriptions) == 1
    desc = descriptions[0]

    assert desc.child_device_id == "bat_123"
    assert desc.child_device_name == "Sunsynk Battery"
    assert desc.child_device_manufacturer == "Sunsynk"

    sensor = FrankEnergieBinarySensor(mock_coordinator, desc, mock_config_entry)
    # Test value_fn evaluation
    assert sensor.is_on is True


def test_smart_pv_systems_binary_sensor(mock_coordinator, mock_config_entry):
    """Test the smart PV systems binary sensor."""
    # smart_pv_systems is index 5
    desc = BINARY_SENSOR_DESCRIPTIONS[5]
    mock_coordinator.last_update_success = True
    sensor = FrankEnergieBinarySensor(mock_coordinator, desc, mock_config_entry)

    # Test when data is missing
    mock_coordinator.data = {}
    assert sensor.available is True
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
