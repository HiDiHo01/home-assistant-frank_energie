"""Supplier check status is separate from coordinator success and data quality."""

from datetime import date

from custom_components.frank_energie.diagnostic_trace import DiagnosticTrace
from custom_components.frank_energie.source_checks import source_check_evidence


TODAY = date(2026, 11, 1)


def _checks(trace: DiagnosticTrace, *, authenticated=True, site=True, finance=True):
    return source_check_evidence(
        trace,
        authenticated=authenticated,
        site_selected=site,
        monthly_finance_enabled=finance,
        local_today=TODAY,
    )["checks"]


def test_restart_without_queries_reports_not_observed_instead_of_success() -> None:
    trace = DiagnosticTrace()

    result = _checks(trace)

    assert result["public_market_prices"]["state"] == "not_observed_in_retained_trace"
    assert result["customer_prices"]["state"] == "not_observed_in_retained_trace"
    assert result["delivery_sites"]["state"] == "not_observed_in_retained_trace"
    assert result["month_summary"]["state"] == "not_observed_in_retained_trace"
    assert result["daily_usage"]["state"] == "not_observed_in_retained_trace"
    assert result["month_insights_current"]["state"] == "not_observed_in_retained_trace"
    assert all(not c["complete_data_verified"] for c in result.values())


def test_public_market_check_does_not_prove_customer_tariff() -> None:
    trace = DiagnosticTrace()
    trace.record(
        "graphql_public",
        "ok",
        operation="MarketPrices",
        response={"data": {"marketPrices": {"electricityPrices": [1]}}},
    )
    checks = _checks(trace)

    assert checks["public_market_prices"]["state"] == "source_present"
    assert checks["customer_prices"]["state"] == "not_observed_in_retained_trace"
    assert checks["public_market_prices"]["complete_data_verified"] is False


def test_customer_tariff_is_separate_despite_same_graphql_operation() -> None:
    trace = DiagnosticTrace()
    trace.record(
        "graphql_account",
        "ok",
        operation="MarketPrices",
        response={"data": {"customerMarketPrices": {"electricityPrices": [1]}}},
    )

    checks = _checks(trace)

    assert checks["customer_prices"]["state"] == "source_present"
    assert checks["public_market_prices"]["state"] == "not_observed_in_retained_trace"


def test_one_successful_cost_source_does_not_validate_others() -> None:
    trace = DiagnosticTrace()
    trace.record(
        "graphql_account",
        "ok",
        operation="MonthSummary",
        response={"data": {"monthSummary": {"_id": "not_exported"}}},
    )

    checks = _checks(trace)

    assert checks["month_summary"]["state"] == "source_present"
    assert checks["invoices"]["state"] == "not_observed_in_retained_trace"
    assert checks["daily_usage"]["state"] == "not_observed_in_retained_trace"
    assert "not_exported" not in str(checks)


def test_monthly_insights_is_explicitly_off_not_mysteriously_missing() -> None:
    trace = DiagnosticTrace()
    checks = _checks(trace, finance=False)

    assert checks["month_insights_current"]["state"] == "disabled_by_option"
    assert checks["month_insights_previous"]["state"] == "disabled_by_option"
    assert not checks["month_insights_current"]["expected"]


def test_current_and_previous_periods_do_not_cross_contaminate() -> None:
    trace = DiagnosticTrace()
    trace.record(
        "graphql_account",
        "ok",
        operation="MonthInsights",
        period="2026-10",
        response={"data": {"monthInsights": {"expectedCosts": 25.0}}},
    )
    trace.record(
        "graphql_account",
        "ok",
        operation="MonthInsights",
        period="2026-11",
        response={"data": {"monthInsights": None}},
    )

    checks = _checks(trace)

    assert checks["month_insights_current"]["state"] == "source_null"
    assert checks["month_insights_current"]["requested_period"] == "2026-11"
    assert checks["month_insights_previous"]["state"] == "source_present"
    assert checks["month_insights_previous"]["requested_period"] == "2026-10"
    assert "25.0" not in str(checks)


