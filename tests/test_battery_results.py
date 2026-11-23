"""Test Smart Battery period sensors and the Home Assistant date-range action."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import probatio
import pytest
import yaml
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceResponse
from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.helpers.template import Template
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import RateLimitException, RequestException
from python_frank_energie.models import (
    SmartBatteries,
    SmartBattery,
    SmartBatterySession,
    SmartBatterySessions,
)

from custom_components.frank_energie import FrankEnergieEntryData
from custom_components.frank_energie.battery_daily import (
    DAILY_SENSORS,
    BatteryDailyCoordinator,
    BatteryDailySensor,
)
from custom_components.frank_energie.battery_results import (
    CONF_BATTERY_PERIOD,
    BatteryPeriod,
)
from custom_components.frank_energie.const import DATA_BATTERIES, DOMAIN
from custom_components.frank_energie.coordinator import (
    FrankEnergieBatteryCoordinator,
    FrankEnergieBatterySessionCoordinator,
)
from custom_components.frank_energie.sensor import (
    BATTERY_SESSION_SENSOR_DESCRIPTIONS,
    FrankEnergieBatterySessionSensor,
    async_setup_entry as async_setup_sensors,
)
from custom_components.frank_energie.services import SERVICE_GET_BATTERY_RESULTS

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

_DAY = "2026-11-14"
_YEAR_END = "2026-12-31"
_NEW_YEAR = "2027-01-01"
_BATTERY = "battery-a"


@pytest.fixture(autouse=True)
def outside_maintenance(freezer: FrozenDateTimeFactory) -> None:
    """Keep normal requests outside the provider maintenance window."""
    freezer.move_to("2027-01-02T09:00:00Z")


def _report(
    battery_id: str = _BATTERY, start: str = _DAY, end: str = _DAY, **values: object
) -> SimpleNamespace:
    """Represent SDK output with misleading legacy fields to detect fallbacks."""
    fields = {
        "device_id": battery_id,
        "period_start_date": date.fromisoformat(start),
        "period_end_date": date.fromisoformat(end),
        "period_trade_index": None,
        "period_trading_result": 19,
        "period_total_result": 52,
        "period_imbalance_result": 17,
        "period_epex_result": -8,
        "period_frank_slim": 5,
        "sessions": [],
    }
    return SimpleNamespace(**(fields | values))


@pytest.mark.parametrize(
    ("trading", "slim", "result", "correction", "total"),
    [
        (0.1221, -0.0323, 0.0899, -0.6557, -0.5658),
        (0.1613, 0.0202, 0.1814, -0.0034, 0.1780),
        (0, 0, 0, 0.0009, 0.0009),
        (0, 0, 0, 0.0735, 0.0735),
        (0, 0, 0, -0.8981, -0.8981),
        (0, 0, 0, 0, 0),
        (None, None, 2.5, None, None),
        (None, None, None, 0.0, None),
    ],
)
def test_period_entities_and_attributes(
    trading: float | None,
    slim: float | None,
    result: float | None,
    correction: float | None,
    total: float | None,
) -> None:
    """Preserve entity identities, component meanings, signs and sub-cent values."""
    coordinator = MagicMock()
    coordinator.period_data = _report(
        period_breakdown_trading_result=trading,
        period_breakdown_frank_slim=slim,
        period_result=result,
        period_breakdown_epex_correction=correction,
    )
    expected = {
        "period_trading_result": result,
        "period_imbalance_result": trading,
        "period_frank_slim_bonus": slim,
        "period_epex_result": correction,
        "period_total_result": total,
    }
    entities = {
        desc.key: FrankEnergieBatterySessionSensor(coordinator, desc, _BATTERY)
        for desc in BATTERY_SESSION_SENSOR_DESCRIPTIONS
        if desc.key in expected
    }
    assert entities.keys() == expected.keys()
    attributes = entities["period_total_result"].extra_state_attributes
    for key, amount in expected.items():
        assert entities[key].unique_id == f"{_BATTERY}_{key}"
        assert entities[key].state_class is None
        attribute = "period_frank_slim" if key == "period_frank_slim_bonus" else key
        if amount is None:
            assert entities[key].native_value is None
            assert attributes[attribute] is None
        else:
            assert entities[key].native_value == pytest.approx(amount)
            assert attributes[attribute] == pytest.approx(amount)


async def _load_entry(
    hass: HomeAssistant,
    battery_ids: tuple[str, ...] | None = (_BATTERY,),
    *,
    authenticated: bool = True,
) -> MockConfigEntry:
    """Register the real action with an entry-specific client and inventory."""
    entry = MockConfigEntry(domain=DOMAIN, data={"site_reference": "example-site"})
    entry.add_to_hass(hass)
    api = FrankEnergie(
        async_get_clientsession(hass), auth_token="example" if authenticated else None
    )
    coordinator = FrankEnergieBatteryCoordinator(hass, entry, api, MagicMock())
    inventory = (
        SimpleNamespace(batteries=[SimpleNamespace(id=item) for item in battery_ids])
        if battery_ids is not None
        else None
    )
    coordinator.data[DATA_BATTERIES] = inventory
    entry.runtime_data = SimpleNamespace(battery_coordinator=coordinator)
    with patch("custom_components.frank_energie.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def _request(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    start: str | date = _DAY,
    end: str | date = _DAY,
) -> ServiceResponse:
    """Call the response-only action in the same way as an automation."""
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_BATTERY_RESULTS,
        {"config_entry": entry.entry_id, "start_date": start, "end_date": end},
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict)
    return response


async def test_single_day_through_sdk_parser(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Verify the action and all five daily entities against the SDK's real GraphQL parser."""
    freezer.move_to("2026-11-15T09:00:00Z")
    entry = await _load_entry(hass)
    coordinator = entry.runtime_data.battery_coordinator
    payload = {
        "data": {
            "smartBatterySessions": {
                "deviceId": _BATTERY,
                "periodStartDate": _DAY,
                "periodEndDate": _DAY,
                "periodBreakdownTradingResult": 0.1613,
                "periodBreakdownFrankSlim": 0.0202,
                "periodResult": 0.1814,
                "periodBreakdownEpexCorrection": -0.0034,
                "sessions": [
                    {"date": _DAY, "result": 0.1814, "status": "COMPLETE_FINAL"}
                ],
            }
        }
    }
    with patch.object(
        coordinator.api, "_query", new=AsyncMock(return_value=payload)
    ) as query:
        response = await _request(hass, entry)
    query.assert_awaited_once()
    assert query.await_args.args[0].variables == {
        "deviceId": _BATTERY,
        "startDate": _DAY,
        "endDate": _DAY,
    }
    assert response == {
        "start_date": _DAY,
        "end_date": _DAY,
        "period_days": 1,
        "batteries": {
            _BATTERY: {
                "trading_result": 0.1613,
                "frank_slim": 0.0202,
                "result": 0.1814,
                "epex_correction": -0.0034,
                "total": 0.1780,
                "sessions": [
                    {"date": _DAY, "result": 0.1814, "status": "complete_final"}
                ],
            }
        },
    }
    daily = BatteryDailyCoordinator(hass, entry)
    coordinator.update_interval = None
    with patch.object(coordinator.api, "_query", return_value=payload) as daily_query:
        daily.async_start()
        await hass.async_block_till_done(wait_background_tasks=True)
    daily_query.assert_awaited_once()
    assert (
        daily_query.await_args.args[0].variables == query.await_args.args[0].variables
    )
    assert [
        BatteryDailySensor(daily, _BATTERY, desc).native_value for desc in DAILY_SENSORS
    ] == pytest.approx([0.1814, 0.1613, 0.0202, -0.0034, 0.1780])


