"""Regression tests for throttling, authentication, and invoice isolation."""

import asyncio
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import AuthException, RateLimitException

from custom_components.frank_energie import FrankEnergieComponent, async_remove_entry
from custom_components.frank_energie.config_flow import ConfigFlow
from custom_components.frank_energie.const import (
    CONF_INTERVAL_STATISTICS,
    DATA_INVOICES,
    DATA_MONTH_SUMMARY,
    DATA_USAGE,
    DATA_USER,
    DATA_USER_SITES,
    DOMAIN,
)
from custom_components.frank_energie.coordinator import (
    FrankEnergieBatterySessionCoordinator,
    FrankEnergieInvoiceCoordinator,
    FrankEnergiePriceCoordinator,
    FrankEnergieSettingsCoordinator,
    FrankEnergieStatisticsCoordinator,
)
from custom_components.frank_energie.rate_limit import (
    FrankEnergieSetupData,
    RateLimitState,
    get_rate_limit_state,
)
from custom_components.frank_energie.sensor import (
    FrankEnergieSensor,
    SENSOR_TYPES,
    async_setup_entry as async_setup_sensors,
)

USER_INPUT = {CONF_USERNAME: "test@example.com", CONF_PASSWORD: "test password"}


@pytest.fixture(autouse=True)
def daytime(freezer: FrozenDateTimeFactory) -> None:
    """Keep polling tests outside the documented maintenance window."""
    freezer.move_to("2026-10-28 10:00:00+00:00")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Control cooldown time independently of Home Assistant's scheduled jobs."""
    clock = MagicMock(return_value=100.0)
    monkeypatch.setattr("custom_components.frank_energie.rate_limit.monotonic", clock)
    return clock


@pytest.fixture
def rate_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create an authenticated entry with a known delivery site."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test street 1",
        unique_id="site-1",
        data={
            "site_reference": "site-1",
            CONF_ACCESS_TOKEN: "test access token",
            CONF_TOKEN: "test refresh token",
        },
        options={CONF_INTERVAL_STATISTICS: 1},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def rate_api() -> MagicMock:
    """Return an API stub with no network access or enabled smart devices."""
    api = MagicMock(spec=FrankEnergie)
    api.is_authenticated = True
    api.UserSites = AsyncMock(
        return_value=SimpleNamespace(segments=["ELECTRICITY", "GAS"], deliverySites=[])
    )
    for name in (
        "user",
        "month_summary",
        "period_usage_and_costs",
        "invoices",
        "smart_batteries",
        "enode_chargers",
        "enode_vehicles",
        "smart_pv_systems",
        "user_smart_feed_in",
        "smart_hvac_status",
    ):
        setattr(api, name, AsyncMock(return_value=None))
    api.renew_token = AsyncMock()
    api.login = AsyncMock()
    return api


@pytest.fixture
def settings(
    hass: HomeAssistant, rate_entry: MockConfigEntry, rate_api: MagicMock
) -> FrankEnergieSettingsCoordinator:
    """Supply independent settings to invoice and statistics coordinators."""
    coordinator = FrankEnergieSettingsCoordinator(hass, rate_entry, rate_api)
    coordinator.data = {DATA_USER: None, DATA_USER_SITES: None}
    return coordinator


async def test_resource_cooldown_and_recovery(clock: MagicMock) -> None:
    """Repeated refreshes skip the network until the exact server deadline."""
    state = RateLimitState()
    request = AsyncMock(
        side_effect=[RateLimitException("Invoices", retry_after=120), "recovered"]
    )
    with pytest.raises(RateLimitException):
        await state.async_call("invoices", request)
    clock.return_value = 150.0
    with pytest.raises(RateLimitException) as blocked:
        await state.async_call("invoices", request)
    assert blocked.value.operation_name == "Invoices"
    assert blocked.value.retry_after == 70
    request.assert_awaited_once()

    other_resource = AsyncMock(return_value="usage")
    assert await state.async_call("period_usage_and_costs", other_resource) == "usage"
    other_entry = RateLimitState()
    assert await other_entry.async_call("invoices", other_resource) == "usage"

    clock.return_value = 220.0
    assert await state.async_call("invoices", request) == "recovered"
    assert request.await_count == 2


