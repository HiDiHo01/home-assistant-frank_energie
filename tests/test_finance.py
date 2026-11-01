"""Source-contract regression tests for finance sensors (no live account data)."""

from datetime import date, datetime, UTC
from types import SimpleNamespace

import pytest

from custom_components.frank_energie.finance import (
    MonthlyFinanceSnapshot,
    finance_attributes,
    finance_period_readiness,
    finance_period_readiness_attributes,
    finance_reading,
    month_bounds,
    month_keys,
)
from python_frank_energie.models import MonthInsights


def _snapshot(
    *,
    month="2026-10",
    electricity_cost=37.5,
    gas_cost=18.1,
    gas_unit="m3",
    gas_excluded=False,
    reading="2026-10-25T13:00:00+00:00",
):
    insights = SimpleNamespace(
        lastMeterReadingDate=datetime.fromisoformat(reading),
        meterReadingDayCompleteness=0.9,
        gasExcluded=gas_excluded,
        electricityDifference=SimpleNamespace(
            actualCosts=electricity_cost, actualUsage=128.5, unit="kWh"
        ),
        gasDifference=SimpleNamespace(
            actualCosts=gas_cost, actualUsage=19.5, unit=gas_unit
        ),
        feedInDifference=SimpleNamespace(
            actualCosts=-5.2, actualUsage=42.3, unit="kWh"
        ),
        actualCostsUntilLastMeterReading=61.2,
        expectedCostsUntilLastMeterReading=72.0,
        expectedCosts=80.0,
        expectedCostsElectricity=49.0,
        expectedCostsGas=22.0,
        expectedCostsFixed=9.0,
        expectedCostsFeedIn=-5.0,
    )
    return MonthlyFinanceSnapshot(
        month=month, insights=insights, retrieved_at=datetime(2026, 10, 26, tzinfo=UTC)
    )


def test_calendar_period_keys_handle_year_boundary():
    assert month_keys(date(2027, 1, 1)) == ("2027-01", "2026-12")
    assert month_bounds("2026-12") == (date(2026, 12, 1), date(2027, 1, 1))
    assert month_bounds("2026-11") == (date(2026, 11, 1), date(2026, 12, 1))
    assert month_bounds("2026-13") is None


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        ("electricity_costs", 37.5),
        ("electricity_kwh", 128.5),
        ("gas_costs", 18.1),
        ("gas_m3", 19.5),
        ("feed_in_costs", -5.2),
        ("feed_in_kwh", 42.3),
        ("actual_total", 61.2),
        ("expected_to_reading", 72.0),
        ("expected_total", 80.0),
        ("expected_electricity", 49.0),
        ("expected_gas", 22.0),
        ("expected_fixed", 9.0),
        ("expected_feed_in", -5.0),
    ],
)
def test_month_insights_fields_are_distinct(metric, expected):
    assert finance_reading(_snapshot(), metric) == (expected, "available")


def test_gas_unit_is_not_silently_converted():
    assert finance_reading(_snapshot(), "gas_kwh") == (None, "unit_mismatch")
    assert finance_reading(_snapshot(gas_unit="kWh"), "gas_kwh") == (
        19.5,
        "available",
    )
    assert finance_reading(_snapshot(gas_unit="kWh"), "gas_m3") == (
        None,
        "unit_mismatch",
    )


def test_missing_or_excluded_gas_never_zero_filled():
    assert finance_reading(_snapshot(gas_excluded=True), "gas_costs") == (
        None,
        "gas_excluded",
    )
    assert finance_reading(None, "gas_costs") == (None, "not_published")


def test_late_meter_reading_must_not_relabel_previous_period():
    assert finance_reading(_snapshot(month="2026-11"), "gas_costs") == (
        None,
        "reading_outside_period",
    )
    # A reading on the first day after the month closes is acceptable.
    assert finance_reading(
        _snapshot(month="2026-09", reading="2026-10-01T00:00:00+02:00"),
        "gas_costs",
    ) == (18.1, "available")


