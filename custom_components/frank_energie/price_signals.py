"""Fail-closed hourly market-price signals for Home Assistant automations.

These ranks compare the *market price component* of the selected electricity
price series, not Belgian MODEL customer billing rates. Every physical hour
in the local day must be covered, with no conflicting/invalid overlapping
values. A DST day has 23 or 25 separately ranked physical hours.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from numbers import Real
from zoneinfo import ZoneInfo

from .const import TIMEZONE_AMSTERDAM
from .price_coverage import _local_day_bounds


@dataclass(frozen=True, slots=True)
class MarketHour:
    """One physical market-price hour in UTC, including the repeated DST hour."""

    starts_at_utc: datetime
    ends_at_utc: datetime
    price: float
    rank: int = 0


def market_hour_ranking(prices: object, day: date) -> tuple[MarketHour, ...] | None:
    """Rank fully covered market hours; return None for incomplete price feeds.

    Four PT15M slots are aggregated by time-weighted price into one physical
    hour. Duplicate intervals with identical prices are harmless; conflicting
    overlaps fail closed rather than arbitrarily selecting a source.
    """
    start, end = _local_day_bounds(day)
    raw = getattr(prices, "all", None)
    if not isinstance(raw, (list, tuple)) or not raw:
        return None

    intervals: list[tuple[datetime, datetime, float]] = []
    for item in raw:
        first, last = getattr(item, "date_from", None), getattr(item, "date_till", None)
        if (
            not isinstance(first, datetime)
            or not isinstance(last, datetime)
            or first.tzinfo is None
            or last.tzinfo is None
        ):
            return None
        first, last = first.astimezone(UTC), last.astimezone(UTC)
        if first >= last:
            return None
        first, last = max(first, start), min(last, end)
        if first >= last:
            continue

        value = getattr(item, "market_price", None)
        if not isinstance(value, Real) or isinstance(value, bool):
            return None
        price = float(value)
        if not math.isfinite(price):
            return None
        intervals.append((first, last, price))

    if not intervals:
        return None

    results: list[MarketHour] = []
    cursor = start
    while cursor < end:
        hour_end = min(cursor + timedelta(hours=1), end)
        boundaries = {cursor, hour_end}
        for first, last, _ in intervals:
            if first < hour_end and last > cursor:
                boundaries.add(max(first, cursor))
                boundaries.add(min(last, hour_end))
        points = sorted(boundaries)
        total = 0.0
        for first, last in zip(points, points[1:]):
            values = {
                price
                for covered_from, covered_to, price in intervals
                if covered_from <= first and covered_to >= last
            }
            if len(values) != 1:
                return None
            total += next(iter(values)) * (last - first).total_seconds()

        hour_seconds = (hour_end - cursor).total_seconds()
        if hour_seconds != 3600:
            return None
        results.append(MarketHour(cursor, hour_end, total / hour_seconds))
        cursor = hour_end

    positions = {
        hour.starts_at_utc: rank
        for rank, hour in enumerate(
            sorted(results, key=lambda item: (item.price, item.starts_at_utc)),
            start=1,
        )
    }
    return tuple(replace(hour, rank=positions[hour.starts_at_utc]) for hour in results)


def current_market_hour(
    prices: object, now_utc: datetime
) -> tuple[MarketHour, int] | None:
    """Return current hour and number of ranked hours for a complete local day."""
    if now_utc.tzinfo is None:
        return None
    moment = now_utc.astimezone(UTC)
    day = moment.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
    ranking = market_hour_ranking(prices, day)
    if not ranking:
        return None
    for hour in ranking:
        if hour.starts_at_utc <= moment < hour.ends_at_utc:
            return hour, len(ranking)
    return None


def cheap_hour_count(value: object) -> int:
    """Bound an HA option to safe supported integer hours."""
    if isinstance(value, bool):
        return 4
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return 4
    return max(1, min(count, 12))
