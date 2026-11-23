"""Publish finalized Smart Battery results for the previous Amsterdam calendar day."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Literal, Self
from zoneinfo import ZoneInfo

from aiohttp import ClientError
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from python_frank_energie.exceptions import FrankEnergieException, RateLimitException
from python_frank_energie.models import SmartBatterySessions

from .battery_results import (
    BatteryPeriod,
    PeriodBreakdown,
    current_battery_ids,
    period_breakdown,
    session_status,
)
from .const import ICON, TIMEZONE_AMSTERDAM
from .device_inventory import inventory_device_identifiers
from .helpers import api_maintenance_active

if TYPE_CHECKING:
    from . import FrankEnergieEntryData

_LOGGER = logging.getLogger(__name__)
_AMSTERDAM = ZoneInfo(TIMEZONE_AMSTERDAM)
_DAY = timedelta(days=1)
_FIRST_RETRY = timedelta(minutes=15)
_MAX_RETRY = timedelta(hours=4)
_FINAL = "complete_final"


def _yesterday(now: datetime) -> date:
    """Use Amsterdam's calendar even when Home Assistant has a different timezone."""
    return now.astimezone(_AMSTERDAM).date() - _DAY


@dataclass(frozen=True, slots=True)
class DailyResult:
    """One battery's dated provider status and, once finalized, its five amounts."""

    day: date
    status: str
    amounts: PeriodBreakdown | None = None
    report: SmartBatterySessions | None = None

    @classmethod
    def from_report(
        cls, day: date, battery_id: str, report: SmartBatterySessions | None
    ) -> Self:
        """Accept amounts only when every row is final and belongs to this battery and day."""
        if report is None:
            return cls(day, "missing")
        if not BatteryPeriod(day, day).matches(report, battery_id):
            return cls(day, "unavailable")
        if not report.sessions:
            return cls(day, "missing", report=report)
        for row in report.sessions:
            status = session_status(row)
            if status != _FINAL:
                return cls(day, status, report=report)
        return cls(day, _FINAL, period_breakdown(report), report)


