"""Tests for Frank Energie diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

import pytest

from custom_components.frank_energie.diagnostics import (
    _safe_contract_capabilities,
    _safe_ha_entity_state_summary,
    _safe_price_coverage,
    _safe_provider_data_freshness,
    _serialize,
    async_get_config_entry_diagnostics,
)


@dataclass
class MockDataclass:
    """Test dataclass."""

    name: str
    value: int


class MockEnum(Enum):
    """Test enum."""

    VALUE = "test"


class MockObject:
    """Test object."""

    def __init__(self) -> None:
        """Initialize test object."""
        self.public = "value"
        self._private = "hidden"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("test", "test"),
        (123, 123),
        (12.34, 12.34),
        (True, True),
    ],
)
def test_serialize_basic_types(
    value: object,
    expected: object,
) -> None:
    """Test serialization of basic types."""
    assert _serialize(value) == expected


def test_serialize_decimal() -> None:
    """Test Decimal serialization."""
    assert _serialize(Decimal("12.34")) == "12.34"


def test_serialize_datetime() -> None:
    """Test datetime serialization."""
    value = datetime(2026, 1, 1, 12, 0, 0)

    assert _serialize(value) == "2026-01-01T12:00:00"


def test_serialize_date() -> None:
    """Test date serialization."""
    value = date(2026, 1, 1)

    assert _serialize(value) == "2026-01-01"


def test_serialize_time() -> None:
    """Test time serialization."""
    value = time(12, 30, 45)

    assert _serialize(value) == "12:30:45"


def test_serialize_enum() -> None:
    """Test enum serialization."""
    assert _serialize(MockEnum.VALUE) == "test"


def test_serialize_uuid() -> None:
    """Test UUID serialization."""
    value = UUID("12345678-1234-5678-1234-567812345678")

    assert _serialize(value) == "12345678-1234-5678-1234-567812345678"


def test_serialize_bytes() -> None:
    """Test bytes serialization."""
    assert _serialize(b"\x01\x02") == "0102"


def test_serialize_tuple() -> None:
    """Test tuple serialization."""
    assert _serialize((1, "a")) == [1, "a"]


def test_serialize_set() -> None:
    """Test set serialization."""
    result = _serialize({"b", "a"})

    assert result == ["a", "b"]


def test_serialize_frozenset() -> None:
    """Test frozenset serialization."""
    result = _serialize(frozenset({"b", "a"}))

    assert result == ["a", "b"]


def test_serialize_dataclass() -> None:
    """Test dataclass serialization."""
    value = MockDataclass(
        name="test",
        value=123,
    )

    assert _serialize(value) == {
        "name": "test",
        "value": 123,
    }


def test_serialize_object() -> None:
    """Test generic object serialization."""
    assert _serialize(MockObject()) == {
        "public": "value",
    }


@pytest.mark.asyncio
async def test_diagnostics_redacts_sensitive_data() -> None:
    """Test diagnostics redact sensitive data."""

    coordinator = SimpleNamespace(
        last_update_success=True,
        update_interval=timedelta(minutes=15),
        site_reference="secret-site",
        country_code="NL",
        _user_country="NL",
        resolution="PT15M",
        api_resolution="PT15M",
        user_electricity_enabled=True,
        user_gas_enabled=True,
        last_fetch_today="today",
        last_fetch_tomorrow="tomorrow",
        cached_prices={},
        cached_prices_today={},
        cached_prices_tomorrow={},
        _resolution_change_pending=False,
        data={
            "email": "test@example.com",
            "ean": "871234567890123456",
            "safe": "value",
        },
    )

    runtime_data = SimpleNamespace(
        coordinator=coordinator,
        battery_session_coordinators=[],
    )

    entry = MagicMock()
    entry.entry_id = "123"
    entry.title = "Frank Energie"
    entry.options = {}
    entry.data = {
        "username": "user@example.com",
        "password": "secret",
        "access_token": "token",
        "site_reference": "site",
    }
    entry.runtime_data = runtime_data

    diagnostics = await async_get_config_entry_diagnostics(
        MagicMock(),
        entry,
    )

    assert diagnostics["entry"]["data"] == {
        "configured": True,
        "site_selected": True,
    }
    assert diagnostics["entry"]["options"]["monthly_finance_enabled"] is False
    assert diagnostics["raw_provider_payloads"] == "omitted_for_privacy"
    assert "user@example.com" not in str(diagnostics)
    assert "test@example.com" not in str(diagnostics)
    assert "871234567890123456" not in str(diagnostics)
    assert "secret-site" not in str(diagnostics)


@pytest.mark.asyncio
async def test_diagnostics_coordinator_metadata() -> None:
    """Test coordinator metadata is included."""

    coordinator = SimpleNamespace(
        last_update_success=True,
        update_interval=timedelta(minutes=15),
        site_reference="site",
        country_code="NL",
        _user_country="NL",
        resolution="PT15M",
        api_resolution="PT15M",
        user_electricity_enabled=True,
        user_gas_enabled=False,
        last_fetch_today="today",
        last_fetch_tomorrow="tomorrow",
        cached_prices={"prices": []},
        cached_prices_today={"prices": []},
        cached_prices_tomorrow={"prices": []},
        _resolution_change_pending=True,
        data={},
    )

    runtime_data = SimpleNamespace(
        coordinator=coordinator,
        battery_session_coordinators=[object(), object()],
    )

    entry = MagicMock()
    entry.entry_id = "123"
    entry.title = "Frank Energie"
    entry.options = {}
    entry.data = {}
    entry.runtime_data = runtime_data

    diagnostics = await async_get_config_entry_diagnostics(
        MagicMock(),
        entry,
    )

    coordinator_data = diagnostics["coordinator"]

    assert coordinator_data["last_update_success"] is True
    assert coordinator_data["country_code"] == "NL"
    assert coordinator_data["resolution"] == "PT15M"
    assert coordinator_data["api_resolution"] == "PT15M"
    assert coordinator_data["user_electricity_enabled"] is True
    assert coordinator_data["user_gas_enabled"] is False
    assert coordinator_data["has_cached_prices"] is True
    assert coordinator_data["has_cached_today"] is True
    assert coordinator_data["has_cached_tomorrow"] is True
    assert coordinator_data["resolution_change_pending"] is True

    assert diagnostics["battery_session_coordinators"] == 2


@pytest.mark.asyncio
async def test_diagnostics_distinguish_coordinator_health_from_data_coverage() -> None:
    """Price success must not be mistaken for successful monthly accounting."""
    price = SimpleNamespace(
        last_update_success=True,
        update_interval=timedelta(minutes=60),
        data={
            "electricity": SimpleNamespace(prices=[], resolution_minutes=60),
            "gas": SimpleNamespace(prices=[], resolution_minutes=60),
        },
    )
    statistics = SimpleNamespace(
        last_update_success=False,
        update_interval=timedelta(hours=1),
        data={
            "month_summary": None,
            "invoices": None,
            "usage": None,
            "month_insights_current": None,
            "month_insights_previous": None,
        },
    )
    coordinator = SimpleNamespace(
        last_update_success=True,
        update_interval=timedelta(minutes=60),
        site_reference="private",
        country_code="BE",
        _user_country="BE",
        resolution="PT15M",
        api_resolution=None,
        user_electricity_enabled=False,
        user_gas_enabled=False,
        last_fetch_today=None,
        last_fetch_tomorrow=None,
        cached_prices=None,
        cached_prices_today=None,
        cached_prices_tomorrow=None,
        _resolution_change_pending=False,
        data=price.data,
    )
    runtime = SimpleNamespace(
        coordinator=coordinator,
        price_coordinator=price,
        statistics_coordinator=statistics,
        battery_session_coordinators={},
    )
    entry = MagicMock()
    entry.entry_id = "private-entry"
    entry.title = "Private address"
    entry.data = {"site_reference": "private"}
    entry.options = {"username": "private-email"}
    entry.runtime_data = runtime

    diagnostics = await async_get_config_entry_diagnostics(MagicMock(), entry)
    evidence = diagnostics["coordinator_evidence"]
    assert evidence["price"]["last_update_success"] is True
    assert evidence["statistics"]["last_update_success"] is False
    assert evidence["price_coverage"]["electricity"]["price_points"] == 0
    assert evidence["price_coverage"]["electricity"]["resolution_minutes"] == 60
    assert evidence["finance_coverage"]["month_summary"] is False
    assert evidence["finance_coverage"]["month_insights_previous"] is False
    assert evidence["finance_coverage"]["invoices"] is False
    assert "private" not in str(evidence)


def test_variable_model_contract_reports_meter_codes_as_opaque_api_metadata() -> None:
    """Variable tariff and raw meter codes are independent source capabilities."""
    settings = {
        "user_sites": SimpleNamespace(propositionType="MODEL"),
        "user": SimpleNamespace(
            connections=[
                {
                    "segment": "ELECTRICITY",
                    "meterType": "SMR3",
                    "lastMeterReadingDate": "2026-10-30",
                    "externalDetails": {"contract": {"contractType": "model"}},
                },
                {
                    "segment": "GAS",
                    "meterType": "SMR1",
                    "lastMeterReadingDate": None,
                    "externalDetails": {"contract": {"contractType": "model"}},
                },
            ]
        ),
    }
    result = _safe_contract_capabilities(settings)
    assert result["proposition_type"] == "MODEL"
    assert result["variable_contract"] is True
    assert result["meter_code_source"] == "frank_api_metadata"
    assert result["segments"]["electricity"] == {
        "contract_type": "model",
        "reported_meter_code": "SMR3",
        "last_meter_reading_reported": True,
    }
    assert result["segments"]["gas"] == {
        "contract_type": "model",
        "reported_meter_code": "SMR1",
        "last_meter_reading_reported": False,
    }
    assert "productName" not in str(result)


def test_pinned_client_connection_dataclasses_are_reported_without_ids() -> None:
    """Real User.connections are Connection dataclasses, not dicts."""
    from python_frank_energie.models import Connection

    electricity = Connection.from_dict(
        {
            "id": "sensitive-connection-123",
            "segment": "ELECTRICITY",
            "meterType": "SMR3",
            "lastMeterReadingDate": "2026-10-30",
            "externalDetails": {
                "contract": {
                    "contractType": "MODEL",
                    "productName": "sensitive-product-name",
                },
            },
        }
    )
    gas = Connection.from_dict(
        {
            "id": "sensitive-gas-id",
            "segment": "GAS",
            "meterType": "SMR1",
            "lastMeterReadingDate": None,
            "externalDetails": {"contract": {"contractType": "MODEL"}},
        }
    )
    result = _safe_contract_capabilities(
        {
            "user_sites": SimpleNamespace(propositionType="MODEL"),
            "user": SimpleNamespace(connections=[electricity, gas]),
        }
    )
    assert result["segments"]["electricity"] == {
        "contract_type": "model",
        "reported_meter_code": "SMR3",
        "last_meter_reading_reported": True,
    }
    assert result["segments"]["gas"] == {
        "contract_type": "model",
        "reported_meter_code": "SMR1",
        "last_meter_reading_reported": False,
    }
    for sensitive in (
        "sensitive-connection",
        "sensitive-gas-id",
        "sensitive-product-name",
    ):
        assert sensitive not in str(result)


def test_nested_fallback_contract_dataclass_and_absent_metadata() -> None:
    """Known source metadata survives; absent meter properties are not invented."""
    from python_frank_energie.models import Connection

    connection = Connection.from_dict(
        {"segment": "GAS", "contract": {"contractType": "MODEL"}}
    )
    result = _safe_contract_capabilities(
        {
            "user_sites": SimpleNamespace(propositionType="MODEL"),
            "user": SimpleNamespace(connections=[connection]),
        }
    )
    assert result["segments"]["gas"]["contract_type"] == "model"
    assert result["segments"]["gas"]["reported_meter_code"] is None
    assert result["segments"]["gas"]["last_meter_reading_reported"] is False


def test_contract_metadata_does_not_infer_missing_physical_meter_details() -> None:
    """No code -> no invented meter class; model must not imply dynamic tariff."""
    result = _safe_contract_capabilities(
        {
            "user_sites": SimpleNamespace(propositionType="MODEL"),
            "user": SimpleNamespace(
                connections=[
                    {
                        "segment": "ELECTRICITY",
                        "meterType": None,
                        "externalDetails": {"contract": {"contractType": "model"}},
                    }
                ]
            ),
        }
    )
    assert result["segments"]["electricity"]["reported_meter_code"] is None
    assert result["variable_contract"] is True
    assert _safe_contract_capabilities({})["variable_contract"] is None


@pytest.mark.asyncio
async def test_diagnostics_redact_nested_provider_identifiers_and_payment_data() -> (
    None
):
    """Raw API models must not expose identifying account, referral or payment IDs."""
    coordinator = SimpleNamespace(
        last_update_success=True,
        update_interval=timedelta(hours=1),
        site_reference="sensitive-site",
        country_code="BE",
        _user_country="BE",
        resolution="PT15M",
        api_resolution=None,
        user_electricity_enabled=False,
        user_gas_enabled=False,
        last_fetch_today=None,
        last_fetch_tomorrow=None,
        cached_prices=None,
        cached_prices_today=None,
        cached_prices_tomorrow=None,
        _resolution_change_pending=False,
        data={
            "user": {
                "id": "sensitive-account",
                "InviteLinkUser": {
                    "fromName": "sensitive-name",
                    "slug": "sensitive-referral",
                },
                "activePaymentAuthorization": {
                    "mandateId": "sensitive-mandate",
                    "id": "sensitive-authorization",
                },
                "advancedPaymentAmount": 265,
            }
        },
    )
    runtime = SimpleNamespace(
        coordinator=coordinator,
        battery_session_coordinators={},
    )
    entry = MagicMock()
    entry.entry_id = "sensitive-entry"
    entry.title = "sensitive-title"
    entry.options = {}
    entry.data = {}
    entry.runtime_data = runtime

    diagnostics = await async_get_config_entry_diagnostics(MagicMock(), entry)
    text = str(diagnostics)
    for secret in (
        "sensitive-account",
        "sensitive-name",
        "sensitive-referral",
        "sensitive-mandate",
        "sensitive-authorization",
        "sensitive-entry",
        "sensitive-title",
        "265",
    ):
        assert secret not in text
    assert diagnostics["entry"]["entry_id"] == "**REDACTED**"
    assert diagnostics["coordinator"]["has_cached_today"] is False
    assert diagnostics["coordinator"]["has_active_today_prices"] is False


def test_price_coverage_counts_effective_prices_after_merge_not_raw_input() -> None:
    """Merged PriceData keeps effective .all slots even when .prices is empty."""
    from python_frank_energie.models import PriceData

    electricity = PriceData(prices=[], energy_type="electricity")
    electricity.price_data = [object(), object()]
    gas = PriceData(prices=[], energy_type="gas")
    coverage = _safe_price_coverage({"electricity": electricity, "gas": gas})

    assert coverage["electricity"]["raw_input_price_points"] == 0
    assert coverage["electricity"]["price_points"] == 2
    assert coverage["electricity"]["has_price_points"] is True
    assert coverage["gas"]["price_points"] == 0
    assert coverage["gas"]["has_price_points"] is False


def test_price_coverage_splits_actual_selected_slots_by_local_day() -> None:
    """A 30-slot stream can conceal 24 today plus only 6 tomorrow."""
    gas = SimpleNamespace(
        all=[
            SimpleNamespace(
                date_from=datetime.fromisoformat("2026-10-31T12:00:00+01:00")
            )
            for _ in range(24)
        ]
        + [
            SimpleNamespace(
                date_from=datetime.fromisoformat("2026-11-01T12:00:00+01:00")
            )
            for _ in range(6)
        ],
        prices=[],
        resolution_minutes=60,
    )
    coverage = _safe_price_coverage({"gas": gas})
    assert coverage["gas"]["price_points"] == 30
    assert coverage["gas"]["slots_by_local_day"] == {
        "2026-10-31": 24,
        "2026-11-01": 6,
    }


def test_unparseable_price_date_does_not_leak_or_break_diagnostics() -> None:
    source = SimpleNamespace(
        all=[
            SimpleNamespace(date_from="private-customer"),
            SimpleNamespace(date_from=datetime(2026, 10, 31, 12, 0)),
        ],
        prices=[],
        resolution_minutes=60,
    )
    result = _safe_price_coverage({"electricity": source})
    assert result["electricity"]["slots_by_local_day"] == {}
    assert "private-customer" not in str(result)


def test_provider_data_freshness_separates_api_objects_from_meter_data() -> None:
    """An HTTP success can still mean stale meter dates or empty usage."""
    summary = SimpleNamespace(lastMeterReadingDate="2026-10-25")
    usage = SimpleNamespace(
        electricity=SimpleNamespace(usage_total=0.0, costs_total=None, items=[]),
        gas=None,
        feed_in=SimpleNamespace(usage_total=None, costs_total=0.0, items=[object()]),
    )
    invoices = SimpleNamespace(
        all_periods_invoices=[
            SimpleNamespace(
                StartDate=datetime.fromisoformat("2026-09-01T00:00:00+02:00")
            ),
            SimpleNamespace(
                StartDate=datetime.fromisoformat("2026-10-01T00:00:00+02:00")
            ),
        ]
    )
    result = _safe_provider_data_freshness(
        {
            "month_summary": summary,
            "usage": usage,
            "invoices": invoices,
        },
        local_today=date(2026, 11, 1),
    )
    assert result["month_summary"] == {
        "present": True,
        "last_meter_reading_age_days": 7,
        "last_meter_reading_in_current_local_month": False,
    }
    assert result["usage"]["segments"]["electricity"] == {
        "present": True,
        "usage_total_reported": True,
        "costs_total_reported": False,
        "intervals_reported": 0,
    }
    assert result["usage"]["segments"]["gas"] == {"present": False}
    assert result["usage"]["segments"]["feed_in"] == {
        "present": True,
        "usage_total_reported": False,
        "costs_total_reported": True,
        "intervals_reported": 1,
    }
    assert result["invoices"]["invoice_count"] == 2
    assert result["invoices"]["latest_invoice_start_age_days"] == 31
    assert "0.0" not in str(result)


def test_provider_data_freshness_handles_missing_dates_without_guessing() -> None:
    result = _safe_provider_data_freshness(
        {
            "month_summary": SimpleNamespace(lastMeterReadingDate="invalid-private"),
            "invoices": SimpleNamespace(all_periods_invoices=[]),
            "usage": SimpleNamespace(electricity=None, gas=None, feed_in=None),
        },
        local_today=date(2026, 11, 1),
    )
    assert result["month_summary"]["last_meter_reading_age_days"] is None
    assert result["month_summary"]["last_meter_reading_in_current_local_month"] is None
    assert result["invoices"]["invoice_count"] == 0
    assert result["invoices"]["latest_invoice_start_age_days"] is None
    assert result["usage"]["segments"]["gas"]["present"] is False
    assert "invalid-private" not in str(result)


def test_ha_entity_state_summary_counts_loaded_missing_and_disabled(
    hass, monkeypatch
) -> None:
    """HA entity-state evidence is separate from provider API success."""
    from homeassistant.helpers import entity_registry as er

    hass.states.async_set("sensor.frank_one", "1")
    hass.states.async_set("sensor.frank_two", "unavailable")
    hass.states.async_set("sensor.frank_three", "unknown")

    fake_entities = [
        SimpleNamespace(
            config_entry_id="selected-entry",
            entity_id=entity_id,
            disabled_by=disabled,
        )
        for entity_id, disabled in (
            ("sensor.frank_one", None),
            ("sensor.frank_two", None),
            ("sensor.frank_three", None),
            ("sensor.frank_missing", None),
            ("sensor.frank_disabled", "user"),
        )
    ]
    fake_entities.append(
        SimpleNamespace(
            config_entry_id="other-entry",
            entity_id="sensor.some_other_integration",
            disabled_by=None,
        )
    )
    monkeypatch.setattr(
        er,
        "async_get",
        lambda _: SimpleNamespace(
            entities={f"key{i}": entity for i, entity in enumerate(fake_entities)}
        ),
    )
    result = _safe_ha_entity_state_summary(
        hass, SimpleNamespace(entry_id="selected-entry")
    )
    assert result["registered"] == 5
    assert result["disabled"] == 1
    assert result["not_loaded"] == 1
    assert result["available"] == 1
    assert result["unavailable"] == 1
    assert result["unknown"] == 1
    assert result["not_reported_in_90_minutes"] == 0
    assert "frank_one" not in str(result)


def test_ha_entity_state_summary_never_infers_from_fake_hass() -> None:
    """A mock object is not an actual loaded HA entity registry."""
    assert _safe_ha_entity_state_summary(
        MagicMock(), SimpleNamespace(entry_id="redacted-entry")
    ) == {"configured": False}


def test_ha_entity_summary_limits_device_group_names_to_known_services(
    hass, monkeypatch
) -> None:
    """Private device models and entity IDs must not leak via diagnostics."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    hass.states.async_set("sensor.example_private", "ok")
    hass.states.async_set("sensor.example_price", "42")
    entities = {
        "one": SimpleNamespace(
            config_entry_id="my-entry",
            entity_id="sensor.example_private",
            device_id="secret-device",
            disabled_by=None,
        ),
        "two": SimpleNamespace(
            config_entry_id="my-entry",
            entity_id="sensor.example_price",
            device_id="prices-device",
            disabled_by=None,
        ),
    }
    monkeypatch.setattr(er, "async_get", lambda _: SimpleNamespace(entities=entities))
    monkeypatch.setattr(
        dr,
        "async_get",
        lambda _: SimpleNamespace(
            devices={
                "secret-device": SimpleNamespace(model="private-meter-model"),
                "prices-device": SimpleNamespace(model="Prices"),
            }
        ),
    )

    output = _safe_ha_entity_state_summary(hass, SimpleNamespace(entry_id="my-entry"))
    assert output["by_service"]["Prices"]["available"] == 1
    assert output["by_service"]["Other"]["available"] == 1
    assert "private-meter-model" not in str(output)
    assert "example_private" not in str(output)


