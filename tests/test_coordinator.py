import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from custom_components.frank_energie.const import (
    DATA_ELECTRICITY,
    DATA_GAS,
    DATA_INVOICES,
    DATA_MONTH_SUMMARY,
    DATA_USER,
)
from custom_components.frank_energie.exceptions import NoSuitableSitesFoundError
from custom_components.frank_energie.coordinator import (
    FrankEnergieCoordinator,
    PricesTodayCache,
    FrankEnergieSettingsCoordinator,
    FrankEnergiePriceCoordinator,
    FrankEnergieBatteryCoordinator,
    FrankEnergieChargerCoordinator,
    FrankEnergiePVCoordinator,
    FrankEnergieVehicleCoordinator,
    FrankEnergieStatisticsCoordinator,
)
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
        data={
            "site_reference": "test_reference",
            "access_token": "new_token",
            "token": "new_refresh_token",  # NOSONAR
        },
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
    from datetime import datetime
    from zoneinfo import ZoneInfo

    # Price release window is between 13:00 and 15:00 local time
    # On May 27th (summer), 12:00 UTC = 14:00 local time (inside window)
    now_utc = datetime(2026, 5, 27, 12, 0, 0, tzinfo=ZoneInfo("UTC"))

    coordinator._adjust_update_interval(now_utc)
    # Exactly 300 seconds (5 minutes)
    assert coordinator.update_interval.total_seconds() == 300


@pytest.mark.asyncio
async def test_adjust_update_interval_outside_window(coordinator):
    """Test update interval adjustment outside the price release window."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    # On May 27th, 10:00 UTC = 12:00 local time (outside window)
    now_utc = datetime(2026, 5, 27, 10, 0, 0, tzinfo=ZoneInfo("UTC"))

    coordinator._adjust_update_interval(now_utc)
    # Update interval is disabled (None) outside window
    assert coordinator.update_interval is None


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
        entry_data_without_site_ref = {
            k: v for k, v in mock_entry_data.items() if k != "site_reference"
        }
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

    def test_site_reference_updates_dynamically(
        self, mock_frank_energie: AsyncMock
    ) -> None:
        """site_reference must update dynamically when config_entry.data is updated."""
        entry = MockConfigEntry(
            version=1,
            domain="frank_energie",
            title="Frank Energie",
            data={"access_token": "tok"},
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
        assert coord.site_reference is None

        # Setup side effect to simulate async_update_entry behavior on entry.__dict__
        def mock_update_entry(entry, **kwargs):
            if "data" in kwargs:
                entry.__dict__["data"] = kwargs["data"]

        coord.hass.config_entries.async_update_entry.side_effect = mock_update_entry

        # Simulate updating config entry data during setup using the public API
        coord.hass.config_entries.async_update_entry(
            entry, data={**entry.data, "site_reference": "ref-xyz"}
        )
        assert coord.site_reference == "ref-xyz"


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

    def test_logs_warning_when_unexpected_drift_detected(self, coordinator):
        """Must log a warning when config and API resolution values differ and no matching change is pending."""
        from unittest.mock import patch

        mock_state = MagicMock()
        mock_state.activeOption = "PT60M"
        mock_state.upcomingChange = None
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
            assert (
                "drift" in warning_args[0].lower()
                or "resolution" in warning_args[0].lower()
            )

    def test_logs_debug_when_expected_drift_detected(self, coordinator):
        """Must log a debug message when config and API values differ but a matching change is pending."""
        from unittest.mock import patch

        mock_state = MagicMock()
        mock_state.activeOption = "PT60M"
        mock_state.upcomingChange = "PT15M"
        coordinator._api_resolution_state = mock_state
        mock_config_entry = MagicMock()
        mock_config_entry.options = {"resolution": "PT15M"}
        coordinator.config_entry = mock_config_entry

        with patch(
            "custom_components.frank_energie.coordinator._LOGGER"
        ) as mock_logger:
            coordinator._reconcile_resolution()
            mock_logger.debug.assert_called_once()
            debug_args = mock_logger.debug.call_args[0]
            assert (
                "drift" in debug_args[0].lower()
                or "resolution" in debug_args[0].lower()
            )

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
    async def test_raises_update_failed_when_api_returns_none(
        self, coordinator, mock_frank_energie
    ):
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
        MagicMock(),  # user_sites
        MagicMock(),  # data_month_summary
        MagicMock(),  # data_invoices
        MagicMock(),  # data_period_usage
        mock_user,  # data_user
        None,  # data_contract_price_resolution_state
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

    from homeassistant.helpers.update_coordinator import UpdateFailed

    # Perform the fetch - expect UpdateFailed to be raised
    with pytest.raises(UpdateFailed):
        await coordinator._fetch_today_data(
            datetime.now(timezone.utc).date(),
            datetime.now(timezone.utc).date() + timedelta(days=1),
        )

    assert call_count == 1
    coordinator._try_renew_token.assert_called_once()


@pytest.mark.asyncio
async def test_dynamic_fetch_network_errors_during_first_refresh(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Test that transient network errors in dynamic fetch methods propagate only during initial refresh."""
    from python_frank_energie.exceptions import NetworkError

    coordinator.last_fetch_today = None
    mock_frank_energie.is_authenticated = True

    # Under initial refresh, it should propagate NetworkError
    mock_frank_energie.enode_chargers.side_effect = NetworkError(
        "transient network issue"
    )
    with pytest.raises(NetworkError):
        await coordinator._fetch_enode_chargers(datetime.now(UTC).date(), True)

    # Under subsequent refresh (last_fetch_today is set), it should swallow NetworkError and return None
    coordinator.last_fetch_today = datetime.now(UTC)
    result = await coordinator._fetch_enode_chargers(datetime.now(UTC).date(), True)
    assert result is None


