from datetime import datetime, timedelta

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry
from homeassistant.util import dt
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    MockConfigEntry,
)

from custom_components.frank_energie import const
from tests.utils import ResponseMocks


@pytest.fixture
async def frank_energie_config_entry(hass: HomeAssistant, enable_custom_integrations):
    config_entry = MockConfigEntry(
        domain=const.DOMAIN,
        data={},
        unique_id=const.UNIQUE_ID,
    )
    config_entry.add_to_hass(hass)

    return config_entry


def price_generator(base: float, var: float) -> list:
    """
    Return a list of 24 prices which has two peaks of price `base` and 3 bottoms of `base - 6 * var`.
    :param base:
    :param var:
    :return:
    """
    return [round(base - var * abs(6 - (i % 12)), 3) for i in range(24)]


async def enable_all_sensors(hass):
    """Enable all sensors of the integration."""
    er = entity_registry.async_get(hass)
    for entry in list(er.entities.values()):
        if entry.domain == "sensor" and entry.disabled_by is not None:
            er.async_update_entity(entry.entity_id, disabled_by=None)
    await hass.async_block_till_done()
    await trigger_update(hass)


async def trigger_update(hass, delta_seconds=config_entries.RELOAD_AFTER_UPDATE_DELAY):
    """Trigger a reload of the data"""
    async_fire_time_changed(
        hass,
        dt.utcnow() + timedelta(seconds=delta_seconds + 1),
    )
    await hass.async_block_till_done()


async def test_sensors(
    freezer,
    aioclient_responses: ResponseMocks,
    frank_energie_config_entry: MockConfigEntry,
    hass: HomeAssistant,
):
    import zoneinfo

    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = zoneinfo.ZoneInfo("Europe/Amsterdam")
    now = datetime.now(tz).replace(hour=14, minute=15, second=0, microsecond=0)
    freezer.move_to(now)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    aioclient_responses.add(
        start_of_day,
        [0.2] * 10 + [0.25, 0.3, 0.5, 0.4] + [0.15] * 10,
        [1.75] * 6 + [1.23] * 18,
    )
    aioclient_responses.add(
        start_of_day + timedelta(days=1),
        [0.3] * 12 + [0.15] * 12,
        [1.23] * 24,
    )
    aioclient_responses.cyclic()

    await hass.config_entries.async_setup(frank_energie_config_entry.entry_id)
    await hass.async_block_till_done()

    # Check the state of all sensors which are enabled by default
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_price_all_in"
        ).state
        == "0.15"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_market_price"
        ).state
        == "0.105"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_price_including_tax"
        ).state
        == "0.1125"
    )
    assert (
        hass.states.get("sensor.frank_energie_gasprices_current_gas_price_all_in").state
        == "1.23"
    )
    assert (
        hass.states.get("sensor.frank_energie_gasprices_current_gas_market_price").state
        == "0.861"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_gasprices_current_gas_price_including_tax"
        ).state
        == "0.9225"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_gasprices_lowest_gas_price_today_all_in"
        ).state
        == "1.23"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_gasprices_highest_gas_price_today_all_in"
        ).state
        == "1.75"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_lowest_electricity_price_today_all_in"
        ).state
        == "0.15"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_highest_electricity_price_today_all_in"
        ).state
        == "0.5"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_average_electricity_price_today_all_in"
        ).state
        == "0.20625"
    )

    # Check the default values of these sensors which are enabled by default
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_vat_price"
        ).state
        == "0.0075"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_sourcing_markup"
        ).state
        == "0.015"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_tax_only"
        ).state
        == "0.0225"
    )
    assert (
        hass.states.get("sensor.frank_energie_gasprices_current_gas_vat_price").state
        == "0.1845"
    )
    assert (
        hass.states.get(
            "sensor.frank_energie_gasprices_current_gas_sourcing_price"
        ).state
        == "0.123"
    )
    assert (
        hass.states.get("sensor.frank_energie_gasprices_current_gas_tax_only").state
        == "0.0615"
    )


