"""Regression tests for throttling, authentication, and invoice isolation."""

import asyncio
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError
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
from pytest_homeassistant.common import MockConfigEntry
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import (
    AuthException,
    RateLimitException,
    RequestException,
)
from python_frank_energie.models import MarketPrices, PriceData

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


async def test_login_throttle_does_not_block_valid_token_requests(
    clock: MagicMock,
) -> None:
    """Throttle repeated logins without blocking existing authenticated sessions."""
    state = RateLimitState()
    login_request = AsyncMock(
        side_effect=[RateLimitException("Login", retry_after=120), "logged in"]
    )
    with pytest.raises(RateLimitException):
        await state.async_call("login", login_request, authenticated=False)

    clock.return_value = 150.0
    with pytest.raises(RateLimitException) as blocked:
        await state.async_call("login", login_request, authenticated=False)
    assert blocked.value.operation_name == "Login"
    assert blocked.value.retry_after == 70
    login_request.assert_awaited_once()

    for resource in ("prices", "period_usage_and_costs", "invoices"):
        request = AsyncMock(return_value=resource)
        assert await state.async_call(resource, request) == resource
        request.assert_awaited_once()

    clock.return_value = 220.0
    assert (
        await state.async_call("login", login_request, authenticated=False)
        == "logged in"
    )
    assert login_request.await_count == 2


async def test_login_cooldown_blocks_only_auth_recovery(
    clock: MagicMock,
) -> None:
    """A login throttle stops recovery attempts but not valid-token requests."""
    state = RateLimitState()
    login = AsyncMock(side_effect=RateLimitException("Login", retry_after=120))
    with pytest.raises(RateLimitException):
        await state.async_call("login", login, authenticated=False)

    with pytest.raises(RateLimitException) as blocked:
        state.check_authentication_recovery()
    assert blocked.value.operation_name == "Login"
    assert blocked.value.retry_after == 120

    user_sites = AsyncMock(return_value="valid token response")
    assert await state.async_call("UserSites", user_sites) == "valid token response"
    user_sites.assert_awaited_once()

    clock.return_value = 220.0
    state.check_authentication_recovery()
    login.assert_awaited_once()


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


@pytest.mark.parametrize(
    "api_error",
    [
        RequestException("Private provider reference must not be logged"),
        ClientError("Connection interrupted"),
    ],
)
async def test_invoice_transport_failures_preserve_last_result(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    api_error: Exception,
) -> None:
    """Request failures retain invoices and report a failed refresh."""
    invoices = FrankEnergieInvoiceCoordinator(hass, rate_entry, rate_api, settings)
    previous = object()
    rate_api.invoices.side_effect = [previous, api_error]

    await invoices.async_refresh()
    assert invoices.last_update_success
    await invoices.async_refresh()

    assert not invoices.last_update_success
    assert invoices.data[DATA_INVOICES] is previous
    assert isinstance(invoices.last_exception, UpdateFailed)
    assert str(api_error) not in str(invoices.last_exception)
    assert rate_api.invoices.await_count == 2


async def test_invoice_authentication_error_is_not_empty_data(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
) -> None:
    """Let the coordinator handle authentication rather than clearing invoices."""
    invoices = FrankEnergieInvoiceCoordinator(hass, rate_entry, rate_api, settings)
    auth_error = AuthException("Expired invoice access")
    rate_api.invoices.side_effect = auth_error

    with pytest.raises(AuthException) as caught:
        await invoices._fetch_invoices()

    assert caught.value is auth_error


@pytest.mark.parametrize(
    (
        "electricity_enabled",
        "gas_enabled",
        "cached_electricity",
        "cached_gas",
        "expected",
    ),
    [
        (True, False, True, False, True),
        (False, True, False, True, True),
        (True, True, True, True, True),
        (True, True, True, False, False),
        (False, False, True, False, True),
    ],
)
def test_price_cache_respects_active_contract_segments(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    electricity_enabled: bool,
    gas_enabled: bool,
    cached_electricity: bool,
    cached_gas: bool,
    expected: bool,
) -> None:
    """Accept only the cached fuel types required by the account."""
    now_utc = datetime(2026, 10, 28, 10, tzinfo=UTC)

    def make_prices(energy_type: str, enabled: bool) -> PriceData:
        """Build a real typed price series for the tested interval."""
        periods = (
            [
                {
                    "from": now_utc.isoformat(),
                    "till": (now_utc + timedelta(minutes=15)).isoformat(),
                    "marketPrice": 0.2,
                }
            ]
            if enabled
            else []
        )
        return PriceData(periods, energy_type=energy_type)

    prices = FrankEnergiePriceCoordinator(hass, rate_entry, rate_api, settings)
    prices.user_electricity_enabled = electricity_enabled
    prices.user_gas_enabled = gas_enabled
    prices._static_prices_today = MarketPrices(
        electricity=make_prices("electricity", cached_electricity),
        gas=make_prices("gas", cached_gas),
        energy_country="NL",
    )
    assert (
        prices._cached_prices_active(prices._static_prices_today, now_utc) is expected
    )