class BatteryDailyCoordinator(DataUpdateCoordinator[dict[str, DailyResult]]):
    """Share one cache and one sequential batch per site, independently of editable periods."""

    config_entry: ConfigEntry[FrankEnergieEntryData]

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry[FrankEnergieEntryData]
    ) -> None:
        """Keep all results and retry deadlines local to this config-entry lifecycle."""
        super().__init__(
            hass,
            _LOGGER,
            name="Frank Energie daily battery results",
            config_entry=entry,
        )
        self._inventory = entry.runtime_data.battery_coordinator
        self.data = {}
        self._day = _yesterday(datetime.now(UTC))
        self._active: set[str] = set()
        self._retry_at: dict[str, datetime] = {}
        self._delay: dict[str, timedelta] = {}
        self._blocked_until: datetime | None = None
        self._batch: asyncio.Task[None] | None = None
        self._stopped = False
        self._cancel_midnight: Callable[[], None] | None = None

    @callback
    def async_start(self) -> None:
        """Start on first battery discovery; use inventory refreshes to retry unfinished days."""
        self.config_entry.async_on_unload(
            self._inventory.async_add_listener(self.async_check)
        )
        self.config_entry.async_on_unload(self._async_stop)
        self._async_schedule_midnight()
        self.async_check()

    @callback
    def _async_stop(self) -> None:
        """Release the date timer and stop in-flight batches from publishing after unload."""
        self._stopped = True
        if self._cancel_midnight is not None:
            self._cancel_midnight()

    @callback
    def _async_schedule_midnight(self) -> None:
        """Schedule the next local midnight using its UTC offset on that particular date."""
        tomorrow = datetime.now(_AMSTERDAM).date() + _DAY
        midnight = datetime.combine(tomorrow, time.min, _AMSTERDAM)
        self._cancel_midnight = async_track_point_in_utc_time(
            self.hass, self._async_midnight, midnight
        )

    @callback
    def _async_midnight(self, _now: datetime) -> None:
        """Advance both the displayed day and any calendar-based period preset."""
        self._async_schedule_midnight()
        self._inventory.async_update_listeners()

    def result(self, battery_id: str) -> DailyResult:
        """Hide an old day's data even when a response crosses midnight."""
        day = _yesterday(datetime.now(UTC))
        if (result := self.data.get(battery_id)) is not None and result.day == day:
            return result
        return DailyResult(day, "pending")

    def has_battery(self, battery_id: str) -> bool:
        """Report whether the last verified inventory included this battery."""
        return battery_id in self._active

    def covers(self, period: BatteryPeriod) -> bool:
        """Only an exact single-day interval may reuse this site's daily response."""
        return period.start_date == period.end_date == _yesterday(datetime.now(UTC))

    async def _async_update_data(self) -> dict[str, DailyResult]:
        """Join due work on manual refresh without bypassing finality or retry deadlines."""
        self.async_check()
        if self._batch is not None:
            await asyncio.shield(self._batch)
        return self.data

    async def async_report(
        self, battery_id: str, period: BatteryPeriod
    ) -> SmartBatterySessions | None:
        """Join the daily read and return only the requested day's verified raw report."""
        await self._async_update_data()
        snapshot = self.result(battery_id)
        return snapshot.report if snapshot.day == period.start_date else None

    @callback
    def _async_update_targets(self, day: date) -> None:
        """Expire the previous date and invalidate only batteries that actually disappeared."""
        ids = current_battery_ids(self._inventory)
        active = self._active if ids is None else ids
        removed = self._active - active if self._day == day else set(self.data)
        changed = active != self._active or day != self._day
        for battery_id in removed:
            self.data.pop(battery_id, None)
            self._delay.pop(battery_id, None)
            self._retry_at.pop(battery_id, None)
        self._active = active
        self._day = day
        if changed:
            self.async_update_listeners()

    def _due(self, battery_id: str, now: datetime) -> bool:
        """Final snapshots have no retry deadline; new batteries are immediately eligible."""
        if battery_id not in self.data:
            return True
        retry = self._retry_at.get(battery_id)
        return retry is not None and now >= retry

    @callback
    def async_check(self) -> None:
        """Coalesce inventory and clock notifications into a single due batch."""
        if self._stopped:
            return
        now = datetime.now(UTC)
        self._async_update_targets(_yesterday(now))
        if self._batch is not None or self._paused(now):
            return
        due = sorted(
            battery_id for battery_id in self._active if self._due(battery_id, now)
        )
        if due:
            self._batch = self.config_entry.async_create_background_task(
                self.hass,
                self._async_fetch_due(self._day, due),
                "Frank Energie daily battery results",
                eager_start=False,
            )

    def _paused(self, now: datetime) -> bool:
        """Observe maintenance, authentication and the site-wide Retry-After deadline."""
        return (
            not self._inventory.api.is_authenticated
            or not self._inventory.site_reference
            or api_maintenance_active(now)
            or (self._blocked_until is not None and now < self._blocked_until)
        )

    async def _async_fetch_due(self, day: date, battery_ids: list[str]) -> None:
        """Read due batteries serially and discard results that crossed local midnight."""
        try:
            for battery_id in battery_ids:
                now = datetime.now(UTC)
                if self._stopped or _yesterday(now) != day or self._paused(now):
                    break
                if battery_id not in self._active:
                    continue
                result = await self._async_read(day, battery_id)
                if (
                    not self._stopped
                    and _yesterday(datetime.now(UTC)) == day
                    and battery_id in self._active
                ):
                    self._async_store(battery_id, result)
        finally:
            self._batch = None
            if not self._stopped and day != _yesterday(datetime.now(UTC)):
                self.async_check()

    async def _async_read(self, day: date, battery_id: str) -> DailyResult:
        """Use the shared SDK cooldown and isolate provider failures to their battery."""
        try:
            report = await self._inventory.async_api_call(
                "smart_battery_sessions",
                self._inventory.api.smart_battery_sessions,
                battery_id,
                day,
                day,
            )
            return DailyResult.from_report(day, battery_id, report)
        except RateLimitException as err:
            delay = max(_FIRST_RETRY.total_seconds(), err.retry_after or 0)
            self._blocked_until = datetime.now(UTC) + timedelta(seconds=delay)
        except (
            FrankEnergieException,
            ClientError,
            TimeoutError,
            ValueError,
            TypeError,
        ) as err:
            _LOGGER.debug(
                "Daily Smart Battery result unavailable (%s)", type(err).__name__
            )
        return DailyResult(day, "unavailable")

    @callback
    def _async_store(self, battery_id: str, result: DailyResult) -> None:
        """Stop on finality and exponentially back off all other statuses."""
        self.data[battery_id] = result
        if result.status == _FINAL:
            self._retry_at.pop(battery_id, None)
        else:
            delay = self._delay.get(battery_id, _FIRST_RETRY)
            self._retry_at[battery_id] = datetime.now(UTC) + delay
            self._delay[battery_id] = min(delay * 2, _MAX_RETRY)
        self.async_update_listeners()


