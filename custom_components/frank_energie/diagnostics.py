"""Diagnostics support for the Frank Energie integration."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
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
from .const import DATA_INVOICES

# Security: Keys redacted to prevent leaking credentials, personal data, and identifiers
# in exported diagnostics, fulfilling SPECS/redact_spec.md requirements.
TO_REDACT: set[str] = {
    CONF_ACCESS_TOKEN,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_PASSWORD,
    "Authorization",
    "authorization",
    "authToken",
    "bearer",
    "jwt",
    "refreshToken",
    "auth_token",
    "accessToken",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "otp",
    "mfa_code",
    "username",
    "email",
    "phone",
    "mobile",
    "telephone",
    "address",
    "addressFormatted",
    "address_formatted",
    "street",
    "houseNumber",
    "houseNumberAddition",
    "postalCode",
    "postal_code",
    "zipCode",
    "city",
    "connectionAddress",
    "connection_address",
    "deliveryAddress",
    "delivery_address",
    "latitude",
    "longitude",
    "customer_id",
    "customerId",
    "customer_number",
    "customerNumber",
    "account_id",
    "accountId",
    "userId",
    "user_id",
    # Generic IDs are used by nested user, battery, vehicle, HVAC, and PV models.
    "id",
    "userCreatedAt",
    "personId",
    "title",
    "contract_id",
    "contractId",
    "contractReference",
    "contractNumber",
    "agreementId",
    "productName",
    "contractProductName",
    "site_reference",
    "siteReference",
    "reference",
    "externalReference",
    "external_reference",
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
    "mandateId",
    "EAN",
    "ean",
    "connectionEAN",
    "connection_ean",
    "electricityEAN",
    "electricity_ean",
    "gasEAN",
    "gas_ean",
    "meteringPoint",
    "cluster",
    "firstName",
    "lastName",
    "fullName",
    "displayName",
    "emailAddress",
    "phoneNumber",
    "mobileNumber",
    "serial_number",
    "serialNumber",
    "deviceSerialNumber",
    "batterySerialNumber",
    "battery_id",
    "batteryId",
    "charger_id",
    "chargerId",
    "vehicle_id",
    "vehicleId",
    "vehicleIdentificationNumber",
    "device_id",
    "deviceId",
    "hardwareId",
    "installation_id",
    "installationId",
    "pvSystemId",
    "providerAccountId",
    "providerUserId",
    "vin",
    "inverter_serial_numbers",
    "ip",
    "ipv4",
    "ipv6",
    "mac",
    "macAddress",
    "hostname",
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
    """Return redacted config-entry diagnostics, including optional HVAC counts."""

    runtime_data: FrankEnergieEntryData = entry.runtime_data
    coordinator = getattr(runtime_data, "coordinator", None)

    diagnostics: dict[str, object] = {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "options": dict(entry.options),
            "data": dict(entry.data),
        },
        "coordinator": (
            {
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
                    coordinator, "_resolution_change_pending", False
                ),
            }
            if coordinator is not None
            else {}
        ),
        "data": _serialize(coordinator.data) if coordinator is not None else {},
        "cached_prices": (
            _serialize(coordinator.cached_prices) if coordinator is not None else None
        ),
        "battery_session_coordinators": len(
            getattr(runtime_data, "battery_session_coordinators", {})
        ),
        "invoice_coordinator": {
            "last_update_success": (
                getattr(runtime_data.invoice_coordinator, "last_update_success", False)
                if getattr(runtime_data, "invoice_coordinator", None) is not None
                else False
            ),
            "has_cached_invoices": (
                isinstance(getattr(runtime_data.invoice_coordinator, "data", None), dict)
                and getattr(runtime_data.invoice_coordinator, "data", {}).get(DATA_INVOICES)
                is not None
                if getattr(runtime_data, "invoice_coordinator", None) is not None
                else False
            ),
        },
    }

    if (smart_hvac := runtime_data.smart_hvac_coordinator) is not None:
        snapshot = smart_hvac.data
        diagnostics["smart_hvac"] = {
            "last_update_success": smart_hvac.last_update_success,
            "device_count": len(snapshot.devices)
            if snapshot.devices is not None
            else None,
            "connection_request_count": (
                len(snapshot.requests) if snapshot.requests is not None else None
            ),
        }

    return async_redact_data(diagnostics, TO_REDACT)
