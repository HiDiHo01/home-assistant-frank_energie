"""Helper functions for the Frank Energie integration."""

from __future__ import annotations


def build_charge_settings_input(settings) -> dict:
    """Build the full charge settings dict required by the mutation.

    All 13 fields must be present — the API does not support partial updates.
    """
    return {
        "id": settings.id,
        "deadline": settings.deadline.isoformat() if settings.deadline else None,
        "isSmartChargingEnabled": settings.is_smart_charging_enabled,
        "isSolarChargingEnabled": settings.is_solar_charging_enabled,
        "minChargeLimit": settings.min_charge_limit,
        "maxChargeLimit": settings.max_charge_limit,
        "hourMonday": settings.hour_monday,
        "hourTuesday": settings.hour_tuesday,
        "hourWednesday": settings.hour_wednesday,
        "hourThursday": settings.hour_thursday,
        "hourFriday": settings.hour_friday,
        "hourSaturday": settings.hour_saturday,
        "hourSunday": settings.hour_sunday,
    }
