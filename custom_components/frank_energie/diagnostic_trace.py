"""Bounded observability of actual Frank Energie GraphQL POST calls.

Captures only structured metadata at the pinned client's _query boundary:
operation names, duration, response presence, array sizes, safe HTTP failure
status and exception class. Never retain query variables, URLs, headers,
tokens, response bodies, GraphQL error strings, readings or monetary values.

No extra requests, debug-log activation, disk writes or persisted credentials.
"""

from __future__ import annotations

import re
import time
from collections import deque
from datetime import UTC, date, datetime
from typing import Any, Final

from aiohttp import ClientResponseError

_MAX_EVENTS: Final[int] = 96
_SAFE_OPERATION = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,47}\Z")
_RESPONSE_ROOTS: Final[frozenset[str]] = frozenset(
    {
        "marketPrices",
        "customerMarketPrices",
        "monthSummary",
        "monthInsights",
        "invoices",
        "periodUsageAndCosts",
        "user",
        "me",
        "userSites",
        "deliverySites",
        "contractPriceResolutionState",
        "smartBatteries",
        "enodeChargers",
        "enodeVehicles",
        "smartPvSystems",
        "userSmartFeedIn",
    }
)
# Explicit allowlist only. GraphQL error messages and arbitrary extension values
# can contain user identifiers and must never enter diagnostics.
_GRAPHQL_ERROR_CODES: Final[dict[str, str]] = {
    "GRAPHQL_VALIDATION_FAILED": "schema_validation",
    "BAD_USER_INPUT": "invalid_request",
    "UNAUTHENTICATED": "authentication",
    "FORBIDDEN": "authorization",
    "INTERNAL_SERVER_ERROR": "provider_internal",
    "SERVICE_UNAVAILABLE": "provider_unavailable",
    "TIMEOUT": "provider_timeout",
    "RATE_LIMITED": "rate_limited",
    "NOT_FOUND": "not_found",
}


def _graphql_error_classification(errors: object) -> dict[str, object]:
    """Classify provider errors without preserving arbitrary message contents.

    The returned categories identify likely investigation paths, not the
    underlying business cause. Unknown messages remain explicitly unknown.
    """
    if not isinstance(errors, list) or not errors:
        return {}
    categories: set[str] = set()
    roots: set[str] = set()
    for item in errors:
        if not isinstance(item, dict):
            categories.add("unclassified")
            continue
        message = item.get("message")
        extension = item.get("extensions")
        code = extension.get("code") if isinstance(extension, dict) else None
        category = _GRAPHQL_ERROR_CODES.get(code) if isinstance(code, str) else None
        if category is None:
            if message == "Graphql validation error" or (
                isinstance(message, str)
                and message.startswith(("Cannot query field ", "Unknown type "))
            ):
                category = "schema_validation"
            elif isinstance(message, str) and message.startswith(
                "Cannot return null for non-nullable field "
            ):
                category = "non_nullable_field"
            elif isinstance(message, str) and message.startswith(
                "No reading dates found for user"
            ):
                category = "readings_unavailable"
            elif isinstance(message, str) and message.startswith(
                "No marketprices found for segment"
            ):
                category = "market_prices_unavailable"
            elif message == "request-error:request-not-supported-in-country":
                category = "unsupported_country"
            elif message in (
                "user-error:auth-not-authorised",
                "user-error:auth-required",
                "user-error:refresh-token-invalid",
            ):
                category = "authentication"
            else:
                category = "unclassified"
        categories.add(category)
        path = item.get("path")
        if (
            isinstance(path, list)
            and path
            and isinstance(path[0], str)
            and path[0] in _RESPONSE_ROOTS
        ):
            roots.add(path[0])
    return {
        "graphql_error_categories": sorted(categories),
        "graphql_error_roots": sorted(roots),
    }


_SOURCES: Final[frozenset[str]] = frozenset(
    {
        "graphql_account",
        "graphql_public",
        "public_market",
        "customer_tariff",
        "price_selection",
        "month_summary",
        "invoices",
        "period_usage",
        "month_insights",
        "scheduler",
    }
)
_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        "ok",
        "empty_response",
        "graphql_errors",
        "exception",
        "present",
        "empty",
        "not_published",
        "skipped_unauthenticated",
        "skipped_missing_site",
        "api_error",
        "network_error",
        "auth_error",
        "public_fallback",
        "customer",
        "mixed",
        "public",
        "cached_fallback",
        "not_available",
        "recovery_requested",
        "refresh_ok",
        "refresh_incomplete",
        "refresh_failed",
        "skipped_maintenance",
    }
)


