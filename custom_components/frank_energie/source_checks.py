"""Privacy-safe evidence of which supplier sources were actually checked.

This module reads the existing bounded GraphQL observer only. It performs
no provider requests and makes no assertion that a successful response
contains complete meter/contract/billing data.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .finance import month_keys

# Each check uses both the owning client and the actual GraphQL response
# root. Public market and customer prices share operation "MarketPrices" in
# python-frank-energie 2026.9.20, so operation name alone is insufficient.
_SOURCES: dict[str, tuple[str, str, str]] = {
    "public_market_prices": ("graphql_public", "MarketPrices", "marketPrices"),
    "customer_prices": ("graphql_account", "MarketPrices", "customerMarketPrices"),
    "delivery_sites": ("graphql_account", "UserSites", "userSites"),
    "user_profile": ("graphql_account", "Me", "me"),
    "month_summary": ("graphql_account", "MonthSummary", "monthSummary"),
    "invoices": ("graphql_account", "Invoices", "invoices"),
    "daily_usage": (
        "graphql_account",
        "PeriodUsageAndCosts",
        "periodUsageAndCosts",
    ),
    "month_insights_current": (
        "graphql_account",
        "MonthInsights",
        "monthInsights",
    ),
    "month_insights_previous": (
        "graphql_account",
        "MonthInsights",
        "monthInsights",
    ),
}


# Optional provider services are reported when queried, but their absence is
# not a failed check: a Belgian tariff customer need not have Frank-controlled
# batteries, chargers, vehicles, PV, smart feed-in or HVAC.
_OPTIONAL_SOURCES: dict[str, tuple[str, str, str]] = {
    "smart_batteries": ("graphql_account", "SmartBatteries", "smartBatteries"),
    "enode_chargers": ("graphql_account", "EnodeChargers", "enodeChargers"),
    "enode_vehicles": ("graphql_account", "EnodeVehicles", "enodeVehicles"),
    "smart_pv_systems": ("graphql_account", "SmartPvSystems", "smartPvSystems"),
    "smart_feed_in": ("graphql_account", "UserSmartFeedIn", "userSmartFeedIn"),
    "smart_hvac": ("graphql_account", "SmartHvacStatus", "me"),
}


def _source_check(
    events: list[dict[str, Any]],
    source: str,
    operation: str,
    root: str,
    period: str | None = None,
) -> dict[str, object]:
    """Report most recent matching request, without falsely promoting errors."""
    for event in reversed(events):
        if (
            event.get("source") != source
            or event.get("operation") != operation
            or (period is not None and event.get("period") != period)
        ):
            continue
        outcome = event.get("outcome")
        roots = event.get("response_roots")
        root_state = roots.get(root) if isinstance(roots, dict) else None
        if outcome != "ok":
            state = "request_failed"
        elif root_state == "present":
            state = "source_present"
        elif root_state in ("null", "empty"):
            state = f"source_{root_state}"
        else:
            state = "source_not_in_response"

        return {
            "state": state,
            "last_request_utc": event.get("at_utc"),
            "last_request_outcome": outcome,
            # Source-present means the JSON root exists, not fresh readings.
            "complete_data_verified": False,
        }
    return {
        # The observer has finite retention; absence is not proof that no
        # request has occurred since startup if earlier entries were evicted.
        "state": "not_observed_in_retained_trace",
        "last_request_utc": None,
        "last_request_outcome": None,
        "complete_data_verified": False,
    }


def source_check_evidence(
    trace: object | None,
    *,
    authenticated: bool,
    site_selected: bool,
    monthly_finance_enabled: bool,
    local_today: date,
    price_cache_present: bool = False,
) -> dict[str, object]:
    """Provide a fixed supplier-source checklist from bounded request evidence."""
    snapshot = (
        trace.snapshot()
        if trace is not None and callable(getattr(trace, "snapshot", None))
        else {}
    )
    raw_events = snapshot.get("events") if isinstance(snapshot, dict) else None
    events: list[dict[str, Any]] = (
        [item for item in raw_events if isinstance(item, dict)]
        if isinstance(raw_events, list)
        else []
    )
    current, previous = month_keys(local_today)
    periods = {
        "month_insights_current": current,
        "month_insights_previous": previous,
    }
    result: dict[str, dict[str, object]] = {}
    for key, (source, operation, root) in _SOURCES.items():
        period = periods.get(key)
        if key == "public_market_prices":
            skip_reason = None
        elif not authenticated:
            skip_reason = "not_applicable_anonymous"
        elif not site_selected and key != "delivery_sites":
            skip_reason = "not_applicable_no_selected_site"
        elif key in periods and not monthly_finance_enabled:
            skip_reason = "disabled_by_option"
        else:
            skip_reason = None
        if skip_reason is not None:
            check = {
                "state": skip_reason,
                "last_request_utc": None,
                "last_request_outcome": None,
                "complete_data_verified": False,
            }
        else:
            check = _source_check(events, source, operation, root, period)
            if (
                price_cache_present
                and key in ("public_market_prices", "customer_prices")
                and check["state"] == "not_observed_in_retained_trace"
            ):
                check["state"] = "cached_price_state_without_source_check"
        result[key] = {
            **check,
            "expected": skip_reason is None,
            "requested_period": period,
        }

    for key, (source, operation, root) in _OPTIONAL_SOURCES.items():
        if not authenticated:
            check = {
                "state": "not_applicable_anonymous",
                "last_request_utc": None,
                "last_request_outcome": None,
                "complete_data_verified": False,
            }
        else:
            check = _source_check(events, source, operation, root)
            if check["state"] == "not_observed_in_retained_trace":
                check["state"] = "optional_not_observed"
        result[key] = {
            **check,
            "expected": False,
            "optional_provider_service": True,
            "requested_period": None,
        }

    return {
        "basis": "bounded_provider_graphql_requests_since_startup",
        "retained_events": len(events),
        "retention_note": (
            "Only last 96 observer events; not observed does not prove never checked. "
            "Source presence does not prove fresh or complete data."
        ),
        "checks": result,
    }
