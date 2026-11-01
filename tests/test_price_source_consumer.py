"""Consumer-facing Frank price provenance without contract or invoice claims.

These tests use synthetic timestamps and amounts; never real customer data.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from python_frank_energie.models import MarketPrices, PriceData

from custom_components.frank_energie.coordinator import FrankEnergiePriceCoordinator
from custom_components.frank_energie.sensor import FrankEnergieSensor


def _prices(*, electricity: bool = True, gas: bool = True) -> MarketPrices:
    first = datetime(2026, 10, 31, tzinfo=ZoneInfo("Europe/Amsterdam")).astimezone(UTC)
    slots = [
        {
            "from": (first + timedelta(hours=hour)).isoformat(),
            "till": (first + timedelta(hours=hour + 1)).isoformat(),
            "marketPrice": 0.12,
            "marketPriceTax": 0.02,
            "sourcingMarkupPrice": 0.01,
            "energyTaxPrice": 0.10,
        }
        for hour in range(24)
    ]
    return MarketPrices(
        electricity=PriceData(slots if electricity else [], energy_type="electricity"),
        gas=PriceData(slots if gas else [], energy_type="gas"),
        energy_country="BE",
    )


def _coordinator() -> FrankEnergiePriceCoordinator:
    hass = MagicMock()
    hass.config.country = "BE"
    entry = MagicMock()
    entry.options = {"resolution": "PT15M"}
    entry.data = {"site_reference": "synthetic-reference"}
    api = MagicMock()
    api.is_authenticated = True
    coordinator = FrankEnergiePriceCoordinator(hass, entry, api, MagicMock())
    coordinator.user_electricity_enabled = True
    coordinator.user_gas_enabled = True
    coordinator._record_price_fetch = MagicMock()
    return coordinator


@pytest.mark.asyncio
async def test_full_day_public_fallback_requires_customer_retry() -> None:
    coordinator = _coordinator()
    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=_prices())
    coordinator._fetch_user_prices_for_range = AsyncMock(
        return_value=_prices(electricity=False, gas=False)
    )
    day = date(2026, 10, 31)
    await coordinator._fetch_prices_with_fallback(day, day + timedelta(days=1))

    assert coordinator.price_source_for("electricity", day) == "public"
    assert coordinator.price_source_for("gas", day) == "public"
    assert coordinator._selected_customer_source_needs_retry(day)


@pytest.mark.asyncio
async def test_complete_customer_prices_end_source_retry() -> None:
    coordinator = _coordinator()
    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=_prices())
    coordinator._fetch_user_prices_for_range = AsyncMock(return_value=_prices())
    day = date(2026, 10, 31)
    await coordinator._fetch_prices_with_fallback(day, day + timedelta(days=1))

    assert coordinator.price_source_for("electricity", day) == "customer"
    assert coordinator.price_source_for("gas", day) == "customer"
    assert not coordinator._selected_customer_source_needs_retry(day)


@pytest.mark.asyncio
async def test_cache_only_cannot_gain_fresh_public_provenance() -> None:
    coordinator = _coordinator()
    coordinator._cached_prices = _prices()
    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=None)
    coordinator._fetch_user_prices_for_range = AsyncMock(return_value=None)
    day = date(2026, 10, 31)
    await coordinator._fetch_prices_with_fallback(day, day + timedelta(days=1))

    assert coordinator.price_source_for("electricity", day) == "cached_unverified"
    assert coordinator.price_source_for("gas", day) == "cached_unverified"
    assert coordinator._selected_customer_source_needs_retry(day)


@pytest.mark.asyncio
async def test_mixed_customer_public_sources_have_independent_labels() -> None:
    coordinator = _coordinator()
    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=_prices())
    coordinator._fetch_user_prices_for_range = AsyncMock(
        return_value=_prices(gas=False)
    )
    day = date(2026, 10, 31)
    await coordinator._fetch_prices_with_fallback(day, day + timedelta(days=1))

    assert coordinator.price_source_for("electricity", day) == "customer"
    assert coordinator.price_source_for("gas", day) == "public"
    assert coordinator._selected_customer_source_needs_retry(day)


def test_ha_price_attributes_distinguish_public_from_customer_invoice() -> None:
    calls: list[str] = []

    def get_source(segment: str, _day: date) -> str:
        calls.append(segment)
        return "public"

    entity = SimpleNamespace(
        coordinator=SimpleNamespace(
            data={"electricity": object()},
            price_source_for=get_source,
        ),
        entity_description=SimpleNamespace(
            key="elec_markup",
            attr_fn=lambda _: {"prices": []},
        ),
    )
    attrs = FrankEnergieSensor.extra_state_attributes.fget(entity)
    assert attrs["selected_price_origin"] == "public"
    assert attrs["selected_price_segment"] == "electricity"
    assert attrs["customer_billing_rate_verified"] is False
    assert attrs["cost_scope"] == "provider_price_not_reconciled_invoice"
    assert calls == ["electricity"]


def test_non_price_sensor_is_not_relabelled_with_price_provenance() -> None:
    entity = SimpleNamespace(
        coordinator=SimpleNamespace(data={"month_summary": object()}),
        entity_description=SimpleNamespace(
            key="month_summary",
            attr_fn=lambda _: {"quality": "source_reported"},
        ),
    )
    attrs = FrankEnergieSensor.extra_state_attributes.fget(entity)
    assert attrs == {"quality": "source_reported"}