@pytest.mark.parametrize("retry_after", [None, 0.0])
async def test_no_hint_and_zero_do_not_invent_a_deadline(
    retry_after: float | None, clock: MagicMock
) -> None:
    """Keep the original metadata and permit a caller's next explicit attempt."""
    error = RateLimitException("Invoices", retry_after=retry_after)
    request = AsyncMock(side_effect=[error, "ok"])
    state = RateLimitState()
    with pytest.raises(RateLimitException) as raised:
        await state.async_call("invoices", request)
    assert raised.value is error
    assert raised.value.retry_after == retry_after
    assert await state.async_call("invoices", request) == "ok"


async def test_concurrent_responses_do_not_shorten_cooldown(clock: MagicMock) -> None:
    """A later response with a shorter delay cannot reopen a blocked resource."""
    entered = asyncio.Event()
    release = asyncio.Event()
    state = RateLimitState()

    async def slow_request() -> None:
        """Finish with a shorter delay after the other response arrives."""
        entered.set()
        await release.wait()
        raise RateLimitException("Invoices", retry_after=20)

    slow = asyncio.create_task(state.async_call("invoices", slow_request))
    await entered.wait()
    throttled_request = AsyncMock(
        side_effect=RateLimitException("Invoices", retry_after=120)
    )
    with pytest.raises(RateLimitException):
        await state.async_call("invoices", throttled_request)
    release.set()
    with pytest.raises(RateLimitException):
        await slow
    request = AsyncMock()
    with pytest.raises(RateLimitException) as blocked:
        await state.async_call("invoices", request)
    assert blocked.value.retry_after == 120
    request.assert_not_awaited()


async def test_cancellation_is_not_a_rate_limit() -> None:
    """Cancellation propagates without retaining a false cooldown."""
    state = RateLimitState()
    request = AsyncMock(side_effect=[asyncio.CancelledError(), "ok"])
    with pytest.raises(asyncio.CancelledError):
        await state.async_call("invoices", request)
    assert await state.async_call("invoices", request) == "ok"


async def test_automatic_renewal_throttle_is_shared(clock: MagicMock) -> None:
    """An error from implicit renewal also gates other authenticated resources."""
    state = RateLimitState()
    throttled_request = AsyncMock(
        side_effect=RateLimitException("RenewToken", retry_after=120)
    )
    with pytest.raises(RateLimitException):
        await state.async_call("invoices", throttled_request)
    request = AsyncMock(return_value="public data")
    with pytest.raises(RateLimitException) as blocked:
        await state.async_call("month_summary", request)
    assert blocked.value.operation_name == "RenewToken"
    request.assert_not_awaited()
    assert (
        await state.async_call("prices", request, authenticated=False) == "public data"
    )


def test_entry_states_are_isolated_and_survive_core_runtime_cleanup(
    hass: HomeAssistant, rate_entry: MockConfigEntry
) -> None:
    """Core's removal of runtime_data on reload does not erase a deadline."""
    state = get_rate_limit_state(hass, rate_entry)
    second_entry = MockConfigEntry(domain=DOMAIN, data={})
    second_entry.add_to_hass(hass)
    assert get_rate_limit_state(hass, second_entry) is not state
    del rate_entry.runtime_data
    assert get_rate_limit_state(hass, rate_entry) is state


async def test_removing_entry_forgets_cooldowns(
    hass: HomeAssistant, rate_entry: MockConfigEntry
) -> None:
    """Permanently removed entries do not leave cooldowns behind."""
    state = get_rate_limit_state(hass, rate_entry)
    with patch("custom_components.frank_energie.price_cache_store") as store:
        store.return_value.async_remove = AsyncMock()
        await async_remove_entry(hass, rate_entry)
    del rate_entry.runtime_data
    assert get_rate_limit_state(hass, rate_entry) is not state


@pytest.mark.parametrize("method", ["_fetch_invoices", "_fetch_month_summary"])
async def test_helpers_propagate_rate_limit(
    settings: FrankEnergieSettingsCoordinator, rate_api: MagicMock, method: str
) -> None:
    """RequestException handlers must not convert throttles into missing data."""
    error = RateLimitException("Invoices", retry_after=120)
    api_method = "invoices" if method == "_fetch_invoices" else "month_summary"
    getattr(rate_api, api_method).side_effect = error
    fetch_resource = getattr(settings, method)
    with pytest.raises(RateLimitException) as raised:
        await fetch_resource()
    assert raised.value is error


