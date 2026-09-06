from types import SimpleNamespace

from python_frank_energie.domain import SmartBatteryMode
import pytest
from unittest.mock import MagicMock, AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.frank_energie.const import (
    DATA_BATTERY_DETAILS,
    DATA_ENODE_VEHICLES,
    DATA_ENODE_CHARGERS,
    DEFAULT_ENERGY_TAX_ODE,
    DEFAULT_ENERGY_TAX_REDUCTION,
    DEFAULT_EXPORT_ELECTRICITY_FEE,
    DEFAULT_MONTHLY_SUBSCRIPTION_FEE,
    DEFAULT_NETWORK_CHARGES,
)
from custom_components.frank_energie.number import (
    CONFIG_NUMBER_DESCRIPTIONS,
    FrankEnergieBatteryThresholdNumber,
    FrankEnergieEnodeChargeLimitNumber,
)


def test_battery_threshold_number_properties(mock_coordinator, mock_config_entry):
    """Test properties of battery threshold number entity."""
    battery_id = "bat_123"
    mock_battery = MagicMock()
    mock_battery.smart_battery = MagicMock()
    mock_battery.smart_battery.id = battery_id
    mock_battery.smart_battery.brand = "Sessy"
    mock_battery.smart_battery.settings = MagicMock()
    mock_battery.smart_battery.settings.self_consumption_trading_threshold_price = 0.25
    mock_battery.smart_battery.settings.battery_mode = (
        SmartBatteryMode.SELF_CONSUMPTION_MIX
    )

    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}

    entity = FrankEnergieBatteryThresholdNumber(
        mock_coordinator, mock_config_entry, battery_id
    )
    assert entity.native_value == pytest.approx(0.25)
    assert entity.available is True
    assert entity.native_min_value == pytest.approx(0.20)
    assert entity.native_max_value == pytest.approx(0.40)
    assert entity.native_step == pytest.approx(0.05)
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
    mock_battery.smart_battery.settings.battery_mode = (
        SmartBatteryMode.SELF_CONSUMPTION_MIX
    )

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
    mock_battery.smart_battery.settings.battery_mode = SmartBatteryMode.TRADING
    mock_coordinator.data = {DATA_BATTERY_DETAILS: [mock_battery]}
    assert entity.available is False

    # Available when battery mode is SELF_CONSUMPTION_MIX
    mock_battery.smart_battery.settings.battery_mode = (
        SmartBatteryMode.SELF_CONSUMPTION_MIX
    )
    assert entity.available is True


def test_enode_charge_limit_number_properties(mock_coordinator):
    """Test properties of enode charge limit number entity."""
    vehicle_id = "veh_123"
    mock_vehicle = MagicMock()
    mock_vehicle.id = vehicle_id
    mock_vehicle.information = MagicMock()
    mock_vehicle.information.brand = "Tesla"
    mock_vehicle.information.model = "Model 3"
    mock_vehicle.charge_settings = MagicMock()
    mock_vehicle.charge_settings.min_charge_limit = 20
    mock_vehicle.charge_settings.max_charge_limit = 80

    mock_coordinator.data = {DATA_ENODE_VEHICLES: MagicMock(vehicles=[mock_vehicle])}

    entity = FrankEnergieEnodeChargeLimitNumber(
        mock_coordinator, vehicle_id, "maxChargeLimit", "max_charge_limit", True
    )
    assert entity.native_value == 80
    assert entity.icon == "mdi:battery-charging-80"
    assert entity.native_min_value == 50
    assert entity.native_max_value == 100
    assert entity.native_step == 5
    assert entity.native_unit_of_measurement == "%"
    assert entity.device_info["manufacturer"] == "Tesla"
    assert entity.device_info["model"] == "Model 3"


