"""Import completed Frank Energie usage intervals into Recorder."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from math import fsum, isfinite
from typing import Final
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models.statistics import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.unit_conversion import EnergyConverter, VolumeConverter
from python_frank_energie.models import EnergyCategory, PeriodUsageAndCosts, UsageItem
from sqlalchemy.exc import SQLAlchemyError

from .const import DOMAIN, TIMEZONE_AMSTERDAM

_LOGGER = logging.getLogger(__name__)
_LOCAL_ZONE: Final = ZoneInfo(TIMEZONE_AMSTERDAM)
_HOUR: Final = timedelta(hours=1)
_INTERVAL_LENGTHS: Final = (timedelta(minutes=15), _HOUR)
_ENERGY_UNIT: Final = UnitOfEnergy.KILO_WATT_HOUR
_VOLUME_UNIT: Final = UnitOfVolume.CUBIC_METERS
_COST_UNIT: Final = "EUR"
_USAGE_UNITS: Final = {"kwh": _ENERGY_UNIT, "m3": _VOLUME_UNIT, "m³": _VOLUME_UNIT}
_UNIT_CLASSES: Final = {
    _ENERGY_UNIT: EnergyConverter.UNIT_CLASS,
    _VOLUME_UNIT: VolumeConverter.UNIT_CLASS,
}
_IMPORT_LOCKS_KEY: Final = f"{DOMAIN}_usage_import_locks"


def _import_lock(hass: HomeAssistant, entry: ConfigEntry) -> asyncio.Lock:
    """Share one Recorder import lock across coordinators for this entry."""
    locks: dict[str, asyncio.Lock] = hass.data.setdefault(_IMPORT_LOCKS_KEY, {})
    return locks.setdefault(entry.entry_id, asyncio.Lock())


def remove_import_lock(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget an entry's import lock only when the entry is permanently removed."""
    hass.data.get(_IMPORT_LOCKS_KEY, {}).pop(entry.entry_id, None)


def _current_import_owner(entry: ConfigEntry, owner: object | None) -> bool:
    """Reject a stale coordinator after the entry has published a replacement."""
    if owner is None:
        return True
    runtime = getattr(entry, "runtime_data", None)
    return getattr(runtime, "statistics_coordinator", None) is owner


@dataclass(frozen=True, slots=True)
class _UsageInterval:
    """One provider interval after UTC and unit normalization."""

    start: datetime
    end: datetime
    unit: str
    usage: float
    costs: float


def _utc_time(value: datetime) -> datetime | None:
    """Resolve an offset or an unambiguous Amsterdam wall time to UTC."""
    if value.utcoffset() is not None:
        return value.astimezone(UTC)
    local = value.replace(tzinfo=_LOCAL_ZONE, fold=0)
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        return None
    return local.astimezone(UTC)


def _clock_bounds(item: UsageItem) -> tuple[datetime, datetime]:
    """Combine separate provider date and clock fields across midnight."""
    day = date.fromisoformat(item.date)
    start_clock = time.fromisoformat(item.from_time)
    if item.till_time in {"24:00", "24:00:00"}:
        return datetime.combine(day, start_clock), datetime.combine(
            day + timedelta(days=1), time.min
        )
    end_clock = time.fromisoformat(item.till_time)
    end_day = day
    if end_clock.replace(tzinfo=None) <= start_clock.replace(tzinfo=None):
        end_day += timedelta(days=1)
    return datetime.combine(day, start_clock), datetime.combine(end_day, end_clock)


def _usage_bounds(item: UsageItem) -> tuple[datetime | None, datetime | None]:
    """Read ISO timestamps or provider date and clock fields."""
    try:
        start = datetime.fromisoformat(item.from_time)
        end = datetime.fromisoformat(item.till_time)
    except ValueError:
        start, end = _clock_bounds(item)
    return _utc_time(start), _utc_time(end)


def _usage_interval(
    item: UsageItem, category_unit: str, now_utc: datetime, *, gas: bool
) -> _UsageInterval | None:
    """Accept only finite measurements in closed, aligned provider intervals."""
    raw_unit = item.unit or category_unit
    if not isinstance(raw_unit, str):
        return None
    unit = _USAGE_UNITS.get(raw_unit.strip().lower())
    if unit is None or (not gas and unit != _ENERGY_UNIT):
        return None
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        for value in (item.usage, item.costs)
    ):
        return None
    try:
        start, end = _usage_bounds(item)
    except (ValueError, OverflowError):
        return None
    if start is None or end is None or end > now_utc:
        return None
    if start.minute % 15 or start.second or start.microsecond:
        return None
    if end - start not in _INTERVAL_LENGTHS or end > start.replace(minute=0) + _HOUR:
        return None
    return _UsageInterval(start, end, unit, item.usage, item.costs)


def _completed_usage_hours(
    category: EnergyCategory, now_utc: datetime, *, gas: bool
) -> Iterator[tuple[datetime, str, float, float]]:
    """Yield complete UTC hours and ignore gaps and conflicting readings."""
    hours: dict[datetime, set[_UsageInterval]] = defaultdict(set)
    for item in category.items:
        if interval := _usage_interval(item, category.unit, now_utc, gas=gas):
            hours[interval.start.replace(minute=0)].add(interval)

    for hour, intervals in hours.items():
        ordered = sorted(intervals, key=lambda interval: interval.start)
        if ordered[0].start != hour or ordered[-1].end - hour != _HOUR:
            continue
        if any(left.end != right.start for left, right in zip(ordered, ordered[1:])):
            continue
        if len(units := {interval.unit for interval in ordered}) != 1:
            continue
        yield (
            hour,
            next(iter(units)),
            fsum(interval.usage for interval in ordered),
            fsum(interval.costs for interval in ordered),
        )