async def test_sensors_get_data_of_current_hour(
    freezer,
    aioclient_responses: ResponseMocks,
    frank_energie_config_entry: MockConfigEntry,
    hass: HomeAssistant,
):
    import zoneinfo

    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = zoneinfo.ZoneInfo("Europe/Amsterdam")
    now = datetime.now(tz).replace(hour=5, minute=15, second=0, microsecond=0)
    freezer.move_to(now)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    aioclient_responses.add(
        start_of_day, [0.3] * 12 + [0.15] * 12, [1.75] * 6 + [1.23] * 18
    )
    aioclient_responses.add(
        start_of_day + timedelta(days=1),
        [0.25] * 12 + [0.1] * 12,
        [1.23] * 6 + [1.11] * 18,
    )
    aioclient_responses.cyclic()

    await hass.config_entries.async_setup(frank_energie_config_entry.entry_id)
    await hass.async_block_till_done()

    # Check the state at 5:15
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_price_all_in"
        ).state
        == "0.3"
    )
    assert (
        hass.states.get("sensor.frank_energie_gasprices_current_gas_price_all_in").state
        == "1.75"
    )

    # Change time to 12:15
    now = datetime.now(tz).replace(hour=12, minute=15, second=0, microsecond=0)
    freezer.move_to(now)
    await trigger_update(hass, 7 * 3600)

    assert (
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_price_all_in"
        ).state
        == "0.15"
    )
    assert (
        hass.states.get("sensor.frank_energie_gasprices_current_gas_price_all_in").state
        == "1.23"
    )


async def test_sensors_no_data_for_tomorrow(
    freezer,
    aioclient_responses: ResponseMocks,
    frank_energie_config_entry: MockConfigEntry,
    hass: HomeAssistant,
):
    import zoneinfo

    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = zoneinfo.ZoneInfo("Europe/Amsterdam")
    now = datetime.now(tz).replace(hour=20, minute=0, second=0, microsecond=0)
    freezer.move_to(now)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # First response is for today's data, 2nd for tomorrow's data
    aioclient_responses.add(start_of_day, [0.3] * 24, [1.75] * 6 + [1.23] * 18)
    aioclient_responses.add(start_of_day + timedelta(days=1), [], [])

    await hass.config_entries.async_setup(frank_energie_config_entry.entry_id)
    await hass.async_block_till_done()

    # Check the state at 5:15
    assert (
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_price_all_in"
        ).state
        == "0.3"
    )
    assert (
        hass.states.get("sensor.frank_energie_gasprices_current_gas_price_all_in").state
        == "1.23"
    )


async def test_sensors_hour_price_attr(
    freezer,
    aioclient_responses: ResponseMocks,
    frank_energie_config_entry: MockConfigEntry,
    hass: HomeAssistant,
):
    import zoneinfo

    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = zoneinfo.ZoneInfo("Europe/Amsterdam")
    now = datetime.now(tz).replace(hour=20, minute=0, second=0, microsecond=0)
    freezer.move_to(now)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # First response is for today's data, 2nd for tomorrow's data
    aioclient_responses.add(
        start_of_day, price_generator(0.25, 0.05), gas_prices=[1.75] * 6 + [1.23] * 18
    )
    aioclient_responses.add(
        start_of_day + timedelta(days=1),
        price_generator(0.3, 0.02),
        gas_prices=[1.23] * 6 + [0.75] * 18,
    )

    await hass.config_entries.async_setup(frank_energie_config_entry.entry_id)
    await hass.async_block_till_done()

    # Check the all in electricity prices
    price_attr = [
        a["price"]
        for a in hass.states.get(
            "sensor.frank_energie_prices_current_electricity_price_all_in"
        ).attributes["prices"]
    ]
    assert price_attr == price_generator(0.25, 0.05) + price_generator(0.3, 0.02)

    # Check the all in electricity prices
    price_attr = [
        a["price"]
        for a in hass.states.get(
            "sensor.frank_energie_gasprices_current_gas_price_all_in"
        ).attributes["prices"]
    ]
    assert price_attr == [1.75] * 6 + [1.23] * 24 + [0.75] * 18

    # For the other sensors just check if the prices attribute is there
    assert 48 == len(
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_market_price"
        ).attributes["prices"]
    )
    assert 48 == len(
        hass.states.get(
            "sensor.frank_energie_prices_current_electricity_price_including_tax"
        ).attributes["prices"]
    )
    assert 48 == len(
        hass.states.get(
            "sensor.frank_energie_gasprices_current_gas_market_price"
        ).attributes["prices"]
    )
    assert 48 == len(
        hass.states.get(
            "sensor.frank_energie_gasprices_current_gas_price_including_tax"
        ).attributes["prices"]
    )


