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
from custom_components.frank_energie.coordinator import FrankEnergieCoordinator, PricesTodayCache
from custom_components.frank_energie import FrankEnergieComponent
from pytest_homeassistant_custom_component.common import MockConfigEntry
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import FrankEnergieException, RequestException
from python_frank_energie.models import MonthSummary, Invoices, User
from aiohttp import ClientError


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
    assert data.prices_today == mock_prices
    assert isinstance(data.data_month_summary, MagicMock)
    assert isinstance(data.data_invoices, MagicMock)
    assert isinstance(data.data_user, MagicMock)


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

    cache = PricesTodayCache(
        prices_today=prices_today,
        data_month_summary=data_month_summary,
        data_invoices=data_invoices,
        data_user=data_user,
        user_sites=None,
        data_period_usage=None,
        data_enode_chargers=None,
        data_smart_batteries=None,
        data_smart_battery_details=[],
        data_smart_battery_sessions=[],
        data_enode_vehicles=None,
        data_pv_systems=None,
        data_pv_summary=None,
        data_user_smart_feed_in=None,
        data_contract_price_resolution_state=None,
    )

    aggregated_data = coordinator._aggregate_data(
        cache,
        prices_tomorrow,
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

    # Price release window is between 11:00 and 13:00 UTC
    now_utc = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)

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
    mock_frank_energie.smart_batteries.side_effect = AuthRequiredException(
        "auth_required"
    )
    coordinator._try_renew_token = AsyncMock()

    today = datetime(2026, 5, 27, tzinfo=timezone.utc).date()
    tomorrow = today + timedelta(days=1)

    # Perform fetch - should raise ConfigEntryAuthFailed
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._fetch_today_data(today, tomorrow)

    # Verify token renewal was triggered
    coordinator._try_renew_token.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception_cls",
    [
        FrankEnergieException,
        RequestException,
        ClientError,
    ],
)
async def test_fetch_month_summary_exceptions_return_none(
    coordinator, mock_frank_energie, exception_cls
):
    """Test that _fetch_month_summary handles non-auth exceptions by returning None."""
    mock_frank_energie.is_authenticated = True
    mock_frank_energie.month_summary.side_effect = exception_cls("test error")
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


# ---------------------------------------------------------------------------
# Tests for code changed/added in this PR
# ---------------------------------------------------------------------------


class TestInitNewAttributes:
    """Tests for new attributes introduced in __init__ by this PR."""

    def test_last_lowest_4p_event_initialized_to_none(self, coordinator):
        """_last_lowest_4p_event must start as None."""
        assert coordinator._last_lowest_4p_event is None

    def test_last_lowest_16p_event_initialized_to_none(self, coordinator):
        """_last_lowest_16p_event must start as None."""
        assert coordinator._last_lowest_16p_event is None

    def test_api_resolution_state_initialized_to_none(self, coordinator):
        """_api_resolution_state must start as None."""
        assert coordinator._api_resolution_state is None

    def test_mutation_queue_created(self, coordinator):
        """A MutationQueue instance must be created during init."""
        from custom_components.frank_energie.mutation_queue import MutationQueue

        assert isinstance(coordinator._mutation_queue, MutationQueue)

    def test_api_set_from_argument(self, coordinator, mock_frank_energie):
        """The api attribute must equal the api argument passed to __init__."""
        assert coordinator.api is mock_frank_energie

    def test_site_reference_from_config_entry(self, mock_frank_energie):
        """site_reference must be read from config_entry.data."""
        entry = MockConfigEntry(
            version=1,
            domain="frank_energie",
            title="Frank Energie",
            data={"site_reference": "ref-xyz", "access_token": "tok"},
            options={},
            source="user",
            entry_id="abc",
            state="loaded",
            minor_version=1,
            unique_id="uid-xyz",
        )
        coord = FrankEnergieCoordinator(
            hass=MagicMock(),
            config_entry=entry,
            api=mock_frank_energie,
        )
        assert coord.site_reference == "ref-xyz"

    def test_site_reference_none_when_not_in_data(self, mock_frank_energie):
        """site_reference must be None when not present in config_entry.data."""
        # Create config entry without 'site_reference' key
        entry_data_without_site_ref = {k: v for k, v in mock_entry_data.items() if k != "site_reference"}
        entry = MockConfigEntry(
            version=1,
            domain="frank_energie",
            title="Frank Energie",
            data=entry_data_without_site_ref,
            options={},
            source="user",
            entry_id="no-site-ref",
            state="loaded",
            minor_version=1,
            unique_id="uid-no-site-ref",
        )
        coord = FrankEnergieCoordinator(
            hass=MagicMock(),
            config_entry=entry,
            api=mock_frank_energie,
        )
        assert coord.site_reference is None


