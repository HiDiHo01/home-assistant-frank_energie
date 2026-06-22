"""Diagnostics support for the Frank Energie integration."""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant

from . import FrankEnergieEntryData

TO_REDACT: set[str] = {
    CONF_ACCESS_TOKEN,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_PASSWORD,
    "authToken",
    "refreshToken",
    "access_token",
    "refresh_token",
    "password",
    "username",
    "email",
    "phone",
    "address",
    "customer_id",
    "customerId",
    "account_id",
    "accountId",
    "title",
    "contract_id",
    "contractId",
    "site_reference",
    "siteReference",
    "reference",
    "TotalAmount",
    "Total amount",
    "Total_amount",
    "total_costs_previous_year",
    "total_costs_this_year",
    "site_id",
    "siteId",
    "_id",
    "connection_id",
    "connectionId",
    "bankAccountNumber",
    "EAN",
    "cluster",
    "firstName",
    "lastName",
    "emailAddress",
    "phoneNumber",
    "mobileNumber",
    "ean",
    "electricity_ean",
    "gas_ean",
    "serial_number",
    "serialNumber",
    "battery_id",
    "batteryId",
    "charger_id",
    "chargerId",
    "vehicle_id",
    "vehicleId",
    "device_id",
    "deviceId",
    "installation_id",
    "installationId",
}


def _serialize(value: object) -> object:
    """Convert objects into diagnostics-safe structures."""
    if value is None:
        return None

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, bytes):
        return value.hex()

    if isinstance(value, (set, frozenset)):
        return [_serialize(item) for item in sorted(value, key=str)]

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _serialize(getattr(value, field.name))
            for field in fields(value)
        }

    if isinstance(value, dict):
        return {str(key): _serialize(val) for key, val in value.items()}

    if isinstance(value, list):
        return [_serialize(item) for item in value]

    if isinstance(value, tuple):
        return [_serialize(item) for item in value]

    if hasattr(value, "__dict__"):
        return {
            key: _serialize(val)
            for key, val in vars(value).items()
            if not key.startswith("_")
        }

    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, object]:
    """Return diagnostics for a config entry."""

    runtime_data: FrankEnergieEntryData = entry.runtime_data
    coordinator = runtime_data.coordinator

    diagnostics: dict[str, object] = {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "options": dict(entry.options),
            "data": dict(entry.data),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "site_reference": coordinator.site_reference,
            "country_code": coordinator.country_code,
            "user_country": getattr(coordinator, "_user_country", None),
            "resolution": coordinator.resolution,
            "api_resolution": coordinator.api_resolution,
            "user_electricity_enabled": coordinator.user_electricity_enabled,
            "user_gas_enabled": coordinator.user_gas_enabled,
            "last_fetch_today": coordinator.last_fetch_today,
            "last_fetch_tomorrow": coordinator.last_fetch_tomorrow,
            "has_cached_prices": coordinator.cached_prices is not None,
            "has_cached_today": coordinator.cached_prices_today is not None,
            "has_cached_tomorrow": coordinator.cached_prices_tomorrow is not None,
            "resolution_change_pending": getattr(
                coordinator,
                "_resolution_change_pending",
                False,
            ),
        },
        "data": _serialize(coordinator.data),
        "cached_prices": _serialize(coordinator.cached_prices),
        "battery_session_coordinators": len(
            runtime_data.battery_session_coordinators,
        ),
    }

    return async_redact_data(diagnostics, TO_REDACT)
