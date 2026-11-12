import logging
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace

from python_frank_energie.domain import (
    SmartBatteryImbalanceStrategy,
    SmartBatteryTradingAlgorithm,
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
from pytest_homeassistant.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.frank_energie.const import (
    ATTRIBUTION,
    DATA_BATTERIES,
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
    battery_id: str = _BATTERY_ID,
) -> SmartBatteryDetails:
    """Build real library battery details for one battery."""
    return SmartBatteryDetails(
        smart_battery=SmartBattery(
            id=battery_id,
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


def _select(cls, coordinator, entry, key: str, battery_id: str = _BATTERY_ID):
    return cls(
        coordinator,
        entry,
        battery_id,
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
    assert entity.extra_state_attributes["battery_mode"] == mode
    assert entity.extra_state_attributes["attribution"] == ATTRIBUTION


def test_battery_mode_select_logs_missing_option_once(
    mock_coordinator, mock_config_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """Identify a missing known battery mode without repeating log messages."""
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.IMBALANCE_TRADING)]
    }
    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.frank_energie.select"
    ):
        assert entity.options == [
            "self_consumption_mix",
            "trading",
            "imbalance_trading",
        ]
        assert entity.options == [
            "self_consumption_mix",
            "trading",
            "imbalance_trading",
        ]

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "custom_components.frank_energie.select"
        and "current option missing from selectable options" in record.getMessage()
    ]
    assert len(messages) == 1
    assert messages[0].endswith("imbalance_trading")


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
        **asdict(mock_coordinator.data[DATA_BATTERY_DETAILS][0].smart_battery.settings),
        "is_change_request_possible": True,
        "change_request_effective_date": "2026-11-05T23:00:00+00:00",
        "upcoming_change": "imbalance_trading",
        "upcoming_change_effective_date": "2026-11-05T23:00:00+00:00",
        "attribution": ATTRIBUTION,
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


@pytest.mark.asyncio
async def test_t3_trading_algorithm_is_not_an_imbalance_strategy(
    mock_coordinator, mock_config_entry
) -> None:
    """Keep T3 algorithm metadata distinct from selectable imbalance strategies."""
    parsed_settings = SmartBatterySettings.from_dict(
        {
            "batteryMode": "IMBALANCE_TRADING",
            "imbalanceTradingStrategy": "STANDARD",
            "tradingAlgorithm": "T3",
        }
    )
    assert parsed_settings is not None
    assert parsed_settings.trading_algorithm is SmartBatteryTradingAlgorithm.T3
    assert (
        parsed_settings.imbalance_trading_strategy
        is SmartBatteryImbalanceStrategy.STANDARD
    )

    details = _details(SmartBatteryMode.IMBALANCE_TRADING)
    details.smart_battery.settings = parsed_settings
    mock_coordinator.data = {DATA_BATTERY_DETAILS: [details]}
    entity = _select(
        FrankEnergieBatteryStrategySelect,
        mock_coordinator,
        mock_config_entry,
        "battery_strategy",
    )

    assert entity.available is True
    assert entity.current_option == "standard"
    assert "standard" in entity.options
    assert "t3" not in entity.options

    with pytest.raises(HomeAssistantError, match="not selectable"):
        await entity.async_select_option("t3")
    mock_coordinator.async_update_battery_settings.assert_not_awaited()


# --- Real-platform layer for the battery selects ---------------------------
#
# These add the battery selects through a real ``MockEntityPlatform`` so Home
# Assistant itself writes ``current_option`` into the state machine and
# validates ``select.select_option`` against ``options`` -- the path that turns
# a current option missing from ``options`` into the ``unknown`` state.


async def _add_battery_selects(
    hass: HomeAssistant, data: dict, battery_ids: tuple[str, ...] = (_BATTERY_ID,)
) -> MagicMock:
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
            entity
            for battery_id in battery_ids
            for entity in (
                _select(
                    FrankEnergieBatteryModeSelect,
                    coordinator,
                    entry,
                    "battery_mode",
                    battery_id,
                ),
                _select(
                    FrankEnergieBatteryStrategySelect,
                    coordinator,
                    entry,
                    "battery_strategy",
                    battery_id,
                ),
            )
        ]
    )
    await hass.async_block_till_done()
    return coordinator


