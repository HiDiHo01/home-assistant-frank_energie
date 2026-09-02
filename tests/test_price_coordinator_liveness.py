"""Regression tests for price coordinator refresh scheduling."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from python_frank_energie.models import MarketPrices, PriceData

from custom_components.frank_energie.coordinator import FrankEnergiePriceCoordinator


def _market_prices_dated(*iso_starts: str) -> MarketPrices:
    """Minimal real MarketPrices with one electricity entry per ISO start."""
    raw = [
        {
            "from": iso,
            "till": iso,
            "marketPrice": 0.1,
            "marketPriceTax": 0.02,
            "sourcingMarkupPrice": 0.01,
            "energyTaxPrice": 0.1,
        }
        for iso in iso_starts
    ]
    return MarketPrices(
        electricity=PriceData(raw, energy_type="electricity"),
        gas=PriceData([], energy_type="gas"),
        energy_country="NL",
    )


@pytest.fixture
def price_coordinator() -> FrankEnergiePriceCoordinator:
    """Create a price coordinator with lightweight mocks for interval tests."""
    config_entry = MagicMock()
    config_entry.options = {"resolution": "PT15M"}
    coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), config_entry, MagicMock(), MagicMock()
    )
    coordinator.cached_prices_tomorrow = _market_prices_dated("2026-08-31T22:00:00.000Z")
    coordinator.last_fetch_tomorrow = datetime(2026, 8, 31, 11, tzinfo=UTC)
    coordinator.update_interval = None
    return coordinator


@pytest.mark.parametrize(
    ("resolution", "expected_interval"),
    [("PT15M", timedelta(minutes=15)), ("PT60M", timedelta(minutes=60))],
)
def test_cached_tomorrow_prices_keep_coordinator_alive(
    price_coordinator: FrankEnergiePriceCoordinator,
    resolution: str,
    expected_interval: timedelta,
) -> None:
    """Keep a non-None fallback interval after tomorrow prices are cached."""
    price_coordinator.config_entry.options["resolution"] = resolution
    now_utc = datetime(2026, 8, 31, 14, 0, tzinfo=UTC)

    price_coordinator._adjust_update_interval(now_utc)

    assert price_coordinator.update_interval == expected_interval


def test_before_publication_keeps_coordinator_alive(
    price_coordinator: FrankEnergiePriceCoordinator,
) -> None:
    """Keep a low-frequency fallback interval before tomorrow's publication."""
    price_coordinator.cached_prices_tomorrow = None
    price_coordinator.last_fetch_tomorrow = None
    now_utc = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)

    price_coordinator._adjust_update_interval(now_utc)

    assert price_coordinator.update_interval == timedelta(hours=1)