def test_price_cache_rejects_expired_interval_after_midnight(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
) -> None:
    """Reject yesterday's cache even when a previous entry was once current."""
    prices = FrankEnergiePriceCoordinator(hass, rate_entry, rate_api, settings)
    prices.user_electricity_enabled = True
    prices._static_prices_today = MarketPrices(
        electricity=PriceData(
            [
                {
                    "from": "2026-10-28T22:00:00+00:00",
                    "till": "2026-10-28T23:00:00+00:00",
                    "marketPrice": 0.2,
                }
            ],
            energy_type="electricity",
        ),
        gas=PriceData([], energy_type="gas"),
        energy_country="NL",
    )
    # Amsterdam has switched to CET: 23:05 UTC is 00:05 local.
    now_utc = datetime(2026, 10, 28, 23, 5, tzinfo=UTC)
    assert not prices._cached_prices_active(prices._static_prices_today, now_utc)


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

    assert raised.value.__cause__ is None
    assert str(raised.value) == "Authentication has expired"
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
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep unexpected login errors retryable without exposing provider details."""
    hass.config_entries.async_update_entry(rate_entry, options=USER_INPUT)
    original_data = dict(rate_entry.data)
    original_options = dict(rate_entry.options)
    rate_api.renew_token.side_effect = AuthException("expired")
    unexpected_error = RuntimeError("fictitious-private-account-secret")
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

    assert raised.value.__cause__ is None
    assert "fictitious-private-account-secret" not in str(raised.value)
    assert "fictitious-private-account-secret" not in caplog.text
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
    rate_api.me = AsyncMock(side_effect=RateLimitException("Me", retry_after=120))
    assert await settings._fetch_user_data() is user
    assert user.smartHvac is True
    assert await settings._fetch_user_data() is user
    rate_api.me.assert_awaited_once_with(settings.site_reference)


async def test_price_resolution_rate_limit_keeps_last_state(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
) -> None:
    """A throttled optional resolution query retains the previous state."""
    prices = FrankEnergiePriceCoordinator(hass, rate_entry, rate_api, settings)
    previous = MagicMock()
    prices._api_resolution_state = previous
    rate_api.contract_price_resolution_state = AsyncMock(
        side_effect=RateLimitException("Resolution", retry_after=7200)
    )
    for _ in range(2):
        state = await prices._fetch_contract_price_resolution_state("connection-1")
        assert state is previous
    rate_api.contract_price_resolution_state.assert_awaited_once()


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
        # HA clears partial runtime_data, but the entry cooldown survives.
        assert not hasattr(rate_entry, "runtime_data")
        assert isinstance(
            hass.data[DOMAIN]["rate_limit_states"][rate_entry.entry_id], RateLimitState
        )
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
        # Valid-token site discovery remains allowed during login cooldowns;
        # repeat authentication and renewal are still suppressed.
        expected_counts = (
            (counts[0] + 1, counts[1], counts[2])
            if source == "silent_login"
            else counts
        )
        assert current_counts == expected_counts
    assert dict(rate_entry.data) == original_data
    # Home Assistant's current entry migration adds the default resolution
    # independently of a throttled provider request.
    assert dict(rate_entry.options) == {**original_options, "resolution": "PT15M"}
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
    # Reauth, reconfigure and options must retain site-ownership verification.
    # Align this isolated API fixture with the selected delivery site.
    api.UserSites.return_value.deliverySites[0].reference = rate_entry.data[
        "site_reference"
    ]
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
    # Retry uses the saved tokens; it must not prompt for ignored credentials.
    assert result["data_schema"].schema == {}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["errors"] == {"base": "rate_limited"}
    api.UserSites.assert_awaited_once()
    api.login.assert_awaited_once()
    api.UserSites.side_effect = None
    clock.return_value = 220.0
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["step_id"] == "site"
    assert not result.get("errors")
    assert api.UserSites.await_count == 2


async def test_site_retry_aborts_on_legacy_username_duplicate(
    hass: HomeAssistant,
    mock_auth_success: MagicMock,
    mock_setup_entry: MagicMock,
    enable_custom_integrations: None,
    clock: MagicMock,
) -> None:
    """A successful retry must not create a duplicate of a username-keyed entry."""
    legacy_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Existing legacy account",
        unique_id=USER_INPUT[CONF_USERNAME],
        data={},
    )
    legacy_entry.add_to_hass(hass)

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
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "rate_limited"}

    api.UserSites.side_effect = None
    clock.return_value = 220.0
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
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
            await flow._get_available_sites()
    assert raised.value is error
    api.UserSites.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.parametrize("throttled_source", ["public", "user"])
async def test_rate_limited_price_source_keeps_successful_sibling(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
    throttled_source: str,
) -> None:
    """A throttle must not discard the other price source's completed result."""
    coordinator = FrankEnergiePriceCoordinator(hass, rate_entry, rate_api, settings)
    public = MagicMock(spec=MarketPrices)
    user = MagicMock(spec=MarketPrices)
    for prices in (public, user):
        prices.electricity = SimpleNamespace(all=[1])
        prices.gas = SimpleNamespace(all=[])
    public_result = AsyncMock(return_value=public)
    user_result = AsyncMock(return_value=user)
    if throttled_source == "public":
        public_result.side_effect = RateLimitException("prices", retry_after=120)
        expected = (None, user)
    else:
        user_result.side_effect = RateLimitException("UserPrices", retry_after=120)
        expected = (public, None)

    with (
        patch.object(coordinator, "_fetch_public_prices_for_range", public_result),
        patch.object(coordinator, "_fetch_user_prices_for_range", user_result),
    ):
        actual = await coordinator._fetch_price_sources(
            date(2026, 10, 28), date(2026, 10, 29), "NL"
        )

    assert actual == expected
    public_result.assert_awaited_once()
    user_result.assert_awaited_once()


