import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone
from custom_components.frank_energie.const import (
    DATA_ELECTRICITY,
    DATA_GAS,
    DATA_INVOICES,
    DATA_MONTH_SUMMARY,
    DATA_USER,
)
from custom_components.frank_energie.exceptions import NoSuitableSitesFoundError
from custom_components.frank_energie.coordinator import FrankEnergieCoordinator
from custom_components.frank_energie import FrankEnergieComponent
from pytest_homeassistant_custom_component.common import MockConfigEntry
from python_frank_energie import FrankEnergie
from python_frank_energie.models import MonthSummary, Invoices, User

# Sample data for mocking
mock_entry_data = {
    "site_reference": "test_reference",
    "access_token": "test_token",
}


@pytest.mark.asyncio
async def test_no_suitable_sites_found():
    """NoSuitableSitesFoundError is raised when the API returns no delivery sites.

    This exercises the real code path in FrankEnergieComponent._get_site_reference_and_title
    rather than directly raising the exception, so the test fails if the guard
    logic is removed or the exception class changes.
    """
    # Stub UserSites to return a response with an empty deliverySites list
    mock_user_sites = MagicMock()
    mock_user_sites.deliverySites = []

    mock_api = AsyncMock(spec=FrankEnergie)
    mock_api.UserSites = AsyncMock(return_value=mock_user_sites)

    mock_coordinator = MagicMock()
    mock_coordinator.api = mock_api

    mock_entry = MagicMock()
    mock_hass = MagicMock()

    component = FrankEnergieComponent(mock_hass, mock_entry)

    with pytest.raises(NoSuitableSitesFoundError):
        await component._get_site_reference_and_title(mock_coordinator)


@pytest.fixture
def mock_frank_energie():
    """Create a mock FrankEnergie API instance."""
    return AsyncMock(spec=FrankEnergie)


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    return MockConfigEntry(
        version=1,
        domain="frank_energie",
        title="Frank Energie",
        data=mock_entry_data,
        options={},
        source="user",
        entry_id="123",
        state="loaded",
        minor_version=1,  # Set this to the appropriate minor version
        unique_id="test_unique_id",  # Ensure this is unique
    )


@pytest.fixture
def coordinator(mock_frank_energie, mock_config_entry):
    """Create an instance of FrankEnergieCoordinator."""
    return FrankEnergieCoordinator(
        hass=MagicMock(),
        config_entry=mock_config_entry,
        api=mock_frank_energie,
    )


@pytest.mark.asyncio
async def test_fetch_today_data(coordinator, mock_frank_energie):
    """Test fetching today's data."""
    # Setup mock return values
    mock_prices = MagicMock()
    mock_prices.electricity.all = [MagicMock()]
    mock_prices.gas.all = [MagicMock()]
    mock_prices.electricity.today_min = MagicMock()
    mock_frank_energie.user_prices.return_value = mock_prices
    mock_frank_energie.month_summary.return_value = MagicMock()
    mock_frank_energie.invoices.return_value = MagicMock()

    mock_user = MagicMock()
    mock_user.connections = []
    mock_frank_energie.user.return_value = mock_user

    # Perform the fetch
    data = await coordinator._fetch_today_data(
        datetime.now(timezone.utc).date(),
        datetime.now(timezone.utc).date() + timedelta(days=1),
    )

    # Assertions
    assert data is not None
    (
        prices_today,
        data_month_summary,
        data_invoices,
        data_user,
        user_sites,
        data_period_usage,
        data_enode_chargers,
        data_smart_batteries,
        data_smart_battery_details,
        data_smart_battery_sessions,
        data_enode_vehicles,
        data_contract_price_resolution_state,
    ) = data

    assert prices_today == mock_prices
    assert isinstance(data_month_summary, MagicMock)
    assert isinstance(data_invoices, MagicMock)
    assert isinstance(data_user, MagicMock)


