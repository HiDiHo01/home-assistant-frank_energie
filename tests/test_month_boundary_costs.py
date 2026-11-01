"""MonthSummary financial sensors must not leak a prior month across rollover."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.frank_energie.const import DATA_MONTH_SUMMARY
from custom_components.frank_energie.sensor import SENSOR_TYPES


CURRENT_MONTH_KEYS = (
    "actual_costs_until_last_meter_reading_date",
    "expected_costs_until_last_meter_reading_date",
    "difference_costs_until_last_meter_reading_date",
    "difference_costs_per_day",
    "expected_costs_this_month",
    "expected_costs_per_day_this_month",
    "costs_per_day_till_now_this_month",
)


def _values(reading_date: object, expected: float = 252.0) -> dict:
    """Provide actual provider-shaped attributes without a live API request."""
    return {
        DATA_MONTH_SUMMARY: SimpleNamespace(
            lastMeterReadingDate=reading_date,
            expectedCosts=expected,
            actualCostsUntilLastMeterReadingDate=expected,
            expectedCostsUntilLastMeterReadingDate=expected,
            differenceUntilLastMeterReadingDate=expected,
            differenceUntilLastMeterReadingDateAvg=0.0,
            expectedCostsPerDay=8.13,
            costs_per_day_till_now=11.5,
        )
    }


@pytest.mark.parametrize(
    "reading_date",
    [date(2026, 10, 30), "2026-10-30", "2026-10-30T23:00:00Z", None],
)
def test_previous_month_summary_does_not_appear_under_november(
    reading_date: object,
) -> None:
    """A lagging meter read is not a November cost or November forecast."""
    with patch(
        "custom_components.frank_energie.sensor.dt_util.now",
        return_value=datetime(2026, 11, 1, 2, tzinfo=ZoneInfo("Europe/Brussels")),
    ):
        for key in CURRENT_MONTH_KEYS:
            sensor = next(sensor for sensor in SENSOR_TYPES if sensor.key == key)
            assert sensor.value_fn(_values(reading_date)) is None


def test_current_month_summary_allows_explicit_zero() -> None:
    """Zero is valid for the current period; missing data is not zero."""
    with patch(
        "custom_components.frank_energie.sensor.dt_util.now",
        return_value=datetime(2026, 11, 1, 2, tzinfo=ZoneInfo("Europe/Brussels")),
    ):
        actual = {
            sensor.key: sensor.value_fn(_values("2026-11-01", expected=0.0))
            for sensor in SENSOR_TYPES
            if sensor.key in CURRENT_MONTH_KEYS
        }
    assert actual == {
        "actual_costs_until_last_meter_reading_date": 0.0,
        "expected_costs_until_last_meter_reading_date": 0.0,
        "difference_costs_until_last_meter_reading_date": 0.0,
        "difference_costs_per_day": 0.0,
        "expected_costs_this_month": 0.0,
        "expected_costs_per_day_this_month": 8.13,
        "costs_per_day_till_now_this_month": 11.5,
    }


def test_timezone_aware_reading_uses_local_month() -> None:
    """A UTC October timestamp can represent the start of November in Belgium."""
    reading = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)
    with patch(
        "custom_components.frank_energie.sensor.dt_util.now",
        return_value=datetime(2026, 11, 1, 2, tzinfo=ZoneInfo("Europe/Brussels")),
    ):
        sensor = next(
            sensor
            for sensor in SENSOR_TYPES
            if sensor.key == "expected_costs_this_month"
        )
        assert sensor.value_fn(_values(reading)) == 252.0