def _metadata(statistic: str, name: str, unit: str) -> StatisticMetaData:
    """Describe an hourly sum with the unit understood by Core 2026.11."""
    return {
        "statistic_id": f"{DOMAIN}:{statistic}",
        "source": DOMAIN,
        "name": name,
        "unit_of_measurement": unit,
        "unit_class": _UNIT_CLASSES.get(unit),
        "has_sum": True,
        "mean_type": StatisticMeanType.NONE,
    }


def _usage_series(
    entry: ConfigEntry,
    site_reference: str,
    usage: PeriodUsageAndCosts,
    now_utc: datetime,
) -> Iterator[tuple[StatisticMetaData, dict[datetime, float]]]:
    """Create independent series for each site, direction, and measurement unit."""
    site_key = sha256(f"{entry.entry_id}\0{site_reference}".encode()).hexdigest()[:24]
    categories = (
        (
            "electricity",
            usage.electricity,
            "Electricity consumption",
            "Electricity cost",
        ),
        ("gas", usage.gas, "Gas consumption", "Gas cost"),
        ("feed_in", usage.feed_in, "Electricity feed-in", "Feed-in compensation"),
    )
    for kind, category, usage_name, cost_name in categories:
        if category is None:
            continue
        amounts: dict[str, dict[datetime, float]] = defaultdict(dict)
        costs: dict[datetime, float] = {}
        for hour, unit, amount, cost in _completed_usage_hours(
            category, now_utc, gas=kind == "gas"
        ):
            amounts[unit][hour] = amount
            costs[hour] = cost
        for unit, measurements in amounts.items():
            unit_key = "kwh" if unit == _ENERGY_UNIT else "m3"
            yield (
                _metadata(
                    f"{kind}_usage_{unit_key}_{site_key}",
                    f"{entry.title} {usage_name}",
                    unit,
                ),
                measurements,
            )
        if costs:
            yield (
                _metadata(
                    f"{kind}_cost_{site_key}",
                    f"{entry.title} {cost_name}",
                    _COST_UNIT,
                ),
                costs,
            )


def _saved_usage(
    hass: HomeAssistant, statistic_id: str, start: datetime
) -> tuple[float, dict[datetime, float]]:
    """Read the prior sum and recorded contributions from the correction point."""
    if not (
        latest := get_last_statistics(hass, 1, statistic_id, False, {"sum"}).get(
            statistic_id
        )
    ):
        return 0.0, {}
    if latest[0]["start"] < start.timestamp():
        return _recorded_number(latest[0].get("sum")), {}

    saved = statistics_during_period(
        hass, start, None, {statistic_id}, "hour", None, {"state", "sum"}
    ).get(statistic_id)
    if not saved:
        raise ValueError("Recorder did not return the expected usage history")
    first = saved[0]
    previous_sum = fsum(
        (_recorded_number(first.get("sum")), -_recorded_number(first.get("state")))
    )
    amounts = {
        datetime.fromtimestamp(row["start"], UTC): _recorded_number(row.get("state"))
        for row in saved
    }
    return previous_sum, amounts


def _recorded_number(value: float | None) -> float:
    """Require a usable saved value before overwriting a cumulative sum."""
    if value is None or not isfinite(value):
        raise ValueError("Recorder usage history has an invalid numeric value")
    return value


def _cumulative_usage(
    amounts: dict[datetime, float], previous_sum: float
) -> list[StatisticData]:
    """Recalculate later sums when the provider corrects a recorded hour."""
    statistics: list[StatisticData] = []
    total = previous_sum
    for hour, value in sorted(amounts.items()):
        total = fsum((total, value))
        statistics.append({"start": hour, "state": value, "sum": total})
    return statistics


async def async_record_period_usage(
    hass: HomeAssistant,
    entry: ConfigEntry,
    site_reference: str,
    usage: PeriodUsageAndCosts,
    now_utc: datetime,
    *,
    owner: object | None = None,
) -> None:
    """Import completed hours, serialized across entry reloads and coordinators."""
    try:
        series = list(_usage_series(entry, site_reference, usage, now_utc))
        if not series:
            return
        recorder = get_instance(hass)
        async with asyncio.timeout(10), _import_lock(hass, entry):
            # A retired coordinator must never overwrite the replacement's data.
            if not _current_import_owner(entry, owner):
                return
            # Read and queue all series under the same entry-scoped lock.
            await recorder.async_block_till_done()
            for metadata, measurements in series:
                try:
                    previous_sum, recorded = await recorder.async_add_executor_job(
                        _saved_usage, hass, metadata["statistic_id"], min(measurements)
                    )
                except ValueError as err:
                    _LOGGER.warning(
                        "Unable to read Frank Energie usage history (%s)",
                        type(err).__name__,
                    )
                    continue
                recorded.update(measurements)
                async_add_external_statistics(
                    hass, metadata, _cumulative_usage(recorded, previous_sum)
                )
    except (
        HomeAssistantError,
        KeyError,
        OverflowError,
        RuntimeError,
        SQLAlchemyError,
        TimeoutError,
    ) as err:
        _LOGGER.warning(
            "Unable to import Frank Energie usage statistics (%s)", type(err).__name__
        )
