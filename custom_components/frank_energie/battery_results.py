"""Read signed Smart Battery results for sensors and date-range actions."""

from datetime import date, datetime
from decimal import Decimal
from typing import TypedDict
from zoneinfo import ZoneInfo

from python_frank_energie.models import SmartBatterySession, SmartBatterySessions

from .const import TIMEZONE_AMSTERDAM


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
    data: SmartBatterySessions | None, battery_id: str, start: date, end: date
) -> dict[str, object] | None:
    """Serialize only sessions and amounts belonging to this battery and range."""
    if data is None or data.device_id != battery_id:
        return None
    if battery_day(data.period_start_date) not in (None, start):
        return None
    if battery_day(data.period_end_date) not in (None, end):
        return None

    sessions = []
    for session in data.sessions:
        day = battery_day(session.date)
        if day is None or not start <= day <= end:
            return None
        sessions.append(
            {
                "date": day.isoformat(),
                "result": session.result,
                "status": session_status(session),
            }
        )
    return {**period_breakdown(data), "sessions": sessions}
