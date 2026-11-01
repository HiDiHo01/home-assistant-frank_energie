"""Regression coverage for missing data after UTC maintenance and token renewal."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.frank_energie import FrankEnergieComponent
from custom_components.frank_energie.auth_lifecycle import attach_token_persistence
from custom_components.frank_energie.coordinator import (
    FrankEnergieSettingsCoordinator,
)
from custom_components.frank_energie.diagnostic_trace import DiagnosticTrace
from homeassistant.helpers.update_coordinator import UpdateFailed


@pytest.mark.asyncio
async def test_automatic_token_renewal_persisted_once_without_new_api_calls() -> None:
    hass = MagicMock()
    entry = MagicMock()
    entry.data = {
        "site_reference": "private-site",
        "access_token": "old",
        "token": "old-refresh",
    }
    api = SimpleNamespace(
        renew_token=AsyncMock(
            return_value=SimpleNamespace(authToken="new", refreshToken="new-refresh")
        )
    )

    attach_token_persistence(hass, entry, api)
    attach_token_persistence(hass, entry, api)
    first = await api.renew_token()
    assert first.authToken == "new"
    assert api.renew_token is not None
    hass.config_entries.async_update_entry.assert_called_once_with(
        entry,
        data={
            "site_reference": "private-site",
            "access_token": "new",
            "token": "new-refresh",
        },
    )
    # HA mutates ConfigEntry.data on save; a repeated same-token renewal is a no-op.
    entry.data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
    await api.renew_token()
    hass.config_entries.async_update_entry.assert_called_once()


@pytest.mark.asyncio
async def test_failed_auto_persistence_does_not_break_successful_renewal() -> None:
    hass = MagicMock()
    hass.config_entries.async_update_entry.side_effect = RuntimeError("not writable")
    entry = MagicMock(data={"access_token": "old", "token": "old-refresh"})
    api = SimpleNamespace(
        renew_token=AsyncMock(
            return_value=SimpleNamespace(authToken="new", refreshToken="new-refresh")
        )
    )
    attach_token_persistence(hass, entry, api)
    assert (await api.renew_token()).authToken == "new"


@pytest.mark.asyncio
async def test_missing_authenticated_user_sites_is_not_false_success() -> None:
    coordinator = object.__new__(FrankEnergieSettingsCoordinator)
    coordinator.api = MagicMock(is_authenticated=True)
    coordinator.config_entry = MagicMock()
    coordinator.data = {"user_sites": None}
    coordinator._fetch_user_sites = AsyncMock(return_value=None)
    coordinator._fetch_user_data = AsyncMock(return_value=None)
    coordinator._should_skip_api_calls = MagicMock(return_value=False)

    with pytest.raises(UpdateFailed, match="delivery sites"):
        await FrankEnergieSettingsCoordinator._async_update_data(coordinator)


@pytest.mark.asyncio
async def test_hourly_scheduler_recovers_missing_core_streams_after_maintenance() -> (
    None
):
    hass = MagicMock()
    entry = MagicMock()
    component = FrankEnergieComponent(hass, entry)

    trace = DiagnosticTrace()
    price = MagicMock()
    price._diagnostic_trace = trace
    price.api.is_authenticated = True
    price.data = {"electricity": None, "gas": None}
    price.last_fetch_today = None
    price.last_update_success = True
    price.async_request_refresh = AsyncMock()
    price._should_skip_api_calls.return_value = False
    price._today_cache_covers_selected_site.return_value = False
    settings = price.settings_coordinator
    settings.api.is_authenticated = True
    settings.data = {"user_sites": None}
    settings.async_request_refresh = AsyncMock()
    stats = MagicMock()
    stats.api.is_authenticated = True
    stats.data = {"month_summary": None, "invoices": None, "usage": None}
    stats.async_request_refresh = AsyncMock()
    entry.runtime_data.statistics_coordinator = stats

    callback = None

    def record_callback(_hass, cb, **_kwargs):
        nonlocal callback
        callback = cb
        return MagicMock()

    local = datetime(2026, 11, 1, 2, 0, tzinfo=ZoneInfo("Europe/Brussels"))
    utc = local.astimezone(UTC)

    async def recover_settings() -> None:
        settings.data = {"user_sites": object()}
        settings.last_update_success = True

    async def recover_statistics() -> None:
        stats.data = {"month_summary": object()}
        stats.last_update_success = True

    async def recover_prices() -> None:
        price.data = {"electricity": SimpleNamespace(current=0.2)}
        price._today_cache_covers_selected_site.return_value = True
        price.last_update_success = True

    settings.async_request_refresh.side_effect = recover_settings
    stats.async_request_refresh.side_effect = recover_statistics
    price.async_request_refresh.side_effect = recover_prices

    with (
        patch(
            "custom_components.frank_energie.async_track_utc_time_change",
            record_callback,
        ),
        patch("custom_components.frank_energie.dt_util.now", return_value=local),
        patch("custom_components.frank_energie.dt_util.utcnow", return_value=utc),
    ):
        await component._schedule_aligned_updates(price)
        await callback(utc)
    settings.async_request_refresh.assert_awaited_once()
    stats.async_request_refresh.assert_awaited_once()
    price.async_request_refresh.assert_awaited_once()
    outcomes = [event["outcome"] for event in trace.snapshot()["events"]]
    assert outcomes.count("recovery_requested") == 3
    assert outcomes.count("refresh_ok") == 3


@pytest.mark.asyncio
async def test_hourly_recovery_does_not_call_api_during_maintenance() -> None:
    hass = MagicMock()
    entry = MagicMock()
    component = FrankEnergieComponent(hass, entry)
    price = MagicMock()
    price._diagnostic_trace = DiagnosticTrace()
    price._should_skip_api_calls.return_value = True
    price.async_request_refresh = AsyncMock()

    callback = None

    def record_callback(_hass, cb, **_kwargs):
        nonlocal callback
        callback = cb
        return MagicMock()

    local = datetime(2026, 11, 1, 1, 15, tzinfo=ZoneInfo("Europe/Brussels"))
    utc = local.astimezone(UTC)
    with (
        patch(
            "custom_components.frank_energie.async_track_utc_time_change",
            record_callback,
        ),
        patch("custom_components.frank_energie.dt_util.now", return_value=local),
        patch("custom_components.frank_energie.dt_util.utcnow", return_value=utc),
    ):
        await component._schedule_aligned_updates(price)
        await callback(utc)
    price.async_request_refresh.assert_not_awaited()
    price.publish_price_slot.assert_called_once_with(utc)


@pytest.mark.asyncio
async def test_recovery_does_not_report_false_success_when_data_is_still_missing() -> (
    None
):
    """A successful request call is not necessarily successful source recovery."""
    hass = MagicMock()
    entry = MagicMock()
    component = FrankEnergieComponent(hass, entry)
    trace = DiagnosticTrace()
    price = MagicMock()
    price._diagnostic_trace = trace
    price._should_skip_api_calls.return_value = False
    price.api.is_authenticated = True
    price.last_update_success = False
    price.data = {"electricity": None, "gas": None}
    price.last_fetch_today = None
    price._today_cache_covers_selected_site.return_value = False
    price.async_request_refresh = AsyncMock()

    settings = price.settings_coordinator
    settings.api.is_authenticated = False
    settings.data = {"user_sites": object()}
    stats = MagicMock()
    stats.api.is_authenticated = False
    stats.data = {"month_summary": object()}
    entry.runtime_data.statistics_coordinator = stats

    callbacks: list = []

    def record_callback(_hass, callback, **_kwargs) -> MagicMock:
        callbacks.append(callback)
        return MagicMock()

    local = datetime(2026, 11, 1, 2, tzinfo=ZoneInfo("Europe/Brussels"))
    utc = local.astimezone(UTC)
    with (
        patch(
            "custom_components.frank_energie.async_track_utc_time_change",
            record_callback,
        ),
        patch("custom_components.frank_energie.dt_util.now", return_value=local),
        patch("custom_components.frank_energie.dt_util.utcnow", return_value=utc),
    ):
        await component._schedule_aligned_updates(price)
        await callbacks[0](utc)

    price.async_request_refresh.assert_awaited_once()
    outcomes = [event["outcome"] for event in trace.snapshot()["events"]]
    assert "refresh_incomplete" in outcomes
    assert "refresh_ok" not in outcomes
