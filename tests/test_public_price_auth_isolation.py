"""Regression tests for public-price authentication isolation.

Public MarketPrices must use an anonymous client even when a Frank Energie
account is configured; customerMarketPrices must keep the authenticated one.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch, sentinel

import pytest

from custom_components.frank_energie.coordinator import FrankEnergiePriceCoordinator
from python_frank_energie.models import ContractPriceResolutionState


@pytest.fixture
def clients():
    hass = MagicMock()
    hass.config.country = "BE"
    entry = MagicMock()
    entry.data = {}
    entry.options = {"resolution": "PT15M"}
    entry.entry_id = "test-auth-isolation"

    authenticated = MagicMock()
    authenticated.is_authenticated = True
    authenticated.country_prices = AsyncMock()
    authenticated.prices = AsyncMock()
    authenticated.user_prices = AsyncMock(return_value=sentinel.customer_prices)

    with (
        patch(
            "custom_components.frank_energie.coordinator.async_get_clientsession",
            return_value=sentinel.ha_session,
        ),
        patch("custom_components.frank_energie.coordinator.FrankEnergie") as factory,
    ):
        public = factory.return_value
        public.is_authenticated = False
        public.country_prices = AsyncMock(return_value=sentinel.public_prices)
        public.prices = AsyncMock(return_value=sentinel.public_prices)
        coordinator = FrankEnergiePriceCoordinator(
            hass, entry, authenticated, MagicMock()
        )
        yield coordinator, authenticated, public, factory


@pytest.mark.asyncio
async def test_belgian_public_prices_are_anonymous_even_when_logged_in(clients):
    coordinator, authenticated, public, factory = clients
    assert coordinator.api is authenticated
    factory.assert_not_called()  # client must be lazy

    start, end = date(2026, 10, 21), date(2026, 10, 22)
    result = await coordinator._fetch_public_prices_for_range(start, end, "BE")

    factory.assert_called_once_with(clientsession=sentinel.ha_session)
    assert coordinator._public_api is public
    assert public.is_authenticated is False
    assert result is sentinel.public_prices
    public.country_prices.assert_awaited_once_with("BE", start, end, "PT15M")
    authenticated.country_prices.assert_not_awaited()
    authenticated.prices.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_countries_public_prices_remain_anonymous(clients):
    coordinator, authenticated, public, _ = clients
    start, end = date(2026, 10, 21), date(2026, 10, 22)
    result = await coordinator._fetch_public_prices_for_range(start, end, "NL")

    assert result is sentinel.public_prices
    public.prices.assert_awaited_once_with(start, resolution="PT15M")
    authenticated.prices.assert_not_awaited()


@pytest.mark.asyncio
async def test_customer_prices_still_use_authenticated_client(clients):
    coordinator, authenticated, public, factory = clients
    coordinator._site_reference = "test-site"
    coordinator._user_country = "BE"
    start, end = date(2026, 10, 21), date(2026, 10, 22)

    result = await coordinator._fetch_user_prices_for_range(start, end, "BE")

    assert result is sentinel.customer_prices
    factory.assert_not_called()  # account-only queries do not create public client
    authenticated.user_prices.assert_awaited_once_with("test-site", "BE", start, end)
    public.country_prices.assert_not_awaited()
    public.prices.assert_not_awaited()


@pytest.mark.parametrize(
    ("api_active_option", "configured_resolution", "expected_resolution"),
    [
        (None, "PT15M", "PT15M"),
        (None, "PT60M", "PT60M"),
        ("", "PT15M", "PT15M"),
        ("PT1H", "PT15M", "PT15M"),
        ("PT30M", "PT15M", "PT15M"),
        ("PT15M", "PT60M", "PT15M"),
        ("PT60M", "PT15M", "PT60M"),
        (None, "INVALID", "PT15M"),
    ],
)
@pytest.mark.asyncio
async def test_contract_state_cannot_send_invalid_public_price_resolution(
    clients, api_active_option, configured_resolution, expected_resolution
):
    """The API may omit activeOption for non-dynamic/variable contracts."""
    coordinator, authenticated, public, _ = clients
    coordinator.config_entry.options = {"resolution": configured_resolution}
    coordinator._api_resolution_state = ContractPriceResolutionState(
        active_option=api_active_option
    )
    coordinator._resolution_change_pending = False
    start, end = date(2026, 10, 21), date(2026, 10, 22)

    result = await coordinator._fetch_public_prices_for_range(start, end, "BE")

    assert result is sentinel.public_prices
    assert coordinator.resolution == expected_resolution
    public.country_prices.assert_awaited_once_with(
        "BE", start, end, expected_resolution
    )
    authenticated.country_prices.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_api_resolution_falls_back_while_change_pending(clients):
    """Configured resolution remains authoritative during an active change."""
    coordinator, _, public, _ = clients
    coordinator.config_entry.options = {"resolution": "PT60M"}
    coordinator._api_resolution_state = ContractPriceResolutionState(
        active_option="PT15M"
    )
    coordinator._resolution_change_pending = True
    start, end = date(2026, 10, 21), date(2026, 10, 22)

    await coordinator._fetch_public_prices_for_range(start, end, "BE")

    public.country_prices.assert_awaited_once_with("BE", start, end, "PT60M")
