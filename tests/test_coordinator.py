import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from custom_components.frank_energie.const import (
    DATA_ELECTRICITY,
    DATA_GAS,
    DATA_INVOICES,
    DATA_MONTH_SUMMARY,
    DATA_USER,
    TIMEZONE_AMSTERDAM,
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
from python_frank_energie.models import (
    MonthSummary,
    Invoices,
    User,
    PriceData,
    MarketPrices,
)
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
async def test_aggregate_data_handles_contract_resolution_change(coordinator):
    """A contract resolution change (e.g. PT15M -> PT60M) must not crash the update.

    Regression test: cached "today" prices at one resolution combined with
    freshly-fetched "tomorrow" prices at a different resolution used to raise
    ValueError out of PriceData.__add__ and crash the whole coordinator
    refresh (_aggregate_data -> _combine_price_data). Today's prices carry a
    real 15-minute contract resolution here; tomorrow's switch to 60 minutes.
    """
    raw_today = {
        "from": "2026-07-31T22:00:00.000Z",
        "till": "2026-07-31T22:15:00.000Z",
        "marketPrice": 0.1,
        "marketPriceTax": 0.02,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.1,
    }
    raw_tomorrow = {
        "from": "2026-08-01T22:00:00.000Z",
        "till": "2026-08-01T23:00:00.000Z",
        "marketPrice": 0.2,
        "marketPriceTax": 0.02,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.1,
    }
    today_electricity = PriceData([raw_today], energy_type="electricity")
    tomorrow_electricity = PriceData([raw_tomorrow], energy_type="electricity")
    assert today_electricity.resolution_minutes == 15
    assert tomorrow_electricity.resolution_minutes == 60

    prices_today = MagicMock()
    prices_today.electricity = today_electricity
    prices_today.gas = None
    prices_tomorrow = MagicMock()
    prices_tomorrow.electricity = tomorrow_electricity
    prices_tomorrow.gas = None
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

    aggregated_data = coordinator._aggregate_data(cache, prices_tomorrow)

    # Today's real entry must survive the merge (regression: an earlier fix
    # discarded it in favor of tomorrow's, making "today" sensors go
    # unavailable), and tomorrow's entry must still be present too.
    merged_electricity = aggregated_data[DATA_ELECTRICITY]
    assert [p.date_from for p in merged_electricity.price_data] == [
        today_electricity.price_data[0].date_from,
        tomorrow_electricity.price_data[0].date_from,
    ]


def test_resolve_price_data_merge_conflict_recomputes_resolution_minutes():
    """The merged series' resolution_minutes must reflect its actual entries.

    Regression test: _resolve_price_data_merge_conflict builds `merged` via
    copy.copy(today_data), a shallow copy that keeps today_data's stale
    resolution_minutes even after price_data is replaced. Uses PT60M ->
    PT15M (the reverse direction of the other regression test) so a
    leftover stale value (60) is distinguishable from the correctly
    recomputed one (15, from tomorrow's finer-grained entries).
    """
    raw_today = {
        "from": "2026-07-31T20:00:00.000Z",
        "till": "2026-07-31T21:00:00.000Z",
        "marketPrice": 0.1,
        "marketPriceTax": 0.02,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.1,
    }
    raw_tomorrow = {
        "from": "2026-08-01T20:00:00.000Z",
        "till": "2026-08-01T20:15:00.000Z",
        "marketPrice": 0.2,
        "marketPriceTax": 0.02,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.1,
    }
    today_electricity = PriceData([raw_today], energy_type="electricity")
    tomorrow_electricity = PriceData([raw_tomorrow], energy_type="electricity")
    assert today_electricity.resolution_minutes == 60
    assert tomorrow_electricity.resolution_minutes == 15

    err = ValueError("Cannot merge PriceData with different resolutions: 60 vs 15")
    merged = FrankEnergiePriceCoordinator._resolve_price_data_merge_conflict(
        today_electricity, tomorrow_electricity, err
    )

    assert merged.resolution_minutes == 15


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
async def test_fetch_prices_with_fallback_authenticated_tomorrow_not_published(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Authenticated tomorrow-fetch must not fall back to cached today prices either.

    The unauthenticated guard (see the test above) explicitly returns before
    ever substituting _cached_prices for a missing public-prices response.
    For authenticated users that guard doesn't apply — is_authenticated=True
    means public_prices *does* get substituted with _cached_prices — but that
    substituted value is never actually returned for a tomorrow-fetch: every
    reachable return path in this branch is driven by user_prices (None here,
    so returns None), not public_prices. This proves that by code path, not
    just inspection.
    """
    from datetime import date

    mock_frank_energie.is_authenticated = True

    today_prices = MagicMock()
    coordinator._cached_prices = today_prices

    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=None)
    coordinator._fetch_user_prices_for_range = AsyncMock(return_value=None)

    result = await coordinator._fetch_prices_with_fallback(
        date(2026, 7, 20), date(2026, 7, 21), use_fallback=False
    )

    assert result is None


@pytest.mark.asyncio
async def test_fetch_prices_with_fallback_authenticated_tomorrow_incomplete_user_prices_not_merged_with_public(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Incomplete authenticated tomorrow prices must be returned as-is, not
    merged with (potentially poisoned) public prices — same "no fallback for
    tomorrow" rule that applies when user_prices is missing entirely.
    """
    from datetime import date

    mock_frank_energie.is_authenticated = True

    public_prices = MagicMock()
    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=public_prices)

    # Electricity present, gas missing — incomplete, but real customer data.
    incomplete_user_prices = MagicMock()
    incomplete_user_prices.electricity.all = [MagicMock()]
    incomplete_user_prices.gas = None
    coordinator._fetch_user_prices_for_range = AsyncMock(
        return_value=incomplete_user_prices
    )

    result = await coordinator._fetch_prices_with_fallback(
        date(2026, 7, 20), date(2026, 7, 21), use_fallback=False
    )

    assert result is incomplete_user_prices
    # Must not have been merged with the concurrently-fetched public prices.
    assert result.gas is None


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
@pytest.mark.parametrize("country_code", ["BE", "FR"])
async def test_fetch_public_prices_for_range_uses_country_prices(
    coordinator: FrankEnergieCoordinator,
    mock_frank_energie: AsyncMock,
    country_code: str,
) -> None:
    """BE and FR public prices must go through the country-scoped 'x-country' query."""
    from datetime import date

    start_date = date(2026, 7, 22)
    end_date = date(2026, 7, 23)
    expected = MagicMock()
    mock_frank_energie.country_prices = AsyncMock(return_value=expected)

    result = await coordinator._fetch_public_prices_for_range(
        start_date, end_date, country_code
    )

    mock_frank_energie.country_prices.assert_awaited_once_with(
        country_code, start_date, end_date, coordinator.resolution
    )
    mock_frank_energie.prices.assert_not_awaited()
    assert result is expected


@pytest.mark.asyncio
async def test_fetch_public_prices_for_range_nl_uses_default_prices_query(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """NL (and other non-simplified markets) must keep using the default `prices()` query."""
    from datetime import date

    start_date = date(2026, 7, 22)
    end_date = date(2026, 7, 23)
    expected = MagicMock()
    mock_frank_energie.prices = AsyncMock(return_value=expected)

    result = await coordinator._fetch_public_prices_for_range(
        start_date, end_date, "NL"
    )

    mock_frank_energie.prices.assert_awaited_once_with(
        start_date, resolution=coordinator.resolution
    )
    mock_frank_energie.country_prices.assert_not_awaited()
    assert result is expected


@pytest.mark.asyncio
async def test_fetch_user_data_accepts_fr_country_code(
    coordinator: FrankEnergieCoordinator, mock_frank_energie: AsyncMock
) -> None:
    """Account countryCode 'FR' must be accepted, not silently dropped."""
    mock_frank_energie.is_authenticated = True
    coordinator._country_code = None

    user_data = MagicMock(spec=User)
    user_data.countryCode = "FR"
    user_data.connections = []
    mock_frank_energie.user = AsyncMock(return_value=user_data)
    mock_frank_energie.smart_hvac_status = AsyncMock(return_value=None)

    result = await coordinator._fetch_user_data()

    assert result is user_data
    assert coordinator._country_code == "FR"


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

    # Mock cached tomorrow prices as available, genuinely dated tomorrow, and
    # fetched today.
    tomorrow_prices = _make_market_prices("2026-05-27T22:00:00.000Z")
    price_coordinator.cached_prices_tomorrow = tomorrow_prices
    price_coordinator.last_fetch_tomorrow = mock_now

    price_coordinator._adjust_update_interval(mock_now)

    assert price_coordinator.update_interval is None


@pytest.mark.asyncio
async def test_price_coordinator_not_skipped_when_cache_poisoned(
    mock_frank_energie, mock_config_entry, monkeypatch
) -> None:
    """A same-day cache that isn't actually dated tomorrow must not silence polling.

    Regression test: trusting last_fetch_tomorrow's date alone let a poisoned
    cache set update_interval to None, which meant nothing would ever
    automatically re-trigger _async_update_data (and therefore the
    _refresh_tomorrow_cache self-heal check) again for the rest of the day.
    """
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

    # Poisoned: claims to be fetched today, but entries are dated today too.
    poisoned_prices = _make_market_prices("2026-05-27T10:00:00.000Z")
    price_coordinator.cached_prices_tomorrow = poisoned_prices
    price_coordinator.last_fetch_tomorrow = mock_now

    price_coordinator._adjust_update_interval(mock_now)

    assert price_coordinator.update_interval == timedelta(minutes=5)


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
async def test_promote_tomorrow_prices_rejects_stale_multi_day_old_cache(
    mock_frank_energie, mock_config_entry, freezer
) -> None:
    """Promotion must not merge a tomorrow-cache that isn't dated for the new today.

    Regression test: unlike _refresh_tomorrow_cache and _adjust_update_interval,
    promote_tomorrow_prices trusted cached_prices_tomorrow unconditionally. In
    normal operation _refresh_tomorrow_cache always clears anything not
    fetched today before every 13:00+ run, so this couldn't happen — but if a
    whole day is ever skipped (HA down across it) days-old prices would
    otherwise get merged straight into "today" with no validation at all.
    """
    tz = ZoneInfo(TIMEZONE_AMSTERDAM)

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    existing_today = _make_market_prices("2026-07-19T10:00:00.000Z")
    coordinator._static_prices_today = existing_today
    coordinator.cached_prices = {
        DATA_ELECTRICITY: existing_today.electricity,
        DATA_GAS: existing_today.gas,
    }

    # Three days stale: last genuinely fetched for 2026-07-20, but midnight
    # rollover is now happening for 2026-07-23 (e.g. HA was down in between).
    stale_prices = _make_market_prices("2026-07-19T22:00:00.000Z")
    coordinator.cached_prices_tomorrow = stale_prices
    coordinator.last_fetch_tomorrow = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    freezer.move_to(datetime(2026, 7, 23, 0, 0, tzinfo=tz))
    coordinator.promote_tomorrow_prices()

    assert coordinator.cached_prices_tomorrow is None
    assert coordinator.last_fetch_tomorrow is None
    # Today's prices must be untouched, not poisoned with the stale merge.
    assert coordinator._static_prices_today is existing_today


@pytest.mark.asyncio
async def test_full_day_cycle_fetch_promote_refetch(
    mock_frank_energie, mock_config_entry, freezer
) -> None:
    """Simulate three real days end-to-end through the actual production entry point.

    FrankEnergiePriceCoordinator fully overrides _async_update_data (it does
    NOT use the base class's _refresh_today_cache/_fetch_today_data at all —
    those are dead code in production, since every real coordinator subclass
    replaces _async_update_data). So driving _refresh_tomorrow_cache directly,
    as the previous version of this test did, exercised real logic but not
    the real entry point or the real "today" fetch path
    (_fetch_prices_with_fallback + _carry_forward_previous_day).

    This drives the actual await coordinator._async_update_data() HA calls,
    for Day 1 -> Day 2 -> Day 3 -> (into Day 4), with a midnight
    promote_tomorrow_prices() between each pair — matching production, where
    that call comes from __init__.py's independent midnight scheduler, not
    from _async_update_data itself. Each day also does a second call 5
    minutes later, mirroring the real 5-minute in-window polling, to confirm
    the second call is a genuine no-op (matches the observed real-world log:
    fetch completes one cycle, "update interval changed to None" appears
    only on the next).
    """
    from datetime import date

    tz = ZoneInfo(TIMEZONE_AMSTERDAM)
    day1, day2, day3, day4 = (
        date(2026, 7, 20),
        date(2026, 7, 21),
        date(2026, 7, 22),
        date(2026, 7, 23),
    )

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    settings_coordinator.data = {}

    coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )
    coordinator._fetch_contract_price_resolution_state = AsyncMock(return_value=None)
    coordinator.store.async_save = AsyncMock()

    # "Today" fetches, keyed by the local date being fetched as of.
    today_fetches = {
        day1: _make_market_prices("2026-07-20T10:00:00.000Z"),
        day2: _make_market_prices("2026-07-21T10:00:00.000Z"),
        day3: _make_market_prices("2026-07-22T10:00:00.000Z"),
    }
    # "Tomorrow" fetches, keyed by the tomorrow date being fetched.
    tomorrow_fetches = {
        day2: _make_market_prices("2026-07-20T22:00:00.000Z"),
        day3: _make_market_prices("2026-07-21T22:00:00.000Z"),
        day4: _make_market_prices("2026-07-22T22:00:00.000Z"),
    }

    async def fake_fetch_prices_with_fallback(start_date, end_date, use_fallback=True):
        return today_fetches[start_date]

    async def fake_fetch_tomorrow_data(tomorrow):
        return tomorrow_fetches[tomorrow]

    coordinator._fetch_prices_with_fallback = AsyncMock(
        side_effect=fake_fetch_prices_with_fallback
    )
    coordinator._fetch_tomorrow_data = AsyncMock(side_effect=fake_fetch_tomorrow_data)

    for today, tomorrow in ((day1, day2), (day2, day3), (day3, day4)):
        # --- {today}, 13:00 local: the real update cycle fetches tomorrow ---
        freezer.move_to(datetime(today.year, today.month, today.day, 13, 0, tzinfo=tz))
        now_utc_first = dt_util.utcnow()

        result = await coordinator._async_update_data()

        assert coordinator.cached_prices_tomorrow is tomorrow_fetches[tomorrow]
        assert coordinator.last_fetch_tomorrow == now_utc_first
        assert (
            result[DATA_ELECTRICITY].all[-1].date_from.astimezone(tz).date() == tomorrow
        )
        tomorrow_fetch_calls_after_first = coordinator._fetch_tomorrow_data.call_count
        today_fetch_calls_after_first = (
            coordinator._fetch_prices_with_fallback.call_count
        )

        # --- {today}, 13:05 local: next in-window tick must be a pure no-op ---
        freezer.move_to(datetime(today.year, today.month, today.day, 13, 5, tzinfo=tz))
        await coordinator._async_update_data()

        assert (
            coordinator._fetch_tomorrow_data.call_count
            == tomorrow_fetch_calls_after_first
        )
        # Today's fetch must also skip re-fetching once already done today —
        # the live _async_update_data has its own separate freshness check
        # for this (last_fetch_today.date() == today), distinct from
        # _refresh_tomorrow_cache's.
        assert (
            coordinator._fetch_prices_with_fallback.call_count
            == today_fetch_calls_after_first
        )
        assert coordinator.update_interval is None  # now confirmed idle

        # --- Midnight {today} -> {tomorrow}: promote tomorrow's cache ---
        freezer.move_to(
            datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, tzinfo=tz)
        )
        coordinator.promote_tomorrow_prices()

        assert coordinator.cached_prices_tomorrow is None
        assert (
            coordinator._static_prices_today.electricity.all[-1]
            .date_from.astimezone(tz)
            .date()
            == tomorrow
        )
        # promote_tomorrow_prices deliberately never touches last_fetch_tomorrow —
        # it's still stale for the new today until the next _async_update_data run.
        assert coordinator.last_fetch_tomorrow == now_utc_first