def _safe_period(value: object) -> str | None:
    """Validate a date/month, never retain arbitrary GraphQL variables."""
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    try:
        if len(value) == 7:
            date.fromisoformat(value + "-01")
            return value
        if len(value) == 10:
            date.fromisoformat(value)
            return value
    except ValueError:
        pass
    return None


def _finite_numeric_state(container: dict[str, Any], field: str) -> str:
    """Reveal only missing/null/zero/sign, never source numeric amounts."""
    if field not in container:
        return "missing"
    value = container[field]
    if value is None:
        return "null"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 0:
            return "zero"
        if value > 0:
            return "positive"
        if value < 0:
            return "negative"
    return "other"


def _finance_field_states(root: object) -> dict[str, str]:
    """Probe raw MonthInsights presence before the client coerces null to 0."""
    if not isinstance(root, dict):
        return {}
    fields = (
        "expectedCosts",
        "expectedCostsGas",
        "expectedCostsFixed",
        "expectedCostsElectricity",
        "expectedCostsFeedIn",
        "expectedCostsUntilLastMeterReading",
        "actualCostsUntilLastMeterReading",
        "meterReadingDayCompleteness",
    )
    states = {field: _finite_numeric_state(root, field) for field in fields}
    for group in ("gasDifference", "electricityDifference", "feedInDifference"):
        section = root.get(group)
        if isinstance(section, dict):
            for field in (
                "actualUsage",
                "actualCosts",
                "expectedUsage",
                "expectedCosts",
            ):
                states[f"{group}.{field}"] = _finite_numeric_state(section, field)
        else:
            states[group] = "null" if group in root else "missing"
    states["lastMeterReadingDate"] = (
        "missing"
        if "lastMeterReadingDate" not in root
        else "null"
        if root["lastMeterReadingDate"] is None
        else "present"
    )
    return states


def _response_metadata(response: object) -> dict[str, object]:
    """Extract only recognized GraphQL response topology and price list counts."""
    if not isinstance(response, dict):
        return {"response_kind": "invalid"}
    if not response:
        return {"response_kind": "empty"}

    result: dict[str, object] = {"response_kind": "graphql"}
    errors = response.get("errors")
    result["graphql_error_count"] = (
        len(errors) if isinstance(errors, list) else (1 if errors else 0)
    )
    result.update(_graphql_error_classification(errors))
    data = response.get("data")
    if not isinstance(data, dict):
        result["data_present"] = False
        return result

    result["data_present"] = True
    roots: dict[str, str] = {}
    for key, value in data.items():
        if key not in _RESPONSE_ROOTS:
            continue
        roots[key] = (
            "null"
            if value is None
            else "empty"
            if value == {} or value == []
            else "present"
        )
        if key == "monthInsights":
            result["month_insights_source_states"] = _finance_field_states(value)
        elif key == "monthSummary" and isinstance(value, dict):
            result["month_summary_reading_date_reported"] = bool(
                value.get("lastMeterReadingDate")
            )
        if key in ("marketPrices", "customerMarketPrices") and isinstance(value, dict):
            for segment, field_name in (
                ("electricity", "electricityPrices"),
                ("gas", "gasPrices"),
            ):
                segment_values = value.get(field_name)
                if isinstance(segment_values, list):
                    result[f"response_{segment}_slots"] = len(segment_values)
    result["response_roots"] = roots
    return result


def _safe_http_error_status(error: BaseException) -> int | None:
    """Recover HTTP error status from aiohttp chained cause, when exposed."""
    current: BaseException | None = error
    for _ in range(4):
        if current is None:
            break
        if isinstance(current, ClientResponseError):
            return current.status if 100 <= current.status <= 599 else None
        current = current.__cause__
    return None


