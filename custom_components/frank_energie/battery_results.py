"""Read signed Smart Battery results for sensors and date-range actions."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Self, TypedDict
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from python_frank_energie.models import SmartBatterySession, SmartBatterySessions

from .const import DATA_BATTERIES, DOMAIN, SERVICE_NAME_BATTERIES, TIMEZONE_AMSTERDAM
from .device_inventory import validated_inventory_ids
from .helpers import service_device_info

if TYPE_CHECKING:
    from . import FrankEnergieEntryData
    from .coordinator import FrankEnergieBatteryCoordinator

CONF_BATTERY_PERIOD = "battery_period"


@dataclass(frozen=True, slots=True)
class BatteryPeriod:
    """An inclusive range of Amsterdam calendar dates for one battery report."""

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        """Reject timestamps and reversed ranges before saving or querying."""
        if isinstance(self.start_date, datetime) or isinstance(self.end_date, datetime):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="battery_period_date_only"
            )
        if self.end_date < self.start_date:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="battery_results_reversed_dates",
            )

    @property
    def days(self) -> int:
        """Count both calendar dates, independently of daylight-saving time."""
        return (self.end_date - self.start_date).days + 1

    @classmethod
    def for_entry(cls, entry: ConfigEntry) -> Self:
        """Restore the saved pair, defaulting to the current month through today."""
        stored = entry.options.get(CONF_BATTERY_PERIOD)
        if isinstance(stored, Mapping):
            try:
                return cls(
                    date.fromisoformat(stored["start_date"]),
                    date.fromisoformat(stored["end_date"]),
                )
            except (KeyError, TypeError, ValueError, ServiceValidationError):
                pass
        today = datetime.now(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
        return cls(today.replace(day=1), today)

    def matches(self, data: SmartBatterySessions, battery_id: str) -> bool:
        """Check the device, reported bounds and daily rows before publishing."""
        return (
            data.device_id == battery_id
            and battery_day(data.period_start_date) in (None, self.start_date)
            and battery_day(data.period_end_date) in (None, self.end_date)
            and all(
                (day := battery_day(row.date)) is not None
                and self.start_date <= day <= self.end_date
                for row in data.sessions
            )
        )


def current_battery_ids(coordinator: FrankEnergieBatteryCoordinator) -> set[str] | None:
    """Distinguish a confirmed inventory from a failed or incomplete refresh."""
    if not coordinator.last_update_success:
        return None
    return validated_inventory_ids(
        "batteries", (coordinator.data or {}).get(DATA_BATTERIES)
    )


class BatteryPeriodEntity(CoordinatorEntity["FrankEnergieBatteryCoordinator"]):
    """A local period setting or measurement on this entry's Batteries device."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry[FrankEnergieEntryData], key: str) -> None:
        """Use an entry-scoped identity for the shared period."""
        super().__init__(entry.runtime_data.battery_coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = service_device_info(
            entry.entry_id, SERVICE_NAME_BATTERIES
        )

    @property
    def available(self) -> bool:
        """Keep local settings usable during outages until all batteries are removed."""
        return current_battery_ids(self.coordinator) != set()


@callback
def async_watch_battery_inventory(
    entry: ConfigEntry[FrankEnergieEntryData],
    on_discovery: Callable[[list[str], bool], None],
) -> None:
    """Announce new battery IDs and the first nonempty inventory for this entry."""
    coordinator = entry.runtime_data.battery_coordinator
    if not coordinator.api.is_authenticated:
        return
    known: set[str] = set()

    @callback
    def _async_discover() -> None:
        """Add each battery once following a successful inventory refresh."""
        ids = current_battery_ids(coordinator)
        if ids and (added := ids - known):
            first = not known
            known.update(added)
            on_discovery(sorted(added), first)

    _async_discover()
    entry.async_on_unload(coordinator.async_add_listener(_async_discover))


class PeriodBreakdown(TypedDict):
    """Amounts in EUR for the requested period, including the settled total."""

    trading_result: float | None
    frank_slim: float | None
    result: float | None
    epex_correction: float | None
    total: float | None


def period_breakdown(data: SmartBatterySessions | None) -> PeriodBreakdown:
    """Keep provider signs and add the correction only to the reported result."""
    result = getattr(data, "period_result", None)
    correction = getattr(data, "period_breakdown_epex_correction", None)
    return PeriodBreakdown(
        trading_result=getattr(data, "period_breakdown_trading_result", None),
        frank_slim=getattr(data, "period_breakdown_frank_slim", None),
        result=result,
        epex_correction=correction,
        total=(
            float(Decimal(str(result)) + Decimal(str(correction)))
            if result is not None and correction is not None
            else None
        ),
    )


def battery_day(value: date | str | None) -> date | None:
    """Read an Amsterdam calendar date from an SDK date or timestamp."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value) if value else None
    return value


def session_status(session: SmartBatterySession) -> str:
    """Return the lowercase provider status used in Home Assistant templates."""
    status = getattr(session, "status", None)
    return str(getattr(status, "value", status)).lower() if status else "unknown"


def battery_range_result(
    data: SmartBatterySessions | None, battery_id: str, period: BatteryPeriod
) -> dict[str, object] | None:
    """Serialize only sessions and amounts belonging to this battery and range."""
    if data is None or not period.matches(data, battery_id):
        return None

    return {
        **period_breakdown(data),
        "sessions": [
            {
                "date": day.isoformat(),
                "result": session.result,
                "status": session_status(session),
            }
            for session in data.sessions
            if (day := battery_day(session.date)) is not None
        ],
    }
