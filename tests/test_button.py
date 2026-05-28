import pytest
from unittest.mock import AsyncMock, MagicMock
from homeassistant.config_entries import ConfigEntry

from custom_components.frank_energie.const import (
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
)
from custom_components.frank_energie.button import (
    FrankEnergieRefreshButton,
    FrankEnergieDisableSmartTradingButton,
    FrankEnergieDisableSmartFeedInButton,
    FrankEnergieDisableSmartHvacButton,
)


@pytest.fixture
def mock_coordinator():
    coordinator = MagicMock()
    coordinator.data = {}
    coordinator.api = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.fixture
def mock_config_entry():
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    return entry


@pytest.mark.asyncio
async def test_refresh_button(mock_coordinator):
    """Test manual refresh button press."""
    button = FrankEnergieRefreshButton(
        entry_id="test_entry",
        coordinator=mock_coordinator,
        name="Refresh prices",
    )
    assert button.name == "Refresh prices"
    assert button.unique_id == "test_entry_refresh_prices"

    await button.async_press()
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_disable_smart_trading_button(mock_coordinator, mock_config_entry):
    """Test disable smart trading button availability and action."""
    button = FrankEnergieDisableSmartTradingButton(mock_coordinator, mock_config_entry)

    # Test unavailable when no data
    mock_coordinator.data = {}
    assert button.available is False

    # Test available when active (dict payload)
    mock_user = MagicMock()
    mock_user.smartTrading = {"isActivated": True}
    mock_coordinator.data = {DATA_USER: mock_user}
    assert button.available is True

    # Test unavailable when inactive (dict payload)
    mock_user.smartTrading = {"isActivated": False}
    assert button.available is False

    # Test action
    mock_user.smartTrading = {"isActivated": True}
    mock_coordinator.api.disable_smart_trading.return_value = True

    await button.async_press()
    mock_coordinator.api.disable_smart_trading.assert_called_once()
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_disable_smart_feed_in_button(mock_coordinator, mock_config_entry):
    """Test disable smart feed-in button availability and action."""
    button = FrankEnergieDisableSmartFeedInButton(mock_coordinator, mock_config_entry)

    # Test unavailable when no data
    mock_coordinator.data = {}
    assert button.available is False

    # Test available when active (dict payload)
    mock_coordinator.data = {DATA_USER_SMART_FEED_IN: {"isActivated": True}}
    assert button.available is True

    # Test unavailable when inactive (dict payload)
    mock_coordinator.data = {DATA_USER_SMART_FEED_IN: {"isActivated": False}}
    assert button.available is False

    # Test action
    mock_coordinator.data = {DATA_USER_SMART_FEED_IN: {"isActivated": True}}
    mock_coordinator.api.disable_smart_feed_in.return_value = True

    await button.async_press()
    mock_coordinator.api.disable_smart_feed_in.assert_called_once()
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_disable_smart_hvac_button(mock_coordinator, mock_config_entry):
    """Test disable smart HVAC button availability and action."""
    button = FrankEnergieDisableSmartHvacButton(mock_coordinator, mock_config_entry)

    # Test unavailable when no data
    mock_coordinator.data = {}
    assert button.available is False

    # Test available when active
    mock_user = MagicMock()
    mock_user.smartHvac = {"isActivated": True}
    mock_coordinator.data = {DATA_USER: mock_user}
    assert button.available is True

    # Test unavailable when inactive
    mock_user.smartHvac = {"isActivated": False}
    assert button.available is False

    # Test action
    mock_user.smartHvac = {"isActivated": True}
    mock_coordinator.api.disable_smart_hvac.return_value = True

    await button.async_press()
    mock_coordinator.api.disable_smart_hvac.assert_called_once()
    mock_coordinator.async_request_refresh.assert_called_once()