@pytest.mark.parametrize(
    "retry_after,expected", [(None, None), (0.0, 60.0), (120, 120)]
)
async def test_update_failed_preserves_retry_policy(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    retry_after: float | None,
    expected: float | None,
) -> None:
    """Use server delays without accelerating normal polling or losing None."""
    error = RateLimitException("Invoices", retry_after=retry_after)
    rate_api.invoices.side_effect = error
    coordinator = FrankEnergieInvoiceCoordinator(hass, rate_entry, rate_api, settings)
    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_update_data()
    assert raised.value.retry_after == expected
    assert raised.value.__cause__ is error


async def test_invoice_failure_preserves_data_and_statistics(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    clock: MagicMock,
) -> None:
    """A failed invoice refresh keeps its cache and leaves statistics available."""
    invoices = FrankEnergieInvoiceCoordinator(hass, rate_entry, rate_api, settings)
    statistics = FrankEnergieStatisticsCoordinator(hass, rate_entry, rate_api, settings)
    original = object()
    recovered = object()
    rate_api.invoices.side_effect = [
        original,
        RateLimitException("Invoices", retry_after=120),
        recovered,
    ]
    await invoices.async_refresh()
    assert invoices.last_update_success
    await invoices.async_refresh()
    assert not invoices.last_update_success
    assert invoices.data[DATA_INVOICES] is original
    assert isinstance(invoices.last_exception, UpdateFailed)
    assert invoices.last_exception.retry_after == 120

    summary, usage = object(), object()
    rate_api.month_summary.return_value = summary
    rate_api.period_usage_and_costs.return_value = usage
    await statistics.async_refresh()
    assert statistics.last_update_success
    assert statistics.data[DATA_MONTH_SUMMARY] is summary
    assert statistics.data[DATA_USAGE] is usage
    assert statistics.data[DATA_INVOICES] is None

    await invoices.async_refresh()
    assert rate_api.invoices.await_count == 2
    clock.return_value = 220.0
    await invoices.async_refresh()
    assert invoices.last_update_success
    assert invoices.data[DATA_INVOICES] is recovered
    assert rate_api.invoices.await_count == 3


async def test_renewal_throttle_does_not_reauthenticate(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    clock: MagicMock,
) -> None:
    """Siblings share renewal cooldowns and retain the original tokens."""
    sibling = FrankEnergieStatisticsCoordinator(hass, rate_entry, rate_api, settings)
    original_data = dict(rate_entry.data)
    rate_api.renew_token.side_effect = RateLimitException("RenewToken", retry_after=120)
    for coordinator in (settings, sibling):
        with pytest.raises(RateLimitException):
            await coordinator._try_renew_token()
    rate_api.renew_token.assert_awaited_once()
    rate_api.login.assert_not_awaited()
    assert dict(rate_entry.data) == original_data

    expired_error = AuthException("expired")
    with pytest.raises(UpdateFailed) as raised:
        await sibling._handle_fetch_exceptions(expired_error)
    assert raised.value.retry_after == 120
    rate_api.renew_token.assert_awaited_once()


async def test_silent_login_throttle_is_temporary(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    clock: MagicMock,
) -> None:
    """An auth failure followed by login throttling must not start reauth."""
    hass.config_entries.async_update_entry(rate_entry, options=USER_INPUT)
    original_data, original_options = dict(rate_entry.data), dict(rate_entry.options)
    rate_api.renew_token.side_effect = AuthException("expired")
    rate_api.login.side_effect = RateLimitException("Login", retry_after=120)
    with patch(
        "custom_components.frank_energie.coordinator.decrypt_password",
        return_value="test password",
    ):
        with pytest.raises(RateLimitException):
            await settings._try_renew_token()
        with pytest.raises(RateLimitException):
            await settings._try_renew_token()
    rate_api.renew_token.assert_awaited_once()
    rate_api.login.assert_awaited_once()
    assert dict(rate_entry.data) == original_data
    assert dict(rate_entry.options) == original_options