def test_projected_month_cost_does_not_require_first_month_meter_reading():
    snapshot = _snapshot(month="2026-11")
    # Source query explicitly requested November, but the latest meter reading
    # is still from October. Forecast may be valid; actual cannot be.
    assert finance_reading(snapshot, "expected_total") == (80.0, "available")
    assert finance_reading(snapshot, "expected_to_reading") == (
        None,
        "reading_outside_period",
    )
    assert finance_reading(snapshot, "actual_total") == (
        None,
        "reading_outside_period",
    )


def test_gas_exclusion_also_withholds_expected_gas_component():
    snapshot = _snapshot(gas_excluded=True)
    assert finance_reading(snapshot, "expected_gas") == (None, "gas_excluded")


def test_pinned_api_parser_coerces_missing_fields_to_zero_and_is_withheld():
    data = {
        "data": {
            "monthInsights": {
                "_id": "REDACTED",
                "lastMeterReadingDate": "2026-10-25T13:00:00+00:00",
                "gasDifference": {},
                "electricityDifference": {},
                "feedInDifference": {},
            }
        }
    }
    parsed = MonthInsights.from_dict(data)
    assert parsed is not None
    assert parsed.gasDifference.actualCosts == 0
    reading = MonthlyFinanceSnapshot(
        month="2026-10",
        insights=parsed,
        retrieved_at=datetime(2026, 10, 26, tzinfo=UTC),
    )
    assert finance_reading(reading, "gas_costs") == (
        None,
        "zero_unverified_by_client",
    )
    assert finance_reading(reading, "electricity_costs") == (
        None,
        "zero_unverified_by_client",
    )


def test_raw_source_zero_is_accepted_only_if_explicitly_present():
    snapshot = _snapshot(electricity_cost=0.0)
    verified = MonthlyFinanceSnapshot(
        month=snapshot.month,
        insights=snapshot.insights,
        retrieved_at=snapshot.retrieved_at,
        source_states={"electricityDifference.actualCosts": "zero"},
    )
    assert finance_reading(verified, "electricity_costs") == (0.0, "available")
    assert (
        finance_attributes(verified, "electricity_costs")["raw_field_presence_verified"]
        is True
    )


@pytest.mark.parametrize("state", ["missing", "null"])
def test_raw_source_missing_amount_is_never_interpreted_as_zero(state):
    snapshot = _snapshot(electricity_cost=0.0)
    verified = MonthlyFinanceSnapshot(
        month=snapshot.month,
        insights=snapshot.insights,
        retrieved_at=snapshot.retrieved_at,
        source_states={"electricityDifference.actualCosts": state},
    )
    assert finance_reading(verified, "electricity_costs") == (
        None,
        "source_field_missing",
    )


def test_raw_source_sign_mismatch_is_withheld():
    snapshot = _snapshot(electricity_cost=37.5)
    verified = MonthlyFinanceSnapshot(
        month=snapshot.month,
        insights=snapshot.insights,
        retrieved_at=snapshot.retrieved_at,
        source_states={"electricityDifference.actualCosts": "negative"},
    )
    assert finance_reading(verified, "electricity_costs") == (
        None,
        "source_parser_mismatch",
    )


def test_raw_source_state_must_cover_each_separate_finance_component():
    snapshot = _snapshot()
    verified = MonthlyFinanceSnapshot(
        month=snapshot.month,
        insights=snapshot.insights,
        retrieved_at=snapshot.retrieved_at,
        source_states={"electricityDifference.actualCosts": "positive"},
    )
    assert finance_reading(verified, "electricity_costs") == (37.5, "available")
    assert finance_reading(verified, "gas_costs") == (
        None,
        "source_field_unverified",
    )