@pytest.mark.asyncio
async def test_enode_charge_limit_number_action(mock_coordinator):
    """Test enode charge limit number update action."""
    charger_id = "charger_123"
    charge_settings_id = "cs_123"
    mock_charger = MagicMock()
    mock_charger.id = charger_id
    mock_charger.charge_settings = MagicMock()
    mock_charger.charge_settings.id = charge_settings_id
    mock_charger.charge_settings.initial_charge = 10

    mock_coordinator.data = {DATA_ENODE_CHARGERS: MagicMock(chargers=[mock_charger])}

    async def mock_update(dev_id, is_veh, mutations):
        if "initialCharge" in mutations:
            mock_charger.charge_settings.initial_charge = mutations["initialCharge"]
        return True

    mock_coordinator.async_update_enode_charge_settings = AsyncMock(
        side_effect=mock_update
    )

    entity = FrankEnergieEnodeChargeLimitNumber(
        mock_coordinator, charger_id, "initialCharge", "initial_charge", False
    )

    # Verify invalid service values are rejected before any coordinator call is made
    for invalid_val in (-1, 101, 50.9):
        with pytest.raises(ValueError):
            await entity.async_set_native_value(invalid_val)
        mock_coordinator.async_update_enode_charge_settings.assert_not_called()

    await entity.async_set_native_value(25.0)

    mock_coordinator.async_update_enode_charge_settings.assert_called_once_with(
        charger_id, False, {"initialCharge": 25.0}
    )
    assert mock_charger.charge_settings.initial_charge == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_enode_charge_limit_validation(mock_coordinator):
    """Test validation of values set on Enode charge limit entities."""
    charger_id = "charger_123"
    mock_charger = MagicMock()
    mock_charger.id = charger_id
    mock_charger.charge_settings = MagicMock()
    mock_charger.charge_settings.initial_charge = 10
    mock_charger.charge_settings.max_charge_limit = 80

    mock_coordinator.data = {DATA_ENODE_CHARGERS: MagicMock(chargers=[mock_charger])}
    mock_coordinator.async_update_enode_charge_settings = AsyncMock(return_value=True)

    # Entity for initialCharge
    initial_charge_entity = FrankEnergieEnodeChargeLimitNumber(
        mock_coordinator, charger_id, "initialCharge", "initial_charge", False
    )

    # Test out of bounds (0-100) for initialCharge
    with pytest.raises(ValueError, match="out of range"):
        await initial_charge_entity.async_set_native_value(105.0)
    with pytest.raises(ValueError, match="out of range"):
        await initial_charge_entity.async_set_native_value(-5.0)

    # Test step mismatch (multiple of 5) for initialCharge
    with pytest.raises(ValueError, match="must be a multiple of"):
        await initial_charge_entity.async_set_native_value(23.0)

    # Entity for maxChargeLimit (requires integer, min 50, max 100, step 5)
    max_limit_entity = FrankEnergieEnodeChargeLimitNumber(
        mock_coordinator, charger_id, "maxChargeLimit", "max_charge_limit", False
    )

    # Test out of bounds for maxChargeLimit
    with pytest.raises(ValueError, match="out of range"):
        await max_limit_entity.async_set_native_value(45.0)

    # Test step mismatch for float value (82.5 % 5 != 0) before truncation
    with pytest.raises(ValueError, match="must be a multiple of"):
        await max_limit_entity.async_set_native_value(82.5)

    # Valid integer setting
    await max_limit_entity.async_set_native_value(85.0)
    mock_coordinator.async_update_enode_charge_settings.assert_called_once_with(
        charger_id, False, {"maxChargeLimit": 85}
    )


@pytest.mark.parametrize("description", CONFIG_NUMBER_DESCRIPTIONS, ids=lambda d: d.key)
def test_config_number_default_within_declared_range(description) -> None:
    """A config number's shipped default must sit inside its own min/max.

    Otherwise the value shown on a fresh install is one HA's ``number.set_value``
    rejects with ``ServiceValidationError`` (``value < min_value``), so it can
    never be re-entered from the UI. Regression: ``export_electricity_fee``
    shipped a ``-0.035090`` default with a ``0.0`` lower bound.
    """
    default = description.value_fn(SimpleNamespace(options={}))
    assert description.native_min_value <= default <= description.native_max_value