@pytest.mark.parametrize("credential_source", ["options", "legacy_data"])
async def test_successful_silent_login_preserves_entry_configuration(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    credential_source: str,
) -> None:
    """Persist replacement tokens and remove legacy plaintext credentials."""
    legacy_data = {**rate_entry.data, **USER_INPUT}
    login_options = USER_INPUT if credential_source == "options" else {}
    hass.config_entries.async_update_entry(
        rate_entry, data=legacy_data, options=login_options
    )
    original_options = dict(rate_entry.options)
    rate_api.renew_token.side_effect = AuthException("expired")
    rate_api.login.return_value = SimpleNamespace(
        authToken="replacement access token", refreshToken="replacement refresh token"
    )
    with patch(
        "custom_components.frank_energie.coordinator.decrypt_password",
        return_value="decrypted password",
    ):
        await settings._try_renew_token()

    rate_api.login.assert_awaited_once_with(
        USER_INPUT[CONF_USERNAME], "decrypted password"
    )
    assert rate_entry.data[CONF_ACCESS_TOKEN] == "replacement access token"
    assert rate_entry.data[CONF_TOKEN] == "replacement refresh token"
    assert rate_entry.data["site_reference"] == "site-1"
    assert CONF_USERNAME not in rate_entry.data
    assert CONF_PASSWORD not in rate_entry.data
    assert dict(rate_entry.options) == original_options
    assert not settings._auth_lock.locked()


@pytest.mark.parametrize(
    "failure", ["missing_credentials", "empty_password", "invalid_auth"]
)
async def test_silent_login_authentication_failure_preserves_saved_state(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    failure: str,
) -> None:
    """Require reauthentication without clearing existing tokens or credentials."""
    if failure != "missing_credentials":
        hass.config_entries.async_update_entry(rate_entry, options=USER_INPUT)
    original_data = dict(rate_entry.data)
    original_options = dict(rate_entry.options)
    renewal_error = AuthException("expired")
    rate_api.renew_token.side_effect = renewal_error
    rate_api.login.side_effect = AuthException("invalid credentials")
    decrypted_password = "" if failure == "empty_password" else "test password"
    with (
        patch(
            "custom_components.frank_energie.coordinator.decrypt_password",
            return_value=decrypted_password,
        ),
        pytest.raises(ConfigEntryAuthFailed) as raised,
    ):
        await settings._try_renew_token()

    assert raised.value.__cause__ is renewal_error
    assert dict(rate_entry.data) == original_data
    assert dict(rate_entry.options) == original_options
    assert not settings._auth_lock.locked()
    if failure == "invalid_auth":
        rate_api.login.assert_awaited_once()
    else:
        rate_api.login.assert_not_awaited()


@pytest.mark.parametrize("failure_source", ["decryption", "login"])
async def test_unexpected_silent_login_failure_remains_temporary(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    failure_source: str,
) -> None:
    """Keep unexpected login errors retryable and release the shared auth lock."""
    hass.config_entries.async_update_entry(rate_entry, options=USER_INPUT)
    original_data = dict(rate_entry.data)
    original_options = dict(rate_entry.options)
    rate_api.renew_token.side_effect = AuthException("expired")
    unexpected_error = RuntimeError("temporary login failure")
    if failure_source == "login":
        rate_api.login.side_effect = unexpected_error
    with (
        patch(
            "custom_components.frank_energie.coordinator.decrypt_password",
            return_value="test password",
            side_effect=unexpected_error if failure_source == "decryption" else None,
        ),
        pytest.raises(UpdateFailed) as raised,
    ):
        await settings._try_renew_token()

    assert raised.value.__cause__ is unexpected_error
    assert dict(rate_entry.data) == original_data
    assert dict(rate_entry.options) == original_options
    assert not settings._auth_lock.locked()


async def test_optional_hvac_throttle_keeps_user_settings(
    settings: FrankEnergieSettingsCoordinator, rate_api: MagicMock
) -> None:
    """Optional HVAC cannot discard successful user data or its previous flag."""
    settings.data[DATA_USER] = SimpleNamespace(smartHvac=True)
    user = SimpleNamespace(smartHvac=None, countryCode="NL", connections=[])
    rate_api.user.return_value = user
    rate_api.smart_hvac_status.side_effect = RateLimitException(
        "SmartHvac", retry_after=120
    )
    assert await settings._fetch_user_data() is user
    assert user.smartHvac is True
    assert await settings._fetch_user_data() is user
    rate_api.smart_hvac_status.assert_awaited_once()