@pytest.mark.asyncio
async def test_frank_energie_sensor_native_value_with_datetime(hass: HomeAssistant):
    """FrankEnergieSensor should accept datetime from value_fn and expose it as native_value and timestamp state."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    from homeassistant.components.sensor import SensorDeviceClass

    from custom_components.frank_energie.sensor import (
        FrankEnergieEntityDescription,
        FrankEnergieSensor,
    )

    test_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # value_fn ignores its input and returns a datetime
    description = FrankEnergieEntityDescription(
        key="test_datetime",
        name="Test datetime",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda _data: test_dt,
    )

    mock_coordinator = MagicMock()
    mock_coordinator.data = {"test": "data"}

    mock_entry = MagicMock()
    mock_entry.unique_id = "test_unique_id"
    mock_entry.entry_id = "test_entry_id"

    sensor = FrankEnergieSensor(
        coordinator=mock_coordinator, description=description, entry=mock_entry
    )
    sensor.hass = hass
    sensor.entity_id = "sensor.test_datetime"

    # Write state to Home Assistant
    sensor.async_write_ha_state()

    state = hass.states.get("sensor.test_datetime")
    assert state is not None
    assert state.state == "2024-01-01T12:00:00+00:00"

    native = sensor.native_value
    assert native == test_dt
    assert isinstance(native, datetime)


def test_frank_energie_sensor_native_value_handles_exceptions_and_returns_none():
    """FrankEnergieSensor.native_value should catch configured exceptions and return None."""
    from unittest.mock import MagicMock

    from custom_components.frank_energie.sensor import (
        FrankEnergieEntityDescription,
        FrankEnergieSensor,
    )

    # value_fn that raises one of the handled exceptions (ValueError here)
    def failing_value_fn(_data):
        raise ValueError("test failure")

    description = FrankEnergieEntityDescription(
        key="test_exception",
        name="Test exception",
        value_fn=failing_value_fn,
    )

    mock_coordinator = MagicMock()
    mock_coordinator.data = {"test": "data"}

    mock_entry = MagicMock()
    mock_entry.unique_id = "test_unique_id"
    mock_entry.entry_id = "test_entry_id"

    sensor = FrankEnergieSensor(
        coordinator=mock_coordinator, description=description, entry=mock_entry
    )

    # native_value should swallow the ValueError and return None instead of raising
    assert sensor.native_value is None


def test_frank_energie_sensor_dynamic_gas_unit():
    """Test that FrankEnergieSensor dynamically determines native_unit_of_measurement for gas sensors."""
    from unittest.mock import MagicMock
    from custom_components.frank_energie.sensor import (
        FrankEnergieEntityDescription,
        FrankEnergieSensor,
    )
    from custom_components.frank_energie.const import UNIT_GAS, UNIT_GAS_BE, DATA_GAS

    # Setup mock PriceData with per_unit KWH
    mock_gas_data = MagicMock()
    mock_gas_data.per_unit = "KWH"

    mock_coordinator = MagicMock()
    mock_coordinator.data = {DATA_GAS: mock_gas_data}

    mock_entry = MagicMock()
    mock_entry.unique_id = "test_unique_id"
    mock_entry.entry_id = "test_entry_id"

    # Description of a gas sensor
    description = FrankEnergieEntityDescription(
        key="gas_markup",
        name="Current gas price (All-in)",
        native_unit_of_measurement=UNIT_GAS,
        value_fn=lambda data: 1.23,
    )

    sensor = FrankEnergieSensor(
        coordinator=mock_coordinator, description=description, entry=mock_entry
    )

    # Unit should be dynamically resolved to UNIT_GAS_BE (EUR/kWh)
    assert sensor.native_unit_of_measurement == UNIT_GAS_BE

    # Test with M3 per_unit
    mock_gas_data.per_unit = "M3"
    assert sensor.native_unit_of_measurement == UNIT_GAS

    # Test with None or unknown per_unit (should fall back to UNIT_GAS)
    mock_gas_data.per_unit = None
    assert sensor.native_unit_of_measurement == UNIT_GAS

    mock_gas_data.per_unit = "UNKNOWN"
    assert sensor.native_unit_of_measurement == UNIT_GAS


def test_parse_contract_date():
    """Test the _parse_contract_date helper function."""
    from datetime import datetime, timezone
    from custom_components.frank_energie.sensor import _parse_contract_date
    from homeassistant.util import dt as dt_util

    # None cases
    assert _parse_contract_date(None) is None
    assert _parse_contract_date("") is None

    # Datetime inputs (both naive and timezone aware)
    tz_aware = datetime(2025, 10, 31, 23, 0, tzinfo=timezone.utc)
    expected = dt_util.as_local(tz_aware).strftime("%d-%m-%Y")
    assert _parse_contract_date(tz_aware) == expected

    # String inputs
    assert _parse_contract_date("2025-10-31T23:00:00.000Z") == expected
    assert _parse_contract_date("invalid-date") is None


def test_contract_sensors_with_connections():
    """Test that contractStartDate and contractStatus sensors support Connection objects."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    from custom_components.frank_energie.sensor import SENSOR_TYPES
    from python_frank_energie.models import (
        Connection,
        ConnectionExternalDetails,
        Contract,
    )
    from homeassistant.util import dt as dt_util

    # Test contractStartDate with Connection object
    contract_start_sensor = next(
        s for s in SENSOR_TYPES if s.key == "contractStartDate"
    )
    elec_status_sensor = next(s for s in SENSOR_TYPES if s.key == "EleccontractStatus")
    gas_status_sensor = next(s for s in SENSOR_TYPES if s.key == "GascontractStatus")

    # Mock python_frank_energie Connection object
    tz_aware = datetime(2025, 10, 31, 23, 0, tzinfo=timezone.utc)
    conn_obj = Connection(
        id="c1",
        segment="ELECTRICITY",
        contractStatus="SWITCHED",
        externalDetails=ConnectionExternalDetails(
            contract=Contract(
                startDate=tz_aware,
                endDate=None,
                contractType="dynamic",
                productName="test-product",
                tariffChartId=None,
            )
        ),
    )

    mock_user = MagicMock()
    mock_user.connections = [conn_obj]
    data = {"user": mock_user}

    # Evaluate contractStartDate value_fn
    expected_date = dt_util.as_local(tz_aware).strftime("%d-%m-%Y")
    assert contract_start_sensor.value_fn(data) == expected_date

    # Evaluate EleccontractStatus value_fn
    assert elec_status_sensor.value_fn(data) == "SWITCHED"

    # Evaluate GascontractStatus value_fn (should be None since segment is ELECTRICITY)
    assert gas_status_sensor.value_fn(data) is None

    # Test with GAS segment connection
    conn_obj_gas = Connection(
        id="c2",
        segment="GAS",
        contractStatus="IN_DELIVERY",
    )
    mock_user.connections = [conn_obj_gas]
    assert gas_status_sensor.value_fn(data) == "IN_DELIVERY"
    assert elec_status_sensor.value_fn(data) is None


