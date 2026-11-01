"""Measure actual price-slot coverage within a local calendar day.

Price records may mix PT15M and PT60M intervals, overlap, or cross the local
midnight boundary. A slot *count* cannot establish how much of a day is
covered, especially on 23/25-hour daylight-saving days.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from python_frank_energie.models import PriceData

from .const import TIMEZONE_AMSTERDAM


def _local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Return the exact local-day interval as UTC instants."""
    timezone = ZoneInfo(TIMEZONE_AMSTERDAM)
    start = datetime.combine(day, time.min, tzinfo=timezone).astimezone(UTC)
    end = datetime.combine(
        day + timedelta(days=1), time.min, tzinfo=timezone
    ).astimezone(UTC)
    return start, end


def covered_seconds_in_local_day(prices: PriceData | None, day: date) -> float:
    """Sum unique, valid source interval coverage clipped to the local day.

    Overlapping and duplicate records must not artificially increase coverage.
    Invalid/missing/naive timestamps and intervals of nonpositive duration
    contribute nothing; no provider values or tariff units are inferred.
    """
    if prices is None:
        return 0.0

    start, end = _local_day_bounds(day)
    intervals: list[tuple[datetime, datetime]] = []
    for price in prices.all:
        first = getattr(price, "date_from", None)
        last = getattr(price, "date_till", None)
        if (
            not isinstance(first, datetime)
            or not isinstance(last, datetime)
            or first.tzinfo is None
            or last.tzinfo is None
        ):
            continue
        first_utc, last_utc = first.astimezone(UTC), last.astimezone(UTC)
        first_utc, last_utc = max(first_utc, start), min(last_utc, end)
        if first_utc < last_utc:
            intervals.append((first_utc, last_utc))

    cursor = start
    covered = 0.0
    for first, last in sorted(intervals):
        if last <= cursor:
            continue
        covered += (last - max(first, cursor)).total_seconds()
        cursor = last
    return covered


def covers_local_day(prices: PriceData | None, day: date) -> bool:
    """True only when valid source intervals cover the entire local day."""
    start, end = _local_day_bounds(day)
    return covered_seconds_in_local_day(prices, day) >= (end - start).total_seconds()