async def test_price_resolution_rate_limit_is_normalized(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
) -> None:
    """Resolution fetches outside the price-cache fallback preserve retry hints."""
    prices = FrankEnergiePriceCoordinator(hass, rate_entry, rate_api, settings)
    prices._static_prices_today = MagicMock()
    prices.last_fetch_today = dt_util.utcnow()
    prices._connection_id = "connection-1"
    rate_api.contract_price_resolution_state = AsyncMock(
        side_effect=RateLimitException("Resolution", retry_after=7200)
    )
    with pytest.raises(UpdateFailed) as raised:
        await prices._async_update_data()
    assert raised.value.retry_after == 7200
    assert isinstance(raised.value.__cause__, RateLimitException)


@pytest.mark.parametrize("on_renewal", [False, True])
async def test_battery_sessions_preserve_throttle(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    on_renewal: bool,
) -> None:
    """Dedicated session polling preserves both direct and renewal throttles."""
    coordinator = FrankEnergieBatterySessionCoordinator(
        hass, rate_entry, rate_api, "battery-1"
    )
    rate_api.smart_battery_sessions = AsyncMock(
        side_effect=AuthException("expired")
        if on_renewal
        else RateLimitException("Sessions", retry_after=7200)
    )
    rate_api.renew_token.side_effect = RateLimitException(
        "RenewToken", retry_after=7200
    )
    with pytest.raises(UpdateFailed) as raised:
        await coordinator._async_update_data()
    assert raised.value.retry_after == 7200
    assert isinstance(raised.value.__cause__, RateLimitException)