@pytest.mark.asyncio
async def test_full_day_cycle_survives_contract_resolution_change(
    mock_frank_energie, mock_config_entry, freezer
) -> None:
    """A real end-to-end contract resolution change (PT15M -> PT60M) must not
    crash the update or make "today" sensors go unavailable, across the
    transition day and into the day after.

    Drives the same real entry points as test_full_day_cycle_fetch_promote_refetch
    (_async_update_data + promote_tomorrow_prices), but with day1's "today" fetch
    at 15-minute resolution and day1's "tomorrow" fetch already at 60-minute
    resolution — the exact shape of a genuine in-progress contract resolution
    change reported in production. Confirms three things:
    1. Day 1's live aggregation survives the mismatch and keeps both today's
       and tomorrow's entries visible (regression: an earlier fix dropped
       today's entries in favor of tomorrow's here).
    2. Midnight promotion still cleanly adopts the new (60-minute) resolution.
    3. Day 2 onward, now that both sides agree on resolution, merging goes
       through the normal path with no further conflict.
    """
    from datetime import date

    tz = ZoneInfo(TIMEZONE_AMSTERDAM)
    day1, day2, day3 = date(2026, 7, 31), date(2026, 8, 1), date(2026, 8, 2)

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    settings_coordinator.data = {}

    coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )
    coordinator._fetch_contract_price_resolution_state = AsyncMock(return_value=None)
    coordinator.store.async_save = AsyncMock()

    day1_today = _make_market_prices_with_resolution(15, "2026-07-31T10:00:00.000Z")
    day1_tomorrow = _make_market_prices_with_resolution(
        60, "2026-07-31T22:00:00.000Z"
    )  # day2, already on the new resolution
    day2_tomorrow = _make_market_prices_with_resolution(
        60, "2026-08-01T22:00:00.000Z"
    )  # day3, resolution has settled

    today_fetches = {day1: day1_today}
    tomorrow_fetches = {day2: day1_tomorrow, day3: day2_tomorrow}

    async def fake_fetch_prices_with_fallback(start_date, end_date, use_fallback=True):
        return today_fetches[start_date]

    async def fake_fetch_tomorrow_data(tomorrow):
        return tomorrow_fetches[tomorrow]

    coordinator._fetch_prices_with_fallback = AsyncMock(
        side_effect=fake_fetch_prices_with_fallback
    )
    coordinator._fetch_tomorrow_data = AsyncMock(side_effect=fake_fetch_tomorrow_data)

    # --- Day 1, 13:00: today=15min, tomorrow=60min. Must not crash. ---
    freezer.move_to(datetime(day1.year, day1.month, day1.day, 13, 0, tzinfo=tz))
    result = await coordinator._async_update_data()

    merged_electricity = result[DATA_ELECTRICITY]
    assert len(merged_electricity.today) == 1
    assert merged_electricity.today[0].date_from.astimezone(tz).date() == day1
    assert len(merged_electricity.tomorrow) == 1
    assert merged_electricity.tomorrow[0].date_from.astimezone(tz).date() == day2

    # --- Midnight day1 -> day2: promote tomorrow's (60min) cache ---
    freezer.move_to(datetime(day2.year, day2.month, day2.day, 0, 0, tzinfo=tz))
    coordinator.promote_tomorrow_prices()

    assert coordinator.cached_prices_tomorrow is None
    assert coordinator._static_prices_today.electricity.resolution_minutes == 60
    assert (
        coordinator._static_prices_today.electricity.all[-1]
        .date_from.astimezone(tz)
        .date()
        == day2
    )

    # --- Day 2, 13:00: both sides now agree on 60min, no more conflict ---
    freezer.move_to(datetime(day2.year, day2.month, day2.day, 13, 0, tzinfo=tz))
    # day2's "today" prices are already the ones promoted at midnight (60min).
    coordinator.last_fetch_today = None  # force a live "today" re-check this cycle
    coordinator._fetch_prices_with_fallback = AsyncMock(
        side_effect=lambda *a, **kw: coordinator._static_prices_today
    )

    result_day2 = await coordinator._async_update_data()

    assert coordinator.cached_prices_tomorrow is day2_tomorrow
    assert len(result_day2[DATA_ELECTRICITY].tomorrow) == 1
    assert (
        result_day2[DATA_ELECTRICITY].tomorrow[0].date_from.astimezone(tz).date()
        == day3
    )


