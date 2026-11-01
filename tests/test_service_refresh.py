"""Regression tests for service-level refresh controls and polling evidence."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.frank_energie.const import DATA_BATTERIES, DATA_PV_SYSTEMS
from custom_components.frank_energie.button import (
    FrankEnergieButtonEntityDescription,
    FrankEnergieRefreshButton,
)
from custom_components.frank_energie.diagnostic_trace import DiagnosticTrace
from custom_components.frank_energie.service_refresh import (
    RefreshService,
    service_api_status,
    service_refresh_attributes,
    service_refresh_interval_minutes,
    service_refreshes,
)
from custom_components.frank_energie.service_refresh_sensor import (
    FrankEnergieLastApiCheckSensor,
    FrankEnergieLastSuccessfulApiResponseSensor,
    FrankEnergieRefreshIntervalSensor,
    FrankEnergieServiceApiStatusSensor,
)


def _runtime(authenticated=True, gas=True, inventory=False):
    price = MagicMock(update_interval=timedelta(minutes=5))
    price.user_gas_enabled = gas
    settings = MagicMock(update_interval=timedelta(hours=24))
    settings.api.is_authenticated = authenticated
    statistics = MagicMock(update_interval=timedelta(hours=1))
    battery = SimpleNamespace(
        data={
            DATA_BATTERIES: SimpleNamespace(batteries=[object()] if inventory else [])
        }
    )
    charger = SimpleNamespace(
        data={
            "enode_chargers": SimpleNamespace(chargers=[object()] if inventory else [])
        }
    )
    vehicle = SimpleNamespace(
        data={
            "enode_vehicles": SimpleNamespace(vehicles=[object()] if inventory else [])
        }
    )
    solar = SimpleNamespace(
        data={DATA_PV_SYSTEMS: SimpleNamespace(systems=[object()] if inventory else [])}
    )
    return SimpleNamespace(
        price_coordinator=price,
        settings_coordinator=settings,
        statistics_coordinator=statistics,
        battery_coordinator=battery,
        charger_coordinator=charger,
        vehicle_coordinator=vehicle,
        pv_coordinator=solar,
    )


def test_service_bindings_do_not_create_missing_hardware_or_login_services():
    anonymous = service_refreshes(_runtime(authenticated=False))
    assert [s.key for s in anonymous] == ["prices", "gas_prices"]

    authenticated = service_refreshes(_runtime())
    assert [s.key for s in authenticated] == [
        "prices",
        "gas_prices",
        "costs",
        "usage",
        "user",
    ]

    no_gas = service_refreshes(_runtime(gas=False, inventory=True))
    assert [s.key for s in no_gas] == [
        "prices",
        "costs",
        "usage",
        "user",
        "batteries",
        "chargers",
        "vehicles",
        "solar",
    ]


def test_refresh_intervals_follow_live_coordinator_changes():
    coordinator = MagicMock(update_interval=timedelta(minutes=60))
    service = RefreshService("costs", "Costs", coordinator, ("MonthSummary",))
    assert service_refresh_interval_minutes(service) == 60
    coordinator.update_interval = timedelta(minutes=5)
    assert service_refresh_interval_minutes(service) == 5
    coordinator.update_interval = None
    assert service_refresh_interval_minutes(service) is None


def test_source_timestamps_do_not_claim_financial_completeness():
    trace = DiagnosticTrace()
    trace.record("graphql_account", "ok", operation="MonthSummary")
    trace.record("graphql_account", "graphql_errors", operation="MonthInsights")
    coordinator = MagicMock(
        update_interval=timedelta(hours=1), last_update_success=True
    )
    service = RefreshService(
        "costs", "Costs", coordinator, ("MonthSummary", "MonthInsights")
    )
    result = service_refresh_attributes(service, trace)
    assert result["polling_interval_minutes"] == 60
    assert result["last_api_outcome"] == "graphql_errors"
    assert result["last_api_request_utc"] is not None
    assert result["last_successful_api_response_utc"] is not None
    assert result["next_refresh_is_estimate"] is True
    last = datetime.fromisoformat(result["last_api_request_utc"])
    due = datetime.fromisoformat(result["next_estimated_refresh_utc"])
    assert last.tzinfo == UTC
    assert due - last == timedelta(hours=1)
    assert "private-email" not in str(result)


def test_no_api_evidence_never_invents_last_update_or_future_deadline():
    coordinator = MagicMock(
        update_interval=timedelta(hours=1), last_update_success=True
    )
    service = RefreshService("user", "User", coordinator, ("Me",))
    attrs = service_refresh_attributes(service, None)
    assert attrs["last_api_request_utc"] is None
    assert attrs["last_successful_api_response_utc"] is None
    assert attrs["next_estimated_refresh_utc"] is None
    assert attrs["polling_interval_minutes"] == 60


@pytest.mark.asyncio
async def test_costs_button_refreshes_statistics_only():
    price = MagicMock()
    statistics = MagicMock()
    statistics.async_request_refresh = AsyncMock()
    entry = SimpleNamespace(entry_id="site-one")
    costs = RefreshService("costs", "Costs", statistics, ("MonthInsights",))
    button = FrankEnergieRefreshButton(
        coordinator=statistics,
        description=FrankEnergieButtonEntityDescription(
            key="refresh_costs", name="Refresh Costs", service_name="Costs"
        ),
        entry=entry,
        service=costs,
        trace=None,
    )
    await button.async_press()
    statistics.async_request_refresh.assert_awaited_once()
    price.async_request_refresh.assert_not_called()
    assert button.unique_id == "site-one_refresh_costs"
    assert button.extra_state_attributes["last_api_request_utc"] is None


def test_refresh_interval_sensor_updates_without_api_call():
    coordinator = MagicMock(
        update_interval=timedelta(minutes=30), last_update_success=True
    )
    service = RefreshService("usage", "Usage", coordinator, ("PeriodUsageAndCosts",))
    sensor = FrankEnergieRefreshIntervalSensor(
        service, SimpleNamespace(entry_id="site-one"), None
    )
    assert sensor.unique_id == "site-one_refresh_interval_usage"
    assert sensor.native_value == 30
    assert sensor.available is True
    coordinator.update_interval = timedelta(hours=2)
    assert sensor.native_value == 120
    coordinator.async_request_refresh.assert_not_called()


def test_last_api_check_is_real_source_timestamp_and_missing_is_unavailable():
    coordinator = MagicMock(
        update_interval=timedelta(minutes=60), last_update_success=True
    )
    service = RefreshService("costs", "Costs", coordinator, ("MonthSummary",))
    entry = SimpleNamespace(entry_id="entry")
    trace = DiagnosticTrace()
    last = FrankEnergieLastApiCheckSensor(service, entry, trace)
    assert last.available is False
    assert last.native_value is None
    trace.record("graphql_account", "ok", operation="MonthSummary")
    assert last.available is True
    assert last.native_value.tzinfo == UTC
    assert last.unique_id == "entry_last_api_check_costs"
    coordinator.async_request_refresh.assert_not_called()


def test_api_status_differentiates_provider_errors_from_no_request():
    trace = DiagnosticTrace()
    coordinator = MagicMock(update_interval=timedelta(hours=1))
    service = RefreshService("prices", "Prices", coordinator, ("MarketPrices",))
    assert service_api_status(service, trace) == "not_checked"
    trace.record("graphql_public", "ok", operation="MarketPrices")
    assert service_api_status(service, trace) == "connected"
    trace.record("graphql_account", "graphql_errors", operation="MarketPrices")
    assert service_api_status(service, trace) == "error"

    class RateLimitedTrace:
        def snapshot(self):
            return {
                "events": [
                    {
                        "source": "graphql_public",
                        "operation": "MarketPrices",
                        "outcome": "exception",
                        "http_error_status": 429,
                    }
                ]
            }

    assert service_api_status(service, RateLimitedTrace()) == "rate_limited"
    assert (
        service_api_status(
            RefreshService("user", "User", coordinator, ("Me",)),
            trace,
        )
        == "not_checked"
    )


def test_api_status_and_last_successful_response_are_stable_diagnostic_entities():
    coordinator = MagicMock(update_interval=timedelta(hours=1))
    service = RefreshService("costs", "Costs", coordinator, ("MonthSummary",))
    entry = SimpleNamespace(entry_id="safe")
    trace = DiagnosticTrace()
    status = FrankEnergieServiceApiStatusSensor(service, entry, trace)
    last_good = FrankEnergieLastSuccessfulApiResponseSensor(service, entry, trace)
    assert status.native_value == "not_checked"
    assert status.available is True
    assert last_good.native_value is None
    assert last_good.available is False
    assert status.unique_id == "safe_api_status_costs"
    assert last_good.unique_id == "safe_last_successful_api_response_costs"

    trace.record("graphql_account", "ok", operation="MonthSummary")
    assert status.native_value == "connected"
    assert last_good.available is True
    good_time = last_good.native_value
    trace.record("graphql_account", "graphql_errors", operation="MonthSummary")
    assert status.native_value == "error"
    assert last_good.native_value == good_time
    coordinator.async_request_refresh.assert_not_called()
