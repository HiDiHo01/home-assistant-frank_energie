"""Actual Frank Energie GraphQL request observability without customer data."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.frank_energie.diagnostic_trace import (
    DiagnosticTrace,
    _response_metadata,
    attach_graphql_trace,
)


def test_month_insights_raw_null_zero_distinction_without_finance_values() -> None:
    """Source null-vs-zero evidence survives before upstream parser coercion."""
    response = {
        "data": {
            "monthInsights": {
                "expectedCosts": None,
                "expectedCostsGas": 0,
                "expectedCostsElectricity": 481516.234,
                "gasDifference": {"actualUsage": None, "actualCosts": 0},
                "electricityDifference": {"actualUsage": 9.8},
                "lastMeterReadingDate": None,
            }
        }
    }
    result = _response_metadata(response)
    states = result["month_insights_source_states"]
    assert states["expectedCosts"] == "null"
    assert states["expectedCostsGas"] == "zero"
    assert states["expectedCostsElectricity"] == "positive"
    assert states["expectedCostsFeedIn"] == "missing"
    assert states["gasDifference.actualUsage"] == "null"
    assert states["gasDifference.actualCosts"] == "zero"
    assert states["electricityDifference.expectedUsage"] == "missing"
    assert states["lastMeterReadingDate"] == "null"
    assert "481516" not in str(result)
    assert "9.8" not in str(result)


def test_response_metadata_distinguishes_null_empty_and_present_price_arrays() -> None:
    """Observe raw GraphQL data before the provider parser can discard fields."""
    response = {
        "data": {
            "customerMarketPrices": {
                "electricityPrices": [{"allInPrice": 123456789, "id": "private"}],
                "gasPrices": [],
            },
            "monthSummary": None,
        },
        "errors": [{"message": "private-error-id-1234"}],
    }
    result = _response_metadata(response)
    assert result["data_present"] is True
    assert result["graphql_error_count"] == 1
    assert result["response_roots"]["monthSummary"] == "null"
    assert result["response_roots"]["customerMarketPrices"] == "present"
    assert result["response_electricity_slots"] == 1
    assert result["response_gas_slots"] == 0
    assert "123456789" not in str(result)
    assert "private-error-id-1234" not in str(result)


@pytest.mark.asyncio
async def test_observer_records_real_query_and_never_stores_payload() -> None:
    """Single GraphQL call, original result identity, no private variables."""
    calls = []
    response = {
        "data": {
            "marketPrices": {
                "electricityPrices": [{"marketPrice": 876543210}],
                "gasPrices": [],
            }
        }
    }

    async def original_query(query, extra_headers=None):
        calls.append((query, extra_headers))
        return response

    client = SimpleNamespace(_query=original_query)
    trace = DiagnosticTrace()
    attach_graphql_trace(client, trace, "graphql_public")
    attach_graphql_trace(client, trace, "graphql_public")  # idempotent

    query = SimpleNamespace(
        operation_name="MarketPrices",
        variables={"date": "2026-10-31", "siteReference": "sensitive-site"},
    )
    result = await client._query(
        query, extra_headers={"Authorization": "private-token"}
    )
    assert result is response
    assert len(calls) == 1

    snapshot = trace.snapshot()
    events = snapshot["events"]
    assert len(events) == 1
    assert events[0]["operation"] == "MarketPrices"
    assert events[0]["source"] == "graphql_public"
    assert events[0]["period"] == "2026-10-31"
    assert events[0]["outcome"] == "ok"
    assert events[0]["response_electricity_slots"] == 1
    assert events[0]["response_gas_slots"] == 0
    assert isinstance(events[0]["duration_ms"], int)
    assert events[0]["duration_ms"] >= 0
    for sensitive in ("sensitive-site", "private-token", "876543210"):
        assert sensitive not in str(snapshot)


@pytest.mark.asyncio
async def test_observer_captures_graphql_errors_and_reraises_network_failure() -> None:
    """Preserve vendor exception semantics, record only error type and counts."""
    trace = DiagnosticTrace()

    async def graphql_errors(query, extra_headers=None):
        return {"data": {"monthSummary": None}, "errors": [{"message": "private"}]}

    client = SimpleNamespace(_query=graphql_errors)
    attach_graphql_trace(client, trace, "graphql_account")
    query = SimpleNamespace(
        operation_name="MonthSummary", variables={"siteReference": "private"}
    )
    await client._query(query)
    assert trace.snapshot()["events"][0]["outcome"] == "graphql_errors"

    async def broken(query, extra_headers=None):
        raise RuntimeError("token=private-token account=secret")

    broken_client = SimpleNamespace(_query=broken)
    attach_graphql_trace(broken_client, trace, "graphql_account")
    with pytest.raises(RuntimeError, match="private-token"):
        await broken_client._query(query)
    event = trace.snapshot()["events"][-1]
    assert event["outcome"] == "exception"
    assert event["error_class"] == "RuntimeError"
    assert "private-token" not in str(trace.snapshot())


def test_price_observer_never_breaks_incomplete_provider_return_objects() -> None:
    """Telemetry must accept partial, unexpected, or sentinel-like API values."""
    from unittest.mock import sentinel

    from custom_components.frank_energie.coordinator import _price_counts

    assert _price_counts(None) == (0, 0)
    assert _price_counts(sentinel.unparsed_api_response) == (0, 0)
    assert _price_counts(SimpleNamespace(electricity=None, gas=None)) == (0, 0)
    assert _price_counts(
        SimpleNamespace(
            electricity=SimpleNamespace(all=[object(), object()]),
            gas=SimpleNamespace(all=[]),
        )
    ) == (2, 0)


def test_graphql_trace_stays_bounded_and_rejects_untrusted_operation_names() -> None:
    """Recent history is capped; operation metadata cannot be arbitrary text."""
    trace = DiagnosticTrace()
    for _ in range(160):
        trace.record(
            "graphql_account",
            "ok",
            operation="MarketPrices",
            response={"data": {"marketPrices": {}}},
        )
    events = trace.snapshot()["events"]
    assert len(events) == 96
    trace.record(
        "graphql_account",
        "ok",
        operation="private-id/secret:123",
        period="private-customer",
    )
    event = trace.snapshot()["events"][-1]
    assert event["operation"] == "unknown"
    assert "period" not in event
    assert "private-id/secret:123" not in str(trace.snapshot())


def test_month_source_states_are_not_confused_by_parallel_periods_or_failures():
    trace = DiagnosticTrace()

    def response(gas):
        return {
            "data": {
                "monthInsights": {
                    "lastMeterReadingDate": "2026-10-30T00:00:00Z",
                    "gasDifference": {"actualCosts": gas},
                }
            }
        }

    trace.record(
        "graphql_account",
        "ok",
        operation="MonthInsights",
        period="2026-10",
        response=response(0.0),
    )
    trace.record(
        "graphql_account",
        "ok",
        operation="MonthInsights",
        period="2026-11",
        response=response(99.0),
    )
    assert (
        trace.source_states_for_month("2026-10")["gasDifference.actualCosts"] == "zero"
    )
    assert (
        trace.source_states_for_month("2026-11")["gasDifference.actualCosts"]
        == "positive"
    )
    assert trace.source_states_for_month("2026-09") is None
    assert trace.source_states_for_month("secret-customer") is None

    trace.record(
        "graphql_account",
        "exception",
        operation="MonthInsights",
        period="2026-10",
        error=RuntimeError("PRIVATE-ACCOUNT"),
    )
    assert trace.source_states_for_month("2026-10") is None
    assert trace.source_states_for_month("2026-11") is not None
    assert "PRIVATE-ACCOUNT" not in str(trace.snapshot())


def test_optional_smart_service_roots_keep_only_presence_metadata() -> None:
    from custom_components.frank_energie.diagnostic_trace import _response_metadata

    metadata = _response_metadata(
        {"data": {"enodeChargers": [], "smartPvSystems": [{"id": "SECRET"}]}}
    )
    assert metadata["response_roots"]["enodeChargers"] == "empty"
    assert metadata["response_roots"]["smartPvSystems"] == "present"
    assert "SECRET" not in str(metadata)


def test_graphql_error_categories_are_useful_but_never_expose_messages() -> None:
    """An actual GraphQL null/error carries safe cause and field metadata."""
    response = {
        "data": {"monthInsights": None},
        "errors": [
            {
                "message": "Cannot return null for non-nullable field Account.private-id",
                "path": ["monthInsights", "secret-id-999", "expectedCosts"],
                "extensions": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "account_id": "private-123",
                },
            },
            {
                "message": "Sensitive Graphql details, user=private123",
                "path": ["unknown_secret_field"],
                "extensions": {"code": "SECRET-PRIVATE-123"},
            },
        ],
    }
    result = _response_metadata(response)
    assert result["graphql_error_count"] == 2
    assert result["graphql_error_categories"] == [
        "provider_internal",
        "unclassified",
    ]
    assert result["graphql_error_roots"] == ["monthInsights"]
    assert result["response_roots"] == {"monthInsights": "null"}
    for secret in ("secret-id", "private-123", "PRIVATE-123"):
        assert secret not in str(result)


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("Graphql validation error", "schema_validation"),
        ("Cannot query field 'private' on type Query", "schema_validation"),
        ("Cannot return null for non-nullable field X", "non_nullable_field"),
        ("No reading dates found for user SECRET", "readings_unavailable"),
        ("request-error:request-not-supported-in-country", "unsupported_country"),
        ("Private arbitrary upstream traceback", "unclassified"),
    ],
)
def test_graphql_error_message_matching_is_whitelisted(
    message: str, category: str
) -> None:
    result = _response_metadata(
        {
            "data": {"monthInsights": None},
            "errors": [{"message": message, "path": ["monthInsights"]}],
        }
    )
    assert result["graphql_error_categories"] == [category]
    assert result["graphql_error_roots"] == ["monthInsights"]
    assert message not in str(result)


@pytest.mark.asyncio
async def test_graphql_error_classification_survives_real_observer() -> None:
    """The observer never sends a probe or changes the returned response."""
    response = {
        "data": {"monthInsights": None},
        "errors": [
            {
                "message": "Graphql validation error",
                "path": ["monthInsights"],
                "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"},
            }
        ],
    }

    async def original_query(query, extra_headers=None):
        return response

    client = SimpleNamespace(_query=original_query)
    trace = DiagnosticTrace()
    attach_graphql_trace(client, trace, "graphql_account")
    query = SimpleNamespace(
        operation_name="MonthInsights",
        variables={"date": "2026-11", "siteReference": "SECRET-ACCOUNT"},
    )
    assert await client._query(query) is response
    event = trace.snapshot()["events"][0]
    assert event["outcome"] == "graphql_errors"
    assert event["graphql_error_categories"] == ["schema_validation"]
    assert event["graphql_error_roots"] == ["monthInsights"]
    assert event["period"] == "2026-11"
    assert "SECRET-ACCOUNT" not in str(trace.snapshot())