async def test_multi_battery_range_is_entry_scoped(hass: HomeAssistant) -> None:
    """Keep partial results, daily statuses and polling snapshots independent."""
    entry = await _load_entry(hass, (_BATTERY, "battery-b", "battery-c"))
    other = await _load_entry(hass, ("other-battery",))
    coordinator = entry.runtime_data.battery_coordinator
    original = coordinator.data.copy()
    rows = [
        SmartBatterySession.from_dict(
            {"date": _YEAR_END, "result": 0, "status": "COMPLETE_FINAL"}
        ),
        SmartBatterySession.from_dict(
            {"date": _NEW_YEAR, "result": None, "status": "PENDING"}
        ),
    ]
    first = _report(start=_YEAR_END, end=_NEW_YEAR, period_result=0.0, sessions=rows)
    last = _report(
        "battery-c", _YEAR_END, _NEW_YEAR, period_breakdown_epex_correction=0.0
    )
    with (
        patch.object(
            coordinator.api,
            "smart_battery_sessions",
            side_effect=[first, RequestException(), last],
        ) as fetch,
        patch.object(
            other.runtime_data.battery_coordinator.api, "smart_battery_sessions"
        ) as foreign,
    ):
        response = await _request(hass, entry, _YEAR_END, _NEW_YEAR)
    start, end = date.fromisoformat(_YEAR_END), date.fromisoformat(_NEW_YEAR)
    assert fetch.await_args_list == [
        call(device, start, end) for device in (_BATTERY, "battery-b", "battery-c")
    ]
    foreign.assert_not_awaited()
    assert coordinator.data == original
    assert (response["start_date"], response["end_date"]) == (_YEAR_END, _NEW_YEAR)
    batteries = response["batteries"]
    assert set(batteries) == {_BATTERY, "battery-b", "battery-c"}
    assert batteries[_BATTERY] == {
        "trading_result": None,
        "frank_slim": None,
        "result": 0.0,
        "epex_correction": None,
        "total": None,
        "sessions": [
            {"date": _YEAR_END, "result": 0.0, "status": "complete_final"},
            {"date": _NEW_YEAR, "result": None, "status": "pending"},
        ],
    }
    assert batteries["battery-b"] is None
    assert batteries["battery-c"]["epex_correction"] == pytest.approx(0)
    assert batteries["battery-c"]["total"] is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        TimeoutError(),
        ValueError("invalid upstream session"),
        _report("other-battery"),
        _report(start="2026-11-13"),
        _report(end="2026-11-15"),
        _report(sessions=[SmartBatterySession.from_dict({"date": "2026-11-15"})]),
    ],
)
async def test_unavailable_battery_result(hass: HomeAssistant, value: object) -> None:
    """Reject failed, wrong-device and out-of-range responses for this battery."""
    entry = await _load_entry(hass)
    with patch.object(
        entry.runtime_data.battery_coordinator.api,
        "smart_battery_sessions",
        side_effect=[value],
    ):
        response = await _request(hass, entry)
    assert response["batteries"] == {_BATTERY: None}