@pytest.mark.asyncio
async def test_async_update_data_falls_back_to_cached_today_on_fetch_failure(
    mock_frank_energie, mock_config_entry, freezer
) -> None:
    """A failed today-fetch must fall back to cached data instead of failing the update.

    Coverage gap left by removing the dead _fetch_today_data/_get_static_data
    path: FrankEnergiePriceCoordinator's real _async_update_data has its own,
    different fallback-on-exception logic for today's fetch (checking
    electricity/gas .current instead of a date-range check), which had no
    test coverage of its own — the old tests exercised the dead method's
    version of this behavior, not the live one.
    """
    tz = ZoneInfo(TIMEZONE_AMSTERDAM)

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    settings_coordinator.data = {}

    coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )
    coordinator._fetch_contract_price_resolution_state = AsyncMock(return_value=None)
    coordinator.store.async_save = AsyncMock()
    coordinator._fetch_tomorrow_data = AsyncMock(return_value=None)

    freezer.move_to(datetime(2026, 7, 20, 13, 0, tzinfo=tz))

    # Valid cached "today" data whose current entry covers this exact moment —
    # the fallback check requires both electricity AND gas to have a .current
    # entry, so (unlike _make_market_prices' always-empty gas) both need data.
    from python_frank_energie.models import MarketPrices, PriceData

    cached_today = MarketPrices(
        electricity=_make_market_prices("2026-07-20T11:00:00.000Z").electricity,
        gas=PriceData(
            [
                {
                    "from": "2026-07-20T11:00:00.000Z",
                    "till": "2026-07-20T11:15:00.000Z",
                    "marketPrice": 0.5,
                    "marketPriceTax": 0.1,
                    "sourcingMarkupPrice": 0.05,
                    "energyTaxPrice": 0.3,
                }
            ],
            energy_type="gas",
        ),
        energy_country="NL",
    )
    coordinator._static_prices_today = cached_today

    coordinator._fetch_prices_with_fallback = AsyncMock(
        side_effect=RequestException("simulated network error")
    )

    result = await coordinator._async_update_data()

    assert result[DATA_ELECTRICITY] is cached_today.electricity
    assert coordinator.last_fetch_today is None  # fallback path never updates it


