from datetime import UTC, datetime
from types import SimpleNamespace

from python_frank_energie.domain import (
    SmartBatteryImbalanceStrategy,
    SmartBatteryMode,
    SmartBatteryStatus,
)
from python_frank_energie.models import (
    SmartBattery,
    SmartBatteryDetails,
    SmartBatterySettings,
    SmartBatterySummary,
)
import pytest
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.frank_energie.const import (
    DATA_BATTERY_DETAILS,
    DATA_BATTERY_SETTINGS,
    DOMAIN,
)
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

    entity = FrankEnergieBatteryModeSelect(
        mock_coordinator,
        mock_config_entry,
        battery_id,
        next(d for d in SELECT_DESCRIPTIONS if d.key == "battery_mode"),
    )
    await entity.async_select_option("trading")

    mock_coordinator.async_update_battery_settings.assert_awaited_once_with(
        battery_id, {"batteryMode": "TRADING"}
    )


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

    entity = FrankEnergieBatteryStrategySelect(
        mock_coordinator,
        mock_config_entry,
        battery_id,
        next(d for d in SELECT_DESCRIPTIONS if d.key == "battery_strategy"),
    )
    await entity.async_select_option("aggressive")

    mock_coordinator.async_update_battery_settings.assert_awaited_once_with(
        battery_id, {"imbalanceTradingStrategy": "AGGRESSIVE"}
    )


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

    # Available in both trading modes, as in the Frank Energie app
    for mode in (SmartBatteryMode.TRADING, SmartBatteryMode.IMBALANCE_TRADING):
        mock_battery.smart_battery.settings.battery_mode = mode
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


# --- Per-battery options ------------------------------------------------------
#
# Library releases with ``smart_battery_settings`` give each battery its own
# selectable options. The namespaces below mirror python-frank-energie's
# ``SmartBatteryAdjustableSetting`` shape so the tests also run on the
# released library, which lacks the model.

_BATTERY_ID = "bat_123"


def _details(
    mode: SmartBatteryMode,
    strategy: SmartBatteryImbalanceStrategy = SmartBatteryImbalanceStrategy.AGGRESSIVE,
) -> SmartBatteryDetails:
    """Build real library battery details for one battery."""
    return SmartBatteryDetails(
        smart_battery=SmartBattery(
            id=_BATTERY_ID,
            brand="HYXi",
            settings=SmartBatterySettings(
                battery_mode=mode, imbalance_trading_strategy=strategy
            ),
        ),
        smart_battery_summary=SmartBatterySummary(
            last_known_state_of_charge=50,
            last_known_status=SmartBatteryStatus.UNKNOWN,
            last_update=datetime(2026, 11, 5, 8, tzinfo=UTC),
            total_result=0.0,
        ),
    )


def _adjustable(*, updatable: bool = True, pending=None) -> SimpleNamespace:
    """Mirror the smartBatterySettings response of a T2 battery."""
    return SimpleNamespace(
        battery_mode=SimpleNamespace(
            current=SmartBatteryMode.SELF_CONSUMPTION_MIX,
            updatable=updatable,
            options=[
                SimpleNamespace(
                    code=SmartBatteryMode.IMBALANCE_TRADING, selectable=True
                ),
                SimpleNamespace(
                    code=SmartBatteryMode.SELF_CONSUMPTION_MIX, selectable=True
                ),
                SimpleNamespace(code=SmartBatteryMode.TRADING, selectable=False),
                SimpleNamespace(code=SmartBatteryMode.UNKNOWN, selectable=True),
            ],
            pending=pending,
            pending_value_effective_from=(
                datetime(2026, 11, 5, 23, tzinfo=UTC) if pending else None
            ),
            update_delayed_till=datetime(2026, 11, 5, 23, tzinfo=UTC),
        ),
        imbalance_trading_strategy=SimpleNamespace(
            current=SmartBatteryImbalanceStrategy.AGGRESSIVE,
            updatable=True,
            options=[
                SimpleNamespace(code=SmartBatteryImbalanceStrategy.STANDARD),
                SimpleNamespace(code=SmartBatteryImbalanceStrategy.AGGRESSIVE),
            ],
            pending=None,
            pending_value_effective_from=None,
            update_delayed_till=None,
        ),
    )


def _select(cls, coordinator, entry, key: str):
    return cls(
        coordinator,
        entry,
        _BATTERY_ID,
        next(d for d in SELECT_DESCRIPTIONS if d.key == key),
    )


@pytest.mark.parametrize(
    ("mode", "expected_options"),
    [
        (SmartBatteryMode.SELF_CONSUMPTION_MIX, ["self_consumption_mix", "trading"]),
        (
            SmartBatteryMode.IMBALANCE_TRADING,
            ["self_consumption_mix", "trading", "imbalance_trading"],
        ),
        (SmartBatteryMode.UNKNOWN, ["self_consumption_mix", "trading"]),
    ],
)
def test_battery_mode_select_static_options_include_current_mode(
    mock_coordinator, mock_config_entry, mode, expected_options
):
    """Without per-battery options a reported mode outside the static list is
    still shown instead of ``unknown``; an unknown mode is not offered."""
    mock_coordinator.data = {DATA_BATTERY_DETAILS: [_details(mode)]}

    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    assert entity.options == expected_options
    assert entity.current_option == (None if mode is SmartBatteryMode.UNKNOWN else mode)
    assert entity.extra_state_attributes is None