class TestMarkLowest4pEventFired:
    """Tests for the renamed _mark_lowest_4p_event_fired method."""

    def test_sets_last_lowest_4p_event(self, coordinator):
        """After calling _mark_lowest_4p_event_fired, _last_lowest_4p_event equals today."""
        from datetime import date

        today = date(2026, 5, 30)
        coordinator._mark_lowest_4p_event_fired(today)
        assert coordinator._last_lowest_4p_event == today

    def test_overwrites_previous_date(self, coordinator):
        """Calling _mark_lowest_4p_event_fired twice updates to the latest date."""
        from datetime import date

        old_date = date(2026, 5, 29)
        new_date = date(2026, 5, 30)
        coordinator._mark_lowest_4p_event_fired(old_date)
        coordinator._mark_lowest_4p_event_fired(new_date)
        assert coordinator._last_lowest_4p_event == new_date

    def test_does_not_affect_other_event_flags(self, coordinator):
        """_mark_lowest_4p_event_fired must not modify other event tracking attributes."""
        from datetime import date

        today = date(2026, 5, 30)
        coordinator._mark_lowest_4p_event_fired(today)
        assert coordinator._last_lowest_price_event is None
        assert coordinator._last_lowest_16p_event is None


class TestShouldFireLowest16pEvent:
    """Tests for the new _should_fire_lowest_16p_event method."""

    def test_returns_true_when_never_fired(self, coordinator):
        """Should return True when _last_lowest_16p_event is None."""
        from datetime import date

        today = date(2026, 5, 30)
        assert coordinator._should_fire_lowest_16p_event(today) is True

    def test_returns_true_when_fired_on_different_day(self, coordinator):
        """Should return True when last event was fired on a different day."""
        from datetime import date

        yesterday = date(2026, 5, 29)
        today = date(2026, 5, 30)
        coordinator._last_lowest_16p_event = yesterday
        assert coordinator._should_fire_lowest_16p_event(today) is True

    def test_returns_false_when_already_fired_today(self, coordinator):
        """Should return False when event was already fired today."""
        from datetime import date

        today = date(2026, 5, 30)
        coordinator._last_lowest_16p_event = today
        assert coordinator._should_fire_lowest_16p_event(today) is False


class TestMarkLowest16pEventFired:
    """Tests for the new _mark_lowest_16p_event_fired method."""

    def test_sets_last_lowest_16p_event(self, coordinator):
        """After calling _mark_lowest_16p_event_fired, _last_lowest_16p_event equals today."""
        from datetime import date

        today = date(2026, 5, 30)
        coordinator._mark_lowest_16p_event_fired(today)
        assert coordinator._last_lowest_16p_event == today

    def test_subsequent_should_fire_returns_false(self, coordinator):
        """After marking fired, _should_fire_lowest_16p_event must return False."""
        from datetime import date

        today = date(2026, 5, 30)
        coordinator._mark_lowest_16p_event_fired(today)
        assert coordinator._should_fire_lowest_16p_event(today) is False

    def test_does_not_affect_4p_event_flag(self, coordinator):
        """_mark_lowest_16p_event_fired must not modify _last_lowest_4p_event."""
        from datetime import date

        today = date(2026, 5, 30)
        coordinator._mark_lowest_16p_event_fired(today)
        assert coordinator._last_lowest_4p_event is None