@pytest.mark.asyncio
async def test_async_update_data_raises_when_fetch_fails_and_no_valid_cache(
    mock_frank_energie, mock_config_entry, freezer
) -> None:
    """A failed today-fetch with nothing valid to fall back to must raise UpdateFailed."""
    tz = ZoneInfo(TIMEZONE_AMSTERDAM)

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    settings_coordinator.data = {}

    coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )
    coordinator._fetch_contract_price_resolution_state = AsyncMock(return_value=None)
    coordinator.store.async_save = AsyncMock()

    freezer.move_to(datetime(2026, 7, 20, 13, 0, tzinfo=tz))

    assert coordinator._static_prices_today is None  # nothing cached yet

    coordinator._fetch_prices_with_fallback = AsyncMock(
        side_effect=RequestException("simulated network error")
    )

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_full_day_cycle_recovers_when_first_attempt_is_poisoned(
    mock_frank_energie, mock_config_entry, freezer
) -> None:
    """A day whose first 13:00 attempt is poisoned must still self-heal via retry.

    Reported in the wild: the fix worked for one day, then failed the next.
    This simulates the shape of that report directly — Day 2's first fetch
    attempt at 13:00 comes back non-empty but dated for today instead of
    tomorrow (the API echo case), and only the retry a few minutes later
    (as _adjust_update_interval's 5-minute in-window polling would trigger)
    actually gets tomorrow's real data. Confirms two things stay true across
    a poisoned attempt: the coordinator does not go idle after it (so the
    retry can happen automatically), and the retry itself succeeds cleanly.
    """
    from datetime import date

    tz = ZoneInfo(TIMEZONE_AMSTERDAM)
    day1, day2, day3 = date(2026, 7, 20), date(2026, 7, 21), date(2026, 7, 22)

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    # --- Day 1, 13:00: fetch succeeds cleanly, same as the happy path ---
    freezer.move_to(datetime(2026, 7, 20, 13, 0, tzinfo=tz))
    now_utc_1 = dt_util.utcnow()

    day1_today_prices = _make_market_prices("2026-07-20T10:00:00.000Z")
    coordinator._static_prices_today = day1_today_prices
    coordinator.cached_prices = {
        DATA_ELECTRICITY: day1_today_prices.electricity,
        DATA_GAS: day1_today_prices.gas,
    }

    day2_prices = _make_market_prices("2026-07-20T22:00:00.000Z")
    coordinator._fetch_tomorrow_data = AsyncMock(return_value=day2_prices)
    await coordinator._refresh_tomorrow_cache(day1, day2, now_utc_1)
    coordinator._adjust_update_interval(now_utc_1)

    assert coordinator.cached_prices_tomorrow is day2_prices
    assert coordinator.update_interval is None  # validated cache, safe to go idle

    # --- Midnight Day 1 -> Day 2 ---
    freezer.move_to(datetime(2026, 7, 21, 0, 0, tzinfo=tz))
    coordinator.promote_tomorrow_prices()
    assert coordinator.cached_prices_tomorrow is None

    # --- Day 2, 13:00: first attempt is poisoned (echoes today's date) ---
    freezer.move_to(datetime(2026, 7, 21, 13, 0, tzinfo=tz))
    now_utc_2_first = dt_util.utcnow()

    poisoned_response = _make_market_prices(
        "2026-07-21T10:00:00.000Z"
    )  # today, not tomorrow
    coordinator._fetch_tomorrow_data = AsyncMock(return_value=poisoned_response)

    result_first_attempt = await coordinator._refresh_tomorrow_cache(
        day2, day3, now_utc_2_first
    )
    coordinator._adjust_update_interval(now_utc_2_first)

    assert result_first_attempt is None
    assert coordinator.cached_prices_tomorrow is None
    assert coordinator.last_fetch_tomorrow is None
    # Must not go idle — the retry needs the polling loop to still be running.
    assert coordinator.update_interval == timedelta(minutes=5)

    # --- Day 2, 13:05: retry gets the real data ---
    freezer.move_to(datetime(2026, 7, 21, 13, 5, tzinfo=tz))
    now_utc_2_retry = dt_util.utcnow()

    day3_prices = _make_market_prices("2026-07-21T22:00:00.000Z")
    coordinator._fetch_tomorrow_data = AsyncMock(return_value=day3_prices)

    result_retry = await coordinator._refresh_tomorrow_cache(
        day2, day3, now_utc_2_retry
    )
    coordinator._adjust_update_interval(now_utc_2_retry)

    assert result_retry is day3_prices
    assert coordinator.cached_prices_tomorrow is day3_prices
    assert coordinator.last_fetch_tomorrow == now_utc_2_retry
    assert coordinator.update_interval is None  # now validated, safe to go idle


