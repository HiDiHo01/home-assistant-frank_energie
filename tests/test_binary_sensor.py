from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.frank_energie.const import (
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
    DATA_PV_SYSTEMS,
    DOMAIN,
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
    """Use strictly typed UserSmartHvac activation instead of equipment state."""
    desc = next(item for item in BINARY_SENSOR_DESCRIPTIONS if item.key == "smart_hvac")
    sensor = FrankEnergieBinarySensor(mock_coordinator, desc, mock_config_entry)
    mock_coordinator.last_update_success = True

    mock_coordinator.data = {}
    assert sensor.is_on is None
    assert sensor.available is False
    assert desc.device_class is None

    mock_user = SimpleNamespace(smartHvac={"isActivated": True, "isAvailableOnSite": False})
    mock_coordinator.data = {DATA_USER: mock_user}
    assert sensor.is_on is True
    assert sensor.available is True
    assert sensor.extra_state_attributes == {"available_on_site": False}

    mock_user.smartHvac = {"isActivated": False, "hasAcceptedTerms": True}
    assert sensor.is_on is False
    assert sensor.available is True
    assert sensor.extra_state_attributes == {"has_accepted_terms": True}

    for invalid_activation in (None, "false", 0, 1, [], {}):
        mock_user.smartHvac = {"isActivated": invalid_activation}
        assert sensor.is_on is None
        assert sensor.available is False

    mock_user.smartHvac = SimpleNamespace(
        isActivated=True,
        isAvailableInCountry=True,
        is_available_on_site=True,
        has_accepted_terms=True,
        userId="synthetic-private-id",
        userCreatedAt="2026-11-09T00:00:00Z",
    )
    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "available_in_country": True,
        "available_on_site": True,
        "has_accepted_terms": True,
    }

    mock_coordinator.last_update_success = False
    assert sensor.available is False
    mock_coordinator.last_update_success = True
    assert sensor.available is True


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


# --- Real-hass layer -------------------------------------------------------
#
# The tests above read ``sensor.is_on`` / ``sensor.extra_state_attributes``
# straight off the entity. These add the entity through a real
# ``MockEntityPlatform`` so Home Assistant resolves ``is_on`` into an actual
# on/off/unknown state and serialises the attributes into the state machine --
# the path that would reject a bad ``device_class`` or an unserialisable
# attribute value.


async def _add_through_real_platform(
    hass: HomeAssistant, entry: MockConfigEntry, *sensors: FrankEnergieBinarySensor
) -> None:
    platform = MockEntityPlatform(
        hass, domain=BINARY_SENSOR_DOMAIN, platform_name=DOMAIN
    )
    platform.config_entry = entry
    await platform.async_add_entities(list(sensors))
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, unique_key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        BINARY_SENSOR_DOMAIN, DOMAIN, f"frank_energie_{unique_key}"
    )
    assert entity_id is not None, unique_key
    return entity_id


async def test_smart_feature_binary_sensors_reach_the_state_machine(
    hass: HomeAssistant,
) -> None:
    """The service activation state must not have a running device class."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="frank_energie")
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        DATA_USER: SimpleNamespace(
            smartHvac={
                "isActivated": True,
                "isAvailableInCountry": True,
                "userId": "synthetic-private-id",
                "isAvailableOnSite": True,
            },
        ),
        DATA_USER_SMART_FEED_IN: {"isActivated": False},
    }

    hvac = next(d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == "smart_hvac")
    feed_in = next(d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == "smart_feed_in")
    await _add_through_real_platform(
        hass,
        entry,
        FrankEnergieBinarySensor(coordinator, hvac, entry),
        FrankEnergieBinarySensor(coordinator, feed_in, entry),
    )

    hvac_state = hass.states.get(_entity_id(hass, "smart_hvac"))
    assert hvac_state is not None
    assert hvac_state.state == "on"
    assert "device_class" not in hvac_state.attributes
    assert hvac_state.attributes["available_in_country"] is True
    assert hvac_state.attributes["available_on_site"] is True
    assert "user_id" not in hvac_state.attributes

    feed_in_state = hass.states.get(_entity_id(hass, "smart_feed_in"))
    assert feed_in_state is not None
    assert feed_in_state.state == "off"


async def test_missing_smart_feature_data_resolves_to_unknown(
    hass: HomeAssistant,
) -> None:
    """No user data -> ``is_on`` is None -> HA renders the state as unknown,
    not a stale value or an exception during the state write."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="frank_energie")
    entry.add_to_hass(hass)

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {}

    trading = next(
        d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == "smartTradingisActivated"
    )
    await _add_through_real_platform(
        hass, entry, FrankEnergieBinarySensor(coordinator, trading, entry)
    )

    state = hass.states.get(_entity_id(hass, "smartTradingisActivated"))
    assert state is not None
    assert state.state == "unknown"


# TODO: real-hass coverage for the per-battery binary sensors built by
# _build_battery_descriptions(). Their attr_fn reads sb.capacity /
# sb.max_charge_power / settings.battery_mode, so serialising them into the
# state machine needs faithful SmartBatteryDetails objects -- build them from
# python-frank-energie's smart_battery_details.json fixture (via the model
# from_dict parsers) rather than MagicMock, then add via _add_through_real_platform
# above. A full authenticated async_setup would also work but needs the
# operationName->JSON dispatcher described in the other platform test files.
