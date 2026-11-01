"""Coverage is measured in time, not in quarter-hour/hour record counts."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from python_frank_energie.models import MarketPrices, PriceData

from custom_components.frank_energie.coordinator import FrankEnergiePriceCoordinator
from custom_components.frank_energie.price_coverage import (
    covered_seconds_in_local_day,
    covers_local_day,
)


def _prices(start: datetime, count: int, minutes: int) -> PriceData:
    raw = []
    for index in range(count):
        first = start + timedelta(minutes=index * minutes)
        last = first + timedelta(minutes=minutes)
        raw.append(
            {
                "from": first.isoformat(),
                "till": last.isoformat(),
                "marketPrice": 0.12,
                "marketPriceTax": 0.02,
                "sourcingMarkupPrice": 0.01,
                "energyTaxPrice": 0.10,
            }
        )
    return PriceData(raw, energy_type="electricity")


def _market_prices(electricity: PriceData, gas: PriceData) -> MarketPrices:
    return MarketPrices(
        electricity=electricity,
        gas=gas,
        energy_country="BE",
    )


def test_hourly_coverage_wins_over_more_quarter_hour_records() -> None:
    """24 slots of 15 minutes cover less than 12 distinct hourly slots."""
    day = date(2026, 10, 20)
    start = datetime(2026, 10, 19, 22, tzinfo=UTC)
    cached = _market_prices(_prices(start, 24, 15), _prices(start, 24, 15))
    fetched = _market_prices(_prices(start, 12, 60), _prices(start, 12, 60))

    result = FrankEnergiePriceCoordinator._preserve_today_source_coverage(
        cached, fetched, day
    )

    assert len(cached.electricity.all) == 24
    assert len(fetched.electricity.all) == 12
    assert covered_seconds_in_local_day(cached.electricity, day) == 6 * 3600
    assert covered_seconds_in_local_day(fetched.electricity, day) == 12 * 3600
    assert result is fetched
    assert result.gas is fetched.gas


def test_more_complete_cached_segment_survives_shorter_source_reply() -> None:
    """A short live response may update gas without erasing better electricity."""
    day = date(2026, 10, 20)
    start = datetime(2026, 10, 19, 22, tzinfo=UTC)
    cached = _market_prices(_prices(start, 18, 60), _prices(start, 6, 60))
    fetched = _market_prices(_prices(start, 24, 15), _prices(start, 12, 60))

    result = FrankEnergiePriceCoordinator._preserve_today_source_coverage(
        cached, fetched, day
    )

    assert result is not cached and result is not fetched
    assert result.electricity is cached.electricity
    assert result.gas is fetched.gas
    assert fetched.electricity is not cached.electricity


def test_duplicate_intervals_do_not_outweigh_distinct_coverage() -> None:
    day = date(2026, 10, 20)
    start = datetime(2026, 10, 19, 22, tzinfo=UTC)
    duplicated = PriceData(
        [
            {
                "from": start.isoformat(),
                "till": (start + timedelta(hours=1)).isoformat(),
                "marketPrice": 0.1,
                "marketPriceTax": 0.02,
                "sourcingMarkupPrice": 0.01,
                "energyTaxPrice": 0.1,
            }
            for _ in range(6)
        ],
        energy_type="electricity",
    )
    distinct = _prices(start, 2, 60)
    assert covered_seconds_in_local_day(duplicated, day) == 3600
    assert covered_seconds_in_local_day(distinct, day) == 7200


@pytest.mark.parametrize(
    ("day", "hours"),
    [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)],
)
def test_dst_day_requires_actual_23_or_25_hours(day: date, hours: int) -> None:
    timezone = ZoneInfo("Europe/Amsterdam")
    start = datetime.combine(day, time.min, tzinfo=timezone).astimezone(UTC)
    full = _prices(start, hours, 60)
    incomplete = _prices(start, hours - 1, 60)

    assert covered_seconds_in_local_day(full, day) == hours * 3600
    assert covers_local_day(full, day)
    assert not covers_local_day(incomplete, day)


def test_outside_day_and_overlapping_slots_are_clipped() -> None:
    day = date(2026, 10, 20)
    start = datetime(2026, 10, 19, 22, tzinfo=UTC)
    before_midnight = _prices(start - timedelta(hours=1), 2, 60)
    assert covered_seconds_in_local_day(before_midnight, day) == 3600

    assert covered_seconds_in_local_day(None, day) == 0
    assert not covers_local_day(None, day)