@pytest.mark.asyncio
async def test_dynamic_fetch_non_network_errors_ignored(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Test that non-network errors in dynamic fetch methods are swallowed even during initial refresh."""
    from python_frank_energie.exceptions import SmartTradingNotEnabledException

    coordinator.last_fetch_today = None
    mock_frank_energie.is_authenticated = True

    # SmartTradingNotEnabledException is a subclass of FrankEnergieException, not a network/connection error
    mock_frank_energie.smart_batteries.side_effect = SmartTradingNotEnabledException(
        "disabled"
    )
    result = await coordinator._fetch_smart_batteries(True)
    assert result is None


@pytest.mark.asyncio
async def test_fetch_contract_price_resolution_state_skips_when_unauthenticated_or_no_conn_id(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Test that _fetch_contract_price_resolution_state returns None and skips API fetch when unauthenticated or connection_id is missing."""
    mock_frank_energie.is_authenticated = False
    mock_frank_energie.contract_price_resolution_state = AsyncMock()

    # Case 1: Unauthenticated
    result = await coordinator._fetch_contract_price_resolution_state("conn-123")
    assert result is None
    mock_frank_energie.contract_price_resolution_state.assert_not_called()

    # Case 2: Authenticated but connection_id is None
    mock_frank_energie.is_authenticated = True
    result = await coordinator._fetch_contract_price_resolution_state(None)
    assert result is None
    mock_frank_energie.contract_price_resolution_state.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_prices_with_fallback_unauthenticated_tomorrow_not_published(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Unauthenticated tomorrow-fetch must not fall back to cached today prices.

    Regression test: when the public API hasn't published tomorrow's prices yet,
    the coordinator previously substituted `_cached_prices` (today's data) and
    returned it as if it were tomorrow's prices. `_refresh_tomorrow_cache` would
    then believe tomorrow was successfully fetched, cache a duplicate of today's
    data, and permanently skip retries for the rest of the day.
    """
    from datetime import date

    mock_frank_energie.is_authenticated = False

    today_prices = MagicMock()
    coordinator._cached_prices = today_prices

    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=None)
    coordinator._fetch_user_prices_for_range = AsyncMock(return_value=None)

    result = await coordinator._fetch_prices_with_fallback(
        date(2026, 7, 20), date(2026, 7, 21), use_fallback=False
    )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_prices_with_fallback_unauthenticated_today_uses_cache(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Today's fetch should still fall back to cached prices on transient failure."""
    from datetime import date

    mock_frank_energie.is_authenticated = False

    today_prices = MagicMock()
    coordinator._cached_prices = today_prices

    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=None)
    coordinator._fetch_user_prices_for_range = AsyncMock(return_value=None)

    result = await coordinator._fetch_prices_with_fallback(
        date(2026, 7, 19), date(2026, 7, 20), use_fallback=True
    )

    assert result is today_prices


@pytest.mark.asyncio
async def test_dynamic_fetches_skip_when_feature_disabled(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Test that dynamic fetch methods skip calls when respective smart features are disabled."""
    mock_frank_energie.is_authenticated = True
    mock_frank_energie.enode_chargers = AsyncMock()
    mock_frank_energie.smart_batteries = AsyncMock()
    mock_frank_energie.enode_vehicles = AsyncMock()

    # Chargers fetch skipped when smart charging is disabled
    result_chargers = await coordinator._fetch_enode_chargers(
        datetime.now(UTC).date(), False
    )
    assert result_chargers is None
    mock_frank_energie.enode_chargers.assert_not_called()

    # Batteries fetch skipped when smart trading is disabled
    result_batteries = await coordinator._fetch_smart_batteries(False)
    assert result_batteries is None
    mock_frank_energie.smart_batteries.assert_not_called()

    # Vehicles fetch skipped when smart charging is disabled
    result_vehicles = await coordinator._fetch_enode_vehicles(False)
    assert result_vehicles is None
    mock_frank_energie.enode_vehicles.assert_not_called()


@pytest.mark.asyncio
async def test_coordinator_retry_incomplete_usage_data(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Test that coordinator retries fetching usage data if it is None or incomplete."""
    from python_frank_energie.models import EnergyCategory, PeriodUsageAndCosts

    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)

    # 1. Test helper _has_valid_usage_data
    assert coordinator._has_valid_usage_data(None) is False

    # Incomplete usage: electricity category is present but has usage_total/costs_total as None
    incomplete_electricity = EnergyCategory(
        usage_total=None,
        costs_total=None,
        unit="KWH",
        items=[],
    )
    incomplete_usage = PeriodUsageAndCosts(
        _id="123",
        gas=None,
        electricity=incomplete_electricity,
        feed_in=None,
    )
    assert coordinator._has_valid_usage_data(incomplete_usage) is False

    # Complete usage
    complete_electricity = EnergyCategory(
        usage_total=10.5,
        costs_total=2.5,
        unit="KWH",
        items=[],
    )
    complete_usage = PeriodUsageAndCosts(
        _id="123",
        gas=None,
        electricity=complete_electricity,
        feed_in=None,
    )
    assert coordinator._has_valid_usage_data(complete_usage) is True

    # 2. Test coordinator caching & retry logic
    # Mock all other static fetches
    coordinator._fetch_prices_with_fallback = AsyncMock()
    coordinator._fetch_user_sites = AsyncMock()
    coordinator._fetch_month_summary = AsyncMock()
    coordinator._fetch_invoices = AsyncMock()
    coordinator._fetch_user_data = AsyncMock()
    coordinator._fetch_contract_price_resolution_state = AsyncMock()

    # Setup first fetch with incomplete usage
    coordinator._fetch_period_usage = AsyncMock(return_value=incomplete_usage)

    # Trigger first fetch (which populates cache and sets last_fetch_today)
    await coordinator._get_static_data(today, tomorrow, yesterday)
    coordinator.last_fetch_today = datetime.now(timezone.utc)

    # Verify first fetch called _fetch_period_usage
    coordinator._fetch_period_usage.assert_called_once_with(yesterday)
    assert coordinator._static_period_usage == incomplete_usage

    # Reset mock call count
    coordinator._fetch_period_usage.reset_mock()

    # Subsequent fetch with incomplete cache should retry fetching
    coordinator._fetch_period_usage.return_value = complete_usage
    await coordinator._get_static_data(today, tomorrow, yesterday)

    # Verify that it retried fetching usage data, and successfully updated the cache
    coordinator._fetch_period_usage.assert_called_once_with(yesterday)
    assert coordinator._static_period_usage == complete_usage

    # Reset mock call count again
    coordinator._fetch_period_usage.reset_mock()

    # Subsequent fetch with complete cache should NOT retry fetching
    await coordinator._get_static_data(today, tomorrow, yesterday)
    coordinator._fetch_period_usage.assert_not_called()


@pytest.mark.asyncio
async def test_promote_tomorrow_prices_updates_all_caches(coordinator) -> None:
    """Test that promote_tomorrow_prices promotes tomorrow's prices to all relevant today caching fields."""
    tomorrow_prices = MagicMock()
    tomorrow_prices.electricity = MagicMock()
    tomorrow_prices.gas = MagicMock()
    coordinator.cached_prices_tomorrow = tomorrow_prices

    # Mock current cached_prices / data
    coordinator.cached_prices = {
        DATA_ELECTRICITY: MagicMock(),
        DATA_GAS: MagicMock(),
    }

    # Mock cached_prices_today
    coordinator.cached_prices_today = PricesTodayCache(
        prices_today=MagicMock(),
        data_month_summary=None,
        data_invoices=None,
        data_user=None,
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

    coordinator.promote_tomorrow_prices()

    # Assertions
    assert coordinator.cached_prices_tomorrow is None
    # The new behavior preserves the combined electricity/gas from cached_prices
    assert (
        coordinator._static_prices_today.electricity
        is coordinator.cached_prices[DATA_ELECTRICITY]
    )
    assert coordinator._static_prices_today.gas is coordinator.cached_prices[DATA_GAS]
    assert (
        coordinator._static_prices_today.energy_country
        is tomorrow_prices.energy_country
    )
    assert coordinator._static_prices_today.energy_type is tomorrow_prices.energy_type


@pytest.mark.asyncio
async def test_get_static_data_fallback_to_promoted_prices_when_api_returns_empty(
    coordinator,
) -> None:
    """Test that _get_static_data falls back to _static_prices_today if the API returns no prices but cached prices are valid for today."""
    from datetime import date

    today = date(2026, 6, 20)
    tomorrow = date(2026, 6, 21)
    start_date = date(2026, 6, 19)

    # Mock cached prices for today (electricity valid, gas empty)
    valid_price = MagicMock()
    valid_price.date_from.date.return_value = today

    cached_prices = MagicMock()
    cached_prices.electricity.all = [valid_price]
    cached_prices.gas.all = []
    coordinator._static_prices_today = cached_prices

    # Mock fetches returning empty prices (no electricity/gas points)
    empty_prices = MagicMock()
    empty_prices.electricity.all = []
    empty_prices.gas.all = []
    coordinator._fetch_prices_with_fallback = AsyncMock(return_value=empty_prices)
    coordinator._fetch_user_sites = AsyncMock(return_value=None)
    coordinator._fetch_month_summary = AsyncMock(return_value=None)
    coordinator._fetch_invoices = AsyncMock(return_value=None)
    coordinator._fetch_period_usage = AsyncMock(return_value=None)
    coordinator._fetch_user_data = AsyncMock(return_value=None)
    coordinator._fetch_contract_price_resolution_state = AsyncMock(return_value=None)

    # Force refetch by setting last_fetch_today date to yesterday
    coordinator.last_fetch_today = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)

    # Perform get static data
    prices_today, *rest = await coordinator._get_static_data(
        today, tomorrow, start_date
    )

    # Verify fallback happened
    assert prices_today is cached_prices


@pytest.mark.asyncio
async def test_get_static_data_no_fallback_when_cached_prices_belong_to_other_day(
    coordinator,
) -> None:
    """Test that _get_static_data does NOT fall back if the cached prices are for a different day."""
    from datetime import date

    today = date(2026, 6, 20)
    tomorrow = date(2026, 6, 21)
    start_date = date(2026, 6, 19)

    # Mock cached prices for yesterday (not today)
    invalid_price = MagicMock()
    invalid_price.date_from.date.return_value = date(2026, 6, 19)

    cached_prices = MagicMock()
    cached_prices.electricity.all = [invalid_price]
    cached_prices.gas.all = []
    coordinator._static_prices_today = cached_prices

    # Mock fetches returning empty prices
    empty_prices = MagicMock()
    empty_prices.electricity.all = []
    empty_prices.gas.all = []
    coordinator._fetch_prices_with_fallback = AsyncMock(return_value=empty_prices)
    coordinator._fetch_user_sites = AsyncMock(return_value=None)
    coordinator._fetch_month_summary = AsyncMock(return_value=None)
    coordinator._fetch_invoices = AsyncMock(return_value=None)
    coordinator._fetch_period_usage = AsyncMock(return_value=None)
    coordinator._fetch_user_data = AsyncMock(return_value=None)
    coordinator._fetch_contract_price_resolution_state = AsyncMock(return_value=None)

    coordinator.last_fetch_today = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)

    # Perform get static data
    prices_today, *rest = await coordinator._get_static_data(
        today, tomorrow, start_date
    )

    # Verify fallback did NOT happen (we get the empty/fetched prices instead of the stale cached ones)
    assert prices_today is empty_prices


@pytest.mark.asyncio
async def test_get_static_data_fallback_when_both_electricity_and_gas_are_valid(
    coordinator,
) -> None:
    """Test that _get_static_data falls back to cached prices when both electricity and gas are valid for today."""
    from datetime import date

    today = date(2026, 6, 20)
    tomorrow = date(2026, 6, 21)
    start_date = date(2026, 6, 19)

    # Mock cached prices where both are present and valid
    valid_elec = MagicMock()
    valid_elec.date_from.date.return_value = today
    valid_gas = MagicMock()
    valid_gas.date_from.date.return_value = today

    cached_prices = MagicMock()
    cached_prices.electricity.all = [valid_elec]
    cached_prices.gas.all = [valid_gas]
    coordinator._static_prices_today = cached_prices

    # Mock fetches returning empty prices
    empty_prices = MagicMock()
    empty_prices.electricity.all = []
    empty_prices.gas.all = []
    coordinator._fetch_prices_with_fallback = AsyncMock(return_value=empty_prices)
    coordinator._fetch_user_sites = AsyncMock(return_value=None)
    coordinator._fetch_month_summary = AsyncMock(return_value=None)
    coordinator._fetch_invoices = AsyncMock(return_value=None)
    coordinator._fetch_period_usage = AsyncMock(return_value=None)
    coordinator._fetch_user_data = AsyncMock(return_value=None)
    coordinator._fetch_contract_price_resolution_state = AsyncMock(return_value=None)

    coordinator.last_fetch_today = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)

    # Perform get static data
    prices_today, *rest = await coordinator._get_static_data(
        today, tomorrow, start_date
    )

    # Verify fallback happened
    assert prices_today is cached_prices