@pytest.fixture
def setup_stubs(rate_api: MagicMock) -> Iterator[tuple[AsyncMock, AsyncMock]]:
    """Exercise real setup and resource coordinators without price network IO."""
    with (
        patch("custom_components.frank_energie.FrankEnergie", return_value=rate_api),
        patch.object(
            FrankEnergiePriceCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch.object(
            FrankEnergieComponent, "_async_forward_entry_setups", new_callable=AsyncMock
        ) as forward,
        patch.object(
            FrankEnergieComponent, "_schedule_aligned_updates", new_callable=AsyncMock
        ) as align,
    ):
        yield forward, align


async def test_invoice_throttle_allows_entry_setup_and_routes_sensors(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    setup_stubs: tuple[AsyncMock, AsyncMock],
    enable_custom_integrations: None,
    clock: MagicMock,
) -> None:
    """Startup creates the same sensors even if the first invoice call is limited."""
    rate_api.month_summary.return_value = SimpleNamespace(expectedCosts=42.0)
    rate_api.invoices.side_effect = RateLimitException("Invoices", retry_after=120)
    assert await hass.config_entries.async_setup(rate_entry.entry_id)
    await hass.async_block_till_done()
    assert rate_entry.state is ConfigEntryState.LOADED
    setup_stubs[0].assert_awaited_once()
    runtime = rate_entry.runtime_data
    assert not runtime.invoice_coordinator.last_update_success
    assert runtime.statistics_coordinator.last_update_success

    add_entities = MagicMock()
    await async_setup_sensors(hass, rate_entry, add_entities)
    sensors = {
        entity.entity_description.key: entity
        for entity in add_entities.call_args.args[0]
        if isinstance(entity, FrankEnergieSensor)
    }
    invoice = sensors["invoice_current_period"]
    monthly = sensors["expected_costs_this_month"]
    assert invoice.coordinator is runtime.invoice_coordinator
    assert invoice.unique_id == "site-1.invoice_current_period"
    assert not invoice.available
    assert monthly.coordinator is runtime.statistics_coordinator
    assert monthly.available
    assert monthly.native_value == 42.0
    for key in (
        "invoice_previous_period",
        "invoice_current_period",
        "invoice_upcoming_period",
        "costs_this_year",
        "total_costs",
        "average_costs_per_month",
        "average_costs_per_year",
        "average_costs_per_year_corrected",
        "average_costs_per_month_previous_year",
        "average_costs_per_month_this_year",
        "expected_costs_this_year",
        "costs_previous_year",
    ):
        assert sensors[key].coordinator is runtime.invoice_coordinator
        assert sensors[key].unique_id == f"site-1.{key}"

    rate_api.invoices.side_effect = None
    rate_api.invoices.return_value = SimpleNamespace(
        current_period_invoice=SimpleNamespace(
            TotalAmount=15.0, PeriodDescription="October"
        )
    )
    clock.return_value = 220.0
    await runtime.invoice_coordinator.async_refresh()
    assert invoice.available
    assert invoice.native_value == 15.0
    assert monthly.available
    assert monthly.extra_state_attributes == {"Description": "October"}


async def test_invoice_attributes_do_not_change_monthly_availability(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
) -> None:
    """Invoice listeners refresh optional attributes and unsubscribe on removal."""
    description = next(
        item for item in SENSOR_TYPES if item.key == "expected_costs_this_month"
    )
    settings.data[DATA_MONTH_SUMMARY] = SimpleNamespace(expectedCosts=42.0)
    settings.last_update_success = True
    invoices = FrankEnergieInvoiceCoordinator(hass, rate_entry, rate_api, settings)
    invoices.data = {DATA_INVOICES: object()}
    invoices.last_update_success = False
    sensor = FrankEnergieSensor(
        settings, description, rate_entry, invoice_coordinator=invoices
    )
    assert sensor.available
    assert sensor.native_value == 42.0
    assert sensor.extra_state_attributes == {}
    cleanups: list[Callable[[], None]] = []
    with (
        patch.object(sensor, "async_on_remove", side_effect=cleanups.append),
        patch.object(sensor, "async_write_ha_state") as write,
    ):
        await sensor.async_added_to_hass()
        try:
            invoices.async_set_updated_data(
                {
                    DATA_INVOICES: SimpleNamespace(
                        current_period_invoice=SimpleNamespace(
                            PeriodDescription="October"
                        )
                    )
                }
            )
            write.assert_called_once()
            assert sensor.available
            assert sensor.extra_state_attributes == {"Description": "October"}
        finally:
            for cleanup in cleanups:
                cleanup()
        assert len(cleanups) == 2
        assert not list(invoices.async_contexts())
        assert not list(settings.async_contexts())


@pytest.mark.parametrize("source", ["sites", "settings", "renewal", "silent_login"])
async def test_setup_throttling_is_temporary_and_respects_reload(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    setup_stubs: tuple[AsyncMock, AsyncMock],
    enable_custom_integrations: None,
    clock: MagicMock,
    source: str,
) -> None:
    """Setup retries preserve tokens and do not bypass authentication cooldowns."""
    if source != "settings":
        data = dict(rate_entry.data)
        data.pop("site_reference")
        hass.config_entries.async_update_entry(rate_entry, data=data)
    if source == "silent_login":
        hass.config_entries.async_update_entry(rate_entry, options=USER_INPUT)
    original_data, original_options = dict(rate_entry.data), dict(rate_entry.options)
    if source in ("renewal", "silent_login"):
        rate_api.UserSites.side_effect = AuthException("expired")
        rate_api.renew_token.side_effect = (
            AuthException("expired")
            if source == "silent_login"
            else RateLimitException("RenewToken", retry_after=120)
        )
        rate_api.login.side_effect = RateLimitException("Login", retry_after=120)
    else:
        rate_api.UserSites.side_effect = RateLimitException(
            "UserSites", retry_after=120
        )
    with patch(
        "custom_components.frank_energie.coordinator.decrypt_password",
        return_value="test password",
    ):
        assert not await hass.config_entries.async_setup(rate_entry.entry_id)
        await hass.async_block_till_done()
        assert rate_entry.state is ConfigEntryState.SETUP_RETRY
        assert isinstance(rate_entry.runtime_data, FrankEnergieSetupData)
        counts = (
            rate_api.UserSites.await_count,
            rate_api.renew_token.await_count,
            rate_api.login.await_count,
        )
        assert not await hass.config_entries.async_reload(rate_entry.entry_id)
        await hass.async_block_till_done()
        current_counts = (
            rate_api.UserSites.await_count,
            rate_api.renew_token.await_count,
            rate_api.login.await_count,
        )
        assert current_counts == counts
    assert dict(rate_entry.data) == original_data
    assert dict(rate_entry.options) == original_options
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    if source != "silent_login":
        rate_api.login.assert_not_awaited()

    rate_api.UserSites.side_effect = None
    rate_api.UserSites.return_value = SimpleNamespace(
        reference="site-1",
        segments=["ELECTRICITY", "GAS"],
        deliverySites=[
            SimpleNamespace(
                reference="site-1",
                address=SimpleNamespace(
                    street="Test", houseNumber="1", houseNumberAddition=""
                ),
            )
        ],
    )
    clock.return_value = 220.0
    assert await hass.config_entries.async_reload(rate_entry.entry_id)
    assert rate_entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize("source", ["user", "reauth", "reconfigure", "options"])
async def test_login_flows_show_rate_limit_and_recover(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    mock_auth_success: MagicMock,
    mock_setup_entry: MagicMock,
    enable_custom_integrations: None,
    clock: MagicMock,
    source: str,
) -> None:
    """All login forms preserve stored credentials and reuse cooldowns on retry."""
    form_input = (
        {**USER_INPUT, CONF_INTERVAL_STATISTICS: 15}
        if source == "options"
        else USER_INPUT
    )
    original_data, original_options = dict(rate_entry.data), dict(rate_entry.options)
    api = mock_auth_success.return_value
    api.login.side_effect = RateLimitException("Login", retry_after=120)
    manager = (
        hass.config_entries.options if source == "options" else hass.config_entries.flow
    )
    if source == "options":
        result = await manager.async_init(rate_entry.entry_id)
    else:
        result = await manager.async_init(
            DOMAIN, context={"source": source, "entry_id": rate_entry.entry_id}, data={}
        )
        if source == "user":
            result = await manager.async_configure(
                result["flow_id"], user_input={"authentication": True}
            )
    result = await manager.async_configure(result["flow_id"], user_input=form_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "rate_limited"}
    assert dict(rate_entry.data) == original_data
    assert dict(rate_entry.options) == original_options
    result = await manager.async_configure(result["flow_id"], user_input=form_input)
    assert result["errors"] == {"base": "rate_limited"}
    api.login.assert_awaited_once()

    api.login.side_effect = None
    clock.return_value = 220.0
    result = await manager.async_configure(result["flow_id"], user_input=form_input)
    assert result.get("errors") in (None, {})
    assert result["type"] is (
        FlowResultType.FORM
        if source == "user"
        else FlowResultType.CREATE_ENTRY
        if source == "options"
        else FlowResultType.ABORT
    )
    assert api.login.await_count == 2
    await hass.async_block_till_done()


async def test_site_form_keeps_typed_error_and_recovers(
    hass: HomeAssistant,
    mock_auth_success: MagicMock,
    mock_setup_entry: MagicMock,
    enable_custom_integrations: None,
    clock: MagicMock,
) -> None:
    """A successful login followed by a site throttle has a retryable site form."""
    api = mock_auth_success.return_value
    api.UserSites.side_effect = RateLimitException("UserSites", retry_after=120)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"authentication": True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=USER_INPUT
    )
    assert result["step_id"] == "site"
    assert result["errors"] == {"base": "rate_limited"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=USER_INPUT
    )
    assert result["errors"] == {"base": "rate_limited"}
    api.UserSites.assert_awaited_once()
    api.login.assert_awaited_once()
    api.UserSites.side_effect = None
    clock.return_value = 220.0
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=USER_INPUT
    )
    assert result["step_id"] == "site"
    assert not result.get("errors")
    assert api.UserSites.await_count == 2


async def test_site_helper_does_not_swallow_or_retry_rate_limits(
    hass: HomeAssistant, mock_auth_success: MagicMock
) -> None:
    """The connection-retry helper must not immediately retry a typed throttle."""
    flow = ConfigFlow()
    flow.hass = hass
    api = mock_auth_success.return_value
    error = RateLimitException("UserSites", retry_after=120)
    api.UserSites.side_effect = error
    with patch(
        "custom_components.frank_energie.config_flow.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        with pytest.raises(RateLimitException) as raised:
            await flow._get_available_sites("test@example.com")
    assert raised.value is error
    api.UserSites.assert_awaited_once()
    sleep.assert_not_awaited()
