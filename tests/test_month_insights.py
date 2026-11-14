"""Test MonthInsights Costs sensors and month-specific data isolation."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CURRENCY_EURO, UnitOfEnergy, UnitOfVolume
from python_frank_energie.models import MonthInsights

from custom_components.frank_energie import const
from custom_components.frank_energie.coordinator import (
    FrankEnergieStatisticsCoordinator,
)
from custom_components.frank_energie.sensor import (
    FrankEnergieSensor,
    SENSOR_TYPES,
    _sensor_energy_contract,
)


FIELDS = (
    ("expected_costs", "component"),
    ("actual_usage", "actualUsage"),
    ("actual_average_unit_price", "actualAverageUnitPrice"),
    ("actual_costs", "actualCosts"),
    ("expected_usage", "expectedUsage"),
    ("expected_average_unit_price", "expectedAverageUnitPrice"),
    ("expected_costs_comparison", "expectedCosts"),
)


def _insights(
    *, gas_excluded: bool = False, gas_unit: str = "m3", electricity_unit: str = "kWh"
) -> MonthInsights:
    """Parse a sanitized GraphQL response with deliberately distinct amounts."""
    result = MonthInsights.from_dict(
        {
            "data": {
                "monthInsights": {
                    "_id": "synthetic|2026-11",
                    "lastMeterReadingDate": "2026-11-13T00:00:00+00:00",
                    "gasExcluded": gas_excluded,
                    "expectedCostsGas": 71.5,
                    "expectedCostsElectricity": 52.5,
                    "gasDifference": {
                        "actualUsage": 4.0,
                        "actualAverageUnitPrice": 1.5,
                        "actualCosts": 6.0,
                        "expectedUsage": 8.0,
                        "expectedAverageUnitPrice": 2.5,
                        "expectedCosts": 20.0,
                        "unit": gas_unit,
                    },
                    "electricityDifference": {
                        "actualUsage": 14.0,
                        "actualAverageUnitPrice": 0.25,
                        "actualCosts": 3.5,
                        "expectedUsage": 18.0,
                        "expectedAverageUnitPrice": 0.35,
                        "expectedCosts": 6.3,
                        "unit": electricity_unit,
                    },
                }
            }
        }
    )
    assert result is not None
    return result


def _description(commodity: str, metric: str):
    """Return an independently keyed monthly finance description."""
    return next(
        item
        for item in SENSOR_TYPES
        if item.key == f"month_insights_{commodity}_{metric}"
    )


@pytest.mark.parametrize("commodity", ("electricity", "gas"))
@pytest.mark.parametrize("metric,field", FIELDS)
def test_all_fourteen_cost_fields(commodity: str, metric: str, field: str) -> None:
    """Each sensor must map its exact MonthInsights or Difference source field."""
    insights = _insights()
    description = _description(commodity, metric)
    expected = (
        getattr(
            insights,
            "expectedCostsGas" if commodity == "gas" else "expectedCostsElectricity",
        )
        if field == "component"
        else getattr(getattr(insights, f"{commodity}Difference"), field)
    )
    data = {const.DATA_MONTH_INSIGHTS: insights}
    assert description.value_fn(data) == pytest.approx(expected)
    assert description.service_name == const.SERVICE_NAME_COSTS
    assert description.requires_known_contract
    assert description.authenticated
    assert _sensor_energy_contract(description) == commodity.upper()
    assert description.translation_key == description.key


def test_registration_has_fourteen_unique_scoped_descriptions() -> None:
    """Add seven sensors per commodity without altering previous unique keys."""
    keys = [item.key for item in SENSOR_TYPES]
    assert len(keys) == len(set(keys))
    entries = [item for item in SENSOR_TYPES if item.requires_known_contract]
    assert len(entries) == 14
    assert sum(item.is_gas for item in entries) == 7
    assert sum(item.is_electricity for item in entries) == 7


@pytest.mark.parametrize("commodity", ("electricity", "gas"))
@pytest.mark.parametrize("metric,field", FIELDS)
def test_missing_insights_is_unknown(commodity: str, metric: str, field: str) -> None:
    """Absent MonthInsights cannot be turned into a valid numeric value."""
    assert (
        _description(commodity, metric).value_fn({const.DATA_MONTH_INSIGHTS: None})
        is None
    )


@pytest.mark.parametrize("commodity", ("electricity", "gas"))
def test_current_sdk_missing_numeric_fields_are_not_fabricated(commodity: str) -> None:
    """Reject SDK-defaulted zeros that cannot be distinguished from source null."""
    insights = MonthInsights.from_dict(
        {
            "data": {
                "monthInsights": {
                    "_id": "synthetic|2026-11",
                    "lastMeterReadingDate": "2026-11-13T00:00:00+00:00",
                    f"{commodity}Difference": {
                        "unit": "m3" if commodity == "gas" else "kWh"
                    },
                }
            }
        }
    )
    assert insights is not None
    for metric, _field in FIELDS:
        assert (
            _description(commodity, metric).value_fn(
                {const.DATA_MONTH_INSIGHTS: insights}
            )
            is None
        )


@pytest.mark.parametrize("metric,field", FIELDS)
def test_gas_excluded_is_not_reported(metric: str, field: str) -> None:
    """Gas-excluded statements must not claim real gas usage or costs."""
    assert (
        _description("gas", metric).value_fn(
            {const.DATA_MONTH_INSIGHTS: _insights(gas_excluded=True)}
        )
        is None
    )


@pytest.mark.parametrize("unit", ("", "unexpected", "m3"))
def test_invalid_electricity_unit_blocks_usage_and_prices(unit: str) -> None:
    """Never manufacture a kWh conversion from missing or gas volume units."""
    data = {const.DATA_MONTH_INSIGHTS: _insights(electricity_unit=unit)}
    assert _description("electricity", "actual_usage").value_fn(data) is None
    assert (
        _description("electricity", "expected_average_unit_price").value_fn(data)
        is None
    )


@pytest.mark.parametrize(
    "commodity,unit,expected",
    [
        ("electricity", "kWh", UnitOfEnergy.KILO_WATT_HOUR),
        ("gas", "m3", UnitOfVolume.CUBIC_METERS),
        ("gas", "kWh", UnitOfEnergy.KILO_WATT_HOUR),
    ],
)
def test_dynamic_units(commodity: str, unit: str, expected: str) -> None:
    """Use the provider's known unit instead of assuming every gas site is m³."""
    insights = _insights(
        gas_unit=unit if commodity == "gas" else "m3",
        electricity_unit=unit if commodity == "electricity" else "kWh",
    )
    coordinator = MagicMock()
    coordinator.data = {const.DATA_MONTH_INSIGHTS: insights}
    entry = MagicMock()
    entry.unique_id = "synthetic"
    entry.entry_id = "synthetic"
    for metric, expected_unit in (
        ("actual_usage", expected),
        ("expected_average_unit_price", f"{CURRENCY_EURO}/{expected}"),
    ):
        sensor = FrankEnergieSensor(coordinator, _description(commodity, metric), entry)
        assert sensor.native_unit_of_measurement == expected_unit