@pytest.mark.asyncio
async def test_price_coordinator_before_window(
    mock_frank_energie, mock_config_entry, monkeypatch
) -> None:
    """Test that price coordinator update interval is None before local 13:00 when tomorrow's prices are not cached."""
    # Mock utcnow to 10:00 UTC (12:00 local time CEST on May 27th)
    mock_now = datetime(2026, 5, 27, 10, 0, 0, tzinfo=ZoneInfo("UTC"))
    from homeassistant.util import dt as dt_util

    monkeypatch.setattr(dt_util, "utcnow", lambda: mock_now)

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    price_coordinator.cached_prices_tomorrow = None
    price_coordinator._adjust_update_interval(mock_now)

    assert price_coordinator.update_interval is None


@pytest.mark.asyncio
async def test_price_coordinator_inside_window(
    mock_frank_energie, mock_config_entry, monkeypatch
) -> None:
    """Test that price coordinator update interval is 5 minutes inside local 13:00 to 15:00 when tomorrow's prices are not cached."""
    # Mock utcnow to 12:00 UTC (14:00 local time CEST on May 27th)
    mock_now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
    from homeassistant.util import dt as dt_util

    monkeypatch.setattr(dt_util, "utcnow", lambda: mock_now)

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    price_coordinator.cached_prices_tomorrow = None
    price_coordinator._adjust_update_interval(mock_now)

    assert price_coordinator.update_interval == timedelta(minutes=5)


