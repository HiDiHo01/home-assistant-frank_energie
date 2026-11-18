"""Validate years, months, and dates accepted by the Frank Energie usage API."""

import re
from datetime import date, timedelta

_PERIOD_PATTERN = re.compile(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?\Z")


def validate_insight_period(period: str, today: date) -> str:
    """Accept only valid, non-future ISO years, months, or days."""
    if not isinstance(period, str) or not _PERIOD_PATTERN.fullmatch(period):
        raise ValueError("Use YYYY, YYYY-MM, or YYYY-MM-DD for Insight period")
    components = period.split("-")
    try:
        year = int(components[0])
        month = int(components[1]) if len(components) > 1 else 1
        day = int(components[2]) if len(components) > 2 else 1
        first_day = date(year, month, day)
    except ValueError as err:
        raise ValueError("Insight period is not a valid calendar year, month, or day") from err
    if first_day > today:
        raise ValueError("Insight period cannot be in the future")
    return period


def default_insight_period(today: date) -> str:
    """Return yesterday's local date in ISO format."""
    return (today - timedelta(days=1)).isoformat()