def test_later_failed_query_overrides_older_valid_source_evidence() -> None:
    trace = DiagnosticTrace()
    trace.record(
        "graphql_account",
        "ok",
        operation="Invoices",
        response={"data": {"invoices": {"allInvoices": []}}},
    )
    trace.record("graphql_account", "exception", operation="Invoices")
    check = _checks(trace)["invoices"]

    assert check["state"] == "request_failed"
    assert check["last_request_outcome"] == "exception"


def test_null_empty_and_missing_roots_are_distinct() -> None:
    trace = DiagnosticTrace()
    trace.record(
        "graphql_account",
        "ok",
        operation="UserSites",
        response={"data": {"userSites": []}},
    )
    trace.record(
        "graphql_account",
        "ok",
        operation="Me",
        response={"data": {"me": None}},
    )
    trace.record(
        "graphql_account",
        "ok",
        operation="PeriodUsageAndCosts",
        response={"data": {}},
    )

    checks = _checks(trace)

    assert checks["delivery_sites"]["state"] == "source_empty"
    assert checks["user_profile"]["state"] == "source_null"
    assert checks["daily_usage"]["state"] == "source_not_in_response"


def test_anonymous_and_missing_site_are_explicitly_not_applicable() -> None:
    trace = DiagnosticTrace()
    anon = _checks(trace, authenticated=False)
    missing_site = _checks(trace, site=False)

    assert anon["public_market_prices"]["expected"] is True
    assert anon["customer_prices"]["state"] == "not_applicable_anonymous"
    assert anon["month_summary"]["state"] == "not_applicable_anonymous"
    assert missing_site["delivery_sites"]["expected"] is True
    assert missing_site["customer_prices"]["state"] == "not_applicable_no_selected_site"
    assert missing_site["daily_usage"]["state"] == "not_applicable_no_selected_site"


def test_evidence_is_bounded_and_does_not_contain_raw_responses() -> None:
    trace = DiagnosticTrace()
    for _ in range(100):
        trace.record(
            "graphql_account",
            "ok",
            operation="MonthSummary",
            response={"data": {"monthSummary": {"customerToken": "TOP_SECRET"}}},
        )
    evidence = source_check_evidence(
        trace,
        authenticated=True,
        site_selected=True,
        monthly_finance_enabled=False,
        local_today=TODAY,
    )

    assert evidence["retained_events"] == 96
    assert evidence["checks"]["month_summary"]["state"] == "source_present"
    assert "TOP_SECRET" not in str(evidence)


def test_cached_startup_is_explicitly_not_a_live_source_check() -> None:
    trace = DiagnosticTrace()
    result = source_check_evidence(
        trace,
        authenticated=True,
        site_selected=True,
        monthly_finance_enabled=True,
        local_today=TODAY,
        price_cache_present=True,
    )["checks"]

    assert (
        result["public_market_prices"]["state"]
        == "cached_price_state_without_source_check"
    )
    assert (
        result["customer_prices"]["state"] == "cached_price_state_without_source_check"
    )
    assert result["public_market_prices"]["complete_data_verified"] is False
    assert result["delivery_sites"]["state"] == "not_observed_in_retained_trace"


def test_smart_services_are_optional_but_their_actual_calls_are_visible() -> None:
    trace = DiagnosticTrace()
    trace.record(
        "graphql_account",
        "ok",
        operation="EnodeChargers",
        response={"data": {"enodeChargers": []}},
    )
    trace.record(
        "graphql_account",
        "ok",
        operation="SmartPvSystems",
        response={"data": {"smartPvSystems": [{"id": "private-not-exported"}]}},
    )
    trace.record("graphql_account", "exception", operation="SmartHvacStatus")

    result = _checks(trace)
    assert result["enode_chargers"]["state"] == "source_empty"
    assert result["smart_pv_systems"]["state"] == "source_present"
    assert result["smart_hvac"]["state"] == "request_failed"
    assert result["smart_batteries"]["state"] == "optional_not_observed"
    assert not result["enode_chargers"]["expected"]
    assert "private-not-exported" not in str(result)


def test_anonymous_account_does_not_require_smart_service_checks() -> None:
    trace = DiagnosticTrace()
    result = _checks(trace, authenticated=False)
    assert result["enode_chargers"]["state"] == "not_applicable_anonymous"
    assert result["smart_pv_systems"]["expected"] is False