@pytest.mark.asyncio
async def test_price_coordinator_skipped_when_cached(
    mock_frank_energie, mock_config_entry, monkeypatch
) -> None:
    """Test that price coordinator update interval is None if tomorrow's prices are already cached."""
    # Mock utcnow to 12:00 UTC (14:00 local time CEST on May 27th)
    mock_now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
    from homeassistant.util import dt as dt_util

    monkeypatch.setattr(dt_util, "utcnow", lambda: mock_now)

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    # Mock cached tomorrow prices as available and fetched today
    tomorrow_prices = MagicMock()
    price_coordinator.cached_prices_tomorrow = tomorrow_prices
    price_coordinator.last_fetch_tomorrow = mock_now

    price_coordinator._adjust_update_interval(mock_now)

    assert price_coordinator.update_interval is None


@pytest.mark.asyncio
async def test_price_coordinator_midnight_rollover(
    mock_frank_energie, mock_config_entry
) -> None:
    """Test that promote_tomorrow_prices correctly promotes tomorrow's prices to today."""
    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    tomorrow_prices = MagicMock()
    tomorrow_prices.electricity = MagicMock()
    tomorrow_prices.gas = MagicMock()
    price_coordinator.cached_prices_tomorrow = tomorrow_prices

    price_coordinator.cached_prices = {
        DATA_ELECTRICITY: MagicMock(),
        DATA_GAS: MagicMock(),
    }

    original_electricity = price_coordinator.cached_prices[DATA_ELECTRICITY]
    original_gas = price_coordinator.cached_prices[DATA_GAS]

    price_coordinator.promote_tomorrow_prices()

    assert price_coordinator.cached_prices_tomorrow is None

    expected_electricity = original_electricity + tomorrow_prices.electricity
    expected_gas = original_gas + tomorrow_prices.gas

    assert price_coordinator.cached_prices[DATA_ELECTRICITY] == expected_electricity
    assert price_coordinator.cached_prices[DATA_GAS] == expected_gas
    assert price_coordinator._static_prices_today.electricity == expected_electricity
    assert price_coordinator._static_prices_today.gas == expected_gas