def test_valid_zero_is_conservatively_withheld_until_client_contract_improves():
    assert finance_reading(_snapshot(electricity_cost=0.0), "electricity_costs") == (
        None,
        "zero_unverified_by_client",
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True])
def test_invalid_numeric_amounts_are_never_published(value):
    assert (
        finance_reading(_snapshot(electricity_cost=value), "electricity_costs")[0]
        is None
    )


def test_attributes_have_period_quality_and_no_site_or_invoice_identifiers():
    attrs = finance_attributes(_snapshot(), "gas_costs")
    assert attrs["period_start"] == "2026-10-01"
    assert attrs["period_end_exclusive"] == "2026-11-01"
    assert attrs["quality"] == "available"
    assert "site_reference" not in attrs
    assert "invoiceId" not in attrs
    assert "REDACTED" not in str(attrs)


def test_missing_snapshot_quality_is_explained():
    assert finance_attributes(None, "electricity_costs")["quality"] == "not_published"


def test_unknown_finance_metric_rejected():
    with pytest.raises(ValueError):
        finance_reading(_snapshot(), "invented_capacity_tariff")


def test_month_summary_total_reconciles_only_with_same_meter_cutoff():
    from custom_components.frank_energie.finance import finance_source_reconciliation

    snapshot = _snapshot()
    matching = SimpleNamespace(
        actualCostsUntilLastMeterReadingDate=61.2,
        lastMeterReadingDate="2026-10-25",
    )
    assert finance_source_reconciliation(snapshot, matching) == (
        "matched_within_5_cents"
    )
    assert (
        finance_source_reconciliation(
            snapshot,
            SimpleNamespace(
                actualCostsUntilLastMeterReadingDate=61.3,
                lastMeterReadingDate="2026-10-25",
            ),
        )
        == "provider_totals_disagree"
    )
    assert (
        finance_source_reconciliation(
            snapshot,
            SimpleNamespace(
                actualCostsUntilLastMeterReadingDate=61.2,
                lastMeterReadingDate="2026-10-24",
            ),
        )
        == "source_cutoff_mismatch"
    )
    assert finance_source_reconciliation(None, matching) == "not_comparable"


def test_month_audit_only_returns_quality_not_billing_values_or_identifiers():
    from custom_components.frank_energie.finance import finance_evidence

    snapshot = _snapshot()
    audit = finance_evidence(
        snapshot,
        None,
        SimpleNamespace(
            actualCostsUntilLastMeterReadingDate=61.2,
            lastMeterReadingDate="2026-10-25",
        ),
    )
    assert audit["source_total_reconciliation"] == "matched_within_5_cents"
    assert audit["invoice_reconciliation_verified"] is False
    assert audit["current"]["metric_quality"]["gas_costs"] == "available"
    assert audit["previous"]["metric_quality"]["gas_costs"] == "not_published"
    assert "61.2" not in str(audit)
    assert "37.5" not in str(audit)


def test_contracted_gas_exclusion_prevents_partial_month_totals():
    original = _snapshot(gas_excluded=True)
    contracted = MonthlyFinanceSnapshot(
        month=original.month,
        insights=original.insights,
        retrieved_at=original.retrieved_at,
        gas_expected=True,
    )
    for metric in ("actual_total", "expected_to_reading", "expected_total"):
        assert finance_reading(contracted, metric) == (
            None,
            "gas_excluded_from_total",
        )
    assert finance_reading(contracted, "electricity_costs") == (37.5, "available")

    electricity_only = MonthlyFinanceSnapshot(
        month=original.month,
        insights=original.insights,
        retrieved_at=original.retrieved_at,
        gas_expected=False,
    )
    assert finance_reading(electricity_only, "actual_total") == (61.2, "available")