def test_battery_mode_select_uses_per_battery_options(
    mock_coordinator, mock_config_entry
):
    """Only the battery's selectable, known modes are offered, with the pending
    change and change window as attributes."""
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.SELF_CONSUMPTION_MIX)],
        DATA_BATTERY_SETTINGS: {
            _BATTERY_ID: _adjustable(pending=SmartBatteryMode.IMBALANCE_TRADING)
        },
    }

    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    assert entity.options == ["imbalance_trading", "self_consumption_mix"]
    assert entity.current_option == "self_consumption_mix"
    assert entity.extra_state_attributes == {
        "is_change_request_possible": True,
        "change_request_effective_date": "2026-11-05T23:00:00+00:00",
        "upcoming_change": "imbalance_trading",
        "upcoming_change_effective_date": "2026-11-05T23:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_battery_mode_select_not_updatable_offers_only_current(
    mock_coordinator, mock_config_entry
):
    """A mode that cannot change right now offers only the current mode."""
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.SELF_CONSUMPTION_MIX)],
        DATA_BATTERY_SETTINGS: {_BATTERY_ID: _adjustable(updatable=False)},
    }

    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    assert entity.options == ["self_consumption_mix"]
    assert entity.extra_state_attributes["is_change_request_possible"] is False

    # Selecting the only offered value sends nothing; anything else is rejected
    await entity.async_select_option("self_consumption_mix")
    mock_coordinator.async_update_battery_settings.assert_not_called()
    with pytest.raises(HomeAssistantError, match="cannot be changed right now"):
        await entity.async_select_option("imbalance_trading")
    mock_coordinator.async_update_battery_settings.assert_not_called()

    # Details without a known mode fall back to the per-battery current mode
    mock_coordinator.data[DATA_BATTERY_DETAILS] = [_details(SmartBatteryMode.UNKNOWN)]
    assert entity.current_option == "self_consumption_mix"
    assert entity.options == ["self_consumption_mix"]


def test_battery_strategy_select_uses_per_battery_options(
    mock_coordinator, mock_config_entry
):
    """The strategy select offers only the battery's own strategies."""
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.IMBALANCE_TRADING)],
        DATA_BATTERY_SETTINGS: {_BATTERY_ID: _adjustable()},
    }

    entity = _select(
        FrankEnergieBatteryStrategySelect,
        mock_coordinator,
        mock_config_entry,
        "battery_strategy",
    )

    assert entity.available is True
    assert entity.options == ["standard", "aggressive"]
    assert entity.current_option == "aggressive"


# --- Real-platform layer for the battery selects ---------------------------
#
# These add the battery selects through a real ``MockEntityPlatform`` so Home
# Assistant itself writes ``current_option`` into the state machine and
# validates ``select.select_option`` against ``options`` -- the path that turns
# a current option missing from ``options`` into the ``unknown`` state.


async def _add_battery_selects(hass: HomeAssistant, data: dict) -> MagicMock:
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id="frank_energie")
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, SELECT_DOMAIN, {})

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = data
    coordinator.async_update_battery_settings = AsyncMock(return_value=True)

    platform = MockEntityPlatform(hass, domain=SELECT_DOMAIN, platform_name=DOMAIN)
    platform.config_entry = entry
    await platform.async_add_entities(
        [
            _select(FrankEnergieBatteryModeSelect, coordinator, entry, "battery_mode"),
            _select(
                FrankEnergieBatteryStrategySelect,
                coordinator,
                entry,
                "battery_strategy",
            ),
        ]
    )
    await hass.async_block_till_done()
    return coordinator


def _battery_entity_id(hass: HomeAssistant, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        SELECT_DOMAIN, DOMAIN, f"{DOMAIN}_{_BATTERY_ID}_{key}"
    )
    assert entity_id is not None, key
    return entity_id


async def test_imbalance_trading_battery_reaches_the_state_machine(
    hass: HomeAssistant,
) -> None:
    """A battery trading in IMBALANCE_TRADING shows its mode instead of
    ``unknown`` and keeps its strategy select available, even on library
    releases without per-battery options."""
    await _add_battery_selects(
        hass, {DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.IMBALANCE_TRADING)]}
    )

    mode = hass.states.get(_battery_entity_id(hass, "battery_mode"))
    assert mode is not None
    assert mode.state == "imbalance_trading"
    assert "imbalance_trading" in mode.attributes["options"]

    strategy = hass.states.get(_battery_entity_id(hass, "battery_strategy"))
    assert strategy is not None
    assert strategy.state == "aggressive"


async def test_battery_mode_select_option_goes_through_ha_validation(
    hass: HomeAssistant,
) -> None:
    """``select.select_option`` sends a mode the battery offers and rejects one
    it does not offer before the API is called."""
    coordinator = await _add_battery_selects(
        hass,
        {
            DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.SELF_CONSUMPTION_MIX)],
            DATA_BATTERY_SETTINGS: {_BATTERY_ID: _adjustable()},
        },
    )
    entity_id = _battery_entity_id(hass, "battery_mode")
    state = hass.states.get(entity_id)
    assert state.attributes["options"] == ["imbalance_trading", "self_consumption_mix"]
    assert state.attributes["upcoming_change"] is None

    await hass.services.async_call(
        SELECT_DOMAIN,
        "select_option",
        {"entity_id": entity_id, "option": "imbalance_trading"},
        blocking=True,
    )
    coordinator.async_update_battery_settings.assert_awaited_once_with(
        _BATTERY_ID, {"batteryMode": "IMBALANCE_TRADING"}
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            SELECT_DOMAIN,
            "select_option",
            {"entity_id": entity_id, "option": "trading"},
            blocking=True,
        )
    coordinator.async_update_battery_settings.assert_awaited_once()
