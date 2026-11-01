"""Integration-facing monthly finance polling and entity lifecycle guards."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call
from zoneinfo import ZoneInfo

import pytest

from custom_components.frank_energie.const import (
    CONF_MONTHLY_FINANCE_ENABLED,
    DATA_MONTH_INSIGHTS_CURRENT,
    DATA_MONTH_INSIGHTS_PREVIOUS,
    DATA_USAGE,
)
from custom_components.frank_energie.coordinator import (
    FrankEnergieStatisticsCoordinator,
)
from custom_components.frank_energie.finance import (
    MonthlyFinanceSnapshot,
    month_keys,
)
from custom_components.frank_energie.sensor import (
    SENSOR_TYPES,
    _monthly_finance_descriptions,
)
from python_frank_energie.exceptions import FrankEnergieException


def _statistics_coordinator(*, enabled: bool, site: str = "SITE_A"):
    hass = MagicMock()
    hass.config.country = "BE"
    entry = MagicMock()
    entry.entry_id = "finance-test-entry"
    entry.options = {CONF_MONTHLY_FINANCE_ENABLED: enabled}
    entry.data = {"site_reference": site}
    api = MagicMock()
    api.is_authenticated = True
    api.month_insights = AsyncMock(
        return_value=SimpleNamespace(
            lastMeterReadingDate=datetime.now().astimezone(),
        )
    )
    settings = SimpleNamespace(data={})
    c = FrankEnergieStatisticsCoordinator(hass, entry, api, settings)
    c._should_skip_api_calls = MagicMock(return_value=False)
    c._fetch_month_summary = AsyncMock(return_value=None)
    c._fetch_invoices = AsyncMock(return_value=None)
    c._fetch_period_usage = AsyncMock(return_value=None)
    return c, entry, api


@pytest.mark.asyncio
async def test_monthly_insights_opt_in_prevents_legacy_api_traffic():
    coordinator, _, api = _statistics_coordinator(enabled=False)
    result = await coordinator._async_update_data()
    api.month_insights.assert_not_awaited()
    assert result[DATA_MONTH_INSIGHTS_CURRENT] is None
    assert result[DATA_MONTH_INSIGHTS_PREVIOUS] is None


@pytest.mark.asyncio
async def test_monthly_insights_current_and_previous_are_site_scoped_and_throttled():
    coordinator, entry, api = _statistics_coordinator(enabled=True)
    first = await coordinator._async_update_data()
    assert isinstance(first[DATA_MONTH_INSIGHTS_CURRENT], MonthlyFinanceSnapshot)
    assert isinstance(first[DATA_MONTH_INSIGHTS_PREVIOUS], MonthlyFinanceSnapshot)
    current, previous = month_keys(datetime.now(ZoneInfo("Europe/Amsterdam")).date())
    assert {
        x.month
        for x in (
            first[DATA_MONTH_INSIGHTS_CURRENT],
            first[DATA_MONTH_INSIGHTS_PREVIOUS],
        )
    } == {current, previous}

    await coordinator._async_update_data()
    assert api.month_insights.await_count == 3  # current twice, previous once
    assert api.month_insights.await_args_list.count(call("SITE_A", previous)) == 1

    entry.data["site_reference"] = "SITE_B"
    await coordinator._async_update_data()
    assert api.month_insights.await_count == 5
    assert call("SITE_B", previous) in api.month_insights.await_args_list
    assert call("SITE_B", current) in api.month_insights.await_args_list


@pytest.mark.asyncio
async def test_previous_month_missing_response_retries_then_caches_success():
    """An API gap cannot make last month's finance unavailable all day."""
    coordinator, _, api = _statistics_coordinator(enabled=True)
    _, previous = month_keys(datetime.now(ZoneInfo("Europe/Amsterdam")).date())
    attempts = 0

    async def month_response(_site: str, period: str):
        nonlocal attempts
        if period == previous:
            attempts += 1
            if attempts == 1:
                return None
        return SimpleNamespace(lastMeterReadingDate=datetime.now().astimezone())

    api.month_insights.side_effect = month_response
    first = await coordinator._async_update_data()
    assert first[DATA_MONTH_INSIGHTS_PREVIOUS] is None

    second = await coordinator._async_update_data()
    assert isinstance(second[DATA_MONTH_INSIGHTS_PREVIOUS], MonthlyFinanceSnapshot)

    await coordinator._async_update_data()
    assert attempts == 2  # cached only after the successful source read


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_error",
    [
        ValueError("Invalid lastMeterReadingDate"),
        TypeError("Incomplete monthly response"),
        TimeoutError("Monthly endpoint timed out"),
    ],
)
async def test_monthly_parser_errors_do_not_disable_daily_statistics(source_error):
    """Optional monthInsights errors must not break yesterday or invoices."""
    coordinator, _, api = _statistics_coordinator(enabled=True)
    api.month_insights.side_effect = source_error

    result = await coordinator._async_update_data()
    assert result[DATA_MONTH_INSIGHTS_CURRENT] is None
    assert result[DATA_MONTH_INSIGHTS_PREVIOUS] is None
    coordinator._fetch_period_usage.assert_awaited_once()
    coordinator._fetch_month_summary.assert_awaited_once()
    coordinator._fetch_invoices.assert_awaited_once()