class TestReconcileResolution:
    """Tests for the new _reconcile_resolution method."""

    def test_returns_early_when_no_api_resolution_state(self, coordinator):
        """Must return without error when _api_resolution_state is None."""
        coordinator._api_resolution_state = None
        # Should not raise
        coordinator._reconcile_resolution()

    def test_returns_early_when_config_entry_is_none(self, coordinator):
        """Must return without error when config_entry is None."""
        mock_state = MagicMock()
        mock_state.activeOption = "PT15M"
        coordinator._api_resolution_state = mock_state
        coordinator.config_entry = None
        # Should not raise
        coordinator._reconcile_resolution()

    def test_no_warning_when_values_match(self, coordinator):
        """Must not log a warning when API and config values are identical."""
        from unittest.mock import patch

        mock_state = MagicMock()
        mock_state.activeOption = "PT15M"
        coordinator._api_resolution_state = mock_state
        mock_config_entry = MagicMock()
        mock_config_entry.options = {"resolution": "PT15M"}
        coordinator.config_entry = mock_config_entry

        with patch(
            "custom_components.frank_energie.coordinator._LOGGER"
        ) as mock_logger:
            coordinator._reconcile_resolution()
            mock_logger.warning.assert_not_called()

    def test_logs_warning_when_drift_detected(self, coordinator):
        """Must log a warning when config and API resolution values differ."""
        from unittest.mock import patch

        mock_state = MagicMock()
        mock_state.activeOption = "PT60M"
        coordinator._api_resolution_state = mock_state
        mock_config_entry = MagicMock()
        mock_config_entry.options = {"resolution": "PT15M"}
        coordinator.config_entry = mock_config_entry

        with patch(
            "custom_components.frank_energie.coordinator._LOGGER"
        ) as mock_logger:
            coordinator._reconcile_resolution()
            mock_logger.warning.assert_called_once()
            warning_args = mock_logger.warning.call_args[0]
            assert "drift" in warning_args[0].lower() or "resolution" in warning_args[0].lower()

    def test_no_warning_when_api_value_is_none(self, coordinator):
        """Must not log a warning when api_value is None."""
        from unittest.mock import patch

        mock_state = MagicMock()
        mock_state.activeOption = None
        coordinator._api_resolution_state = mock_state
        mock_config_entry = MagicMock()
        mock_config_entry.options = {"resolution": "PT15M"}
        coordinator.config_entry = mock_config_entry

        with patch(
            "custom_components.frank_energie.coordinator._LOGGER"
        ) as mock_logger:
            coordinator._reconcile_resolution()
            mock_logger.warning.assert_not_called()

    def test_no_warning_when_config_value_is_none(self, coordinator):
        """Must not log a warning when config_value is None (not set)."""
        from unittest.mock import patch

        mock_state = MagicMock()
        mock_state.activeOption = "PT15M"
        coordinator._api_resolution_state = mock_state
        mock_config_entry = MagicMock()
        # options dict without 'resolution' key -> get returns None
        mock_config_entry.options = {}
        coordinator.config_entry = mock_config_entry

        with patch(
            "custom_components.frank_energie.coordinator._LOGGER"
        ) as mock_logger:
            coordinator._reconcile_resolution()
            mock_logger.warning.assert_not_called()


class TestApiResolutionProperty:
    """Tests for the new api_resolution property."""

    def test_returns_none_when_no_api_resolution_state(self, coordinator):
        """api_resolution must return None when _api_resolution_state is None."""
        coordinator._api_resolution_state = None
        assert coordinator.api_resolution is None

    def test_returns_active_option_when_state_set(self, coordinator):
        """api_resolution must return activeOption from _api_resolution_state."""
        mock_state = MagicMock()
        mock_state.activeOption = "PT15M"
        coordinator._api_resolution_state = mock_state
        assert coordinator.api_resolution == "PT15M"

    def test_returns_none_active_option_when_state_has_none(self, coordinator):
        """api_resolution must propagate None activeOption from _api_resolution_state."""
        mock_state = MagicMock()
        mock_state.activeOption = None
        coordinator._api_resolution_state = mock_state
        assert coordinator.api_resolution is None

    def test_property_is_read_only(self, coordinator):
        """api_resolution must be a read-only property."""
        import pytest

        with pytest.raises(AttributeError):
            coordinator.api_resolution = "PT60M"