def test_month_attributes_do_not_leak_source_identifier() -> None:
    """Date-only estimates may still have period and original retrieval time."""
    desc = _description("gas", "expected_costs")
    data = {
        const.DATA_MONTH_INSIGHTS: SimpleNamespace(lastMeterReadingDate=None),
        const.DATA_MONTH_INSIGHTS_PERIOD: "2026-11",
        const.DATA_MONTH_INSIGHTS_FETCHED_AT: datetime(2026, 11, 14, tzinfo=UTC),
    }
    assert desc.attr_fn(data) == {
        "period": "2026-11",
        "source_updated_at": "2026-11-14T00:00:00+00:00",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "segments,calls",
    [
        ({"GAS"}, 1),
        ({"ELECTRICITY"}, 1),
        ({"GAS", "ELECTRICITY"}, 1),
        (set(), 0),
        (None, 0),
    ],
)
async def test_only_known_authenticated_segments_are_queried(
    segments: set[str] | None, calls: int
) -> None:
    """Fetch once for each known-contract site, never for an unknown site."""
    coordinator = MagicMock()
    coordinator.api.is_authenticated = True
    coordinator.site_reference = "synthetic"
    coordinator.settings_coordinator.data = {
        const.DATA_DELIVERY_SITE: SimpleNamespace(segments=segments)
        if segments is not None
        else None,
    }
    coordinator.async_api_call = AsyncMock(return_value=_insights())
    result = await FrankEnergieStatisticsCoordinator._fetch_month_insights(
        coordinator, "2026-11"
    )
    assert (result is not None) is bool(calls)
    assert coordinator.async_api_call.await_count == calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_id",
    [
        "synthetic|2026-10",
        "synthetic|2027-01",
        "synthetic",
        "|2026-11",
        "synthetic|2026-11junk",
    ],
)
async def test_period_mismatch_is_rejected(source_id: str) -> None:
    """Reject stale month and year rollover payloads from the SDK."""
    coordinator = MagicMock()
    coordinator.api.is_authenticated = True
    coordinator.site_reference = "synthetic"
    coordinator.settings_coordinator.data = {
        const.DATA_DELIVERY_SITE: SimpleNamespace(segments={"GAS"})
    }
    value = _insights()
    value._id = source_id
    coordinator.async_api_call = AsyncMock(return_value=value)
    assert (
        await FrankEnergieStatisticsCoordinator._fetch_month_insights(
            coordinator, "2026-11"
        )
        is None
    )


@pytest.mark.asyncio
async def test_absent_month_insights_does_not_hide_other_stats() -> None:
    """Keep existing usage and summary results during insights failure."""
    coordinator = MagicMock()
    coordinator._should_skip_api_calls.return_value = False
    coordinator.settings_coordinator.data = {}
    coordinator.site_reference = "synthetic"
    coordinator._month_summary_site_reference = None
    coordinator._month_insights_site_reference = None
    coordinator._month_insights_period = None
    coordinator._fetch_month_summary = AsyncMock(return_value=None)
    coordinator._fetch_period_usage = AsyncMock(return_value="usage_result")
    coordinator._fetch_month_insights = AsyncMock(return_value=None)
    coordinator.data = {}
    result = await FrankEnergieStatisticsCoordinator._async_update_data(coordinator)
    assert result[const.DATA_MONTH_INSIGHTS] is None
    assert result[const.DATA_USAGE] == "usage_result"
    assert result[const.DATA_MONTH_SUMMARY] is None