async def test_shared_rate_limit_and_maintenance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Stop the batch on throttling and honor the same cooldown on later calls."""
    entry = await _load_entry(hass, (_BATTERY, "battery-b", "battery-c"))
    coordinator = entry.runtime_data.battery_coordinator
    limited = RateLimitException("SmartBatterySessions", retry_after=120)
    with patch.object(
        coordinator.api,
        "smart_battery_sessions",
        side_effect=[_report(period_result=1), limited],
    ) as fetch:
        first = await _request(hass, entry)
        assert first["batteries"][_BATTERY]["result"] == pytest.approx(1)
        assert first["batteries"]["battery-b"] is None
        assert first["batteries"]["battery-c"] is None
        second = await _request(hass, entry)
        assert all(value is None for value in second["batteries"].values())
        assert fetch.await_count == 2

        freezer.move_to("2027-01-03T00:30:00Z")
        during_maintenance = await _request(hass, entry)
        assert all(value is None for value in during_maintenance["batteries"].values())
        assert fetch.await_count == 2


async def test_inventory_and_entry_validation(hass: HomeAssistant) -> None:
    """Distinguish a known empty inventory from unavailable data or credentials."""
    empty = await _load_entry(hass, ())
    assert (await _request(hass, empty))["batteries"] == {}
    descriptions = await async_get_all_descriptions(hass)
    fields = descriptions[DOMAIN][SERVICE_GET_BATTERY_RESULTS]["fields"]
    assert set(fields) == {"config_entry", "start_date", "end_date"}

    for ids in (None, ("",), (_BATTERY, _BATTERY)):
        invalid = await _load_entry(hass, ids)
        with pytest.raises(HomeAssistantError) as exc_info:
            await _request(hass, invalid)
        assert exc_info.value.translation_key == "battery_inventory_unavailable"
    empty.runtime_data.battery_coordinator.last_update_success = False
    with pytest.raises(HomeAssistantError) as exc_info:
        await _request(hass, empty)
    assert exc_info.value.translation_key == "battery_inventory_unavailable"

    public = await _load_entry(hass, authenticated=False)
    with pytest.raises(ServiceValidationError) as exc_info:
        await _request(hass, public)
    assert exc_info.value.translation_key == "battery_results_auth_required"
    unloaded = MockConfigEntry(domain=DOMAIN)
    unloaded.add_to_hass(hass)
    with pytest.raises(ServiceValidationError) as exc_info:
        await _request(hass, unloaded)
    assert exc_info.value.translation_key == "service_config_entry_not_loaded"


async def test_invalid_dates_are_rejected_before_fetch(hass: HomeAssistant) -> None:
    """Reject reversed ranges, impossible dates and timestamps before any API call."""
    entry = await _load_entry(hass)
    with patch.object(
        entry.runtime_data.battery_coordinator.api, "smart_battery_sessions"
    ) as fetch:
        with pytest.raises(ServiceValidationError) as exc_info:
            await _request(hass, entry, _NEW_YEAR, _YEAR_END)
        assert exc_info.value.translation_key == "battery_results_reversed_dates"
        for invalid in (
            "2026-02-30",
            "tomorrow",
            datetime.fromisoformat("2026-11-14T12:00:00+01:00"),
        ):
            with pytest.raises(probatio.Invalid):
                await _request(hass, entry, invalid)
        fetch.assert_not_awaited()


@pytest.mark.parametrize(
    ("start", "end", "days"),
    [
        ("2026-11-14", "2026-11-14", 1),
        ("2026-12-31", "2027-01-02", 3),
        ("2028-02-28", "2028-03-01", 3),
        ("2026-03-28", "2026-03-30", 3),
        ("2026-10-24", "2026-10-26", 3),
    ],
)
def test_period_calendar_days(start: str, end: str, days: int) -> None:
    """Count inclusive local dates across leap days, DST and year boundaries."""
    assert (
        BatteryPeriod(date.fromisoformat(start), date.fromisoformat(end)).days == days
    )


async def test_default_period_rolls_over_at_amsterdam_midnight(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Only entries without a saved selection follow the new local calendar month."""
    automatic = await _load_entry(hass)
    fixed = await _load_entry(hass)
    saved = {"start_date": _DAY, "end_date": _DAY}
    hass.config_entries.async_update_entry(fixed, options={CONF_BATTERY_PERIOD: saved})
    freezer.move_to("2026-03-31T21:59:59Z")
    assert BatteryPeriod.for_entry(automatic) == BatteryPeriod(
        date(2026, 3, 1), date(2026, 3, 31)
    )
    freezer.move_to("2026-03-31T22:00:00Z")
    assert BatteryPeriod.for_entry(automatic) == BatteryPeriod(
        date(2026, 4, 1), date(2026, 4, 1)
    )
    assert BatteryPeriod.for_entry(fixed) == BatteryPeriod(
        date.fromisoformat(_DAY), date.fromisoformat(_DAY)
    )