@pytest.mark.asyncio
async def test_monthly_failure_does_not_disable_unrelated_statistics():
    coordinator, _, api = _statistics_coordinator(enabled=True)
    api.month_insights.side_effect = FrankEnergieException("API unavailable")
    result = await coordinator._async_update_data()
    assert result[DATA_MONTH_INSIGHTS_CURRENT] is None
    assert result[DATA_MONTH_INSIGHTS_PREVIOUS] is None
    coordinator._fetch_period_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_anonymous_entry_never_calls_monthly_api():
    coordinator, _, api = _statistics_coordinator(enabled=True)
    api.is_authenticated = False
    result = await coordinator._async_update_data()
    api.month_insights.assert_not_awaited()
    assert result[DATA_MONTH_INSIGHTS_CURRENT] is None


def test_monthly_description_is_opt_in_and_legacy_daily_alias_never_reused():
    from custom_components.frank_energie import const

    data = {DATA_USAGE: SimpleNamespace(electricity=SimpleNamespace(costs_total=14.50))}
    yesterday = next(d for d in SENSOR_TYPES if d.key == "costs_electricity_yesterday")
    legacy = next(d for d in SENSOR_TYPES if d.key == "costs_electricity_this_month")
    assert yesterday.value_fn(data) == 14.50
    assert legacy.value_fn(data) is None

    keys = {d.key for d in _monthly_finance_descriptions()}
    assert "finance_current_electricity_costs" in keys
    assert "finance_previous_gas_costs" in keys
    assert "finance_current_expected_fixed" in keys
    assert len(keys) == 25
    assert "finance_current_readiness" in keys
    assert "finance_previous_readiness" in keys
    assert all(d.state_class is None for d in _monthly_finance_descriptions())
    assert all(d.authenticated for d in _monthly_finance_descriptions())
    assert const.CONF_MONTHLY_FINANCE_ENABLED == "monthly_finance_enabled"


def test_all_monthly_keys_translated_in_all_shipped_locales():
    import json
    from pathlib import Path

    root = (
        Path(__file__).resolve().parent.parent / "custom_components" / "frank_energie"
    )
    keys = {d.translation_key for d in _monthly_finance_descriptions()}
    for lang in (
        "strings.json",
        "translations/en.json",
        "translations/nl.json",
        "translations/nl-BE.json",
        "translations/fr.json",
    ):
        content = json.loads((root / lang).read_text(encoding="utf-8"))
        assert keys <= content["entity"]["sensor"].keys(), lang
        assert "monthly_finance_enabled" in content["options"]["step"]["user"]["data"]