def _synthetic_gas_price_data(day: date, slots: int):
    """Create typed, privacy-safe gas records for a future local day."""
    from datetime import UTC, timedelta
    from zoneinfo import ZoneInfo

    from python_frank_energie.models import PriceData

    tz = ZoneInfo("Europe/Brussels")
    first = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    data = [
        {
            "from": (first + timedelta(hours=i)).isoformat(),
            "till": (first + timedelta(hours=i + 1)).isoformat(),
            "marketPrice": 0.1,
            "marketPriceTax": 0.02,
            "sourcingMarkupPrice": 0.01,
            "energyTaxPrice": 0.1,
        }
        for i in range(slots)
    ]
    return PriceData(data, energy_type="gas")


def test_gas_pipeline_distinguishes_source_slots_from_sensor_attributes(
    freezer,
) -> None:
    """GraphQL parse, PriceData.asdict, historical usage and coverage are distinct."""
    from custom_components.frank_energie.coordinator import FrankEnergieCoordinator
    from custom_components.frank_energie.diagnostics import (
        _safe_gas_delivery_pipeline,
    )

    freezer.move_to(datetime(2026, 7, 20, 13, 0, 0))
    tomorrow = date(2026, 7, 21)
    gas = _synthetic_gas_price_data(tomorrow, slots=6)
    price = SimpleNamespace(
        data={"gas": gas},
        user_gas_enabled=True,
        _covers_local_day=FrankEnergieCoordinator._covers_local_day,
    )
    stats = SimpleNamespace(
        data={
            "usage": SimpleNamespace(
                gas=SimpleNamespace(
                    unit="M3",
                    usage_total=None,
                    costs_total=0.0,
                )
            )
        }
    )

    result = _safe_gas_delivery_pipeline(
        MagicMock(),
        SimpleNamespace(entry_id="private"),
        price,
        stats,
        local_today=date(2026, 7, 20),
    )
    assert result["site_gas_subscription"] is True
    assert result["prices"]["parsed_price_slots"] == 6
    assert result["prices"]["tomorrow_slots"] == 6
    assert result["prices"]["tomorrow_attribute_slots"] == 6
    assert result["prices"]["tomorrow_complete"] is False
    assert result["historical_usage"]["api_segment_parsed"] is True
    assert result["historical_usage"]["usage_total_reported"] is False
    assert result["historical_usage"]["costs_total_reported"] is True
    assert result["historical_usage"]["unit"] == "M3"
    assert result["recorder_history_verified"] is False

    price.data["gas"] = _synthetic_gas_price_data(tomorrow, slots=0)
    empty = _safe_gas_delivery_pipeline(
        MagicMock(),
        SimpleNamespace(entry_id="private"),
        price,
        stats,
        local_today=date(2026, 7, 20),
    )
    assert empty["prices"]["tomorrow_attribute_slots"] == 0


