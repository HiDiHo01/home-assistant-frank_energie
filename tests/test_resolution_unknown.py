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
    ("active_option", "expected"),
    [
        (None, STATE_UNKNOWN),
        (STATE_UNKNOWN, STATE_UNKNOWN),
        ("PT15M", "pt15m"),
        ("PT60M", "pt60m"),
    ],
)
async def test_resolution_sensor_accepts_unknown_and_known_states(
    hass: HomeAssistant,
    resolution_sensor: FrankEnergieSensor,
    active_option: str | None,
    expected: str,
) -> None:
    """Accept missing and explicit unknown values alongside real resolutions."""
    resolution_sensor.coordinator.data = {
        DATA_CONTRACT_PRICE_RESOLUTION_STATE: ContractPriceResolutionState(
            active_option=active_option
        )
    }
    assert resolution_sensor.native_value == expected
    assert resolution_sensor.state == expected
    assert resolution_sensor.available

    resolution_sensor.async_write_ha_state()
    state = hass.states.get(_SENSOR_ID)
    assert state is not None
    assert state.state == expected


@pytest.mark.parametrize("missing_key", [True, False])
async def test_resolution_sensor_accepts_absent_contract_state(
    resolution_sensor: FrankEnergieSensor, missing_key: bool
) -> None:
    """Keep an unknown resolution readable without changing its identity."""
    resolution_sensor.coordinator.data = (
        {} if missing_key else {DATA_CONTRACT_PRICE_RESOLUTION_STATE: None}
    )
    assert resolution_sensor.state == STATE_UNKNOWN
    assert resolution_sensor.available
    assert STATE_UNKNOWN in resolution_sensor.options
    assert resolution_sensor.unique_id == (
        "resolution-site.contract_price_resolution_state"
    )


async def test_resolution_sensor_recovers_from_unknown(
    resolution_sensor: FrankEnergieSensor,
) -> None:
    """Allow the sensor to move between unknown and a real resolution."""
    resolution_sensor.coordinator.data = {}
    assert resolution_sensor.state == STATE_UNKNOWN
    resolution_sensor.coordinator.data = {
        DATA_CONTRACT_PRICE_RESOLUTION_STATE: ContractPriceResolutionState(
            active_option="PT60M"
        )
    }
    assert resolution_sensor.state == "pt60m"
    resolution_sensor.coordinator.data = {}
    assert resolution_sensor.state == STATE_UNKNOWN


async def test_failed_resolution_refresh_remains_unavailable(
    hass: HomeAssistant, resolution_sensor: FrankEnergieSensor
) -> None:
    """Preserve coordinator failure availability even when resolution is absent."""
    resolution_sensor.coordinator.data = {DATA_CONTRACT_PRICE_RESOLUTION_STATE: None}
    resolution_sensor.coordinator.last_update_success = False
    assert not resolution_sensor.available
    resolution_sensor.async_write_ha_state()
    state = hass.states.get(_SENSOR_ID)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_unknown_is_rejected_by_resolution_select(
    hass: HomeAssistant,
    frank_energie_setup: Callable[[], Awaitable[MockConfigEntry]],
) -> None:
    """Offer only real resolutions and reject unknown through the select service."""
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
            {"entity_id": _SELECT_ID, "option": STATE_UNKNOWN},
            blocking=True,
        )
    await hass.async_block_till_done()

    state = hass.states.get(_SELECT_ID)
    assert state is not None
    assert state.state == previous_option
    assert state.attributes["options"] == ["pt15m", "pt60m"]
    assert dict(entry.options) == previous_options