def test_enode_charger_sensor_properties_and_value(
    mock_coordinator, mock_config_entry, create_mock_charger
):
    """Test properties and native value retrieval of EnodeChargerSensor."""
    from unittest.mock import MagicMock
    from custom_components.frank_energie.sensor import (
        EnodeChargerSensor,
        ENODE_CHARGER_SENSOR_TYPES,
    )
    from custom_components.frank_energie.const import DATA_ENODE_CHARGERS

    charger_id = "chg_123"
    mock_charger = create_mock_charger(
        charger_id=charger_id, brand="Wallbox", model="Copper"
    )
    mock_charger.can_smart_charge = True
    mock_charger.is_reachable = True
    mock_charger.charge_settings.capacity = 54
    mock_charger.charge_settings.is_smart_charging_enabled = True
    mock_charger.charge_settings.is_solar_charging_enabled = False
    mock_charger.charge_state.is_plugged_in = True
    mock_charger.charge_state.power_delivery_state = "PLUGGED_IN:CHARGING"
    mock_charger.charge_state.is_charging = True
    mock_charger.charge_state.charge_rate = 11.0

    mock_chargers = MagicMock()
    mock_chargers.chargers = [mock_charger]
    mock_coordinator.data = {DATA_ENODE_CHARGERS: mock_chargers}

    # Find the brand description
    brand_desc = next(d for d in ENODE_CHARGER_SENSOR_TYPES if d.key == "charger_brand")
    sensor = EnodeChargerSensor(
        coordinator=mock_coordinator, description=brand_desc, charger=mock_charger
    )

    assert sensor.unique_id == f"frank_energie_{charger_id}_charger_brand"
    assert sensor.device_info["identifiers"] == {("frank_energie", charger_id)}
    assert sensor.device_info["manufacturer"] == "Wallbox"
    assert sensor.device_info["model"] == "Copper"
    assert sensor.device_info["name"] == "Wallbox Copper"
    assert sensor.native_value == "Wallbox"

    # Find and test the charge rate sensor
    rate_desc = next(d for d in ENODE_CHARGER_SENSOR_TYPES if d.key == "charge_rate")
    sensor_rate = EnodeChargerSensor(
        coordinator=mock_coordinator, description=rate_desc, charger=mock_charger
    )
    assert sensor_rate.native_value == 11.0


