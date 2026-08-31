"""Regression tests for price coordinator refresh scheduling."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from custom_components.frank_energie.coordinator import FrankEnergiePriceCoordinator


@pytest.fixture
def price_coordinator() -> FrankEnergiePriceCoordinator:
    """Create a minimally initialized price coordinator for interval tests."""
    coordinator = object.__new__(FrankEnergiePriceCoordinator)
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.options = {"resolution": "PT15M"}
    coordinator._api_resolution_state = None
    coordinator._resolution_change_pending = False
    coordinator.cached_prices_tomorrow = object()
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

    with patch.object(
        FrankEnergiePriceCoordinator,
        "_tomorrow_cache_matches_date",
        return_value=True,
    ):
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