def test_full_energy_total_requires_proven_electricity_and_gas_components():
    original = _snapshot()
    source = {
        "actualCostsUntilLastMeterReading": "positive",
        "expectedCosts": "positive",
        "electricityDifference.actualCosts": "positive",
        "electricityDifference.actualUsage": "positive",
        "gasDifference.actualCosts": "positive",
        "gasDifference.actualUsage": "positive",
        "expectedCostsElectricity": "positive",
        "expectedCostsGas": "positive",
    }

    def scoped(states):
        return MonthlyFinanceSnapshot(
            month=original.month,
            insights=original.insights,
            retrieved_at=original.retrieved_at,
            source_states=states,
            electricity_expected=True,
            gas_expected=True,
        )

    assert finance_reading(scoped(source), "actual_total") == (61.2, "available")
    assert finance_reading(scoped(source), "expected_total") == (80.0, "available")
    without_gas = {
        key: value
        for key, value in source.items()
        if not key.startswith("gas") and key != "expectedCostsGas"
    }
    assert finance_reading(scoped(without_gas), "actual_total") == (
        None,
        "gas_component_missing",
    )
    assert finance_reading(scoped(without_gas), "expected_total") == (
        None,
        "gas_component_missing",
    )
    assert finance_reading(scoped(None), "actual_total") == (
        None,
        "electricity_component_unverified",
    )


def test_electricity_only_site_does_not_require_uncontracted_gas_breakdown():
    original = _snapshot()
    snapshot = MonthlyFinanceSnapshot(
        month=original.month,
        insights=original.insights,
        retrieved_at=original.retrieved_at,
        source_states={
            "actualCostsUntilLastMeterReading": "positive",
            "electricityDifference.actualCosts": "positive",
            "electricityDifference.actualUsage": "positive",
        },
        electricity_expected=True,
        gas_expected=False,
    )
    assert finance_reading(snapshot, "actual_total") == (61.2, "available")


def test_full_month_forecast_can_precede_first_meter_reading():
    """A named-month forecast is not a meter-cutoff actual amount."""
    snapshot = _snapshot(month="2026-11")
    snapshot.insights.lastMeterReadingDate = None
    assert finance_reading(snapshot, "expected_fixed") == (9.0, "available")
    assert finance_reading(snapshot, "expected_total") == (80.0, "available")
    assert finance_reading(snapshot, "actual_total") == (None, "missing_meter_date")
    assert finance_reading(snapshot, "electricity_costs") == (
        None,
        "missing_meter_date",
    )


def test_provider_readiness_never_claims_a_complete_household_bill():
    original = _snapshot()
    source_states = {
        "actualCostsUntilLastMeterReading": "positive",
        "electricityDifference.actualCosts": "positive",
        "electricityDifference.actualUsage": "positive",
        "gasDifference.actualCosts": "positive",
        "gasDifference.actualUsage": "positive",
    }

    def scoped(source, *, gas_excluded=False):
        original.insights.gasExcluded = gas_excluded
        return MonthlyFinanceSnapshot(
            month=original.month,
            insights=original.insights,
            retrieved_at=original.retrieved_at,
            source_states=source,
            electricity_expected=True,
            gas_expected=True,
        )

    assert finance_period_readiness(None) == "not_published"
    assert finance_period_readiness(_snapshot()) == "contract_unverified"
    full = scoped(source_states)
    assert finance_period_readiness(full) == "provider_components_present"
    attrs = finance_period_readiness_attributes(full)
    assert attrs["invoice_reconciled"] is False
    assert attrs["gas_cost_quality"] == "available"
    assert "61.2" not in str(attrs)

    assert (
        finance_period_readiness(scoped(source_states, gas_excluded=True))
        == "gas_missing"
    )
    no_gas = {k: v for k, v in source_states.items() if not k.startswith("gas")}
    assert finance_period_readiness(scoped(no_gas)) == "gas_missing"
    no_total = {
        k: v
        for k, v in source_states.items()
        if k != "actualCostsUntilLastMeterReading"
    }
    assert finance_period_readiness(scoped(no_total)) == "provider_total_missing"