type AmountKey = Literal[
    "trading_result", "frank_slim", "result", "epex_correction", "total"
]


@dataclass(frozen=True, kw_only=True)
class DailySensorDescription(SensorEntityDescription):
    """Map a translated daily entity to one signed amount in the provider snapshot."""

    amount: AmountKey


DAILY_SENSORS = (
    DailySensorDescription(key="daily_trading_result", amount="result"),
    DailySensorDescription(key="yesterday_trading_result", amount="trading_result"),
    DailySensorDescription(key="yesterday_frank_slim", amount="frank_slim"),
    DailySensorDescription(key="yesterday_epex_correction", amount="epex_correction"),
    DailySensorDescription(key="yesterday_total", amount="total"),
)


class BatteryDailySensor(CoordinatorEntity[BatteryDailyCoordinator], SensorEntity):
    """One of five views of a finalized single-day settlement."""

    entity_description: DailySensorDescription
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 4
    _attr_icon = ICON

    def __init__(
        self,
        coordinator: BatteryDailyCoordinator,
        battery_id: str,
        description: DailySensorDescription,
    ) -> None:
        """Preserve the original result identity and scope new entities to the entry."""
        super().__init__(coordinator)
        self.entity_description = description
        self._battery_id = battery_id
        entry_id = coordinator.config_entry.entry_id
        prefix = (
            battery_id
            if description.key == "daily_trading_result"
            else f"{entry_id}_{battery_id}"
        )
        self._attr_unique_id = f"{prefix}_{description.key}"
        self._attr_translation_key = description.key
        self._attr_device_info = DeviceInfo(
            identifiers=inventory_device_identifiers(entry_id, "battery", battery_id),
            name=f"Smart Battery {battery_id}",
            manufacturer="Frank Energie",
            model="SmartBattery",
        )

    @property
    def available(self) -> bool:
        """Keep status attributes readable while the provider is finalizing the day."""
        return self.coordinator.has_battery(self._battery_id)

    @property
    def native_value(self) -> float | None:
        """Return the signed, unrounded provider amount once the report is final."""
        amounts = self.coordinator.result(self._battery_id).amounts
        return amounts[self.entity_description.amount] if amounts is not None else None

    @property
    def last_reset(self) -> datetime:
        """Start a new signed daily total at the reported day's local midnight."""
        return datetime.combine(
            self.coordinator.result(self._battery_id).day, time.min, _AMSTERDAM
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Identify the requested date and the status that gates its values."""
        result = self.coordinator.result(self._battery_id)
        return {"result_date": result.day.isoformat(), "session_status": result.status}
