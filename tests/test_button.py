import pytest
from unittest.mock import MagicMock

from custom_components.frank_energie.button import (
    FrankEnergieRefreshButton,
    FrankEnergieButtonEntityDescription,
)


@pytest.mark.asyncio
async def test_refresh_button(mock_coordinator):
    """Test manual refresh button press for prices and battery sessions."""
    button = FrankEnergieRefreshButton(
        coordinator=mock_coordinator,
        description=FrankEnergieButtonEntityDescription(
            key="refresh_prices",
            translation_key="refresh_prices",
        ),
        entry=MagicMock(entry_id="test_entry"),
    )
    assert button.translation_key == "refresh_prices"
    assert button.unique_id == "test_entry_refresh_prices"
    assert button.has_entity_name is True

    await button.async_press()
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_battery_sessions_button(mock_coordinator):
    """Test manual refresh button press for battery sessions."""
    from custom_components.frank_energie.const import SERVICE_NAME_BATTERIES

    button = FrankEnergieRefreshButton(
        coordinator=mock_coordinator,
        description=FrankEnergieButtonEntityDescription(
            key="refresh_battery_sessions",
            translation_key="refresh_battery_sessions",
            service_name=SERVICE_NAME_BATTERIES,
        ),
        entry=MagicMock(entry_id="test_entry"),
    )
    assert button.translation_key == "refresh_battery_sessions"
    assert button.unique_id == "test_entry_refresh_battery_sessions"
    assert button.has_entity_name is True

    await button.async_press()
    mock_coordinator.async_request_refresh.assert_called_once()