def test_gas_entity_pipeline_checks_real_states_and_correct_service_devices(
    hass, monkeypatch
) -> None:
    """Source success does not hide missing gas forecast/usage entities."""
    from custom_components.frank_energie.diagnostics import _safe_gas_entity_pipeline
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    # Entity IDs and device IDs are synthetic and must never be returned.
    fake = {
        "one": SimpleNamespace(
            config_entry_id="target",
            entity_id="sensor.gas_price_private",
            unique_id="some-key.gas_markup",
            device_id="gas-device",
            disabled_by=None,
        ),
        "two": SimpleNamespace(
            config_entry_id="target",
            entity_id="sensor.gas_forecast_private",
            unique_id="some-key.gas_tomorrow_avg",
            device_id="wrong-device",
            disabled_by=None,
        ),
        "three": SimpleNamespace(
            config_entry_id="target",
            entity_id="sensor.gas_usage_private",
            unique_id="some-key.usage_gas_yesterday",
            device_id="usage-device",
            disabled_by=None,
        ),
    }
    hass.states.async_set("sensor.gas_price_private", "0.1")
    hass.states.async_set("sensor.gas_forecast_private", "unavailable")
    monkeypatch.setattr(er, "async_get", lambda _: SimpleNamespace(entities=fake))
    monkeypatch.setattr(
        dr,
        "async_get",
        lambda _: SimpleNamespace(
            devices={
                "gas-device": SimpleNamespace(model="Gasprices"),
                "wrong-device": SimpleNamespace(model="Usage"),
                "usage-device": SimpleNamespace(model="Usage"),
            }
        ),
    )

    result = _safe_gas_entity_pipeline(hass, SimpleNamespace(entry_id="target"))
    assert result["current_prices"]["gas_markup"]["state"] == "available"
    assert result["current_prices"]["gas_markup"]["correct_device_group"] is True
    assert result["forecast"]["gas_tomorrow_avg"]["state"] == "unavailable"
    assert result["forecast"]["gas_tomorrow_avg"]["correct_device_group"] is False
    assert result["historical_usage"]["usage_gas_yesterday"]["state"] == "not_loaded"
    assert (
        result["historical_usage"]["costs_gas_yesterday"]["state"] == "not_registered"
    )
    assert "gas_price_private" not in str(result)
    assert "some-key" not in str(result)