def test_calculate_market_percent_tax() -> None:
    """Test the _calculate_market_percent_tax helper function under various pricing scenarios."""
    from unittest.mock import MagicMock
    from custom_components.frank_energie.sensor import _calculate_market_percent_tax

    # Case 1: None/empty inputs
    assert _calculate_market_percent_tax(None) is None

    # Case 2: Current hour is present and non-zero
    current_hour = MagicMock()
    current_hour.market_price = 0.10
    current_hour.market_price_tax = 0.021

    price_data = MagicMock()
    price_data.current_hour = current_hour
    price_data.all = [current_hour]

    assert _calculate_market_percent_tax(price_data) == 21.0

    # Case 3: Current hour has price 0, but other hours are non-zero
    zero_hour = MagicMock()
    zero_hour.market_price = 0.0
    zero_hour.market_price_tax = 0.0

    other_hour = MagicMock()
    other_hour.market_price = 0.20
    other_hour.market_price_tax = 0.042

    price_data_fallback = MagicMock()
    price_data_fallback.current_hour = zero_hour
    price_data_fallback.all = [zero_hour, other_hour]

    assert _calculate_market_percent_tax(price_data_fallback) == 21.0

    # Case 4: Negative prices (should calculate correctly)
    negative_hour = MagicMock()
    negative_hour.market_price = -0.05
    negative_hour.market_price_tax = -0.0105

    price_data_neg = MagicMock()
    price_data_neg.current_hour = negative_hour
    price_data_neg.all = [negative_hour]

    assert _calculate_market_percent_tax(price_data_neg) == 21.0

    # Case 5: All hours are zero
    price_data_all_zero = MagicMock()
    price_data_all_zero.current_hour = zero_hour
    price_data_all_zero.all = [zero_hour, zero_hour]

    assert _calculate_market_percent_tax(price_data_all_zero) == 0.0