async def test_action_reads_own_selection_and_accepts_explicit_pair(
    hass: HomeAssistant,
) -> None:
    """Omitting both dates selects only this entry's range; explicit dates are one-shot."""
    first = await _load_entry(hass)
    other = await _load_entry(hass)
    saved = {"start_date": _YEAR_END, "end_date": _NEW_YEAR}
    hass.config_entries.async_update_entry(first, options={CONF_BATTERY_PERIOD: saved})

    async def report(entry: MockConfigEntry, **fields: str) -> ServiceResponse:
        """Call the action with precisely the fields a dashboard or automation supplied."""
        return await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_BATTERY_RESULTS,
            {"config_entry": entry.entry_id, **fields},
            blocking=True,
            return_response=True,
        )

    with (
        patch.object(
            first.runtime_data.battery_coordinator.api,
            "smart_battery_sessions",
            return_value=None,
        ) as fetch,
        patch.object(
            other.runtime_data.battery_coordinator.api,
            "smart_battery_sessions",
            return_value=None,
        ),
    ):
        for field in ("start_date", "end_date"):
            with pytest.raises(probatio.Invalid):
                await report(first, **{field: _DAY})
        fetch.assert_not_awaited()
        selected = await report(first)
        assert (
            selected["start_date"],
            selected["end_date"],
            selected["period_days"],
        ) == (_YEAR_END, _NEW_YEAR, 2)
        fetch.assert_awaited_once_with(_BATTERY, date(2026, 12, 31), date(2027, 1, 1))
        automatic = await report(other)
        assert (automatic["start_date"], automatic["end_date"]) == (
            _NEW_YEAR,
            "2027-01-02",
        )
        override = await _request(hass, first)
        assert override["period_days"] == 1
        assert first.options[CONF_BATTERY_PERIOD] == saved


