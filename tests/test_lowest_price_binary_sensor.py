"""Regression coverage for the lowest electricity price binary sensor."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from python_frank_energie.models import PriceData
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.frank_energie.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    FrankEnergieBinarySensor,
    async_setup_entry,
)
from custom_components.frank_energie.const import (
    CONF_RESOLUTION,
    DATA_ELECTRICITY,
    DOMAIN,
    TIMEZONE_AMSTERDAM,
)
from tests.utils import ResponseMocks

SENSOR_KEY = "electricity_price_lowest_today"


def _price_data(
    start_time: datetime,
    market_prices: list[float],
    resolution_minutes: int = 15,
    taxes: list[float] | None = None,
) -> PriceData:
    """Build real library price models with explicit, aware price intervals."""
    interval = timedelta(minutes=resolution_minutes)
    return PriceData(
        [
            {
                "from": (start_time + index * interval).isoformat(),
                "till": (start_time + (index + 1) * interval).isoformat(),
                "marketPrice": market_price,
                "marketPriceTax": taxes[index] if taxes is not None else 0.0,
                "sourcingMarkupPrice": 0.0,
                "energyTaxPrice": 0.0,
                "perUnit": "kwh",
            }
            for index, market_price in enumerate(market_prices)
        ],
        energy_type="electricity",
        resolution_minutes=resolution_minutes,
    )


@pytest.fixture
def lowest_price_sensor(
    mock_coordinator: MagicMock, mock_config_entry: MagicMock
) -> FrankEnergieBinarySensor:
    """Create the price entity using its registered entity description."""
    description = next(
        item for item in BINARY_SENSOR_DESCRIPTIONS if item.key == SENSOR_KEY
    )
    mock_coordinator.last_update_success = True
    return FrankEnergieBinarySensor(mock_coordinator, description, mock_config_entry)


@pytest.mark.parametrize("resolution_minutes", [15, 60])
@pytest.mark.parametrize(
    ("market_prices", "active_index", "expected"),
    [
        ([0.3, 0.1, 0.2], 0, False),
        ([0.3, 0.1, 0.2], 1, True),
        ([0.3, 0.1, 0.2], 2, False),
        ([0.3, 0.1, 0.1], 1, True),
        ([0.3, 0.1, 0.1], 2, True),
        ([0.1, 0.1, 0.1], 1, True),
        ([0.2, 0.0, 0.3], 1, True),
        ([-0.1, -0.3, 0.0], 1, True),
        ([0.10004, 0.10003], 0, False),
    ],
)
def test_lowest_price_state(
    lowest_price_sensor: FrankEnergieBinarySensor,
    freezer: FrozenDateTimeFactory,
    resolution_minutes: int,
    market_prices: list[float],
    active_index: int,
    expected: bool,
) -> None:
    """Compare actual interval prices without rounding or losing tied minima."""
    start_time = datetime(2026, 10, 29, 23, tzinfo=UTC)
    first_index = 10 * 60 // resolution_minutes
    prices = [1.0] * (24 * 60 // resolution_minutes)
    prices[first_index : first_index + len(market_prices)] = market_prices
    freezer.move_to(
        start_time
        + timedelta(minutes=(first_index + active_index) * resolution_minutes)
    )
    lowest_price_sensor.coordinator.data = {
        DATA_ELECTRICITY: _price_data(start_time, prices, resolution_minutes)
    }

    assert lowest_price_sensor.is_on is expected
    assert lowest_price_sensor.available


def test_lowest_price_uses_all_in_total(
    lowest_price_sensor: FrankEnergieBinarySensor, freezer: FrozenDateTimeFactory
) -> None:
    """An interval with a higher market price can have the lowest all-in total."""
    start_time = datetime(2026, 10, 29, 23, tzinfo=UTC)
    prices = [1.0] * 96
    taxes = [0.0] * 96
    prices[40:42] = [0.05, 0.2]
    taxes[40] = 0.4
    freezer.move_to(start_time + timedelta(minutes=15 * 41))
    lowest_price_sensor.coordinator.data = {
        DATA_ELECTRICITY: _price_data(start_time, prices, taxes=taxes)
    }

    assert lowest_price_sensor.is_on is True


@pytest.mark.parametrize("invalid_price", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("active_index", [0, 1])
def test_nonfinite_today_price_is_unknown(
    lowest_price_sensor: FrankEnergieBinarySensor,
    freezer: FrozenDateTimeFactory,
    invalid_price: float,
    active_index: int,
) -> None:
    """An indeterminate daily minimum must never publish a confirmed on/off."""
    start_time = datetime(2026, 10, 29, 23, tzinfo=UTC)
    prices = [1.0] * 96
    prices[40:42] = [0.0, invalid_price]
    freezer.move_to(start_time + timedelta(minutes=15 * (40 + active_index)))
    lowest_price_sensor.coordinator.data = {
        DATA_ELECTRICITY: _price_data(start_time, prices)
    }

    assert lowest_price_sensor.is_on is None


@pytest.mark.parametrize(
    "scenario", ["missing", "null", "empty", "expired", "future", "partial"]
)
async def test_missing_current_price_reaches_unknown_state(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    scenario: str,
) -> None:
    """Missing or stale prices reach Home Assistant as unknown, never off."""
    now_utc = datetime(2026, 10, 30, 9, tzinfo=UTC)
    freezer.move_to(now_utc)
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {}
    if scenario == "null":
        coordinator.data[DATA_ELECTRICITY] = None
    elif scenario == "empty":
        coordinator.data[DATA_ELECTRICITY] = _price_data(now_utc, [])
    elif scenario == "expired":
        coordinator.data[DATA_ELECTRICITY] = _price_data(
            now_utc - timedelta(minutes=15), [0.1]
        )
    elif scenario == "future":
        coordinator.data[DATA_ELECTRICITY] = _price_data(
            now_utc + timedelta(days=1), [0.1]
        )
    elif scenario == "partial":
        coordinator.data[DATA_ELECTRICITY] = _price_data(now_utc, [0.1])

    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="test-prices")
    entry.add_to_hass(hass)
    description = next(
        item for item in BINARY_SENSOR_DESCRIPTIONS if item.key == SENSOR_KEY
    )
    entity = FrankEnergieBinarySensor(coordinator, description, entry)
    platform = MockEntityPlatform(
        hass, domain=BINARY_SENSOR_DOMAIN, platform_name=DOMAIN
    )
    platform.config_entry = entry
    await platform.async_add_entities([entity])
    await hass.async_block_till_done()

    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN

    coordinator.last_update_success = False
    entity.async_write_ha_state()
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


def test_lowest_price_uses_amsterdam_day(
    lowest_price_sensor: FrankEnergieBinarySensor, freezer: FrozenDateTimeFactory
) -> None:
    """Exclude yesterday and tomorrow even when the local date differs from UTC."""
    start_time = datetime(2026, 7, 31, 21, tzinfo=UTC)
    freezer.move_to(start_time + timedelta(hours=1, minutes=5))
    electricity = _price_data(start_time, [-0.9, 0.2, 0.3] + [0.5] * 22 + [-1.0], 60)
    lowest_price_sensor.coordinator.data = {DATA_ELECTRICITY: electricity}

    assert lowest_price_sensor.is_on is True


def test_midnight_recalculates_the_daily_minimum(
    lowest_price_sensor: FrankEnergieBinarySensor, freezer: FrozenDateTimeFactory
) -> None:
    """Recompute the daily minimum from cached prices across local midnight."""
    start_time = datetime(2026, 10, 30, 22, tzinfo=UTC)
    prices = [0.5] * 48
    prices[23:26] = [0.1, 0.3, 0.2]
    lowest_price_sensor.coordinator.data = {
        DATA_ELECTRICITY: _price_data(start_time - timedelta(hours=23), prices, 60)
    }

    for moment, expected in [
        (start_time + timedelta(minutes=59), True),
        (start_time + timedelta(hours=1), False),
        (start_time + timedelta(hours=2), True),
    ]:
        freezer.move_to(moment)
        assert lowest_price_sensor.is_on is expected


@pytest.mark.parametrize(
    ("now_utc", "expected"),
    [
        ("2026-10-25T00:30:00+00:00", False),
        ("2026-10-25T01:30:00+00:00", True),
    ],
)
def test_repeated_dst_hour_uses_correct_interval(
    lowest_price_sensor: FrankEnergieBinarySensor,
    freezer: FrozenDateTimeFactory,
    now_utc: str,
    expected: bool,
) -> None:
    """Distinguish the two occurrences of 02:00 during the autumn DST change."""
    freezer.move_to(now_utc)
    lowest_price_sensor.coordinator.data = {
        DATA_ELECTRICITY: _price_data(
            datetime(2026, 10, 24, 22, tzinfo=UTC),
            [0.5, 0.5, 0.4, 0.1] + [0.5] * 21,
            60,
        )
    }

    assert lowest_price_sensor.is_on is expected


@pytest.mark.parametrize("resolution_minutes", [0, -15, None])
def test_invalid_resolution_is_unknown(
    lowest_price_sensor: FrankEnergieBinarySensor,
    freezer: FrozenDateTimeFactory,
    resolution_minutes: int | None,
) -> None:
    """Avoid comparing prices when expected intervals cannot be derived."""
    start_time = datetime(2026, 10, 29, 23, tzinfo=UTC)
    electricity = _price_data(start_time, [0.5] * 96)
    electricity.resolution_minutes = resolution_minutes
    freezer.move_to(start_time + timedelta(hours=1))
    lowest_price_sensor.coordinator.data = {DATA_ELECTRICITY: electricity}

    assert lowest_price_sensor.is_on is None


def test_no_active_interval_in_complete_starts_is_unknown(
    lowest_price_sensor: FrankEnergieBinarySensor, freezer: FrozenDateTimeFactory
) -> None:
    """The presence of every start does not guarantee an active interval."""
    start_time = datetime(2026, 10, 29, 23, tzinfo=UTC)
    electricity = _price_data(start_time, [0.5] * 96)
    electricity.price_data[40].date_till = electricity.price_data[40].date_from
    freezer.move_to(start_time + timedelta(minutes=40 * 15 + 1))
    lowest_price_sensor.coordinator.data = {DATA_ELECTRICITY: electricity}

    assert lowest_price_sensor.is_on is None


@pytest.mark.parametrize("resolution_minutes", [15, 60])
@pytest.mark.parametrize(
    ("start_utc", "hours"),
    [
        ("2026-10-29T23:00:00+00:00", 24),
        ("2026-03-28T23:00:00+00:00", 23),
        ("2026-10-24T22:00:00+00:00", 25),
    ],
)
@pytest.mark.parametrize("missing_position", [None, "first", "middle", "last"])
def test_requires_every_interval_for_local_day(
    lowest_price_sensor: FrankEnergieBinarySensor,
    freezer: FrozenDateTimeFactory,
    resolution_minutes: int,
    start_utc: str,
    hours: int,
    missing_position: str | None,
) -> None:
    """Reject missing starts on ordinary and DST transition days."""
    start_time = datetime.fromisoformat(start_utc)
    count = hours * 60 // resolution_minutes
    prices = [0.5] * count
    prices[1] = 0.1
    electricity = _price_data(start_time, prices, resolution_minutes)
    if missing_position is not None:
        index = {"first": 0, "middle": count // 2, "last": count - 1}[missing_position]
        # A duplicate preserves the number of entries, so length alone cannot pass.
        electricity.price_data[index] = electricity.price_data[1]
    freezer.move_to(start_time + timedelta(minutes=resolution_minutes))
    lowest_price_sensor.coordinator.data = {DATA_ELECTRICITY: electricity}

    assert lowest_price_sensor.is_on is (True if missing_position is None else None)


@pytest.mark.parametrize("authenticated", [False, True])
async def test_setup_routes_price_entity_to_its_own_coordinator(
    hass: HomeAssistant, authenticated: bool
) -> None:
    """Create the public price entity while preserving authenticated routing."""
    coordinators = [MagicMock() for _ in range(4)]
    for coordinator in coordinators:
        coordinator.api.is_authenticated = authenticated
        coordinator.data = {}
    settings, price, pv, battery = coordinators
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="site-one")
    entry.runtime_data = SimpleNamespace(
        settings_coordinator=settings,
        price_coordinator=price,
        pv_coordinator=pv,
        battery_coordinator=battery,
    )
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    entities = add_entities.call_args.args[0]
    price_entities = [
        item for item in entities if item.entity_description.key == SENSOR_KEY
    ]
    assert len(price_entities) == 1
    assert price_entities[0].coordinator is price
    assert price_entities[0].unique_id == f"site-one_{SENSOR_KEY}"
    assert price_entities[0].device_info["identifiers"] == {(DOMAIN, entry.entry_id)}
    if not authenticated:
        assert entities == price_entities
    else:
        for entity in entities:
            if entity.entity_description.authenticated:
                expected_coordinator = (
                    pv
                    if entity.entity_description.key
                    in ("smart_feed_in", "smart_pv_systems")
                    else settings
                )
                assert entity.coordinator is expected_coordinator


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("resolution_minutes", [15, 60])
async def test_public_setup_updates_on_price_boundaries_and_unloads(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    aioclient_responses: ResponseMocks,
    aioclient_mock: AiohttpClientMocker,
    resolution_minutes: int,
) -> None:
    """Exercise setup, aligned cached updates and cleanup through real HA."""
    await hass.config.async_set_time_zone(TIMEZONE_AMSTERDAM)
    local_start = datetime(2026, 10, 30, tzinfo=ZoneInfo(TIMEZONE_AMSTERDAM))
    lowest_index = 9 * 60 // resolution_minutes
    prices = [0.3] * (24 * 60 // resolution_minutes)
    prices[lowest_index : lowest_index + 2] = [0.1, 0.1]
    interval = timedelta(minutes=resolution_minutes)
    freezer.move_to(local_start + (lowest_index - 1) * interval)
    aioclient_responses.add(
        local_start, prices, [1.23] * len(prices), interval=interval
    )
    aioclient_responses.cyclic()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_RESOLUTION: f"PT{resolution_minutes}M"},
        unique_id="frank_energie",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        BINARY_SENSOR_DOMAIN, DOMAIN, f"{entry.unique_id}_{SENSOR_KEY}"
    )
    assert entity_id is not None
    registry = er.async_get(hass)
    current_price_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}.elec_markup"
    )
    assert current_price_id is not None
    binary_entry = registry.async_get(entity_id)
    price_entry = registry.async_get(current_price_id)
    assert binary_entry is not None
    assert price_entry is not None
    assert binary_entry.device_id is not None
    assert binary_entry.device_id == price_entry.device_id
    request_count = aioclient_mock.call_count

    for offset, expected in [(-1, "off"), (0, "on"), (1, "on"), (2, "off")]:
        moment = local_start + (lowest_index + offset) * interval
        freezer.move_to(moment)
        async_fire_time_changed(hass, moment)
        await hass.async_block_till_done()
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == expected
    assert aioclient_mock.call_count == request_count

    price_coordinator = entry.runtime_data.price_coordinator
    with patch.object(price_coordinator, "async_update_listeners") as update_listeners:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        unloaded_state = hass.states.get(entity_id)
        assert unloaded_state is not None
        assert unloaded_state.state == STATE_UNAVAILABLE
        after_unload = local_start + (lowest_index + 3) * interval
        freezer.move_to(after_unload)
        async_fire_time_changed(hass, after_unload)
        await hass.async_block_till_done()
        update_listeners.assert_not_called()