@pytest.mark.asyncio
async def test_price_coordinator_midnight_rollover_resolution_mismatch(
    mock_frank_energie, mock_config_entry
) -> None:
    """On a resolution-change day, promotion must prefer tomorrow's (new) resolution.

    Regression test: when `_merge_prices` raises ValueError because today's and
    tomorrow's cached price series have mismatched resolutions (e.g. a contract
    resolution change taking effect at midnight), the fallback previously kept
    yesterday's stale-resolution data instead of the freshly-fetched,
    correct-resolution tomorrow data.
    """
    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    tomorrow_prices = MagicMock()
    tomorrow_prices.electricity = MagicMock(name="tomorrow_electricity")
    tomorrow_prices.gas = MagicMock(name="tomorrow_gas")
    price_coordinator.cached_prices_tomorrow = tomorrow_prices

    price_coordinator.cached_prices = {
        DATA_ELECTRICITY: MagicMock(name="yesterday_electricity"),
        DATA_GAS: MagicMock(name="yesterday_gas"),
    }

    price_coordinator._merge_prices = MagicMock(
        side_effect=ValueError("resolution mismatch")
    )

    price_coordinator.promote_tomorrow_prices()

    assert (
        price_coordinator._static_prices_today.electricity
        is tomorrow_prices.electricity
    )
    assert price_coordinator._static_prices_today.gas is tomorrow_prices.gas


