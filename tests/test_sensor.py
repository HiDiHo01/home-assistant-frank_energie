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


def test_frank_energie_sensor_native_value_with_datetime():
    """FrankEnergieSensor should accept datetime from value_fn and expose it as native_value."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from custom_components.frank_energie.sensor import (
        FrankEnergieEntityDescription,
        FrankEnergieSensor,
    )

    test_dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # value_fn ignores its input and returns a datetime
    description = FrankEnergieEntityDescription(
        key="test_datetime",
        name="Test datetime",
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
