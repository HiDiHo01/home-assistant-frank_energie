from python_frank_energie.domain import SmartBatteryMode
import pytest
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.frank_energie.const import DATA_BATTERY_DETAILS
from custom_components.frank_energie.select import (
    SELECT_DESCRIPTIONS,
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
    mock_battery.smart_battery.settings.battery_mode = (
        SmartBatteryMode.SELF_CONSUMPTION_MIX
    )

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}

    entity = FrankEnergieBatteryModeSelect(
        mock_coordinator,
        mock_config_entry,
        battery_id,
        next(d for d in SELECT_DESCRIPTIONS if d.key == "battery_mode"),
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
    mock_battery.smart_battery.settings.battery_mode = (
        SmartBatteryMode.SELF_CONSUMPTION_MIX
    )

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}
    mock_coordinator.api.smart_battery_update_settings.return_value = True

    entity = FrankEnergieBatteryModeSelect(
        mock_coordinator,
        mock_config_entry,
        battery_id,
        next(d for d in SELECT_DESCRIPTIONS if d.key == "battery_mode"),
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
    mock_battery.smart_battery.settings.battery_mode = SmartBatteryMode.TRADING

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}

    entity = FrankEnergieBatteryStrategySelect(
        mock_coordinator,
        mock_config_entry,
        battery_id,
        next(d for d in SELECT_DESCRIPTIONS if d.key == "battery_strategy"),
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
    mock_battery.smart_battery.settings.battery_mode = SmartBatteryMode.TRADING

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}
    mock_coordinator.api.smart_battery_update_settings.return_value = True

    entity = FrankEnergieBatteryStrategySelect(
        mock_coordinator,
        mock_config_entry,
        battery_id,
        next(d for d in SELECT_DESCRIPTIONS if d.key == "battery_strategy"),
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
    mock_battery.smart_battery.settings.battery_mode = (
        SmartBatteryMode.SELF_CONSUMPTION_MIX
    )

    entity = FrankEnergieBatteryStrategySelect(
        mock_coordinator,
        mock_config_entry,
        battery_id,
        next(d for d in SELECT_DESCRIPTIONS if d.key == "battery_strategy"),
    )

    # Unavailable when no battery details exist
    mock_coordinator.data = {}
    assert entity.available is False

    # Unavailable when battery mode is not TRADING
    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}
    assert entity.available is False

    # Available when battery mode is TRADING
    mock_battery.smart_battery.settings.battery_mode = SmartBatteryMode.TRADING
    assert entity.available is True


# --- Real-hass layer -------------------------------------------------------
#
# The battery-select tests above call ``entity.async_select_option`` directly
# on a hand-rolled ``mock_coordinator``. The resolution select is always
# present (no auth needed), so it can go through a full ``async_setup`` and
# HA's own ``select.select_option`` option validation.

_RESOLUTION_SELECT = "select.frank_energie_settings_resolution"


async def test_resolution_select_registers_with_real_hass(
    hass: HomeAssistant, frank_energie_setup
) -> None:
    """The resolution select reaches the state machine at the default option
    with the two real resolutions offered."""
    entry = await frank_energie_setup()

    state = hass.states.get(_RESOLUTION_SELECT)
    assert state is not None
    assert state.state == "pt15m"
    assert state.attributes["options"] == ["pt15m", "pt60m"]
    registry_entry = er.async_get(hass).async_get(_RESOLUTION_SELECT)
    assert registry_entry is not None
    assert registry_entry.config_entry_id == entry.entry_id


async def test_resolution_select_option_goes_through_ha_validation(
    hass: HomeAssistant, frank_energie_setup
) -> None:
    """``select.select_option`` persists a valid resolution to the entry
    options (unauthenticated path) and rejects an unknown one with
    ``ServiceValidationError`` before the entity is ever called."""
    entry = await frank_energie_setup()

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": _RESOLUTION_SELECT, "option": "pt60m"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(_RESOLUTION_SELECT).state == "pt60m"
    assert entry.options["resolution"] == "PT60M"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": _RESOLUTION_SELECT, "option": "pt5m"},
            blocking=True,
        )
    assert hass.states.get(_RESOLUTION_SELECT).state == "pt60m"


# TODO: real-hass coverage for the auth-gated battery selects
# (FrankEnergieBatteryModeSelect, FrankEnergieBatteryStrategySelect). They only
# appear once the battery coordinator has authenticated data, so exercising
# them through the real select platform + select.select_option needs an
# operationName->JSON dispatcher on aioclient_mock fed by python-frank-energie's
# smart_battery_details.json fixture, or a patched
# custom_components.frank_energie.FrankEnergie + MockEntityPlatform (see
# test_binary_sensor.py). Until then their option handling stays at the
# direct-call unit level above.