@pytest.mark.asyncio
async def test_sub_coordinators_properties(
    mock_frank_energie, mock_config_entry
) -> None:
    """Test default properties and intervals of sub-coordinators."""
    hass = MagicMock()
    settings = FrankEnergieSettingsCoordinator(
        hass, mock_config_entry, mock_frank_energie
    )
    price = FrankEnergiePriceCoordinator(
        hass, mock_config_entry, mock_frank_energie, settings
    )
    battery = FrankEnergieBatteryCoordinator(
        hass, mock_config_entry, mock_frank_energie, settings
    )
    charger = FrankEnergieChargerCoordinator(
        hass, mock_config_entry, mock_frank_energie, settings
    )
    pv = FrankEnergiePVCoordinator(
        hass, mock_config_entry, mock_frank_energie, settings
    )
    vehicle = FrankEnergieVehicleCoordinator(
        hass, mock_config_entry, mock_frank_energie, settings
    )
    stats = FrankEnergieStatisticsCoordinator(
        hass, mock_config_entry, mock_frank_energie, settings
    )

    assert settings.update_interval == timedelta(hours=24)
    assert price.update_interval is None
    assert battery.update_interval == timedelta(minutes=5)
    assert charger.update_interval == timedelta(minutes=5)
    assert pv.update_interval == timedelta(minutes=5)
    assert vehicle.update_interval == timedelta(minutes=15)
    assert stats.update_interval == timedelta(hours=1)


