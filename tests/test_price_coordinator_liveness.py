"""Regression tests for price coordinator refresh scheduling."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

import pytest
from python_frank_energie.models import MarketPrices, PriceData

from custom_components.frank_energie.coordinator import FrankEnergiePriceCoordinator


def _market_prices_dated(day: date) -> MarketPrices:
    """Create complete synthetic electricity and gas intervals for one local day."""
    local_tz = ZoneInfo("Europe/Amsterdam")
    day_start = datetime.combine(day, time.min, local_tz).astimezone(UTC)
    day_end = datetime.combine(
        day + timedelta(days=1), time.min, local_tz
    ).astimezone(UTC)

    def _build_series(energy_type: str, minutes: int) -> PriceData:
        """Build real provider-style intervals without relying on entry counts."""
        price_rows: list[dict[str, object]] = []
        current = day_start
        while current < day_end:
            following = min(current + timedelta(minutes=minutes), day_end)
            price_rows.append(
                {
                    "from": current.isoformat(),
                    "till": following.isoformat(),
                    "marketPrice": 0.1,
                    "marketPriceTax": 0.02,
                    "sourcingMarkupPrice": 0.01,
                    "energyTaxPrice": 0.1,
                }
            )
            current = following
        return PriceData(price_rows, energy_type=energy_type)

    return MarketPrices(
        electricity=_build_series("electricity", 15),
        gas=_build_series("gas", 60),
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
    coordinator.cached_prices_tomorrow = _market_prices_dated(date(2026, 9, 1))
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
    price_coordinator.cached_prices_tomorrow = _market_prices_dated(date(2026, 9, 2))

    price_coordinator._adjust_update_interval(now_utc)

    assert price_coordinator.update_interval == timedelta(minutes=15)
