"""Test tomorrow-price availability with real price models and Home Assistant."""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)
import pytest
from python_frank_energie import FrankEnergie
from python_frank_energie.models import PriceData

from custom_components.frank_energie.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    FrankEnergieBinarySensor,
    async_setup_entry,
)
from custom_components.frank_energie.const import DATA_ELECTRICITY, DATA_GAS, DOMAIN
from custom_components.frank_energie.coordinator import (
    FrankEnergiePriceCoordinator,
    FrankEnergieSettingsCoordinator,
)


def _prices(
    *starts: str,
    amount: float = 0.2,
    resolution_minutes: int = 60,
    energy_type: str = "electricity",
) -> PriceData:
    """Build real energy prices with aware timestamps and valid intervals."""
    return PriceData(
        [
            {
                "from": start,
                "till": (
                    datetime.fromisoformat(start)
                    + timedelta(minutes=resolution_minutes)
                ).isoformat(),
                "marketPrice": amount,
                "marketPriceTax": 0.0,
                "sourcingMarkupPrice": 0.0,
                "energyTaxPrice": 0.0,
                "perUnit": "kwh" if energy_type == "electricity" else "m3",
            }
            for start in starts
        ],
        energy_type=energy_type,
        resolution_minutes=resolution_minutes,
    )


@pytest.fixture
async def tomorrow_price_coordinator(
    hass: HomeAssistant,
) -> FrankEnergiePriceCoordinator:
    """Create a real per-entry price coordinator without external requests."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="tomorrow-test")
    entry.add_to_hass(hass)
    api = MagicMock(spec=FrankEnergie)
    api.is_authenticated = False
    settings = FrankEnergieSettingsCoordinator(hass, entry, api)
    coordinator = FrankEnergiePriceCoordinator(hass, entry, api, settings)
    coordinator.async_set_updated_data(
        {**coordinator.data, DATA_ELECTRICITY: _prices()}
    )
    return coordinator


async def _async_add_sensor(
    hass: HomeAssistant, coordinator: FrankEnergiePriceCoordinator
) -> FrankEnergieBinarySensor:
    """Add the new entity through the real binary-sensor platform."""
    description = next(
        item
        for item in BINARY_SENSOR_DESCRIPTIONS
        if item.key == "tomorrow_prices_available"
    )
    entity = FrankEnergieBinarySensor(
        coordinator, description, coordinator.config_entry
    )
    platform = MockEntityPlatform(
        hass, domain=BINARY_SENSOR_DOMAIN, platform_name=DOMAIN
    )
    platform.config_entry = coordinator.config_entry
    await platform.async_add_entities([entity])
    await hass.async_block_till_done()
    return entity


@pytest.mark.parametrize(
    ("starts", "amount", "resolution_minutes", "expected_state"),
    [
        ([], 0.2, 60, STATE_OFF),
        (["2026-10-24T00:00:00+02:00"], 0.2, 60, STATE_OFF),
        (["2026-10-25T00:00:00+02:00"], 0.2, 60, STATE_ON),
        (["2026-10-26T00:00:00+01:00"], 0.2, 60, STATE_OFF),
        (["2026-10-25T00:00:00+02:00"], 0.0, 15, STATE_ON),
        (["2026-10-25T00:00:00+02:00"], -0.2, 15, STATE_ON),
    ],
    ids=[
        "empty",
        "today-only",
        "tomorrow",
        "later-day",
        "zero-price",
        "negative-price",
    ],
)
async def test_tomorrow_prices_state(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    tomorrow_price_coordinator: FrankEnergiePriceCoordinator,
    starts: list[str],
    amount: float,
    resolution_minutes: int,
    expected_state: str,
) -> None:
    """Use Amsterdam dates and actual periods, including zero and negative prices."""
    freezer.move_to("2026-10-24T12:00:00+00:00")
    await hass.config.async_set_time_zone("America/New_York")
    tomorrow_price_coordinator.async_set_updated_data(
        {
            **tomorrow_price_coordinator.data,
            DATA_ELECTRICITY: _prices(
                *starts, amount=amount, resolution_minutes=resolution_minutes
            ),
        }
    )
    entity = await _async_add_sensor(hass, tomorrow_price_coordinator)
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == expected_state


@pytest.mark.parametrize("gas_available", [False, True])
async def test_missing_electricity_is_unknown(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    tomorrow_price_coordinator: FrankEnergiePriceCoordinator,
    gas_available: bool,
) -> None:
    """Missing price data must not become a confirmed unpublished-price state."""
    freezer.move_to("2026-10-24T12:00:00+00:00")
    tomorrow_price_coordinator.async_set_updated_data(
        {
            **tomorrow_price_coordinator.data,
            DATA_ELECTRICITY: None,
            DATA_GAS: (
                _prices("2026-10-25T06:00:00+01:00", energy_type="gas")
                if gas_available
                else None
            ),
        }
    )
    entity = await _async_add_sensor(hass, tomorrow_price_coordinator)
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN


@pytest.mark.parametrize(
    ("before_midnight", "after_midnight"),
    [
        ("2026-03-28T23:59:00+01:00", "2026-03-29T00:00:00+01:00"),
        ("2026-10-24T23:59:00+02:00", "2026-10-25T00:00:00+02:00"),
    ],
    ids=["spring", "autumn"],
)
async def test_publication_failure_recovery_and_midnight(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    tomorrow_price_coordinator: FrankEnergiePriceCoordinator,
    before_midnight: str,
    after_midnight: str,
) -> None:
    """Publish updates and failures, then clear tomorrow's flag at local midnight."""
    freezer.move_to(before_midnight)
    entity = await _async_add_sensor(hass, tomorrow_price_coordinator)
    initial_state = hass.states.get(entity.entity_id)
    assert initial_state is not None
    assert initial_state.state == STATE_OFF

    published_data = {
        **tomorrow_price_coordinator.data,
        DATA_ELECTRICITY: _prices(after_midnight),
    }
    tomorrow_price_coordinator.async_set_updated_data(published_data)
    await hass.async_block_till_done()
    published_state = hass.states.get(entity.entity_id)
    assert published_state is not None
    assert published_state.state == STATE_ON

    tomorrow_price_coordinator.async_set_update_error(
        UpdateFailed("Price refresh failed")
    )
    await hass.async_block_till_done()
    failed_state = hass.states.get(entity.entity_id)
    assert failed_state is not None
    assert failed_state.state == STATE_UNAVAILABLE

    tomorrow_price_coordinator.async_set_updated_data(published_data)
    await hass.async_block_till_done()
    recovered_state = hass.states.get(entity.entity_id)
    assert recovered_state is not None
    assert recovered_state.state == STATE_ON

    freezer.move_to(after_midnight)
    tomorrow_price_coordinator.async_update_listeners()
    await hass.async_block_till_done()
    midnight_state = hass.states.get(entity.entity_id)
    assert midnight_state is not None
    assert midnight_state.state == STATE_OFF