@pytest.mark.asyncio
async def test_async_update_enode_charge_settings_optimistic_cache(
    coordinator, mock_frank_energie, create_mock_vehicle
) -> None:
    """Test optimistic cache updates for all mutation fields in async_update_enode_charge_settings."""
    from custom_components.frank_energie.const import DATA_ENODE_VEHICLES
    from datetime import datetime
    from unittest.mock import MagicMock

    vehicle_id = "veh_123"
    mock_vehicle = create_mock_vehicle(
        vehicle_id=vehicle_id,
        charge_settings_kwargs={
            "is_smart_charging_enabled": False,
            "min_charge_limit": 20,
            "max_charge_limit": 80,
        },
    )
    mock_vehicles = MagicMock()
    mock_vehicles.vehicles = [mock_vehicle]
    coordinator.data = {DATA_ENODE_VEHICLES: mock_vehicles}

    # Successful mutation
    mock_frank_energie.enode_update_vehicle_charge_settings.return_value = True

    dt_str = "2026-05-29T08:30:00+00:00"
    mutations = {
        "deadline": dt_str,
        "isSmartChargingEnabled": True,
        "isSolarChargingEnabled": True,
        "minChargeLimit": 30,
        "maxChargeLimit": 90,
        "initialCharge": 15.0,
        "hourMonday": 480,
        "hourTuesday": 490,
        "hourWednesday": 500,
        "hourThursday": 510,
        "hourFriday": 520,
        "hourSaturday": 530,
        "hourSunday": 540,
    }

    success = await coordinator.async_update_enode_charge_settings(
        vehicle_id, is_vehicle=True, mutations=mutations
    )

    assert success is True
    assert mock_vehicle.charge_settings.deadline == datetime.fromisoformat(dt_str)
    assert mock_vehicle.charge_settings.is_smart_charging_enabled is True
    assert mock_vehicle.charge_settings.is_solar_charging_enabled is True
    assert mock_vehicle.charge_settings.min_charge_limit == 30
    assert mock_vehicle.charge_settings.max_charge_limit == 90
    assert mock_vehicle.charge_settings.initial_charge == pytest.approx(15.0)
    assert mock_vehicle.charge_settings.hour_monday == 480
    assert mock_vehicle.charge_settings.hour_tuesday == 490
    assert mock_vehicle.charge_settings.hour_wednesday == 500
    assert mock_vehicle.charge_settings.hour_thursday == 510
    assert mock_vehicle.charge_settings.hour_friday == 520
    assert mock_vehicle.charge_settings.hour_saturday == 530
    assert mock_vehicle.charge_settings.hour_sunday == 540


@pytest.mark.asyncio
async def test_coordinator_helpers() -> None:
    """Test coordinator static helpers."""
    # Test _merge_prices
    base = MagicMock()
    base.__add__ = MagicMock(return_value="merged")
    new_prices = MagicMock()

    assert FrankEnergiePriceCoordinator._merge_prices(None, None) is None
    assert FrankEnergiePriceCoordinator._merge_prices(base, None) is base
    assert FrankEnergiePriceCoordinator._merge_prices(None, new_prices) is new_prices
    assert FrankEnergiePriceCoordinator._merge_prices(base, new_prices) == "merged"
    base.__add__.assert_called_once_with(new_prices)

    # Test _build_tomorrow_cache
    tomorrow = MagicMock()
    tomorrow.energy_country = "NL"
    tomorrow.energy_type = "electricity"
    tomorrow.electricity = MagicMock()
    tomorrow.gas = MagicMock()

    elec_rem = MagicMock()
    gas_rem = MagicMock()

    res = FrankEnergiePriceCoordinator._build_tomorrow_cache(
        tomorrow, elec_rem, gas_rem
    )
    assert res.electricity is elec_rem
    assert res.gas is gas_rem
    assert res.energy_country == "NL"
    assert res.energy_type == "electricity"

    # Test when remaining is None but tomorrow had prices (should replace with empty price_data)
    with patch("custom_components.frank_energie.coordinator.replace") as mock_replace:
        mock_replace.return_value = "empty"
        res2 = FrankEnergiePriceCoordinator._build_tomorrow_cache(tomorrow, None, None)
        assert res2 is None

        res3 = FrankEnergiePriceCoordinator._build_tomorrow_cache(
            tomorrow, elec_rem, None
        )
        assert res3.electricity is elec_rem
        assert res3.gas == "empty"


