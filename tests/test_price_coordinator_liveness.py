"""Regression tests for price coordinator refresh scheduling."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    coordinator.cached_prices_tomorrow = _market_prices_dated(
        "2026-08-31T22:00:00.000Z"
    )
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


def test_fetch_just_after_local_midnight_counts_as_today(
    price_coordinator: FrankEnergiePriceCoordinator,
) -> None:
    """A fetch whose UTC timestamp still falls on the previous calendar day
    (the ~1-2h after Amsterdam midnight) must count as "fetched today".

    ``last_fetch_tomorrow`` is a UTC instant but the freshness checks reason
    in Amsterdam-local days; taking ``.date()`` on the raw UTC value here
    would drop back to the pre-publication hourly interval right after
    midnight instead of the cached-and-fresh fallback.
    """
    # Amsterdam 2026-09-01 00:30 CEST == UTC 2026-08-31 22:30.
    now_utc = datetime(2026, 8, 31, 22, 30, tzinfo=UTC)
    price_coordinator.last_fetch_tomorrow = now_utc
    # Cache holds prices for the new "tomorrow", Amsterdam 2026-09-02.
    price_coordinator.cached_prices_tomorrow = _market_prices_dated(
        "2026-09-01T22:00:00.000Z"
    )

    price_coordinator._adjust_update_interval(now_utc)

    assert price_coordinator.update_interval == timedelta(minutes=15)


@pytest.mark.asyncio
async def test_valid_disk_cache_installs_price_polling_fallback(
    price_coordinator: FrankEnergiePriceCoordinator,
) -> None:
    """Skipping a live initial fetch cannot leave the polling interval unset."""
    now = datetime(2026, 8, 31, 14, 0, tzinfo=UTC)
    price_coordinator.settings_coordinator.data = {}
    price_coordinator._async_setup = AsyncMock(return_value=True)
    price_coordinator._is_cache_fresh = MagicMock(return_value=True)
    price_coordinator.async_set_updated_data = MagicMock()

    with patch(
        "custom_components.frank_energie.coordinator.dt_util.utcnow",
        return_value=now,
    ):
        await price_coordinator.async_config_entry_first_refresh()

    assert price_coordinator.update_interval is not None
    assert price_coordinator.update_interval.total_seconds() > 0
    price_coordinator._is_cache_fresh.assert_called_once_with(now)
    price_coordinator.async_set_updated_data.assert_called_once_with(
        price_coordinator.data
    )


def test_hourly_customer_price_does_not_publish_every_quarter(
    price_coordinator: FrankEnergiePriceCoordinator,
) -> None:
    """Source resolution, not PT15M public query, controls HA state transitions."""
    price_coordinator._static_prices_today = SimpleNamespace(
        electricity=SimpleNamespace(resolution_minutes=60, all=[object()]),
        gas=None,
    )
    price_coordinator._maybe_fire_lowest_price_event = MagicMock()
    price_coordinator._maybe_fire_lowest_4p_event = MagicMock()
    price_coordinator._maybe_fire_lowest_16p_event = MagicMock()
    price_coordinator.async_update_listeners = MagicMock()

    price_coordinator.publish_price_slot(datetime(2026, 8, 31, 12, 15, tzinfo=UTC))
    price_coordinator.async_update_listeners.assert_not_called()
    price_coordinator.publish_price_slot(datetime(2026, 8, 31, 13, 0, tzinfo=UTC))
    price_coordinator.async_update_listeners.assert_called_once()
    price_coordinator._maybe_fire_lowest_price_event.assert_called_once()


def test_quarter_hour_prices_publish_without_api_call(
    price_coordinator: FrankEnergiePriceCoordinator,
) -> None:
    """The local slot clock publishes cached tariff changes independently."""
    price_coordinator._static_prices_today = SimpleNamespace(
        electricity=SimpleNamespace(resolution_minutes=15, all=[object()]),
        gas=None,
    )
    price_coordinator._maybe_fire_lowest_price_event = MagicMock()
    price_coordinator._maybe_fire_lowest_4p_event = MagicMock()
    price_coordinator._maybe_fire_lowest_16p_event = MagicMock()
    price_coordinator.async_update_listeners = MagicMock()
    price_coordinator._fetch_prices_with_fallback = AsyncMock()

    price_coordinator.publish_price_slot(datetime(2026, 8, 31, 12, 15, tzinfo=UTC))

    price_coordinator.async_update_listeners.assert_called_once()
    price_coordinator._fetch_prices_with_fallback.assert_not_awaited()


def test_mixed_source_resolutions_do_not_skip_gas_slots(
    price_coordinator: FrankEnergiePriceCoordinator,
) -> None:
    """A 15-minute gas price can change while the electricity tariff is hourly."""
    price_coordinator._static_prices_today = SimpleNamespace(
        electricity=SimpleNamespace(resolution_minutes=60, all=[object()]),
        gas=SimpleNamespace(resolution_minutes=15, all=[object()]),
    )
    price_coordinator._maybe_fire_lowest_price_event = MagicMock()
    price_coordinator._maybe_fire_lowest_4p_event = MagicMock()
    price_coordinator._maybe_fire_lowest_16p_event = MagicMock()
    price_coordinator.async_update_listeners = MagicMock()

    price_coordinator.publish_price_slot(datetime(2026, 8, 31, 12, 15, tzinfo=UTC))

    price_coordinator.async_update_listeners.assert_called_once()
