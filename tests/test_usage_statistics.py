"""Exercise hourly provider statistics with a real Home Assistant Recorder."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, call, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    StatisticsRow,
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter
from pytest_homeassistant.common import MockConfigEntry
from python_frank_energie.models import EnergyCategory, PeriodUsageAndCosts, UsageItem

from custom_components.frank_energie.const import DATA_USAGE, DOMAIN
from custom_components.frank_energie.usage_statistics import (
    _completed_usage_hours,
    async_record_period_usage,
)

from .test_contract_support import _api_for_contracts

_START = datetime(2026, 7, 19, 2, tzinfo=UTC)
_NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


def _reading(
    start: datetime,
    amount: float,
    costs: float,
    *,
    minutes: int = 60,
    unit: str = "kWh",
) -> UsageItem:
    """Parse a provider interval through the pinned client model."""
    return UsageItem.from_dict(
        {
            "date": start.date().isoformat(),
            "from": start.isoformat(),
            "till": (start + timedelta(minutes=minutes)).isoformat(),
            "usage": amount,
            "costs": costs,
            "unit": unit,
        }
    )


def _category(*readings: UsageItem) -> EnergyCategory:
    """Provide item-level source data without usable day totals."""
    return EnergyCategory(None, None, readings[0].unit, list(readings))


def _entry(hass: HomeAssistant, site_reference: str = "test-site") -> MockConfigEntry:
    """Register an authenticated entry for a fixture delivery site."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=site_reference,
        unique_id=site_reference,
        data={
            "site_reference": site_reference,
            "access_token": "fixture-access",
            "token": "fixture-refresh",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, api: AsyncMock) -> None:
    """Set up the real Frank Energie config entry with a mocked provider."""
    with patch("custom_components.frank_energie.FrankEnergie", return_value=api):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def _history(
    hass: HomeAssistant, recorder: Recorder
) -> tuple[dict[str, dict[str, object]], dict[str, list[StatisticsRow]]]:
    """Read the metadata and rows that the Energy Dashboard can select."""
    await recorder.async_block_till_done()
    statistics = await recorder.async_add_executor_job(list_statistic_ids, hass)
    metadata = {
        fields["statistic_id"]: fields
        for fields in statistics
        if fields["source"] == DOMAIN
    }
    return metadata, await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        _START,
        None,
        set(metadata),
        "hour",
        None,
        {"sum", "state"},
    )


async def test_usage_imports_utc_hours_and_preserves_meter_units(
    async_setup_recorder_instance: Callable[[HomeAssistant], Awaitable[Recorder]],
    hass: HomeAssistant,
) -> None:
    """Import complete hours and retain costs, true zeros, and source units."""
    recorder = await async_setup_recorder_instance(hass)
    entry = _entry(hass)
    quarters = [
        _reading(_START + timedelta(minutes=15 * index), 0.25, -0.125, minutes=15)
        for index in range(4)
    ]
    next_hour = _START + timedelta(hours=1)
    later_hour = _START + timedelta(hours=2)
    usage = PeriodUsageAndCosts(
        "fixture-period",
        _category(_reading(_START, 1, 0.4, unit="m3"), _reading(later_hour, 2, 0.3)),
        _category(
            *quarters,
            quarters[0],
            _reading(next_hour, 0.5, 0.1, minutes=15),
            _reading(later_hour, 0, 0),
            _reading(_NOW, 1, 0.1),
        ),
        _category(_reading(_START, 0.75, 0.2)),
    )

    await async_record_period_usage(hass, entry, "test-site", usage, _NOW)
    metadata, history = await _history(hass, recorder)

    assert len(metadata) == 7
    assert all(
        fields["mean_type"] == StatisticMeanType.NONE for fields in metadata.values()
    )
    assert all(fields["has_sum"] for fields in metadata.values())
    assert all("test-site" not in statistic_id for statistic_id in history)
    by_series = {
        key.removeprefix(f"{DOMAIN}:").rsplit("_", 1)[0]: rows
        for key, rows in history.items()
    }
    electricity = by_series["electricity_usage_kwh"]
    assert [row["start"] for row in electricity] == [
        _START.timestamp(),
        later_hour.timestamp(),
    ]
    assert [row["state"] for row in electricity] == pytest.approx([1, 0])
    assert [row["sum"] for row in electricity] == pytest.approx([1, 1])
    assert [row["sum"] for row in by_series["electricity_cost"]] == pytest.approx(
        [-0.5, -0.5]
    )
    assert by_series["feed_in_usage_kwh"][0]["state"] == pytest.approx(0.75)
    assert by_series["feed_in_cost"][0]["state"] == pytest.approx(0.2)
    unit_classes = {
        fields["statistics_unit_of_measurement"]: fields["unit_class"]
        for fields in metadata.values()
    }
    assert unit_classes == {
        "kWh": EnergyConverter.UNIT_CLASS,
        "m³": VolumeConverter.UNIT_CLASS,
        "EUR": None,
    }