@pytest.mark.parametrize("authenticated", [False, True])
async def test_platform_setup_uses_price_coordinator(
    hass: HomeAssistant,
    tomorrow_price_coordinator: FrankEnergiePriceCoordinator,
    authenticated: bool,
) -> None:
    """Create the public entity on its price coordinator regardless of login."""
    entry = tomorrow_price_coordinator.config_entry
    tomorrow_price_coordinator.api.is_authenticated = authenticated
    pv_coordinator = MagicMock()
    pv_coordinator.api.is_authenticated = authenticated
    battery_coordinator = MagicMock()
    battery_coordinator.api.is_authenticated = authenticated
    battery_coordinator.data = {}
    entry.runtime_data = MagicMock(
        price_coordinator=tomorrow_price_coordinator,
        settings_coordinator=tomorrow_price_coordinator.settings_coordinator,
        pv_coordinator=pv_coordinator,
        battery_coordinator=battery_coordinator,
    )
    entities: list[FrankEnergieBinarySensor] = []
    await async_setup_entry(hass, entry, entities.extend)
    public_entity = next(
        item
        for item in entities
        if item.entity_description.key == "tomorrow_prices_available"
    )
    assert public_entity.coordinator is tomorrow_price_coordinator
    assert public_entity.unique_id == "tomorrow-test_tomorrow_prices_available"
    assert public_entity.entity_registry_enabled_default is True
    assert public_entity.device_info is not None
    assert public_entity.device_info["identifiers"] == {(DOMAIN, entry.entry_id)}
    if not authenticated:
        assert entities == [public_entity]
    tomorrow_price_coordinator.api.assert_not_called()


async def test_public_integration_creates_tomorrow_prices_entity(
    hass: HomeAssistant,
    frank_energie_setup: Callable[[], Awaitable[MockConfigEntry]],
) -> None:
    """Forward the real platform and attach the new public sensor to Prices."""
    entry = await frank_energie_setup()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        BINARY_SENSOR_DOMAIN, DOMAIN, f"{entry.unique_id}_tomorrow_prices_available"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_OFF
    registered_entity = registry.async_get(entity_id)
    price_device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert registered_entity is not None
    assert price_device is not None
    assert registered_entity.device_id == price_device.id
    assert await hass.config_entries.async_unload(entry.entry_id)