def _battery_entity_id(
    hass: HomeAssistant, key: str, battery_id: str = _BATTERY_ID
) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        SELECT_DOMAIN, DOMAIN, f"{DOMAIN}_{battery_id}_{key}"
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
    assert mode.attributes["battery_mode"] == "imbalance_trading"
    assert mode.attributes["imbalance_trading_strategy"] == "aggressive"

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


async def test_battery_selects_keep_each_batterys_own_options(
    hass: HomeAssistant,
) -> None:
    """Two batteries with different per-battery options each offer only their
    own modes and strategies; nothing is shared between devices."""
    t1_settings = SimpleNamespace(
        battery_mode=SimpleNamespace(
            current=SmartBatteryMode.TRADING,
            updatable=True,
            options=[
                SimpleNamespace(code=SmartBatteryMode.TRADING, selectable=True),
                SimpleNamespace(
                    code=SmartBatteryMode.SELF_CONSUMPTION, selectable=True
                ),
            ],
            pending=None,
            pending_value_effective_from=None,
            update_delayed_till=None,
        ),
        imbalance_trading_strategy=SimpleNamespace(
            current=SmartBatteryImbalanceStrategy.BALANCED,
            updatable=True,
            options=[
                SimpleNamespace(code=SmartBatteryImbalanceStrategy.BALANCED),
                SimpleNamespace(code=SmartBatteryImbalanceStrategy.CONSERVATIVE),
            ],
            pending=None,
            pending_value_effective_from=None,
            update_delayed_till=None,
        ),
    )
    await _add_battery_selects(
        hass,
        {
            DATA_BATTERY_DETAILS: [
                _details(SmartBatteryMode.IMBALANCE_TRADING, battery_id="bat_t2"),
                _details(
                    SmartBatteryMode.TRADING,
                    SmartBatteryImbalanceStrategy.BALANCED,
                    battery_id="bat_t1",
                ),
            ],
            DATA_BATTERY_SETTINGS: {"bat_t2": _adjustable(), "bat_t1": t1_settings},
        },
        battery_ids=("bat_t2", "bat_t1"),
    )

    def options(key: str, battery_id: str) -> list[str]:
        return hass.states.get(_battery_entity_id(hass, key, battery_id)).attributes[
            "options"
        ]

    assert options("battery_mode", "bat_t2") == [
        "imbalance_trading",
        "self_consumption_mix",
    ]
    assert options("battery_mode", "bat_t1") == ["trading", "self_consumption"]
    assert options("battery_strategy", "bat_t2") == ["standard", "aggressive"]
    assert options("battery_strategy", "bat_t1") == ["balanced", "conservative"]


@pytest.mark.asyncio
async def test_failed_battery_change_is_reported(mock_coordinator, mock_config_entry):
    """A change Frank Energie did not accept raises instead of silently succeeding."""
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.SELF_CONSUMPTION_MIX)]
    }
    mock_coordinator.async_update_battery_settings.return_value = False
    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    with pytest.raises(HomeAssistantError, match="rejected"):
        await entity.async_select_option("trading")


async def test_failed_battery_change_fails_the_service_call(
    hass: HomeAssistant,
) -> None:
    """``select.select_option`` reports a change Frank Energie did not accept."""
    coordinator = await _add_battery_selects(
        hass,
        {
            DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.SELF_CONSUMPTION_MIX)],
            DATA_BATTERY_SETTINGS: {_BATTERY_ID: _adjustable()},
        },
    )
    coordinator.async_update_battery_settings.return_value = False
    entity_id = _battery_entity_id(hass, "battery_mode")

    with pytest.raises(HomeAssistantError, match="rejected"):
        await hass.services.async_call(
            SELECT_DOMAIN,
            "select_option",
            {"entity_id": entity_id, "option": "imbalance_trading"},
            blocking=True,
        )
    assert hass.states.get(entity_id).state == "self_consumption_mix"


