"""Conservative, source-backed calendar-month finance readings.

Frank's pinned MonthInsights parser silently coerces absent difference amounts
to 0.0. The GraphQL observer therefore checks raw field presence before a
parsed zero can be published as a verified monthly actual; without such
evidence, missing data and a genuine zero cannot be distinguished.
This module is intentionally free of Home Assistant and endpoint concerns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from python_frank_energie.models import MonthInsights

_AMSTERDAM = ZoneInfo("Europe/Amsterdam")


@dataclass(frozen=True, slots=True)
class MonthlyFinanceSnapshot:
    """Period attached to the API result so midnight cannot relabel old data."""

    month: str
    insights: MonthInsights
    retrieved_at: datetime
    # Raw field presence captured before the pinned parser replaces null with 0.
    source_states: dict[str, str] | None = None
    # Selected-site contract truth; prevents presenting electricity-only totals
    # as complete on a contracted gas supply when gas is excluded by provider.
    gas_expected: bool | None = None
    electricity_expected: bool | None = None


def month_keys(today: date) -> tuple[str, str]:
    """Current and last *calendar* month, including the January boundary."""
    first = today.replace(day=1)
    return first.strftime("%Y-%m"), (first - timedelta(days=1)).strftime("%Y-%m")


def month_bounds(month: str) -> tuple[date, date] | None:
    """Return [start, exclusive end) for a trusted YYYY-MM month."""
    try:
        start = date.fromisoformat(f"{month}-01")
        if start.strftime("%Y-%m") != month:
            return None
        end = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
    except (TypeError, ValueError):
        return None
    return start, end


# Each numeric sensor has a *single* source field and a fixed known unit.
# For BE gas the Frank API may report kWh rather than m3: both are separate
# sensors, never silently converted without the calorific conversion factor.
_FIELDS: dict[str, tuple[str | None, str, str | None]] = {
    "electricity_costs": ("electricityDifference", "actualCosts", None),
    "electricity_kwh": ("electricityDifference", "actualUsage", "kwh"),
    "gas_costs": ("gasDifference", "actualCosts", None),
    "gas_kwh": ("gasDifference", "actualUsage", "kwh"),
    "gas_m3": ("gasDifference", "actualUsage", "m3"),
    "feed_in_costs": ("feedInDifference", "actualCosts", None),
    "feed_in_kwh": ("feedInDifference", "actualUsage", "kwh"),
    "actual_total": (None, "actualCostsUntilLastMeterReading", None),
    "expected_to_reading": (None, "expectedCostsUntilLastMeterReading", None),
    "expected_total": (None, "expectedCosts", None),
    "expected_electricity": (None, "expectedCostsElectricity", None),
    "expected_gas": (None, "expectedCostsGas", None),
    "expected_fixed": (None, "expectedCostsFixed", None),
    "expected_feed_in": (None, "expectedCostsFeedIn", None),
}


def finance_reading(
    snapshot: MonthlyFinanceSnapshot | None, metric: str
) -> tuple[float | None, str]:
    """Return (value, quality) with a fail-closed source contract."""
    if metric not in _FIELDS:
        raise ValueError(f"Unknown finance metric: {metric}")
    if snapshot is None:
        return None, "not_published"

    bounds = month_bounds(snapshot.month)
    if bounds is None:
        return None, "invalid_period"
    insights = snapshot.insights
    # Full-month forecasts are tied to the requested period, not the
    # last meter reading. Actual/cutoff-based values still require a
    # plausible meter date, including a first-next-month final reading.
    forecast_metric = metric.startswith("expected_") and (
        metric != "expected_to_reading"
    )
    if not forecast_metric:
        meter_date = getattr(insights, "lastMeterReadingDate", None)
        if not isinstance(meter_date, datetime) or meter_date.tzinfo is None:
            return None, "missing_meter_date"
        start, end = bounds
        local_date = meter_date.astimezone(_AMSTERDAM).date()
        if not start <= local_date <= end:
            return None, "reading_outside_period"

    difference_name, field_name, required_unit = _FIELDS[metric]
    gas_excluded = bool(getattr(insights, "gasExcluded", False))
    if (
        gas_excluded
        and snapshot.gas_expected is True
        and metric
        in (
            "actual_total",
            "expected_to_reading",
            "expected_total",
        )
    ):
        return None, "gas_excluded_from_total"
    if (metric.startswith("gas_") or metric == "expected_gas") and gas_excluded:
        return None, "gas_excluded"

    if metric in ("actual_total", "expected_to_reading", "expected_total"):
        # A provider top-level total is not trustworthy as an electricity +
        # gas cost if a contracted segment's financial contribution is absent.
        # Requiring raw segment presence also protects against zero-fill in
        # the pinned parser. Do not attempt to add segment charges: tariffs,
        # fixed amounts and signed feed-in have independent accounting.
        for segment, contracted in (
            ("electricity", snapshot.electricity_expected),
            ("gas", snapshot.gas_expected),
        ):
            if contracted is not True:
                continue
            if snapshot.source_states is None:
                return None, f"{segment}_component_unverified"
            fields = (
                (f"expectedCosts{segment.capitalize()}",)
                if metric == "expected_total"
                else (
                    f"{segment}Difference.actualCosts",
                    f"{segment}Difference.actualUsage",
                )
            )
            if any(
                snapshot.source_states.get(field)
                not in ("zero", "positive", "negative")
                for field in fields
            ):
                return None, f"{segment}_component_missing"

    block = (
        insights
        if difference_name is None
        else getattr(insights, difference_name, None)
    )
    if block is None:
        return None, "missing_segment"

    if required_unit is not None:
        actual_unit = getattr(block, "unit", "")
        normalized = str(actual_unit).strip().lower().replace("³", "3")
        if normalized not in (
            {required_unit, "m^3"} if required_unit == "m3" else {required_unit}
        ):
            return None, "unit_mismatch"

    raw = getattr(block, field_name, None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, "missing_value"
    number = float(raw)
    if not math.isfinite(number):
        return None, "invalid_value"

    # The pinned client maps absent numeric fields to 0.0. Inspect the raw
    # field presence (captured by the existing, redacted GraphQL observer)
    # before accepting either a zero or a nonzero result.
    source_field = f"{difference_name}.{field_name}" if difference_name else field_name
    states = snapshot.source_states
    if states is not None:
        source_state = states.get(source_field)
        if source_state is None:
            return None, "source_field_unverified"
        if source_state in ("missing", "null"):
            return None, "source_field_missing"
        if source_state not in ("zero", "positive", "negative"):
            return None, "source_field_invalid"
        parsed_sign = (
            "zero" if number == 0 else "positive" if number > 0 else "negative"
        )
        if parsed_sign != source_state:
            return None, "source_parser_mismatch"
    elif number == 0:
        # No raw source evidence: this might be an actual zero or missing data.
        return None, "zero_unverified_by_client"
    return number, "available"


def finance_attributes(
    snapshot: MonthlyFinanceSnapshot | None, metric: str
) -> dict[str, object]:
    """Keep provenance and quality, never site references or account data."""
    value, quality = finance_reading(snapshot, metric)
    attrs: dict[str, object] = {
        "source": "Frank Energie monthInsights",
        "quality": quality,
        "zero_policy": "allow_only_source_verified_zero",
        "cost_scope": "provider_month_insights_not_reconciled_invoice",
        "raw_field_presence_verified": snapshot is not None
        and snapshot.source_states is not None,
    }
    if snapshot is None:
        return attrs
    bounds = month_bounds(snapshot.month)
    if bounds is not None:
        attrs["period_start"] = bounds[0].isoformat()
        attrs["period_end_exclusive"] = bounds[1].isoformat()
    attrs["period"] = snapshot.month
    attrs["retrieved_at"] = snapshot.retrieved_at.isoformat()
    meter_date = getattr(snapshot.insights, "lastMeterReadingDate", None)
    if isinstance(meter_date, datetime):
        attrs["last_meter_reading"] = meter_date.isoformat()
    completeness = getattr(snapshot.insights, "meterReadingDayCompleteness", None)
    if isinstance(completeness, (float, int)) and not isinstance(completeness, bool):
        if math.isfinite(completeness):
            attrs["meter_reading_day_completeness"] = completeness
    if metric.startswith("gas_"):
        attrs["gas_excluded"] = bool(getattr(snapshot.insights, "gasExcluded", False))
    if value is not None:
        attrs["value_status"] = "provider_reported"
    return attrs


def finance_period_readiness(snapshot: MonthlyFinanceSnapshot | None) -> str:
    """Classify provider actual component coverage, not full household costs."""
    if snapshot is None:
        return "not_published"
    electricity = snapshot.electricity_expected
    gas = snapshot.gas_expected
    if electricity is None or gas is None or not (electricity or gas):
        return "contract_unverified"
    if electricity and finance_reading(snapshot, "electricity_costs")[0] is None:
        return "electricity_missing"
    if gas and finance_reading(snapshot, "gas_costs")[0] is None:
        return "gas_missing"
    if finance_reading(snapshot, "actual_total")[0] is None:
        return "provider_total_missing"
    return "provider_components_present"


def finance_period_readiness_attributes(
    snapshot: MonthlyFinanceSnapshot | None,
) -> dict[str, object]:
    """Explain component gaps without leaking money or account identifiers."""
    attrs: dict[str, object] = {
        "scope": "provider_monthly_components_not_all_in_household_bill",
        "invoice_reconciled": False,
        "quality": finance_period_readiness(snapshot),
        "electricity_cost_quality": finance_reading(snapshot, "electricity_costs")[1],
        "gas_cost_quality": finance_reading(snapshot, "gas_costs")[1],
        "provider_total_quality": finance_reading(snapshot, "actual_total")[1],
        "expected_total_quality": finance_reading(snapshot, "expected_total")[1],
    }
    if snapshot is not None:
        attrs["period"] = snapshot.month
        attrs["retrieved_at"] = snapshot.retrieved_at.isoformat()
    return attrs


# Invoice amounts are deliberately excluded: payment and settlement periods
# need not match their underlying consumption calendar month.
_AUDIT_METRICS = tuple(_FIELDS)


def _meter_day(value: object) -> date | None:
    """Convert typed source dates to a Belgian local calendar day."""
    if isinstance(value, datetime):
        return value.astimezone(_AMSTERDAM).date() if value.tzinfo else None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            if len(value) == 10:
                return date.fromisoformat(value)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(_AMSTERDAM).date() if parsed.tzinfo else None
        except ValueError:
            return None
    return None


def finance_source_reconciliation(
    current: MonthlyFinanceSnapshot | None, month_summary: object
) -> str:
    """Compare two provider totals only when their meter cutoffs coincide.

    This is NOT invoice reconciliation, and it never sums estimated fixed
    charges or feed-in across unknown sign conventions.
    """
    if current is None or month_summary is None:
        return "not_comparable"
    current_total, _quality = finance_reading(current, "actual_total")
    summary_total = getattr(month_summary, "actualCostsUntilLastMeterReadingDate", None)
    if current_total is None:
        return "insights_actual_total_unavailable"
    if (
        isinstance(summary_total, bool)
        or not isinstance(summary_total, (float, int))
        or not math.isfinite(summary_total)
    ):
        return "month_summary_actual_total_unavailable"

    current_day = _meter_day(getattr(current.insights, "lastMeterReadingDate", None))
    summary_day = _meter_day(getattr(month_summary, "lastMeterReadingDate", None))
    if current_day is None or summary_day is None:
        return "meter_day_unverified"
    if current_day != summary_day:
        return "source_cutoff_mismatch"
    return (
        "matched_within_5_cents"
        if abs(current_total - summary_total) <= 0.05
        else "provider_totals_disagree"
    )


def finance_evidence(
    current: MonthlyFinanceSnapshot | None,
    previous: MonthlyFinanceSnapshot | None,
    month_summary: object,
) -> dict[str, object]:
    """Privacy-safe readiness map: provenance/status only, no amounts or IDs."""
    result: dict[str, object] = {
        "invoice_reconciliation_verified": False,
        "invoice_period_may_differ_from_consumption_month": True,
        "source_total_reconciliation": finance_source_reconciliation(
            current, month_summary
        ),
    }
    for label, snapshot in (("current", current), ("previous", previous)):
        metrics = {}
        for metric in _AUDIT_METRICS:
            _value, quality = finance_reading(snapshot, metric)
            metrics[metric] = quality
        result[label] = {
            "period": snapshot.month if snapshot is not None else None,
            "source_field_presence_verified": (
                snapshot is not None and snapshot.source_states is not None
            ),
            "metric_quality": metrics,
            "provider_cost_readiness": finance_period_readiness(snapshot),
        }
    return result
