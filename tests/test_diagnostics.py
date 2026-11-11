"""Tests for Frank Energie diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import MagicMock

import pytest

from custom_components.frank_energie.diagnostics import (
    _serialize,
    async_get_config_entry_diagnostics,
)


@dataclass
class MockDataclass:
    """Test dataclass."""

    name: str
    value: int


class MockEnum(Enum):
    """Test enum."""

    VALUE = "test"


class MockObject:
    """Test object."""

    def __init__(self) -> None:
        """Initialize test object."""
        self.public = "value"
        self._private = "hidden"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("test", "test"),
        (123, 123),
        (12.34, 12.34),
        (True, True),
    ],
)
def test_serialize_basic_types(
    value: object,
    expected: object,
) -> None:
    """Test serialization of basic types."""
    assert _serialize(value) == expected


def test_serialize_decimal() -> None:
    """Test Decimal serialization."""
    assert _serialize(Decimal("12.34")) == "12.34"


def test_serialize_datetime() -> None:
    """Test datetime serialization."""
    value = datetime(2026, 1, 1, 12, 0, 0)

    assert _serialize(value) == "2026-01-01T12:00:00"


def test_serialize_date() -> None:
    """Test date serialization."""
    value = date(2026, 1, 1)

    assert _serialize(value) == "2026-01-01"


def test_serialize_time() -> None:
    """Test time serialization."""
    value = time(12, 30, 45)

    assert _serialize(value) == "12:30:45"


def test_serialize_enum() -> None:
    """Test enum serialization."""
    assert _serialize(MockEnum.VALUE) == "test"


def test_serialize_uuid() -> None:
    """Test UUID serialization."""
    value = UUID("12345678-1234-5678-1234-567812345678")

    assert _serialize(value) == "12345678-1234-5678-1234-567812345678"


def test_serialize_bytes() -> None:
    """Test bytes serialization."""
    assert _serialize(b"\x01\x02") == "0102"


def test_serialize_tuple() -> None:
    """Test tuple serialization."""
    assert _serialize((1, "a")) == [1, "a"]


def test_serialize_set() -> None:
    """Test set serialization."""
    result = _serialize({"b", "a"})

    assert result == ["a", "b"]


def test_serialize_frozenset() -> None:
    """Test frozenset serialization."""
    result = _serialize(frozenset({"b", "a"}))

    assert result == ["a", "b"]


def test_serialize_dataclass() -> None:
    """Test dataclass serialization."""
    value = MockDataclass(
        name="test",
        value=123,
    )

    assert _serialize(value) == {
        "name": "test",
        "value": 123,
    }


def test_serialize_object() -> None:
    """Test generic object serialization."""
    assert _serialize(MockObject()) == {
        "public": "value",
    }


@pytest.mark.asyncio
async def test_diagnostics_redacts_sensitive_data() -> None:
    """Test diagnostics redact sensitive data."""

    coordinator = SimpleNamespace(
        last_update_success=True,
        update_interval=timedelta(minutes=15),
        site_reference="secret-site",
        country_code="NL",
        _user_country="NL",
        resolution="PT15M",
        api_resolution="PT15M",
        user_electricity_enabled=True,
        user_gas_enabled=True,
        last_fetch_today="today",
        last_fetch_tomorrow="tomorrow",
        cached_prices={},
        cached_prices_today={},
        cached_prices_tomorrow={},
        _resolution_change_pending=False,
        data={
            "email": "test@example.com",
            "ean": "871234567890123456",
            "safe": "value",
        },
    )

    runtime_data = SimpleNamespace(
        coordinator=coordinator,
        battery_session_coordinators=[],
        smart_hvac_coordinator=None,
    )

    entry = MagicMock()
    entry.entry_id = "123"
    entry.title = "Frank Energie"
    entry.options = {}
    entry.data = {
        "username": "user@example.com",
        "password": "secret",
        "access_token": "token",
        "accessToken": "token_camel",
        "auth_token": "token_auth",
        "site_reference": "site",
    }
    entry.runtime_data = runtime_data

    coordinator.data.update(
        {
            "street": "Main Street",
            "houseNumber": "10",
            "houseNumberAddition": "A",
            "addressFormatted": "Main Street 10 A, 1234 AB Amsterdam",
            "address_formatted": "Main Street 10 A, 1234 AB Amsterdam",
            "postalCode": "1234 AB",
            "postal_code": "1234 AB",
            "zipCode": "1234 AB",
            "city": "Amsterdam",
            "deliveryAddress": "Main Street 10 A",
            "delivery_address": "Main Street 10 A",
            "connectionAddress": "Main Street 10 A",
            "connection_address": "Main Street 10 A",
            "user_sites": [
                {
                    "addressFormatted": "Side Street 2, 5678 CD",
                    "address_formatted": "Side Street 2, 5678 CD",
                    "postal_code": "5678 CD",
                    "delivery_address": "Side Street 2",
                    "connection_address": "Side Street 2",
                    "active": True,
                },
            ],
            "latitude": 52.3676,
            "longitude": 4.9041,
            "vin": "1HGCR2F83HA000000",
            "inverter_serial_numbers": ["INV-123456"],
            "jwt": "eyJhbGciOi...",
            "secret": "top-secret",
            "otp": "123456",
            "macAddress": "00:11:22:33:44:55",
            "ip": "192.168.1.100",
            "contractReference": "CR-9999",
            "batterySerialNumber": "BAT-777",
            "deviceSerialNumber": "DEV-888",
            "productName": "Dynamic Energy",
            "contractProductName": "Dynamic Energy",
            "customer_number": "CUST-12345",
            "customerNumber": "CUST-67890",
            "mandateId": "MANDATE-999",
        }
    )

    diagnostics = await async_get_config_entry_diagnostics(
        MagicMock(),
        entry,
    )

    assert diagnostics["entry"]["data"]["username"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["password"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["access_token"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["accessToken"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["auth_token"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["site_reference"] == "**REDACTED**"

    assert diagnostics["data"]["email"] == "**REDACTED**"
    assert diagnostics["data"]["ean"] == "**REDACTED**"
    assert diagnostics["data"]["street"] == "**REDACTED**"
    assert diagnostics["data"]["houseNumber"] == "**REDACTED**"
    assert diagnostics["data"]["houseNumberAddition"] == "**REDACTED**"
    assert diagnostics["data"]["addressFormatted"] == "**REDACTED**"
    assert diagnostics["data"]["address_formatted"] == "**REDACTED**"
    assert diagnostics["data"]["postalCode"] == "**REDACTED**"
    assert diagnostics["data"]["postal_code"] == "**REDACTED**"
    assert diagnostics["data"]["zipCode"] == "**REDACTED**"
    assert diagnostics["data"]["city"] == "**REDACTED**"
    assert diagnostics["data"]["deliveryAddress"] == "**REDACTED**"
    assert diagnostics["data"]["delivery_address"] == "**REDACTED**"
    assert diagnostics["data"]["connectionAddress"] == "**REDACTED**"
    assert diagnostics["data"]["connection_address"] == "**REDACTED**"
    assert diagnostics["data"]["user_sites"] == [
        {
            "addressFormatted": "**REDACTED**",
            "address_formatted": "**REDACTED**",
            "postal_code": "**REDACTED**",
            "delivery_address": "**REDACTED**",
            "connection_address": "**REDACTED**",
            "active": True,
        },
    ]
    assert diagnostics["data"]["latitude"] == "**REDACTED**"
    assert diagnostics["data"]["longitude"] == "**REDACTED**"
    assert diagnostics["data"]["vin"] == "**REDACTED**"
    assert diagnostics["data"]["inverter_serial_numbers"] == "**REDACTED**"
    assert diagnostics["data"]["jwt"] == "**REDACTED**"
    assert diagnostics["data"]["secret"] == "**REDACTED**"
    assert diagnostics["data"]["otp"] == "**REDACTED**"
    assert diagnostics["data"]["macAddress"] == "**REDACTED**"
    assert diagnostics["data"]["ip"] == "**REDACTED**"
    assert diagnostics["data"]["contractReference"] == "**REDACTED**"
    assert diagnostics["data"]["batterySerialNumber"] == "**REDACTED**"
    assert diagnostics["data"]["deviceSerialNumber"] == "**REDACTED**"
    assert diagnostics["data"]["productName"] == "**REDACTED**"
    assert diagnostics["data"]["contractProductName"] == "**REDACTED**"
    assert diagnostics["data"]["customer_number"] == "**REDACTED**"
    assert diagnostics["data"]["customerNumber"] == "**REDACTED**"
    assert diagnostics["data"]["mandateId"] == "**REDACTED**"

    assert diagnostics["data"]["safe"] == "value"


@pytest.mark.asyncio
async def test_diagnostics_coordinator_metadata() -> None:
    """Test coordinator metadata is included."""

    coordinator = SimpleNamespace(
        last_update_success=True,
        update_interval=timedelta(minutes=15),
        site_reference="site",
        country_code="NL",
        _user_country="NL",
        resolution="PT15M",
        api_resolution="PT15M",
        user_electricity_enabled=True,
        user_gas_enabled=False,
        last_fetch_today="today",
        last_fetch_tomorrow="tomorrow",
        cached_prices={"prices": []},
        cached_prices_today={"prices": []},
        cached_prices_tomorrow={"prices": []},
        _resolution_change_pending=True,
        data={},
    )

    runtime_data = SimpleNamespace(
        coordinator=coordinator,
        battery_session_coordinators=[object(), object()],
        smart_hvac_coordinator=None,
    )

    entry = MagicMock()
    entry.entry_id = "123"
    entry.title = "Frank Energie"
    entry.options = {}
    entry.data = {}
    entry.runtime_data = runtime_data

    diagnostics = await async_get_config_entry_diagnostics(
        MagicMock(),
        entry,
    )

    coordinator_data = diagnostics["coordinator"]

    assert coordinator_data["last_update_success"] is True
    assert coordinator_data["country_code"] == "NL"
    assert coordinator_data["resolution"] == "PT15M"
    assert coordinator_data["api_resolution"] == "PT15M"
    assert coordinator_data["user_electricity_enabled"] is True
    assert coordinator_data["user_gas_enabled"] is False
    assert coordinator_data["has_cached_prices"] is True
    assert coordinator_data["has_cached_today"] is True
    assert coordinator_data["has_cached_tomorrow"] is True
    assert coordinator_data["resolution_change_pending"] is True

    assert diagnostics["battery_session_coordinators"] == 2