class DiagnosticTrace:
    """Bounded in-memory, redacted request and source outcome records."""

    def __init__(self) -> None:
        self._events: deque[dict[str, object]] = deque(maxlen=_MAX_EVENTS)

    def record(
        self,
        source: str,
        outcome: str,
        *,
        operation: str | None = None,
        period: date | str | None = None,
        duration_ms: int | None = None,
        electricity_slots: int | None = None,
        gas_slots: int | None = None,
        error: BaseException | None = None,
        response: object = None,
    ) -> None:
        """Record only whitelisted metadata (not user-controlled content)."""
        if source not in _SOURCES or outcome not in _OUTCOMES:
            raise ValueError("Unknown diagnostic evidence category")

        event: dict[str, object] = {
            "at_utc": datetime.now(UTC).isoformat(),
            "source": source,
            "outcome": outcome,
        }
        if operation is not None:
            event["operation"] = (
                operation
                if isinstance(operation, str) and _SAFE_OPERATION.fullmatch(operation)
                else "unknown"
            )
        if (safe := _safe_period(period)) is not None:
            event["period"] = safe
        for key, value in (
            ("duration_ms", duration_ms),
            ("electricity_slots", electricity_slots),
            ("gas_slots", gas_slots),
        ):
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                event[key] = value
        if error is not None:
            # Exception message may embed entire requests, response bodies or tokens.
            event["error_class"] = type(error).__name__
            if (status := _safe_http_error_status(error)) is not None:
                event["http_error_status"] = status
        if response is not None:
            event.update(_response_metadata(response))
        self._events.append(event)

    def source_states_for_month(self, month: str) -> dict[str, str] | None:
        """Return only the latest matching raw-field evidence for that month.

        Never fall back to a different period or an older successful response
        after a failed request. Monetary values and identifiers are not saved.
        """
        if _safe_period(month) != month or len(month) != 7:
            return None
        for event in reversed(self._events):
            if (
                event.get("source") != "graphql_account"
                or event.get("operation") != "MonthInsights"
                or event.get("period") != month
            ):
                continue
            roots = event.get("response_roots")
            if (
                event.get("outcome") != "ok"
                or not isinstance(roots, dict)
                or roots.get("monthInsights") != "present"
            ):
                return None
            states = event.get("month_insights_source_states")
            return dict(states) if isinstance(states, dict) else None
        return None

    def snapshot(self) -> dict[str, object]:
        """Detached recent history and latest outcome per operation/source."""
        events = [dict(event) for event in self._events]
        latest: dict[str, dict[str, object]] = {}
        for event in events:
            # GraphQL operation name is fixed by the API client, not user data.
            key = str(event["source"]) + ":" + str(event.get("operation", "summary"))
            latest[key] = event
        return {
            "retention": "last_96_events_in_memory_since_restart",
            "events": events,
            "latest_by_source_operation": latest,
        }


def attach_graphql_trace(api: Any, trace: DiagnosticTrace, source: str) -> None:
    """Observe real client GraphQL _query calls without changing their result.

    Only installs on clients instantiated by this integration. The pinned
    python-frank-energie client calls _query for network-bound operations.
    The original coroutine remains responsible for HTTP and auth behavior.
    """
    if source not in ("graphql_account", "graphql_public"):
        raise ValueError("Invalid GraphQL trace source")
    if getattr(api, "_frank_observer_attached", False):
        return

    original_query = api._query

    async def observed_query(query: Any, extra_headers: Any = None) -> Any:
        operation = getattr(query, "operation_name", None)
        variables = getattr(query, "variables", None)
        period = variables.get("date") if isinstance(variables, dict) else None
        start = time.monotonic()
        try:
            response = await original_query(query, extra_headers=extra_headers)
        except Exception as error:
            # The collector must not mask or change the original API error.
            try:
                trace.record(
                    source,
                    "exception",
                    operation=operation,
                    period=period,
                    duration_ms=round((time.monotonic() - start) * 1000),
                    error=error,
                )
            except Exception:
                pass
            raise

        # Neither malformed provider responses nor a future telemetry bug
        # may prevent the actual client from returning its result unchanged.
        try:
            metadata = _response_metadata(response)
            trace.record(
                source,
                (
                    "empty_response"
                    if metadata.get("response_kind") == "empty"
                    else "graphql_errors"
                    if metadata.get("graphql_error_count", 0)
                    else "ok"
                ),
                operation=operation,
                period=period,
                duration_ms=round((time.monotonic() - start) * 1000),
                response=response,
            )
        except Exception:
            pass
        return response

    api._query = observed_query
    api._frank_observer_attached = True
    api._frank_diagnostic_trace = trace