@pytest.mark.asyncio
async def test_previous_month_last_good_survives_provider_outage_next_day():
    """Keep previously verified historical evidence and retry failed refresh."""
    coordinator, _, api = _statistics_coordinator(enabled=True)
    first = await coordinator._async_update_data()
    previous_value = first[DATA_MONTH_INSIGHTS_PREVIOUS]
    assert previous_value is not None
    prior_key = coordinator._previous_finance_cache_key
    assert prior_key is not None
    coordinator._previous_finance_cache_key = (
        prior_key[0],
        prior_key[1],
        prior_key[2] - timedelta(days=1),
    )
    _, previous = month_keys(datetime.now(ZoneInfo("Europe/Amsterdam")).date())

    async def fail_previous(_site, period):
        if period == previous:
            raise TimeoutError("temporary source outage")
        return SimpleNamespace(lastMeterReadingDate=datetime.now().astimezone())

    api.month_insights.side_effect = fail_previous
    second = await coordinator._async_update_data()
    assert second[DATA_MONTH_INSIGHTS_PREVIOUS] is previous_value
    await coordinator._async_update_data()
    assert coordinator._previous_finance_snapshot is previous_value

    api.month_insights.side_effect = None
    third = await coordinator._async_update_data()
    assert third[DATA_MONTH_INSIGHTS_PREVIOUS] is not previous_value


@pytest.mark.asyncio
async def test_previous_month_cached_evidence_never_crosses_sites():
    coordinator, entry, api = _statistics_coordinator(enabled=True)
    first = await coordinator._async_update_data()
    assert first[DATA_MONTH_INSIGHTS_PREVIOUS] is not None
    entry.data["site_reference"] = "SITE_B"
    api.month_insights.side_effect = TimeoutError("other site unavailable")
    second = await coordinator._async_update_data()
    assert second[DATA_MONTH_INSIGHTS_PREVIOUS] is None


@pytest.mark.asyncio
async def test_last_good_previous_month_survives_transient_provider_outage():
    coordinator, _, api = _statistics_coordinator(enabled=True)
    first = await coordinator._async_update_data()
    saved = first[DATA_MONTH_INSIGHTS_PREVIOUS]
    assert saved is not None
    old_key = coordinator._previous_finance_cache_key
    assert old_key is not None

    # A later calendar day makes the previous-month source due for refresh.
    coordinator._previous_finance_cache_key = (
        old_key[0],
        old_key[1],
        old_key[2] - timedelta(days=1),
    )
    _, previous = month_keys(datetime.now(ZoneInfo("Europe/Amsterdam")).date())

    async def temporary_outage(_site, period):
        if period == previous:
            raise TimeoutError("provider unavailable")
        return SimpleNamespace(lastMeterReadingDate=datetime.now().astimezone())

    api.month_insights.side_effect = temporary_outage
    second = await coordinator._async_update_data()
    assert second[DATA_MONTH_INSIGHTS_PREVIOUS] is saved
    await coordinator._async_update_data()
    assert coordinator._previous_finance_snapshot is saved

    api.month_insights.side_effect = None
    third = await coordinator._async_update_data()
    assert third[DATA_MONTH_INSIGHTS_PREVIOUS] is not saved


@pytest.mark.asyncio
async def test_previous_month_cache_is_never_used_for_another_site():
    coordinator, entry, api = _statistics_coordinator(enabled=True)
    first = await coordinator._async_update_data()
    assert first[DATA_MONTH_INSIGHTS_PREVIOUS] is not None
    entry.data["site_reference"] = "SITE_B"
    api.month_insights.side_effect = TimeoutError("other site unavailable")
    second = await coordinator._async_update_data()
    assert second[DATA_MONTH_INSIGHTS_PREVIOUS] is None