@pytest.mark.parametrize(
    ("details_mode", "expected"),
    [
        # Details without a known mode: the per-battery mode decides
        (SmartBatteryMode.UNKNOWN, True),
        # Details with a known mode win over the (possibly older) settings
        (SmartBatteryMode.SELF_CONSUMPTION_MIX, False),
    ],
)
def test_battery_strategy_select_falls_back_to_per_battery_mode(
    mock_coordinator, mock_config_entry, details_mode, expected
):
    """Strategy availability uses the per-battery mode only when the details
    report no known mode."""
    adjustable = _adjustable()
    adjustable.battery_mode.current = SmartBatteryMode.IMBALANCE_TRADING
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(details_mode)],
        DATA_BATTERY_SETTINGS: {_BATTERY_ID: adjustable},
    }

    entity = _select(
        FrankEnergieBatteryStrategySelect,
        mock_coordinator,
        mock_config_entry,
        "battery_strategy",
    )

    assert entity.available is expected


@pytest.mark.asyncio
async def test_rejected_known_option_appears_in_safe_debug_log(
    mock_coordinator, mock_config_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """Identify a known unavailable option without sending an API mutation."""
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.SELF_CONSUMPTION_MIX)],
        DATA_BATTERY_SETTINGS: {_BATTERY_ID: _adjustable()},
    }
    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.frank_energie.select"
    ):
        with pytest.raises(HomeAssistantError, match="not selectable"):
            await entity.async_select_option("trading")

    mock_coordinator.async_update_battery_settings.assert_not_awaited()
    assert any(
        record.getMessage().endswith("missing from selectable options: trading")
        for record in caplog.records
        if record.name == "custom_components.frank_energie.select"
    )


@pytest.mark.asyncio
async def test_nonselectable_current_mode_remains_visible_but_cannot_mutate(
    mock_coordinator, mock_config_entry
) -> None:
    """Retain a non-selectable current mode for display, never for API writes."""
    adjustable = _adjustable(updatable=True)
    adjustable.battery_mode.current = SmartBatteryMode.TRADING
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.TRADING)],
        DATA_BATTERY_SETTINGS: {_BATTERY_ID: adjustable},
    }
    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    assert entity.current_option == "trading"
    assert entity.options == ["imbalance_trading", "self_consumption_mix", "trading"]

    await entity.async_select_option("trading")
    mock_coordinator.async_update_battery_settings.assert_not_awaited()

    with pytest.raises(HomeAssistantError, match="not selectable"):
        await entity.async_select_option("unknown")
    mock_coordinator.async_update_battery_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_select_option_does_not_disclose_sensitive_option(
    mock_coordinator, mock_config_entry, caplog: pytest.LogCaptureFixture
) -> None:
    """Never log the submitted option, but keep it for the API mutation."""
    synthetic_secret = "synthetic-secret-not-a-real-password"
    adjustable = _adjustable()
    adjustable.battery_mode.options.append(
        SimpleNamespace(code=synthetic_secret, selectable=True)
    )
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.SELF_CONSUMPTION_MIX)],
        DATA_BATTERY_SETTINGS: {_BATTERY_ID: adjustable},
    }
    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    with caplog.at_level(
        logging.DEBUG, logger="custom_components.frank_energie.select"
    ):
        await entity.async_select_option(synthetic_secret)

    mock_coordinator.async_update_battery_settings.assert_awaited_once_with(
        _BATTERY_ID, {"batteryMode": synthetic_secret.upper()}
    )
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "custom_components.frank_energie.select"
    ]
    assert any(
        "Submitting a smart battery setting update" in message for message in messages
    )
    assert any("option: [redacted]" in message for message in messages)
    assert all(synthetic_secret not in message for message in messages)


@pytest.mark.asyncio
async def test_select_option_errors_do_not_disclose_sensitive_input(
    mock_coordinator, mock_config_entry
) -> None:
    """Reject unsupported or failed changes without reflecting the input."""
    synthetic_secret = "synthetic-secret-not-a-real-password"
    adjustable = _adjustable()
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.SELF_CONSUMPTION_MIX)],
        DATA_BATTERY_SETTINGS: {_BATTERY_ID: adjustable},
    }
    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    with pytest.raises(HomeAssistantError, match="not selectable") as exc_info:
        await entity.async_select_option(synthetic_secret)
    assert synthetic_secret not in str(exc_info.value)
    mock_coordinator.async_update_battery_settings.assert_not_awaited()

    adjustable.battery_mode.options.append(
        SimpleNamespace(code=synthetic_secret, selectable=True)
    )
    mock_coordinator.async_update_battery_settings.return_value = False

    with pytest.raises(HomeAssistantError, match="rejected") as exc_info:
        await entity.async_select_option(synthetic_secret)
    assert synthetic_secret not in str(exc_info.value)