async def test_empty_sibling_preserves_rate_limit_retry_hint(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
) -> None:
    """An empty public response cannot hide a throttled authenticated source."""
    coordinator = FrankEnergiePriceCoordinator(hass, rate_entry, rate_api, settings)
    empty = MarketPrices(
        electricity=PriceData([], "electricity"),
        gas=PriceData([], "gas"),
        energy_country="NL",
    )
    public_result = AsyncMock(return_value=empty)
    user_result = AsyncMock(
        side_effect=RateLimitException("UserPrices", retry_after=120)
    )
    with (
        patch.object(coordinator, "_fetch_public_prices_for_range", public_result),
        patch.object(coordinator, "_fetch_user_prices_for_range", user_result),
    ):
        with pytest.raises(RateLimitException) as raised:
            await coordinator._fetch_price_sources(
                date(2026, 10, 28), date(2026, 10, 29), "NL"
            )
    assert raised.value.retry_after == 120
    public_result.assert_awaited_once()
    user_result.assert_awaited_once()


async def test_public_prices_survive_implicit_token_renewal_throttle(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
) -> None:
    """A private token cooldown must not block unauthenticated market prices."""
    coordinator = FrankEnergiePriceCoordinator(hass, rate_entry, rate_api, settings)
    market = MarketPrices(
        electricity=PriceData([], "electricity"),
        gas=PriceData([], "gas"),
        energy_country="NL",
    )
    public_api = MagicMock()
    public_api.__aenter__ = AsyncMock(return_value=public_api)
    public_api.__aexit__ = AsyncMock(return_value=False)
    public_api.prices = AsyncMock(return_value=market)
    rate_api.prices.side_effect = RateLimitException(
        FrankEnergie.RENEW_TOKEN_OPERATIONNAME, retry_after=120
    )

    with patch(
        "custom_components.frank_energie.coordinator.FrankEnergie",
        return_value=public_api,
    ) as public_client:
        public_client.RENEW_TOKEN_OPERATIONNAME = FrankEnergie.RENEW_TOKEN_OPERATIONNAME
        for _ in range(2):
            result = await coordinator._fetch_public_prices_for_range(
                date(2026, 10, 28), date(2026, 10, 29), "NL"
            )
            assert result is market

    rate_api.prices.assert_awaited_once()
    assert public_api.prices.await_count == 2
    assert all(
        "auth_token" not in call.kwargs and "refresh_token" not in call.kwargs
        for call in public_client.call_args_list
    )


async def test_site_ownership_reauth_reuses_entry_cooldown(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    clock: MagicMock,
) -> None:
    """A throttled ownership lookup must not bypass the entry's retry deadline."""
    from custom_components.frank_energie.config_flow import (
        _async_account_matches_entry,
    )

    api = MagicMock()
    api.__aenter__ = AsyncMock(return_value=api)
    api.__aexit__ = AsyncMock(return_value=False)
    api.UserSites = AsyncMock(
        side_effect=RateLimitException("UserSites", retry_after=120)
    )
    auth = SimpleNamespace(authToken="sample-access", refreshToken="sample-refresh")
    with patch(
        "custom_components.frank_energie.config_flow.FrankEnergie", return_value=api
    ):
        with pytest.raises(RateLimitException):
            await _async_account_matches_entry(
                hass, rate_entry, auth, USER_INPUT[CONF_USERNAME]
            )
        clock.return_value = 150
        with pytest.raises(RateLimitException) as caught:
            await _async_account_matches_entry(
                hass, rate_entry, auth, USER_INPUT[CONF_USERNAME]
            )
    assert caught.value.retry_after == 70
    api.UserSites.assert_awaited_once()