@pytest.mark.asyncio
async def test_carry_forward_previous_day_merges_yesterday_tail(
    mock_frank_energie: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A live today-only fetch across midnight must keep yesterday's tail.

    Regression test: promote_tomorrow_prices only merges yesterday's prices
    with the already-cached tomorrow prices at exactly 00:00 local time. If
    tomorrow's prices weren't cached yet when midnight hit (late API
    publication, a failed fetch, a restart), promotion has nothing to
    promote, and _async_update_data instead does a live, today-only fetch.
    Without carrying yesterday's cached prices forward, that fetch replaced
    `_static_prices_today` outright and `previous_hour` had no matching entry
    for the entire first hour of the new day.
    """
    from datetime import date

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    # Amsterdam is UTC+2 in July (CEST): 21:00 UTC on the 20th is 23:00 local
    # on the 20th (yesterday), and 22:00 UTC on the 20th is 00:00 local on
    # the 21st (the new "today").
    price_coordinator._static_prices_today = _make_market_prices(
        "2026-07-20T21:00:00.000Z"
    )

    new_today = date(2026, 7, 21)
    fresh_today_prices = _make_market_prices("2026-07-20T22:00:00.000Z")

    merged = price_coordinator._carry_forward_previous_day(
        fresh_today_prices, new_today
    )

    dates = {p.date_from for p in merged.electricity.all}
    assert datetime(2026, 7, 20, 21, 0, tzinfo=UTC) in dates
    assert datetime(2026, 7, 20, 22, 0, tzinfo=UTC) in dates


@pytest.mark.asyncio
async def test_carry_forward_previous_day_noop_within_same_day(
    mock_frank_energie: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """No merge should happen on an ordinary same-day refetch.

    Merging on every same-day fetch (not just the midnight-crossing one)
    would needlessly re-run the merge machinery and risk unbounded growth.
    """
    from datetime import date

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    today = date(2026, 7, 21)
    price_coordinator._static_prices_today = _make_market_prices(
        "2026-07-21T10:00:00.000Z"
    )
    fresh_today_prices = _make_market_prices("2026-07-21T11:00:00.000Z")

    result = price_coordinator._carry_forward_previous_day(fresh_today_prices, today)

    assert result is fresh_today_prices


@pytest.mark.asyncio
async def test_carry_forward_previous_day_prunes_older_than_yesterday(
    mock_frank_energie: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Stale multi-day-old cached data must not accumulate forever."""
    from datetime import date

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    # Cached data is two days stale (e.g. HA was offline over midnight twice):
    # 21:00 UTC on the 19th is 23:00 local on the 19th.
    price_coordinator._static_prices_today = _make_market_prices(
        "2026-07-19T21:00:00.000Z"
    )

    new_today = date(2026, 7, 21)
    fresh_today_prices = _make_market_prices("2026-07-20T22:00:00.000Z")

    merged = price_coordinator._carry_forward_previous_day(
        fresh_today_prices, new_today
    )

    dates = {p.date_from for p in merged.electricity.all}
    assert datetime(2026, 7, 19, 21, 0, tzinfo=UTC) not in dates
    assert datetime(2026, 7, 20, 22, 0, tzinfo=UTC) in dates


@pytest.mark.asyncio
async def test_carry_forward_previous_day_falls_back_on_resolution_mismatch(
    mock_frank_energie: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """A resolution/energy-type change across midnight must not crash; use fresh data."""
    from datetime import date

    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    price_coordinator._static_prices_today = _make_market_prices(
        "2026-07-20T21:00:00.000Z"
    )
    new_today = date(2026, 7, 21)
    fresh_today_prices = _make_market_prices("2026-07-20T22:00:00.000Z")

    price_coordinator._merge_prices = MagicMock(side_effect=ValueError("mismatch"))

    result = price_coordinator._carry_forward_previous_day(
        fresh_today_prices, new_today
    )

    assert result is fresh_today_prices


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


def _make_market_prices(*entry_date_isos: str):
    """Build a minimal real MarketPrices with one electricity entry per given ISO start."""
    return _make_market_prices_with_resolution(15, *entry_date_isos)


def _make_market_prices_with_resolution(
    resolution_minutes: int, *entry_date_isos: str
) -> MarketPrices:
    """Build a minimal real MarketPrices with entries spaced `resolution_minutes` apart."""
    from datetime import timedelta as _timedelta

    raw_prices = []
    for entry_date_iso in entry_date_isos:
        entry_start = datetime.fromisoformat(entry_date_iso.replace("Z", "+00:00"))
        raw_prices.append(
            {
                "from": entry_date_iso,
                "till": (
                    entry_start + _timedelta(minutes=resolution_minutes)
                ).isoformat(),
                "marketPrice": 0.1,
                "marketPriceTax": 0.02,
                "sourcingMarkupPrice": 0.01,
                "energyTaxPrice": 0.1,
            }
        )
    electricity = PriceData(raw_prices, energy_type="electricity")
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


@pytest.mark.asyncio
async def test_refresh_tomorrow_cache_detects_poisoned_entry_after_a_valid_first_entry(
    coordinator: FrankEnergieCoordinator,
) -> None:
    """A poisoned entry later in the series must be caught, not just entry[0].

    Regression test raised in code review: checking only the first cached
    entry would miss a partially-poisoned or unsorted series where a later
    entry is dated today instead of tomorrow.
    """
    from datetime import date

    today = date(2026, 7, 20)
    tomorrow = date(2026, 7, 21)
    now_utc = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    # First entry is correctly dated tomorrow, second entry is poisoned (today).
    coordinator.cached_prices_tomorrow = _make_market_prices(
        "2026-07-20T22:00:00.000Z",  # tomorrow (local)
        "2026-07-20T10:00:00.000Z",  # today (local) — poisoned
    )
    coordinator.last_fetch_tomorrow = now_utc

    fresh_tomorrow_prices = _make_market_prices("2026-07-20T22:00:00.000Z")
    coordinator._fetch_tomorrow_data = AsyncMock(return_value=fresh_tomorrow_prices)

    result = await coordinator._refresh_tomorrow_cache(today, tomorrow, now_utc)

    coordinator._fetch_tomorrow_data.assert_called_once()
    assert result is fresh_tomorrow_prices


@pytest.mark.asyncio
async def test_refresh_tomorrow_cache_accepts_legitimate_multi_day_window(
    coordinator: FrankEnergieCoordinator,
) -> None:
    """A cache spanning tomorrow and the day after must still short-circuit.

    Regression test raised in code review: tightening the date check to
    require every entry to match tomorrow exactly (rather than tomorrow or
    later) would incorrectly discard legitimately-cached multi-day API
    windows, which _price_data_after/_build_tomorrow_cache/
    promote_tomorrow_prices are specifically designed to preserve.
    """
    from datetime import date

    today = date(2026, 7, 20)
    tomorrow = date(2026, 7, 21)
    now_utc = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    multi_day_prices = _make_market_prices(
        "2026-07-20T22:00:00.000Z",  # tomorrow (local)
        "2026-07-21T22:00:00.000Z",  # day after tomorrow (local)
    )
    coordinator.cached_prices_tomorrow = multi_day_prices
    coordinator.last_fetch_tomorrow = now_utc

    coordinator._fetch_tomorrow_data = AsyncMock()

    result = await coordinator._refresh_tomorrow_cache(today, tomorrow, now_utc)

    coordinator._fetch_tomorrow_data.assert_not_called()
    assert result is multi_day_prices


@pytest.mark.asyncio
async def test_refresh_tomorrow_cache_rejects_freshly_fetched_poisoned_data(
    coordinator: FrankEnergieCoordinator,
) -> None:
    """A freshly fetched response dated for today, not tomorrow, must not be cached.

    Regression test: the self-heal check only re-validated an *existing*
    same-day cache one cycle later. It never validated the response of the
    fetch it triggered to fix that cache, so a fetch that itself came back
    non-empty but still dated for today (e.g. the API echoing the latest
    available day back for a not-yet-published date) got cached as a
    genuine success — silently re-poisoning the cache with no further
    warning, and (via _adjust_update_interval) going idle for the rest of
    the day.
    """
    from datetime import date

    today = date(2026, 7, 20)
    tomorrow = date(2026, 7, 21)
    now_utc = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    coordinator.cached_prices_tomorrow = None
    coordinator.last_fetch_tomorrow = None

    poisoned_fetch_result = _make_market_prices("2026-07-20T10:00:00.000Z")
    coordinator._fetch_tomorrow_data = AsyncMock(return_value=poisoned_fetch_result)

    result = await coordinator._refresh_tomorrow_cache(today, tomorrow, now_utc)

    assert result is None
    assert coordinator.cached_prices_tomorrow is None
    assert coordinator.last_fetch_tomorrow is None


@pytest.mark.asyncio
async def test_is_cache_fresh_detects_poisoned_same_day_tomorrow_cache(
    mock_frank_energie, mock_config_entry
) -> None:
    """_is_cache_fresh must not be fooled by a poisoned same-day tomorrow cache.

    Regression test: _is_cache_fresh had the same last_fetch_tomorrow-only
    trust issue as the old _refresh_tomorrow_cache short-circuit. Since a
    "fresh" verdict here makes async_config_entry_first_refresh skip the
    live refresh entirely (super().async_config_entry_first_refresh() is
    never called), a poisoned cache could survive every reload/restart
    indefinitely — the _refresh_tomorrow_cache validation never even runs,
    because _async_update_data is never reached on that path.
    """
    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    now_utc = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)

    price_coordinator.last_fetch_today = now_utc
    price_coordinator._static_prices_today = None  # bypass resolution check

    # Poisoned: claims to be fetched today, but entries are dated today too.
    price_coordinator.last_fetch_tomorrow = now_utc
    price_coordinator.cached_prices_tomorrow = _make_market_prices(
        "2026-07-20T10:00:00.000Z"
    )

    assert price_coordinator._is_cache_fresh(now_utc) is False


@pytest.mark.asyncio
async def test_is_cache_fresh_accepts_valid_same_day_tomorrow_cache(
    mock_frank_energie, mock_config_entry
) -> None:
    """_is_cache_fresh must still return True for a genuinely valid same-day cache."""
    settings_coordinator = FrankEnergieSettingsCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie
    )
    price_coordinator = FrankEnergiePriceCoordinator(
        MagicMock(), mock_config_entry, mock_frank_energie, settings_coordinator
    )

    now_utc = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)

    price_coordinator.last_fetch_today = now_utc
    price_coordinator._static_prices_today = None  # bypass resolution check

    price_coordinator.last_fetch_tomorrow = now_utc
    price_coordinator.cached_prices_tomorrow = _make_market_prices(
        "2026-07-20T22:00:00.000Z"  # genuinely tomorrow (local)
    )

    assert price_coordinator._is_cache_fresh(now_utc) is True