# --- Real-hass layer -------------------------------------------------------
#
# The tests above build entities against a hand-rolled ``mock_coordinator`` and
# call ``entity.async_set_native_value`` directly, which never reaches Home
# Assistant's own ``number.set_value`` range validation. These drive the real
# NUMBER platform through a full ``async_setup`` so that validation runs.

# option key, entity_id, shipped default, an in-range value, an out-of-range value
_CONFIG_NUMBERS = [
    (
        "monthly_subscription_fee",
        "number.frank_energie_costs_monthly_subscription_fee",
        DEFAULT_MONTHLY_SUBSCRIPTION_FEE,
        5.5,
        20.0,
    ),
    (
        "energy_tax_ode",
        "number.frank_energie_costs_energy_tax_ode",
        DEFAULT_ENERGY_TAX_ODE,
        30.0,
        60.0,
    ),
    (
        "energy_tax_reduction",
        "number.frank_energie_costs_energy_tax_reduction",
        DEFAULT_ENERGY_TAX_REDUCTION,
        -40.0,
        20.0,
    ),
    (
        "network_charges",
        "number.frank_energie_costs_network_charges",
        DEFAULT_NETWORK_CHARGES,
        40.0,
        60.0,
    ),
    (
        "export_electricity_fee",
        "number.frank_energie_costs_export_electricity_fee",
        DEFAULT_EXPORT_ELECTRICITY_FEE,
        -0.02,
        100.0,
    ),
]


async def test_config_numbers_register_with_real_hass(
    hass: HomeAssistant, frank_energie_setup
) -> None:
    """The always-present cost-configuration numbers reach the state machine at
    their option-backed shipped defaults and are tied to the config entry."""
    entry = await frank_energie_setup()
    reg = er.async_get(hass)

    for _, entity_id, default, _, _ in _CONFIG_NUMBERS:
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert float(state.state) == default
        assert reg.async_get(entity_id).config_entry_id == entry.entry_id


@pytest.mark.parametrize(
    ("option_key", "entity_id", "in_range", "out_of_range"),
    [(k, eid, lo, hi) for k, eid, _, lo, hi in _CONFIG_NUMBERS],
)
async def test_config_number_set_value_goes_through_ha_range_validation(
    hass: HomeAssistant,
    frank_energie_setup,
    option_key: str,
    entity_id: str,
    in_range: float,
    out_of_range: float,
) -> None:
    """``number.set_value`` persists an in-range value to the entry options and
    rejects an out-of-range one with ``ServiceValidationError`` -- the check
    the direct ``async_set_native_value`` unit tests bypass."""
    entry = await frank_energie_setup()

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id, "value": in_range},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == str(in_range)
    assert entry.options[option_key] == in_range

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": out_of_range},
            blocking=True,
        )
    # The rejected write left the in-range value untouched.
    assert hass.states.get(entity_id).state == str(in_range)


# TODO: real-hass coverage for the auth-gated number entities
# (FrankEnergieBatteryThresholdNumber, FrankEnergieEnodeChargeLimitNumber).
# Those are only reachable once the battery/vehicle/charger coordinators have
# authenticated data, so a full async_setup here needs an operationName->JSON
# dispatcher on aioclient_mock fed by python-frank-energie's own response
# fixtures (smart_battery_details.json, enode_vehicles.json, ...), or a lighter
# variant that patches custom_components.frank_energie.FrankEnergie and adds the
# entities via MockEntityPlatform (see test_binary_sensor.py). Until then the
# range/step checks on those two classes stay at the direct-call unit level
# above and never see HA's own number.set_value validation.
