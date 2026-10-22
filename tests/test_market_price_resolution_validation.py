"""Regression: an authenticated account may have no active price resolution.

Frank Energie returns contractPriceResolutionState.activeOption = null for some
Belgian variable contracts. Public MarketPrices requires a valid enum value.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, sentinel

import pytest
from python_frank_energie.models import ContractPriceResolutionState

from custom_components.frank_energie.coordinator import FrankEnergieCoordinator


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "account_resolution",
        "configured_resolution",
        "authenticated",
        "change_pending",
        "expected_resolution",
    ),
    [
        (None, "PT15M", True, False, "PT15M"),
        (None, "PT60M", True, False, "PT60M"),
        ("", "PT15M", True, False, "PT15M"),
        ("PT1H", "PT15M", True, False, "PT15M"),
        ("PT30M", "PT15M", True, False, "PT15M"),
        ("PT15M", "PT60M", True, False, "PT15M"),
        ("PT60M", "PT15M", True, False, "PT60M"),
        (None, "NOT_A_RESOLUTION", True, False, "PT15M"),
        ("PT60M", "PT15M", True, True, "PT15M"),
        (None, "PT60M", False, False, "PT60M"),
        ("PT15M", "PT60M", False, False, "PT60M"),
        ("PT60M", "PT15M", False, False, "PT15M"),
        ("PT30M", "PT60M", True, False, "PT60M"),
        ("", "PT60M", True, False, "PT60M"),
    ],
)
async def test_public_market_prices_use_supported_resolution(
    account_resolution: str | None,
    configured_resolution: str,
    authenticated: bool,
    change_pending: bool,
    expected_resolution: str,
) -> None:
    """Never send a null or unsupported PriceResolution GraphQL enum."""
    entry = MagicMock()
    entry.options = {"resolution": configured_resolution}
    api = MagicMock()
    api.is_authenticated = authenticated
    api.country_prices = AsyncMock(return_value=sentinel.public_prices)
    coordinator = FrankEnergieCoordinator(MagicMock(), entry, api)
    coordinator._api_resolution_state = ContractPriceResolutionState(
        active_option=account_resolution
    )
    coordinator._resolution_change_pending = change_pending
    today = date(2026, 10, 21)
    tomorrow = date(2026, 10, 22)

    result = await coordinator._fetch_public_prices_for_range(today, tomorrow, "BE")

    assert result is sentinel.public_prices
    assert coordinator.resolution == expected_resolution
    api.country_prices.assert_awaited_once_with(
        "BE", today, tomorrow, expected_resolution
    )


def test_no_config_entry_uses_supported_default_resolution() -> None:
    """A missing config entry cannot turn the GraphQL enum into null."""
    entry = MagicMock()
    entry.options = {}
    coordinator = FrankEnergieCoordinator(MagicMock(), entry, MagicMock())
    coordinator.config_entry = None
    assert coordinator.resolution == "PT15M"