async def test_usage_refresh_corrects_later_sums_after_reload(
    async_setup_recorder_instance: Callable[[HomeAssistant], Awaitable[Recorder]],
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    enable_custom_integrations: None,
) -> None:
    """Keep site history isolated and resume from persisted hours after reloading."""
    recorder = await async_setup_recorder_instance(hass)
    freezer.move_to(_NOW)
    entry = _entry(hass)
    original_usage = PeriodUsageAndCosts(
        "fixture-period",
        None,
        _category(
            _reading(_START, 1, 0.25), _reading(_START + timedelta(hours=2), 2, 0.5)
        ),
        None,
    )
    api = _api_for_contracts(("ELECTRICITY",), 15)
    api.period_usage_and_costs.return_value = original_usage
    await _setup(hass, entry, api)
    coordinator = entry.runtime_data.statistics_coordinator
    assert coordinator.data[DATA_USAGE] is original_usage

    other = _entry(hass, "other-site")
    other_api = _api_for_contracts(("ELECTRICITY", "GAS"), 15)
    other_api.period_usage_and_costs.return_value = PeriodUsageAndCosts(
        "other-period", None, _category(_reading(_START, 5, 0.9)), None
    )
    await _setup(hass, other, other_api)
    metadata, history = await _history(hass, recorder)
    assert len(metadata) == 4
    selected_id = next(
        key
        for key, fields in metadata.items()
        if fields["name"] == "test-site Electricity consumption"
    )
    other_id = next(
        key
        for key, fields in metadata.items()
        if fields["name"] == "other-site Electricity consumption"
    )
    assert [row["sum"] for row in history[selected_id]] == pytest.approx([1, 3])
    assert [row["sum"] for row in history[other_id]] == pytest.approx([5])

    api.period_usage_and_costs.return_value = PeriodUsageAndCosts(
        "corrected-period",
        None,
        _category(
            _reading(_START, 2, 0.25), _reading(_START + timedelta(hours=1), 3, 0.25)
        ),
        None,
    )
    with patch("custom_components.frank_energie.FrankEnergie", return_value=api):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.statistics_coordinator is not coordinator
    await entry.runtime_data.statistics_coordinator.async_refresh()
    _, corrected = await _history(hass, recorder)

    assert [row["state"] for row in corrected[selected_id]] == pytest.approx([2, 3, 2])
    assert [row["sum"] for row in corrected[selected_id]] == pytest.approx([2, 5, 7])
    assert corrected[other_id] == history[other_id]

    api.period_usage_and_costs.return_value = PeriodUsageAndCosts(
        "later-period",
        None,
        _category(_reading(_START + timedelta(hours=3), 1, 0)),
        None,
    )
    await entry.runtime_data.statistics_coordinator.async_refresh()
    _, appended = await _history(hass, recorder)
    assert [row["sum"] for row in appended[selected_id]] == pytest.approx([2, 5, 7, 8])
    assert (
        api.period_usage_and_costs.await_args_list
        == [call("test-site", "2026-07-19")] * 4
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_usage_refresh_without_recorder(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Keep the existing usage refresh available when Recorder is absent."""
    freezer.move_to(_NOW)
    entry = _entry(hass)
    api = _api_for_contracts(("ELECTRICITY",), 15)
    usage = PeriodUsageAndCosts(
        "fixture-period", None, _category(_reading(_START, 1, 0.3)), None
    )
    api.period_usage_and_costs.return_value = usage
    await _setup(hass, entry, api)

    assert "recorder" not in hass.config.components
    assert entry.runtime_data.statistics_coordinator.data[DATA_USAGE] is usage
    api.period_usage_and_costs.assert_awaited_once_with("test-site", "2026-07-19")


def test_usage_wall_clock_and_repeated_dst_hour() -> None:
    """Use explicit offsets for repeated hours and accept an ordinary midnight."""
    records = _category(
        UsageItem("2026-07-19", "23:00:00", "24:00:00", 1, 0.5, "kWh"),
        UsageItem("2026-10-25", "02:00", "03:00", 100, 5, "kWh"),
        UsageItem(
            "2026-10-25",
            "2026-10-25T02:00:00+02:00",
            "2026-10-25T02:00:00+01:00",
            2,
            0,
            "kWh",
        ),
        UsageItem(
            "2026-10-25",
            "2026-10-25T02:00:00+01:00",
            "2026-10-25T03:00:00+01:00",
            3,
            0,
            "kWh",
        ),
    )
    hours = list(
        _completed_usage_hours(records, datetime(2026, 10, 26, tzinfo=UTC), gas=False)
    )
    assert [hour for hour, *_ in hours] == [
        datetime(2026, 7, 19, 21, tzinfo=UTC),
        datetime(2026, 10, 25, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 1, tzinfo=UTC),
    ]
    assert [usage for _, _, usage, _ in hours] == pytest.approx([1, 2, 3])