@pytest.mark.asyncio
async def test_renew_token(coordinator, mock_frank_energie):
    """Test token renewal."""
    # Mock renewal of the token
    mock_frank_energie.renew_token.return_value = AsyncMock(
        authToken="new_token", refreshToken="new_refresh_token"
    )

    await coordinator._try_renew_token()

    # Verify that the entry data was updated with new tokens
    coordinator.hass.config_entries.async_update_entry.assert_called_once_with(
        coordinator.config_entry,
        data={"access_token": "new_token", "token": "new_refresh_token"},  # NOSONAR
    )


@pytest.mark.asyncio
async def test_aggregate_data(coordinator):
    """Test data aggregation."""
    prices_today = MagicMock()
    prices_today.electricity = 0.45
    prices_today.gas = 0.09
    prices_tomorrow = MagicMock()
    prices_tomorrow.electricity = 0.50
    prices_tomorrow.gas = 0.10
    data_month_summary = MagicMock(spec=MonthSummary)
    data_invoices = MagicMock(spec=Invoices)
    data_user = MagicMock(spec=User)

    aggregated_data = coordinator._aggregate_data(
        prices_today,
        prices_tomorrow,
        data_month_summary,
        data_invoices,
        data_user,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )

    # Assertions
    assert aggregated_data[DATA_ELECTRICITY] == pytest.approx(0.95)  # 0.45 + 0.50
    assert aggregated_data[DATA_GAS] == pytest.approx(0.19)  # 0.09 + 0.10
    assert isinstance(aggregated_data[DATA_MONTH_SUMMARY], MagicMock)
    assert isinstance(aggregated_data[DATA_INVOICES], MagicMock)
    assert isinstance(aggregated_data[DATA_USER], MagicMock)


@pytest.mark.asyncio
async def test_adjust_update_interval_inside_window(coordinator):
    """Test update interval adjustment inside the price release window."""
    from datetime import datetime, timezone
    from unittest.mock import patch

    # Price release window is between 13:00 and 15:00 UTC
    now_utc = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)

    with patch("secrets.randbelow", return_value=10) as mock_randbelow:
        coordinator._adjust_update_interval(now_utc)
        # 300 + 10 + 5 = 315 seconds
        assert coordinator.update_interval.total_seconds() == 315
        mock_randbelow.assert_called_once_with(41)


@pytest.mark.asyncio
async def test_adjust_update_interval_outside_window(coordinator):
    """Test update interval adjustment outside the price release window."""
    from datetime import datetime, timezone
    from unittest.mock import patch

    now_utc = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)

    with patch("secrets.randbelow", return_value=20) as mock_randbelow:
        coordinator._adjust_update_interval(now_utc)
        # 900 + 20 + 10 = 930 seconds
        assert coordinator.update_interval.total_seconds() == 930
        mock_randbelow.assert_called_once_with(71)


@pytest.mark.asyncio
async def test_fetch_today_data_caching(coordinator, mock_frank_energie):
    """Test that static data is cached and not refetched on the same day, but refetched on a new day."""
    from datetime import datetime, timezone, timedelta

    mock_prices = MagicMock()
    mock_prices.electricity.all = [MagicMock()]
    mock_prices.gas.all = [MagicMock()]
    mock_prices.electricity.today_min = MagicMock()
    mock_frank_energie.user_prices.return_value = mock_prices
    mock_frank_energie.month_summary.return_value = MagicMock()
    mock_frank_energie.invoices.return_value = MagicMock()

    mock_user = MagicMock()
    mock_user.connections = []
    mock_frank_energie.user.return_value = mock_user

    today = datetime(2026, 5, 27, tzinfo=timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    # First fetch (cache empty)
    await coordinator._fetch_today_data(today, tomorrow)
    assert mock_frank_energie.user_prices.call_count == 1
    coordinator.last_fetch_today = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)

    # Second fetch on same day (should use cache)
    await coordinator._fetch_today_data(today, tomorrow)
    assert mock_frank_energie.user_prices.call_count == 1

    # Fetch on a new day (cache should invalidate)
    new_day = today + timedelta(days=1)
    new_tomorrow = new_day + timedelta(days=1)
    await coordinator._fetch_today_data(new_day, new_tomorrow)
    assert mock_frank_energie.user_prices.call_count == 2