async def test_late_period_response_stays_hidden_after_edit(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A late response or maintenance cannot publish a previous period's settlement."""
    entry = await _load_entry(hass)
    api = entry.runtime_data.battery_coordinator.api
    session = FrankEnergieBatterySessionCoordinator(hass, entry, api, _BATTERY)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(battery_id: str, start: date, end: date) -> SimpleNamespace:
        """Hold the first report until the entry has switched to a single day."""
        started.set()
        await release.wait()
        return _report(
            battery_id, start.isoformat(), end.isoformat(), period_result=0.0
        )

    with patch.object(api, "smart_battery_sessions", side_effect=delayed) as fetch:
        pending = hass.async_create_task(session.async_refresh())
        await started.wait()
        hass.config_entries.async_update_entry(
            entry, options={CONF_BATTERY_PERIOD: {"start_date": _DAY, "end_date": _DAY}}
        )
        release.set()
        await pending
        assert session.period_data is None
        await session.async_refresh()
        assert session.period_data.period_result == pytest.approx(0.0)
        freezer.move_to("2027-01-03T00:15:00Z")
        await session.async_refresh()
        assert not session.last_update_success
        assert fetch.await_count == 2


def test_empty_sdk_session_list_keeps_period_metadata() -> None:
    """The SDK collection can be falsey while carrying period data."""
    data = SmartBatterySessions.from_dict(
        {
            "data": {
                "smartBatterySessions": {
                    "deviceId": _BATTERY,
                    "periodStartDate": _DAY,
                    "periodEndDate": _DAY,
                    "periodTradeIndex": 0,
                    "sessions": [],
                }
            }
        }
    )
    coordinator = MagicMock()
    coordinator.period_data = data
    description = next(
        item
        for item in BATTERY_SESSION_SENSOR_DESCRIPTIONS
        if item.key == "period_total_result"
    )
    entity = FrankEnergieBatterySessionSensor(coordinator, description, _BATTERY)
    assert entity.extra_state_attributes["device_id"] == _BATTERY
    assert entity.extra_state_attributes["period_trade_index"] == 0
    assert entity.native_value is None


async def test_two_batteries_register_each_session_sensor_once(
    hass: HomeAssistant,
) -> None:
    """Keep one unique entity per battery and sensor description on setup."""
    entry = await _load_entry(hass)
    coordinator = entry.runtime_data.battery_coordinator
    ids = (_BATTERY, "battery-b")
    coordinator.data[DATA_BATTERIES] = SmartBatteries(
        [SmartBattery(id=item) for item in ids]
    )
    other_coordinator = MagicMock()
    other_coordinator.api = coordinator.api
    other_coordinator.data = {}
    other_coordinator.config_entry = entry
    entry.runtime_data = FrankEnergieEntryData(
        rate_limits=coordinator.rate_limits,
        coordinator=other_coordinator,
        settings_coordinator=other_coordinator,
        price_coordinator=other_coordinator,
        battery_coordinator=coordinator,
        charger_coordinator=other_coordinator,
        vehicle_coordinator=other_coordinator,
        pv_coordinator=other_coordinator,
        invoice_coordinator=other_coordinator,
        statistics_coordinator=other_coordinator,
    )
    add_entities = MagicMock()
    with (
        patch.object(FrankEnergieBatterySessionCoordinator, "async_refresh"),
        patch.object(coordinator.api, "smart_battery_sessions", return_value=None),
    ):
        await async_setup_sensors(hass, entry, add_entities)
        await hass.async_block_till_done(wait_background_tasks=True)

    entity_ids = [
        entity.unique_id
        for added in add_entities.call_args_list
        for entity in added.args[0]
        if isinstance(entity, FrankEnergieBatterySessionSensor)
    ]
    assert sorted(entity_ids) == sorted(
        f"{battery_id}_{description.key}"
        for battery_id in ids
        for description in BATTERY_SESSION_SENSOR_DESCRIPTIONS
    )


def _daily_report(
    battery_id: str, day: date, status: str, **amounts: float | None
) -> SimpleNamespace:
    """Return a parsed SDK-shaped report for a single day with explicit settlement fields."""
    row = SimpleNamespace(date=day, status=status, result=99)
    return _report(
        battery_id, day.isoformat(), day.isoformat(), sessions=[row], **amounts
    )


async def test_yesterday_sensors_share_final_snapshot_and_retry_only_pending_batteries(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Expose five signed values per day, independently of other batteries and manual periods."""
    entry = await _load_entry(hass, (_BATTERY, "battery-b"))
    inventory = entry.runtime_data.battery_coordinator
    inventory.update_interval = None
    daily = BatteryDailyCoordinator(hass, entry)
    day = date.fromisoformat(_NEW_YEAR)
    final = _daily_report(
        _BATTERY,
        day,
        "COMPLETE_FINAL",
        period_result=0.0899,
        period_breakdown_trading_result=0.1221,
        period_breakdown_frank_slim=-0.0323,
        period_breakdown_epex_correction=-0.6557,
    )
    pending = _daily_report("battery-b", day, "COMPLETED", period_result=0)
    final_b = _daily_report(
        "battery-b",
        day,
        "COMPLETE_FINAL",
        period_result=0,
        period_breakdown_epex_correction=0.0735,
    )
    with patch.object(
        inventory.api,
        "smart_battery_sessions",
        side_effect=[final, pending, None, final_b],
    ) as fetch:
        daily.async_start()
        await hass.async_block_till_done(wait_background_tasks=True)
        sensors = [BatteryDailySensor(daily, _BATTERY, desc) for desc in DAILY_SENSORS]
        assert [sensor.native_value for sensor in sensors] == pytest.approx(
            [0.0899, 0.1221, -0.0323, -0.6557, -0.5658]
        )
        assert sensors[0].unique_id == f"{_BATTERY}_daily_trading_result"
        assert all(
            sensor.unique_id.startswith(f"{entry.entry_id}_{_BATTERY}_")
            for sensor in sensors[1:]
        )
        assert all(
            sensor.extra_state_attributes
            == {"result_date": _NEW_YEAR, "session_status": "complete_final"}
            for sensor in sensors
        )
        assert sensors[0].last_reset.astimezone(UTC) == datetime(
            2026, 12, 31, 23, tzinfo=UTC
        )
        assert daily.result("battery-b").status == "completed"
        assert daily.result("battery-b").amounts is None
        assert fetch.await_args_list == [
            call(_BATTERY, day, day),
            call("battery-b", day, day),
        ]

        hass.config_entries.async_update_entry(
            entry, options={CONF_BATTERY_PERIOD: {"start_date": _DAY, "end_date": _DAY}}
        )
        for clock in ("09:12", "09:15", "09:44", "09:45", "21:59"):
            freezer.move_to(f"2027-01-02T{clock}:00Z")
            inventory.async_update_listeners()
            inventory.async_update_listeners()
            await hass.async_block_till_done(wait_background_tasks=True)
            if clock == "09:15":
                assert daily.result("battery-b").status == "missing"
        assert fetch.await_args_list == [
            call(_BATTERY, day, day),
            *[call("battery-b", day, day)] * 3,
        ]
        assert daily.result("battery-b").amounts["result"] == 0
        assert daily.result("battery-b").amounts["total"] == pytest.approx(0.0735)
        assert daily.result("battery-b").amounts["frank_slim"] is None
        assert daily.result(_BATTERY).amounts["total"] == pytest.approx(-0.5658)


async def test_daily_results_keep_errors_local_and_respect_maintenance_and_throttling(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A throttled site shares a deadline; an independent site with the same battery can finalize."""
    freezer.move_to("2027-01-02T00:20:00Z")
    first = await _load_entry(hass, (_BATTERY, "battery-b"))
    other = await _load_entry(hass)
    first_inventory = first.runtime_data.battery_coordinator
    other_inventory = other.runtime_data.battery_coordinator
    first_inventory.update_interval = other_inventory.update_interval = None
    daily = BatteryDailyCoordinator(hass, first)
    independent = BatteryDailyCoordinator(hass, other)
    day = date.fromisoformat(_NEW_YEAR)
    first_final = _daily_report(
        _BATTERY,
        day,
        "COMPLETE_FINAL",
        period_result=0,
        period_breakdown_epex_correction=0,
    )
    other_final = _daily_report(
        _BATTERY,
        day,
        "COMPLETE_FINAL",
        period_result=0.1814,
        period_breakdown_epex_correction=-0.0034,
    )
    with (
        patch.object(
            first_inventory.api,
            "smart_battery_sessions",
            side_effect=[
                RateLimitException("SmartBatterySessions", retry_after=5400),
                first_final,
                RequestException("test failure"),
            ],
        ) as first_fetch,
        patch.object(
            other_inventory.api, "smart_battery_sessions", return_value=other_final
        ) as other_fetch,
    ):
        daily.async_start()
        independent.async_start()
        await hass.async_block_till_done(wait_background_tasks=True)
        first_fetch.assert_not_awaited()
        other_fetch.assert_not_awaited()
        freezer.move_to("2027-01-02T01:00:00Z")
        daily.async_check()
        independent.async_check()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert daily.result(_BATTERY).status == "unavailable"
        assert independent.result(_BATTERY).amounts["total"] == pytest.approx(0.1780)
        freezer.move_to("2027-01-02T02:29:59Z")
        daily.async_check()
        await hass.async_block_till_done(wait_background_tasks=True)
        first_fetch.assert_awaited_once_with(_BATTERY, day, day)
        freezer.move_to("2027-01-02T02:30:01Z")
        daily.async_check()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert first_fetch.await_args_list == [
            call(_BATTERY, day, day),
            call(_BATTERY, day, day),
            call("battery-b", day, day),
        ]
        assert daily.result(_BATTERY).amounts["total"] == 0
        assert daily.result("battery-b").status == "unavailable"
        other_fetch.assert_awaited_once_with(_BATTERY, day, day)


@pytest.mark.parametrize(
    ("midnight", "day"),
    [
        ("2026-03-29T22:00:00Z", "2026-03-29"),
        ("2026-10-25T23:00:00Z", "2026-10-25"),
        ("2026-12-31T23:00:00Z", _YEAR_END),
    ],
)
async def test_daily_rollover_discards_old_inflight_report(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, midnight: str, day: str
) -> None:
    """Advance while an old request is held open across a short, long or year-end day."""
    boundary = datetime.fromisoformat(midnight)
    freezer.move_to(boundary - timedelta(seconds=2))
    entry = await _load_entry(hass)
    inventory = entry.runtime_data.battery_coordinator
    inventory.update_interval = None
    daily = BatteryDailyCoordinator(hass, entry)
    old_started = asyncio.Event()
    release_old = asyncio.Event()
    target = date.fromisoformat(day)
    old_day = target - timedelta(days=1)

    async def delayed(battery_id: str, start: date, end: date) -> SimpleNamespace:
        """Return different settlement amounts on either side of the simulated midnight."""
        assert start == end
        if start == old_day:
            old_started.set()
            await release_old.wait()
        return _daily_report(
            battery_id,
            start,
            "complete_final",
            period_result=0 if start == target else 12.5,
        )

    with patch.object(
        inventory.api, "smart_battery_sessions", side_effect=delayed
    ) as fetch:
        daily.async_start()
        await old_started.wait()
        freezer.move_to(boundary)
        async_fire_time_changed(hass, boundary)
        await hass.async_block_till_done()
        assert daily.result(_BATTERY).day == target
        assert daily.result(_BATTERY).amounts is None
        release_old.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        assert fetch.await_args_list == [
            call(_BATTERY, old_day, old_day),
            call(_BATTERY, target, target),
        ]
        assert daily.result(_BATTERY).amounts["result"] == 0


async def test_finalized_daily_amount_expires_at_midnight_without_inventory_poll(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A real HA clock event clears yesterday even while the next day's first response is pending."""
    freezer.move_to("2026-12-31T22:59:58Z")
    entry = await _load_entry(hass)
    inventory = entry.runtime_data.battery_coordinator
    inventory.update_interval = None
    daily = BatteryDailyCoordinator(hass, entry)
    started, release = asyncio.Event(), asyncio.Event()
    old_day, new_day = date(2026, 12, 30), date(2026, 12, 31)

    async def report(battery_id: str, start: date, _end: date) -> SimpleNamespace:
        """Delay the new day's settlement until the listener has published its pending state."""
        if start == new_day:
            started.set()
            await release.wait()
        return _daily_report(battery_id, start, "complete_final", period_result=1)

    snapshots = []
    with patch.object(
        inventory.api, "smart_battery_sessions", side_effect=report
    ) as fetch:
        daily.async_start()
        entry.async_on_unload(
            daily.async_add_listener(lambda: snapshots.append(daily.result(_BATTERY)))
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert snapshots[-1].day == old_day and snapshots[-1].amounts["result"] == 1
        boundary = datetime(2026, 12, 31, 23, tzinfo=UTC)
        freezer.move_to(boundary)
        async_fire_time_changed(hass, boundary)
        await asyncio.wait_for(started.wait(), 5)
        assert (snapshots[-1].day, snapshots[-1].status, snapshots[-1].amounts) == (
            new_day,
            "pending",
            None,
        )
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        await daily.async_refresh()
        assert fetch.await_args_list == [
            call(_BATTERY, old_day, old_day),
            call(_BATTERY, new_day, new_day),
        ]


async def test_documented_automation_catches_up_only_after_final_settlement(
    hass: HomeAssistant,
) -> None:
    """Validate the published YAML and render its date cursor and finality guards in Home Assistant."""
    guide = Path(__file__).parents[1] / "docs" / "smart_battery.md"
    section = guide.read_text(encoding="utf-8").split(
        "### Results for a date range", 1
    )[1]
    example = yaml.safe_load(section.split("```yaml\n", 1)[1].split("\n```", 1)[0])
    assert await async_validate_config_item(hass, "automation", example) is not None
    actions = example["actions"]
    cursor, past, finalized = [
        Template(item["value_template"], hass)
        for item in actions
        if item.get("condition") == "template"
    ]
    next_day = Template(
        next(
            item["variables"]["day"]
            for item in actions
            if "day" in item.get("variables", {})
        ),
        hass,
    )
    assert not cursor.async_render({"last_date": "unavailable"})
    assert cursor.async_render({"last_date": _YEAR_END})
    assert next_day.async_render({"last_date": _YEAR_END}) == _NEW_YEAR
    assert past.async_render({"day": _NEW_YEAR})
    assert not past.async_render({"day": "2027-01-02"})
    for status in ("pending", "completed", "complete_final"):
        row = {"total": 0.0, "sessions": [{"status": status}]}
        assert finalized.async_render({"battery": row}) == (status == "complete_final")
    assert not finalized.async_render({"battery": None})
    assert actions[-1]["action"] == "input_datetime.set_datetime"


async def test_cancelling_a_range_stops_later_requests(hass: HomeAssistant) -> None:
    """Let Home Assistant cancel an in-flight batch without issuing more queries."""
    entry = await _load_entry(hass, (_BATTERY, "battery-b"))
    with patch.object(
        entry.runtime_data.battery_coordinator.api,
        "smart_battery_sessions",
        side_effect=asyncio.CancelledError,
    ) as fetch:
        with pytest.raises(asyncio.CancelledError):
            await _request(hass, entry)
    fetch.assert_awaited_once()