async def test_unlisted_mode_is_display_only_on_older_library(
    mock_coordinator, mock_config_entry
) -> None:
    """A mode appended for display is not a permitted legacy mutation."""
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [_details(SmartBatteryMode.IMBALANCE_TRADING)],
    }
    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )

    assert entity.current_option == "imbalance_trading"
    assert "imbalance_trading" in entity.options
    await entity.async_select_option("imbalance_trading")
    mock_coordinator.async_update_battery_settings.assert_not_awaited()
    with pytest.raises(HomeAssistantError, match="not selectable"):
        await entity.async_select_option("invalid")
    mock_coordinator.async_update_battery_settings.assert_not_awaited()


async def test_battery_mode_select_attrs_match_sensor_for_each_battery(
    hass: HomeAssistant,
) -> None:
    """Expose the sensor settings from each matching battery, not another device."""
    first = _details(SmartBatteryMode.IMBALANCE_TRADING, battery_id="bat_a")
    first.smart_battery.settings = SmartBatterySettings(
        battery_mode=SmartBatteryMode.IMBALANCE_TRADING,
        imbalance_trading_strategy=SmartBatteryImbalanceStrategy.AGGRESSIVE,
        self_consumption_trading_allowed=True,
        self_consumption_trading_threshold_price=0.2,
    )
    second = _details(
        SmartBatteryMode.SELF_CONSUMPTION_MIX,
        SmartBatteryImbalanceStrategy.BALANCED,
        battery_id="bat_b",
    )
    second.smart_battery.settings = SmartBatterySettings(
        battery_mode=SmartBatteryMode.SELF_CONSUMPTION_MIX,
        imbalance_trading_strategy=SmartBatteryImbalanceStrategy.BALANCED,
        self_consumption_trading_allowed=False,
        self_consumption_trading_threshold_price=0.3,
    )
    await _add_battery_selects(
        hass,
        {
            DATA_BATTERY_DETAILS: [first, second],
            DATA_BATTERIES: SimpleNamespace(
                batteries=[second.smart_battery, first.smart_battery]
            ),
        },
        battery_ids=("bat_a", "bat_b"),
    )

    for details in (first, second):
        battery_id = details.smart_battery.id
        state = hass.states.get(_battery_entity_id(hass, "battery_mode", battery_id))
        assert state is not None
        for key, value in asdict(details.smart_battery.settings).items():
            assert state.attributes[key] == value
        assert state.attributes["attribution"] == ATTRIBUTION
    first_state = hass.states.get(_battery_entity_id(hass, "battery_mode", "bat_a"))
    second_state = hass.states.get(_battery_entity_id(hass, "battery_mode", "bat_b"))
    assert first_state.attributes["battery_mode"] == "imbalance_trading"
    assert first_state.attributes["self_consumption_trading_threshold_price"] == 0.2
    assert second_state.attributes["battery_mode"] == "self_consumption_mix"
    assert second_state.attributes["self_consumption_trading_threshold_price"] == 0.3


def test_battery_mode_select_attributes_track_refresh_and_missing_settings(
    mock_coordinator, mock_config_entry
) -> None:
    """Attribute values follow coordinator updates and vanish when absent."""
    details = _details(SmartBatteryMode.IMBALANCE_TRADING)
    battery = details.smart_battery
    mock_coordinator.data = {
        DATA_BATTERY_DETAILS: [details],
        DATA_BATTERIES: SimpleNamespace(batteries=[battery]),
    }
    entity = _select(
        FrankEnergieBatteryModeSelect,
        mock_coordinator,
        mock_config_entry,
        "battery_mode",
    )
    assert entity.extra_state_attributes["battery_mode"] == "imbalance_trading"

    battery.settings = SmartBatterySettings(
        battery_mode=SmartBatteryMode.SELF_CONSUMPTION,
        imbalance_trading_strategy=SmartBatteryImbalanceStrategy.BALANCED,
    )
    assert entity.extra_state_attributes["battery_mode"] == "self_consumption"
    assert entity.extra_state_attributes["imbalance_trading_strategy"] == "balanced"

    battery.settings = None
    details.smart_battery.settings = None
    assert entity.extra_state_attributes is None
