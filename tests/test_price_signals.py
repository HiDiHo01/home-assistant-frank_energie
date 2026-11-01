"""Automation signals must not rank partial, corrupt or DST-misaligned data."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.frank_energie.price_coverage import _local_day_bounds
from custom_components.frank_energie.price_signals import (
    cheap_hour_count,
    current_market_hour,
    market_hour_ranking,
)


def _prices(day: date, *, resolution: int = 60, hour_prices=None):
    start, end = _local_day_bounds(day)
    length = int((end - start).total_seconds() // 3600)
    if hour_prices is None:
        hour_prices = [float(index) for index in range(length)]
    rows = []
    for index in range(length * 60 // resolution):
        rows.append(
            SimpleNamespace(
                date_from=start + timedelta(minutes=index * resolution),
                date_till=start + timedelta(minutes=(index + 1) * resolution),
                market_price=hour_prices[index * resolution // 60],
            )
        )
    return SimpleNamespace(all=rows)


@pytest.mark.parametrize(
    ("day", "expected"),
    [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)],
)
def test_dst_day_has_distinct_physical_market_hours(day, expected):
    ranking = market_hour_ranking(_prices(day, resolution=15), day)
    assert ranking is not None
    assert len(ranking) == expected
    assert sorted(hour.rank for hour in ranking) == list(range(1, expected + 1))
    assert len({hour.starts_at_utc for hour in ranking}) == expected


def test_pt15m_aggregation_and_tie_breaks_by_utc_time():
    day = date(2026, 11, 2)
    prices = _prices(day, resolution=15, hour_prices=[1, 1, 2] + [3] * 21)
    ranked = market_hour_ranking(prices, day)
    assert ranked is not None
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2
    assert ranked[2].rank == 3
    assert ranked[0].price == 1

    now = ranked[1].starts_at_utc + timedelta(minutes=30)
    current = current_market_hour(prices, now)
    assert current is not None
    assert current[0].rank == 2
    assert current[1] == 24


def test_quarter_hour_weights_are_averaged_over_physical_hour():
    day = date(2026, 11, 2)
    prices = _prices(day, resolution=15)
    prices.all[0].market_price = 0
    prices.all[1].market_price = 1
    prices.all[2].market_price = 2
    prices.all[3].market_price = 3
    ranked = market_hour_ranking(prices, day)
    assert ranked is not None
    assert ranked[0].price == pytest.approx(1.5)


def test_partial_day_never_labels_a_slot_as_cheapest():
    day = date(2026, 11, 2)
    prices = _prices(day, resolution=15)
    prices.all = prices.all[:24]  # six hours, not a complete day
    assert market_hour_ranking(prices, day) is None
    start, _ = _local_day_bounds(day)
    assert current_market_hour(prices, start + timedelta(hours=2)) is None


def test_missing_and_conflicting_market_data_fail_closed():
    day = date(2026, 11, 2)
    prices = _prices(day)
    prices.all[0].market_price = float("nan")
    assert market_hour_ranking(prices, day) is None

    prices = _prices(day)
    prices.all[0].market_price = None
    assert market_hour_ranking(prices, day) is None

    prices = _prices(day)
    duplicate = SimpleNamespace(
        date_from=prices.all[0].date_from,
        date_till=prices.all[0].date_till,
        market_price=999.0,
    )
    prices.all.append(duplicate)
    assert market_hour_ranking(prices, day) is None


def test_overlap_with_identical_prices_is_allowed():
    day = date(2026, 11, 2)
    prices = _prices(day)
    prices.all.append(prices.all[0])
    assert market_hour_ranking(prices, day) is not None


def test_cheap_hours_option_is_bounded_and_preserves_default():
    assert cheap_hour_count(1) == 1
    assert cheap_hour_count("6") == 6
    assert cheap_hour_count(0) == 1
    assert cheap_hour_count(50) == 12
    assert cheap_hour_count(None) == 4
    assert cheap_hour_count(True) == 4
    assert cheap_hour_count("invalid") == 4
    assert cheap_hour_count(float("inf")) == 4


def test_naive_time_is_not_a_valid_live_market_signal():
    day = date(2026, 11, 2)
    assert current_market_hour(_prices(day), datetime(2026, 11, 2)) is None