def test_provider_period_freshness_uses_local_day_for_utc_meter_timestamp() -> None:
    """Privacy-safe period diagnostic follows calendar day in Belgium."""
    result = _safe_provider_data_freshness(
        {"month_summary": SimpleNamespace(lastMeterReadingDate="2026-10-31T23:30:00Z")},
        local_today=date(2026, 11, 1),
    )
    assert result["month_summary"]["last_meter_reading_in_current_local_month"] is True


def test_price_coverage_reports_real_minutes_and_missing_intervals() -> None:
    """24 quarter-hour slots do not prove 24 hours of Belgian price coverage."""
    from datetime import UTC, timedelta
    from python_frank_energie.models import PriceData

    today = datetime.now(ZoneInfo("Europe/Brussels")).date()
    start = datetime.combine(
        today, time.min, tzinfo=ZoneInfo("Europe/Brussels")
    ).astimezone(UTC)
    quarter_hours = [
        {
            "from": (start + timedelta(minutes=index * 15)).isoformat(),
            "till": (start + timedelta(minutes=(index + 1) * 15)).isoformat(),
            "marketPrice": 0.10,
            "marketPriceTax": 0.02,
            "sourcingMarkupPrice": 0.01,
            "energyTaxPrice": 0.10,
        }
        for index in range(24)
    ]
    result = _safe_price_coverage(
        {"electricity": PriceData(quarter_hours, energy_type="electricity")}
    )["electricity"]

    assert result["price_points"] == 24
    assert result["today_covered_minutes"] == 360
    assert result["today_gap_free"] is False
