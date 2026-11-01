"""Tomorrow's gas summary sensors must reject incomplete calendar-day source slots."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.frank_energie.const import DATA_GAS
from custom_components.frank_energie.price_coverage import _local_day_bounds
from custom_components.frank_energie.sensor import SENSOR_TYPES


_KEYS = (
    "gas_tomorrow_avg",
    "gas_tomorrow_avg_market",
    "gas_tomorrow_avg_market_tax",
    "gas_tomorrow_avg_market_tax_markup",
    "gas_tomorrow_avg_all_in",
    "gas_tomorrow_min",
    "gas_tomorrow_max",
)


def _gas(day: date, hours: int) -> dict:
    start, _ = _local_day_bounds(day)
    prices = SimpleNamespace(
        all=[
            SimpleNamespace(
                date_from=start + timedelta(hours=index),
                date_till=start + timedelta(hours=index + 1),
            )
            for index in range(hours)
        ],
        tomorrow_average_price=0.9,
        tomorrow_prices_market=0.7,
        tomorrow_prices_market_tax=0.75,
        tomorrow_prices_market_tax_markup=0.8,
        tomorrow_prices_total=0.95,
        tomorrow_min=SimpleNamespace(total=0.6),
        tomorrow_max=SimpleNamespace(total=1.2),
        asdict=lambda *args, **kwargs: {},
    )
    return {DATA_GAS: prices}


@pytest.mark.parametrize(
    ("day", "hours"),
    [
        (date(2026, 11, 2), 24),
        (date(2026, 3, 29), 23),
        (date(2026, 10, 25), 25),
    ],
)
def test_gas_tomorrow_aggregate_needs_full_local_day(day, hours):
    yesterday = day - timedelta(days=1)
    now = datetime(
        yesterday.year,
        yesterday.month,
        yesterday.day,
        12,
        tzinfo=ZoneInfo("Europe/Amsterdam"),
    )
    with patch("custom_components.frank_energie.sensor.dt_util.now", return_value=now):
        for key in _KEYS:
            description = next(item for item in SENSOR_TYPES if item.key == key)
            assert description.value_fn(_gas(day, 6)) is None, key
            assert description.value_fn(_gas(day, hours - 1)) is None, key
            assert description.value_fn(_gas(day, hours)) is not None, key


def test_gas_tomorrow_never_averages_missing_source():
    now = datetime(2026, 11, 1, 12, tzinfo=UTC)
    with patch("custom_components.frank_energie.sensor.dt_util.now", return_value=now):
        for key in _KEYS:
            description = next(item for item in SENSOR_TYPES if item.key == key)
            assert description.value_fn({DATA_GAS: None}) is None, key
            assert description.value_fn(_gas(date(2026, 11, 2), 0)) is None, key
