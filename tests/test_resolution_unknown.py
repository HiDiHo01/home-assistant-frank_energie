"""Regression tests for unknown contract resolution sensor states."""

from collections.abc import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)
from python_frank_energie.models import ContractPriceResolutionState

from custom_components.frank_energie.const import (
    DATA_CONTRACT_PRICE_RESOLUTION_STATE,
    DOMAIN,
)
from custom_components.frank_energie.coordinator import FrankEnergieCoordinator
from custom_components.frank_energie.sensor import SENSOR_TYPES, FrankEnergieSensor

_SENSOR_ID = "sensor.frank_energie_contract_price_resolution_state"
_SELECT_ID = "select.frank_energie_settings_resolution"


@pytest.fixture(name="resolution_sensor")
async def _resolution_sensor(hass: HomeAssistant) -> FrankEnergieSensor:
    """Register the real sensor so Home Assistant validates its enum states."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id="resolution-site")
    entry.add_to_hass(hass)
    coordinator = MagicMock(spec=FrankEnergieCoordinator)
    coordinator.config_entry = entry
    coordinator.data = {
        DATA_CONTRACT_PRICE_RESOLUTION_STATE: ContractPriceResolutionState(
            active_option="PT15M"
        )
    }
    coordinator.last_update_success = True
    description = next(
        item for item in SENSOR_TYPES if item.key == "contract_price_resolution_state"
    )
    sensor = FrankEnergieSensor(coordinator, description, entry)
    sensor.entity_id = _SENSOR_ID
    platform = MockEntityPlatform(hass, domain=SENSOR_DOMAIN, platform_name=DOMAIN)
    platform.config_entry = entry
    await platform.async_add_entities([sensor])
    await hass.async_block_till_done()
    return sensor


@pytest.mark.parametrize(
    ("active_option", "expected_native"),
    [
        (None, None),
        (STATE_UNKNOWN, None),
        ("UNKNOWN", None),
        ("", None),
        ("PT5M", None),
        ("PT15M", "pt15m"),
        ("PT60M", "pt60m"),
        ("pt15m", "pt15m"),
        ("pt60m", "pt60m"),
    ],
)
async def test_resolution_sensor_accepts_unknown_and_known_states(
    hass: HomeAssistant,
    resolution_sensor: FrankEnergieSensor,
    active_option: str | None,
    expected_native: str | None,
) -> None:
    """Normalize missing or unsupported values to None and publish unknown."""
    resolution_sensor.coordinator.data = {
        DATA_CONTRACT_PRICE_RESOLUTION_STATE: ContractPriceResolutionState(
            active_option=active_option
        )
    }
    assert resolution_sensor.native_value == expected_native
    assert resolution_sensor.state == expected_native
    assert resolution_sensor.available

    resolution_sensor.async_write_ha_state()
    state = hass.states.get(_SENSOR_ID)
    assert state is not None
    assert state.state == (
        expected_native if expected_native is not None else STATE_UNKNOWN
    )


@pytest.mark.parametrize("missing_key", [True, False])
async def test_resolution_sensor_accepts_absent_contract_state(
    hass: HomeAssistant, resolution_sensor: FrankEnergieSensor, missing_key: bool
) -> None:
    """Keep an unknown resolution readable without changing its identity."""
    resolution_sensor.coordinator.data = (
        {} if missing_key else {DATA_CONTRACT_PRICE_RESOLUTION_STATE: None}
    )
    assert resolution_sensor.native_value is None
    assert resolution_sensor.state is None
    assert resolution_sensor.available
    assert resolution_sensor.options == ["pt15m", "pt60m"]
    assert resolution_sensor.unique_id == (
        "resolution-site.contract_price_resolution_state"
    )
    resolution_sensor.async_write_ha_state()
    state = hass.states.get(_SENSOR_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_resolution_sensor_recovers_from_unknown(
    hass: HomeAssistant, resolution_sensor: FrankEnergieSensor
) -> None:
    """Allow the sensor to move between unknown and a real resolution."""
    resolution_sensor.coordinator.data = {}
    assert resolution_sensor.state is None
    resolution_sensor.async_write_ha_state()
    state = hass.states.get(_SENSOR_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    resolution_sensor.coordinator.data = {
        DATA_CONTRACT_PRICE_RESOLUTION_STATE: ContractPriceResolutionState(
            active_option="PT60M"
        )
    }
    assert resolution_sensor.state == "pt60m"
    resolution_sensor.async_write_ha_state()
    state = hass.states.get(_SENSOR_ID)
    assert state is not None
    assert state.state == "pt60m"
    resolution_sensor.coordinator.data = {}
    assert resolution_sensor.state is None
    resolution_sensor.async_write_ha_state()
    state = hass.states.get(_SENSOR_ID)
    assert state is not None
    assert state.state == STATE_UNKNOWN


@pytest.mark.parametrize("active_option", [None, "PT15M"])
async def test_failed_resolution_refresh_remains_unavailable(
    hass: HomeAssistant,
    resolution_sensor: FrankEnergieSensor,
    active_option: str | None,
) -> None:
    """Keep failed updates unavailable with absent or previously valid values."""
    resolution_sensor.coordinator.data = {
        DATA_CONTRACT_PRICE_RESOLUTION_STATE: ContractPriceResolutionState(
            active_option=active_option
        )
    }
    resolution_sensor.coordinator.last_update_success = False
    assert not resolution_sensor.available
    resolution_sensor.async_write_ha_state()
    state = hass.states.get(_SENSOR_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_resolution_sensor_requires_coordinator_data(
    hass: HomeAssistant, resolution_sensor: FrankEnergieSensor
) -> None:
    """Keep the sensor unavailable before any coordinator payload exists."""
    resolution_sensor.coordinator.data = None
    assert resolution_sensor.native_value is None
    assert not resolution_sensor.available
    resolution_sensor.async_write_ha_state()
    state = hass.states.get(_SENSOR_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_missing_measurement_remains_unavailable(
    hass: HomeAssistant, resolution_sensor: FrankEnergieSensor
) -> None:
    """Preserve unavailability for ordinary sensors with missing measurements."""
    coordinator = resolution_sensor.coordinator
    description = next(item for item in SENSOR_TYPES if item.key == "elec_markup")
    measurement = FrankEnergieSensor(coordinator, description, coordinator.config_entry)
    measurement.entity_id = "sensor.frank_energie_missing_electricity_price"
    platform = MockEntityPlatform(hass, domain=SENSOR_DOMAIN, platform_name=DOMAIN)
    platform.config_entry = coordinator.config_entry
    await platform.async_add_entities([measurement])
    assert measurement.native_value is None
    assert not measurement.available
    state = hass.states.get(measurement.entity_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


@pytest.mark.parametrize("invalid_option", [STATE_UNKNOWN, "None"])
async def test_unknown_is_rejected_by_resolution_select(
    hass: HomeAssistant,
    frank_energie_setup: Callable[[], Awaitable[MockConfigEntry]],
    invalid_option: str,
) -> None:
    """Offer only real resolutions and reject unknown or None as select choices."""
    entry = await frank_energie_setup()
    state = hass.states.get(_SELECT_ID)
    assert state is not None
    assert state.attributes["options"] == ["pt15m", "pt60m"]
    previous_option = state.state
    previous_options = dict(entry.options)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": _SELECT_ID, "option": invalid_option},
            blocking=True,
        )
    await hass.async_block_till_done()

    state = hass.states.get(_SELECT_ID)
    assert state is not None
    assert state.state == previous_option
    assert state.attributes["options"] == ["pt15m", "pt60m"]
    assert dict(entry.options) == previous_options
