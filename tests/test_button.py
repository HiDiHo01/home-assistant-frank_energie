import pytest
from unittest.mock import MagicMock

from custom_components.frank_energie.button import (
    FrankEnergieRefreshButton,
    FrankEnergieButtonEntityDescription,
)


@pytest.mark.asyncio
async def test_refresh_button(mock_coordinator):
    """Test manual refresh button press."""
    button = FrankEnergieRefreshButton(
        coordinator=mock_coordinator,
        description=FrankEnergieButtonEntityDescription(
            key="refresh_prices",
            name="Refresh Frank Energie Prices",
        ),
        entry=MagicMock(entry_id="test_entry"),
    )
    assert button.entity_description.name == "Refresh Frank Energie Prices"
    assert button.unique_id == "test_entry_refresh_prices"

    await button.async_press()
    mock_coordinator.async_request_refresh.assert_called_once()