@pytest.mark.asyncio
async def test_price_data_after_and_build_tomorrow_cache_with_real_pricedata() -> None:
    """_price_data_after/_build_tomorrow_cache must not crash on a real multi-day window.

    Regression test: both methods called `dataclasses.replace(price_data, price_data=...)`,
    but `price_data` is an instance attribute set in `PriceData.__post_init__`, not a
    declared dataclass field. `replace()` always raised TypeError for any real PriceData
    object, so a genuine multi-day API response would silently crash midnight promotion.
    The previous test only exercised this with MagicMocks, which masked the bug since
    mocked `replace()` never touches the real dataclass machinery.
    """
    from datetime import date, timezone
    from python_frank_energie.models import PriceData

    raw_today = {
        "from": "2026-07-19T22:00:00.000Z",
        "till": "2026-07-19T22:15:00.000Z",
        "marketPrice": 0.1,
        "marketPriceTax": 0.02,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.1,
    }
    raw_day_after = {
        "from": "2026-07-20T22:00:00.000Z",
        "till": "2026-07-20T22:15:00.000Z",
        "marketPrice": 0.2,
        "marketPriceTax": 0.02,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.1,
    }
    multi_day_electricity = PriceData(
        [raw_today, raw_day_after], energy_type="electricity"
    )
    empty_gas = PriceData([], energy_type="gas")

    new_today = date(2026, 7, 20)

    remaining_electricity = FrankEnergiePriceCoordinator._price_data_after(
        multi_day_electricity, new_today
    )
    remaining_gas = FrankEnergiePriceCoordinator._price_data_after(empty_gas, new_today)

    assert remaining_electricity is not None
    assert len(remaining_electricity.all) == 1
    assert remaining_electricity.all[0].date_from == datetime(
        2026, 7, 20, 22, 0, tzinfo=timezone.utc
    )
    # Metadata (e.g. resolution) must survive the rebuild.
    assert remaining_electricity.resolution_minutes == 15
    assert remaining_gas is None

    tomorrow_prices = MagicMock()
    tomorrow_prices.electricity = multi_day_electricity
    tomorrow_prices.gas = empty_gas
    tomorrow_prices.energy_country = "NL"
    tomorrow_prices.energy_type = "electricity"

    built = FrankEnergiePriceCoordinator._build_tomorrow_cache(
        tomorrow_prices, remaining_electricity, remaining_gas
    )

    assert built is not None
    assert built.electricity is remaining_electricity
    assert built.gas is not None
    assert built.gas.all == []


def _make_market_prices(entry_date_iso: str):
    """Build a minimal real MarketPrices with one electricity entry at the given ISO start."""
    from datetime import timedelta as _timedelta

    from python_frank_energie.models import MarketPrices, PriceData

    entry_start = datetime.fromisoformat(entry_date_iso.replace("Z", "+00:00"))
    raw_price = {
        "from": entry_date_iso,
        "till": (entry_start + _timedelta(minutes=15)).isoformat(),
        "marketPrice": 0.1,
        "marketPriceTax": 0.02,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.1,
    }
    electricity = PriceData([raw_price], energy_type="electricity")
    gas = PriceData([], energy_type="gas")
    return MarketPrices(electricity=electricity, gas=gas, energy_country="NL")


@pytest.mark.asyncio
async def test_refresh_tomorrow_cache_detects_poisoned_same_day_cache(
    coordinator: FrankEnergieCoordinator,
) -> None:
    """A same-day cache whose entries aren't actually dated for tomorrow must be discarded.

    Regression test for a real post-release bug report: a cache poisoned by
    the (now-fixed) unauthenticated tomorrow-fetch bug has last_fetch_tomorrow
    dated today, so the short-circuit treated it as a legitimate same-day
    fetch forever — surviving reloads, restarts, and the manual refresh
    button, since nothing ever re-validated the cached data against the date
    it claims to represent.
    """
    from datetime import date

    today = date(2026, 7, 20)
    tomorrow = date(2026, 7, 21)
    now_utc = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    # Poisoned: claims to be fetched "today", but its entries are dated today,
    # not tomorrow (exactly what the old bug produced).
    coordinator.cached_prices_tomorrow = _make_market_prices("2026-07-20T10:00:00.000Z")
    coordinator.last_fetch_tomorrow = now_utc

    fresh_tomorrow_prices = _make_market_prices("2026-07-20T22:00:00.000Z")
    coordinator._fetch_tomorrow_data = AsyncMock(return_value=fresh_tomorrow_prices)

    result = await coordinator._refresh_tomorrow_cache(today, tomorrow, now_utc)

    coordinator._fetch_tomorrow_data.assert_called_once()
    assert result is fresh_tomorrow_prices
    assert coordinator.cached_prices_tomorrow is fresh_tomorrow_prices


@pytest.mark.asyncio
async def test_refresh_tomorrow_cache_returns_valid_same_day_cache(
    coordinator: FrankEnergieCoordinator,
) -> None:
    """A genuinely valid same-day cache must still short-circuit without an API call."""
    from datetime import date

    today = date(2026, 7, 20)
    tomorrow = date(2026, 7, 21)
    now_utc = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    valid_tomorrow_prices = _make_market_prices("2026-07-20T22:00:00.000Z")
    coordinator.cached_prices_tomorrow = valid_tomorrow_prices
    coordinator.last_fetch_tomorrow = now_utc

    coordinator._fetch_tomorrow_data = AsyncMock()

    result = await coordinator._refresh_tomorrow_cache(today, tomorrow, now_utc)

    coordinator._fetch_tomorrow_data.assert_not_called()
    assert result is valid_tomorrow_prices