async def test_price_source_throttle_propagates_without_another_result(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
    settings: FrankEnergieSettingsCoordinator,
) -> None:
    """Without either source, preserve the original server retry hint."""
    coordinator = FrankEnergiePriceCoordinator(hass, rate_entry, rate_api, settings)
    public_result = AsyncMock(return_value=None)
    user_result = AsyncMock(
        side_effect=RateLimitException("UserPrices", retry_after=120)
    )

    with (
        patch.object(coordinator, "_fetch_public_prices_for_range", public_result),
        patch.object(coordinator, "_fetch_user_prices_for_range", user_result),
    ):
        with pytest.raises(RateLimitException) as raised:
            await coordinator._fetch_price_sources(
                date(2026, 10, 28), date(2026, 10, 29), "NL"
            )
    assert raised.value.retry_after == 120


async def test_valid_token_site_lookup_ignores_login_cooldown(
    hass: HomeAssistant,
    rate_entry: MockConfigEntry,
    rate_api: MagicMock,
) -> None:
    """Login throttling may not stop site discovery with an existing valid token."""
    coordinator = FrankEnergieSettingsCoordinator(hass, rate_entry, rate_api)
    rate_api.UserSites.return_value = SimpleNamespace(
        reference="site-1",
        deliverySites=[
            SimpleNamespace(
                reference="site-1",
                address=SimpleNamespace(
                    street="Example Street", houseNumber="1", houseNumberAddition=""
                ),
            )
        ],
    )
    login_attempt = AsyncMock(side_effect=RateLimitException("Login", retry_after=120))
    with pytest.raises(RateLimitException):
        await coordinator.rate_limits.async_call(
            "login", login_attempt, authenticated=False
        )

    component = FrankEnergieComponent(hass, rate_entry)
    site_reference, site_title = await component._get_site_reference_and_title(
        coordinator
    )

    assert site_reference == "site-1"
    assert site_title == "Example Street 1"
    rate_api.UserSites.assert_awaited_once()


@pytest.mark.parametrize(
    "sensor_key",
    ["expected_costs_per_day_this_month", "costs_per_day_till_now_this_month"],
)
def test_monthly_cost_reading_date_survives_missing_invoice(
    sensor_key: str,
) -> None:
    """Missing invoices must not remove a valid month-summary reading date."""
    description = next(item for item in SENSOR_TYPES if item.key == sensor_key)
    data = {
        DATA_MONTH_SUMMARY: SimpleNamespace(lastMeterReadingDate="2026-10-28"),
        DATA_INVOICES: None,
    }
    assert description.attr_fn(data) == {"Last update": "2026-10-28"}


def test_multisite_legacy_default_site_is_not_duplicated(
    hass: HomeAssistant,
) -> None:
    """The first site belonging to a legacy account cannot be selected twice."""
    legacy_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Legacy account",
        unique_id=USER_INPUT[CONF_USERNAME],
        data={},
    )
    legacy_entry.add_to_hass(hass)
    flow = ConfigFlow()
    flow.hass = hass
    flow.sign_in_data = {CONF_USERNAME: USER_INPUT[CONF_USERNAME]}
    flow._legacy_default_site_reference = "site-1"
    assert flow._legacy_site_already_configured("site-1")
    assert not flow._legacy_site_already_configured("site-2")


def test_multisite_legacy_id_only_blocks_matching_site(hass: HomeAssistant) -> None:
    """A username-keyed legacy entry blocks its site, not another delivery site."""
    legacy = MockConfigEntry(
        domain=DOMAIN,
        title="Legacy account",
        unique_id=USER_INPUT[CONF_USERNAME],
        data={"site_reference": "site-1"},
    )
    legacy.add_to_hass(hass)

    flow = ConfigFlow()
    flow.hass = hass
    flow.sign_in_data = {CONF_USERNAME: USER_INPUT[CONF_USERNAME]}

    assert flow._legacy_site_already_configured("site-1")
    assert not flow._legacy_site_already_configured("site-2")
    assert flow._legacy_site_already_configured("site-1", single_site=True)
    assert not flow._legacy_site_already_configured("site-2", single_site=True)