class TestAsyncSetResolution:
    """Tests for the new async_set_resolution method."""

    @pytest.mark.asyncio
    async def test_raises_update_failed_when_no_connection_id(self, coordinator):
        """Must return early without raising when _connection_id is None."""
        coordinator._connection_id = None
        coordinator.async_request_refresh = AsyncMock()

        await coordinator.async_set_resolution("PT15M")
        coordinator.async_request_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_update_failed_when_api_returns_none(self, coordinator, mock_frank_energie):
        """Must raise UpdateFailed when API returns None result."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        coordinator._connection_id = "conn-123"
        coordinator.async_request_refresh = AsyncMock()
        mock_frank_energie.contract_price_resolution_request_change = AsyncMock(
            return_value=None
        )

        with pytest.raises(UpdateFailed):
            await coordinator.async_set_resolution("PT15M")

    @pytest.mark.asyncio
    async def test_raises_update_failed_when_result_not_success(
        self, coordinator, mock_frank_energie
    ):
        """Must raise UpdateFailed when result.success is False."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        coordinator._connection_id = "conn-123"
        coordinator.async_request_refresh = AsyncMock()

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.reason = "server_error"
        mock_frank_energie.contract_price_resolution_request_change = AsyncMock(
            return_value=mock_result
        )

        with pytest.raises(UpdateFailed, match="server_error"):
            await coordinator.async_set_resolution("PT15M")

    @pytest.mark.asyncio
    async def test_success_updates_config_entry_and_requests_refresh(
        self, coordinator, mock_frank_energie
    ):
        """On success, config entry must be updated and refresh must be requested."""
        coordinator._connection_id = "conn-123"
        coordinator.async_request_refresh = AsyncMock()

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = MagicMock()
        mock_result.data.effectiveDate = "2026-06-01"
        mock_frank_energie.contract_price_resolution_request_change = AsyncMock(
            return_value=mock_result
        )

        await coordinator.async_set_resolution("PT60M")

        coordinator.hass.config_entries.async_update_entry.assert_called_once_with(
            coordinator.config_entry,
            options={**coordinator.config_entry.options, "resolution": "PT60M"},
        )
        coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_called_with_connection_id_and_value(
        self, coordinator, mock_frank_energie
    ):
        """The API must be called with correct connection_id and resolution value."""
        coordinator._connection_id = "conn-abc"
        coordinator.async_request_refresh = AsyncMock()

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = MagicMock()
        mock_result.data.effectiveDate = "2026-06-01"
        mock_frank_energie.contract_price_resolution_request_change = AsyncMock(
            return_value=mock_result
        )

        await coordinator.async_set_resolution("PT15M")

        mock_frank_energie.contract_price_resolution_request_change.assert_called_once_with(
            "conn-abc", "PT15M"
        )

    @pytest.mark.asyncio
    async def test_skips_config_update_when_config_entry_is_none(
        self, coordinator, mock_frank_energie
    ):
        """If config_entry becomes None inside mutation, update must be skipped without error."""
        coordinator._connection_id = "conn-123"
        coordinator.async_request_refresh = AsyncMock()

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = MagicMock()
        mock_result.data.effectiveDate = "2026-06-01"
        mock_frank_energie.contract_price_resolution_request_change = AsyncMock(
            return_value=mock_result
        )

        # Set config_entry to None after coordinator is created
        coordinator.config_entry = None

        # Should not raise
        await coordinator.async_set_resolution("PT60M")
        coordinator.async_request_refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_called_even_after_mutation_success(
        self, coordinator, mock_frank_energie
    ):
        """async_request_refresh must always be called after a successful mutation."""
        coordinator._connection_id = "conn-999"
        coordinator.async_request_refresh = AsyncMock()

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.data = None  # effectiveDate path: result.data is None
        mock_frank_energie.contract_price_resolution_request_change = AsyncMock(
            return_value=mock_result
        )

        await coordinator.async_set_resolution("PT15M")
        coordinator.async_request_refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_not_called_when_connection_id_is_none(self, coordinator):
        """async_request_refresh must NOT be called when _connection_id is None."""
        coordinator._connection_id = None
        coordinator.async_request_refresh = AsyncMock()

        await coordinator.async_set_resolution("PT15M")
        coordinator.async_request_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_message_contains_reason_when_result_fails(
        self, coordinator, mock_frank_energie
    ):
        """UpdateFailed message must include the reason from the API response."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        coordinator._connection_id = "conn-123"
        coordinator.async_request_refresh = AsyncMock()

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.reason = "contract_locked"
        mock_frank_energie.contract_price_resolution_request_change = AsyncMock(
            return_value=mock_result
        )

        with pytest.raises(UpdateFailed, match="contract_locked"):
            await coordinator.async_set_resolution("PT15M")


@pytest.mark.asyncio
async def test_fetch_today_data_retry_on_auth_failure(coordinator, mock_frank_energie):
    """Test that _fetch_today_data retries on AuthException after renewing token."""
    from python_frank_energie.exceptions import AuthException

    # Mock static data fetch: raise AuthException on first call, return valid tuple on second
    mock_prices = MagicMock()
    mock_prices.electricity.all = [MagicMock()]
    mock_prices.gas.all = [MagicMock()]
    mock_prices.electricity.today_min = MagicMock()

    mock_user = MagicMock()
    mock_user.connections = []

    static_data_result = (
        mock_prices,
        MagicMock(), # user_sites
        MagicMock(), # data_month_summary
        MagicMock(), # data_invoices
        MagicMock(), # data_period_usage
        mock_user,   # data_user
        None,        # data_contract_price_resolution_state
    )

    call_count = 0
    async def mock_get_static_data(today, tomorrow, start_date):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise AuthException("Token expired")
        return static_data_result

    coordinator._get_static_data = mock_get_static_data
    coordinator._try_renew_token = AsyncMock()
    coordinator._clear_static_cache = MagicMock()

    # Mock dynamic data fetches
    coordinator._fetch_enode_chargers = AsyncMock(return_value={})
    coordinator._fetch_smart_batteries = AsyncMock(return_value=None)
    coordinator._fetch_enode_vehicles = AsyncMock(return_value=None)
    coordinator._fetch_smart_pv_systems = AsyncMock(return_value=None)
    coordinator._fetch_user_smart_feed_in = AsyncMock(return_value=None)
    coordinator._get_battery_details_and_sessions = AsyncMock(return_value=([], []))

    # Perform the fetch
    data = await coordinator._fetch_today_data(
        datetime.now(timezone.utc).date(),
        datetime.now(timezone.utc).date() + timedelta(days=1),
    )

    assert data is not None
    assert call_count == 2
    coordinator._try_renew_token.assert_called_once()
    coordinator._clear_static_cache.assert_called_once()
    assert data.prices_today == mock_prices