@pytest.mark.asyncio
async def test_fetch_today_data_auth_failure(coordinator, mock_frank_energie):
    """Test auth failure triggers token renewal attempt and raises ConfigEntryAuthFailed."""
    from datetime import datetime, timezone, timedelta
    from python_frank_energie.exceptions import AuthRequiredException
    from homeassistant.exceptions import ConfigEntryAuthFailed

    mock_frank_energie.user_prices.side_effect = AuthRequiredException("auth_required")
    coordinator._try_renew_token = AsyncMock()

    today = datetime(2026, 5, 27, tzinfo=timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._fetch_today_data(today, tomorrow)

    coordinator._try_renew_token.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_today_data_network_failure(coordinator, mock_frank_energie):
    """Test that a non-auth network failure raises UpdateFailed."""
    from datetime import datetime, timezone, timedelta
    from python_frank_energie.exceptions import RequestException
    from homeassistant.helpers.update_coordinator import UpdateFailed

    mock_frank_energie.user_prices.side_effect = RequestException("network_error")

    today = datetime(2026, 5, 27, tzinfo=timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    with pytest.raises(UpdateFailed):
        await coordinator._fetch_today_data(today, tomorrow)


@pytest.mark.asyncio
async def test_fetch_today_data_dynamic_auth_failure(coordinator, mock_frank_energie):
    """Test that auth failures from dynamic endpoints propagate and trigger token renewal."""
    from datetime import datetime, timezone, timedelta
    from python_frank_energie.exceptions import AuthRequiredException
    from homeassistant.exceptions import ConfigEntryAuthFailed

    # Setup mock return values for static data
    mock_prices = MagicMock()
    mock_prices.electricity.all = [MagicMock()]
    mock_prices.gas.all = [MagicMock()]
    mock_prices.electricity.today_min = MagicMock()
    mock_frank_energie.user_prices.return_value = mock_prices
    mock_frank_energie.month_summary.return_value = MagicMock()
    mock_frank_energie.invoices.return_value = MagicMock()
    mock_user = MagicMock()
    mock_user.connections = []
    # Make sure smart trading and charging are True so dynamic endpoints are queried
    mock_user.smartTrading = {"isActivated": True}
    mock_user.smartCharging = {"isActivated": True}
    mock_frank_energie.user.return_value = mock_user

    # Mock one of the dynamic calls to raise AuthRequiredException
    mock_frank_energie.smart_batteries.side_effect = AuthRequiredException("auth_required")
    coordinator._try_renew_token = AsyncMock()

    today = datetime(2026, 5, 27, tzinfo=timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    # Perform fetch - should raise ConfigEntryAuthFailed
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._fetch_today_data(today, tomorrow)

    # Verify token renewal was triggered
    coordinator._try_renew_token.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_month_summary_exception(coordinator, mock_frank_energie):
    """Test that _fetch_month_summary handles generic/request exceptions by returning None."""
    from python_frank_energie.exceptions import FrankEnergieException

    mock_frank_energie.is_authenticated = True
    mock_frank_energie.month_summary.side_effect = FrankEnergieException("site_reference error")
    result = await coordinator._fetch_month_summary()
    assert result is None


@pytest.mark.asyncio
async def test_fetch_month_summary_auth_exception(coordinator, mock_frank_energie):
    """Test that _fetch_month_summary handles authentication exceptions by returning None."""
    from python_frank_energie.exceptions import AuthException

    mock_frank_energie.is_authenticated = True
    mock_frank_energie.month_summary.side_effect = AuthException("auth error")
    result = await coordinator._fetch_month_summary()
    assert result is None

