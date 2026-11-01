"""Diagnostics support for the Frank Energie integration."""

from __future__ import annotations

from collections import Counter
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from uuid import UUID
from zoneinfo import ZoneInfo

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from . import FrankEnergieEntryData
from .finance import finance_evidence
from .service_refresh import service_refreshes, service_refresh_attributes
from .source_checks import source_check_evidence
from .price_coverage import covered_seconds_in_local_day, covers_local_day
from .const import (
    SERVICE_NAME_COSTS,
    SERVICE_NAME_ELEC_PRICES,
    SERVICE_NAME_GAS_PRICES,
    SERVICE_NAME_INVOICES,
    SERVICE_NAME_MONTH_SUMMARY,
    SERVICE_NAME_PRICES,
    SERVICE_NAME_USAGE,
    SERVICE_NAME_USER,
)

TO_REDACT: set[str] = {
    CONF_ACCESS_TOKEN,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_PASSWORD,
    "authToken",
    "refreshToken",
    "access_token",
    "refresh_token",
    "password",
    "username",
    "email",
    "phone",
    "address",
    "customer_id",
    "customerId",
    "account_id",
    "accountId",
    "title",
    "contract_id",
    "contractId",
    "site_reference",
    "siteReference",
    "reference",
    "TotalAmount",
    "Total amount",
    "Total_amount",
    "total_costs_previous_year",
    "total_costs_this_year",
    "site_id",
    "siteId",
    "_id",
    "connection_id",
    "connectionId",
    "bankAccountNumber",
    "EAN",
    "cluster",
    "firstName",
    "lastName",
    "emailAddress",
    "phoneNumber",
    "mobileNumber",
    "ean",
    "electricity_ean",
    "gas_ean",
    "serial_number",
    "serialNumber",
    "battery_id",
    "batteryId",
    "charger_id",
    "chargerId",
    "vehicle_id",
    "vehicleId",
    "device_id",
    "deviceId",
    "installation_id",
    "installationId",
    "id",
    "entry_id",
    "userId",
    "mandateId",
    "fromName",
    "slug",
    "InviteLinkUser",
    "activePaymentAuthorization",
    "PaymentAuthorizations",
    "Signup",
    "advancedPaymentAmount",
    "discountPerConnection",
}


def _serialize(value: object) -> object:
    """Convert objects into diagnostics-safe structures."""
    if value is None:
        return None

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    if isinstance(value, UUID):
        return str(value)

    if isinstance(value, bytes):
        return value.hex()

    if isinstance(value, (set, frozenset)):
        return [_serialize(item) for item in sorted(value, key=str)]

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _serialize(getattr(value, field.name))
            for field in fields(value)
        }

    if isinstance(value, dict):
        return {str(key): _serialize(val) for key, val in value.items()}

    if isinstance(value, list):
        return [_serialize(item) for item in value]

    if isinstance(value, tuple):
        return [_serialize(item) for item in value]

    if hasattr(value, "__dict__"):
        return {
            key: _serialize(val)
            for key, val in vars(value).items()
            if not key.startswith("_")
        }

    return value


def _bounded_coordinator_evidence(coordinator: object | None) -> dict[str, object]:
    """Expose coordinator liveness and coverage, never its customer payload."""
    if coordinator is None:
        return {"configured": False}
    data = getattr(coordinator, "data", None)
    return {
        "configured": True,
        "last_update_success": getattr(coordinator, "last_update_success", None),
        "update_interval": str(getattr(coordinator, "update_interval", None)),
        "has_data": bool(data),
    }


def _safe_price_coverage(data: object) -> dict[str, object]:
    """Report empty price streams without serializing values or account data."""
    if not isinstance(data, dict):
        return {}
    coverage: dict[str, object] = {}
    local_timezone = ZoneInfo("Europe/Brussels")
    today = datetime.now(local_timezone).date()
    for segment in ("electricity", "gas"):
        source = data.get(segment)
        if source is None:
            coverage[segment] = {"available": False}
            continue
        # PriceData.prices contains raw *input* dicts, but PriceData.__add__
        # deliberately builds a new instance with prices=[] and populates
        # price_data. All live HA price sensors consume PriceData.all. Count
        # the effective slots, not only the dataclass field serialized below.
        effective_prices = getattr(source, "all", None)
        raw_prices = getattr(source, "prices", None)
        if isinstance(effective_prices, (list, tuple)):
            price_count = len(effective_prices)
        elif isinstance(raw_prices, (list, tuple)):
            price_count = len(raw_prices)
        else:
            price_count = None
        # Slot counts by local day are *not* hour coverage: missing intervals,
        # overlapping intervals, and DST transitions still require validation.
        # Keep only the day/count metadata, never any price or provider payload.
        local_days: Counter[str] = Counter()
        if isinstance(effective_prices, (list, tuple)):
            for price in effective_prices:
                start = getattr(price, "date_from", None)
                if isinstance(start, datetime) and start.tzinfo is not None:
                    local_days[start.astimezone(local_timezone).date().isoformat()] += 1
        coverage[segment] = {
            "available": True,
            "slots_by_local_day": dict(sorted(local_days.items())[:4]),
            "price_points": price_count,
            "has_price_points": price_count > 0 if price_count is not None else None,
            "has_current_price": bool(getattr(source, "current", None)),
            "raw_input_price_points": (
                len(raw_prices) if isinstance(raw_prices, (list, tuple)) else None
            ),
            "resolution_minutes": getattr(source, "resolution_minutes", None),
            "today_covered_minutes": (
                round(covered_seconds_in_local_day(source, today) / 60, 2)
                if isinstance(effective_prices, (list, tuple))
                else None
            ),
            "today_gap_free": (
                covers_local_day(source, today)
                if isinstance(effective_prices, (list, tuple))
                else None
            ),
        }
    return coverage


def _safe_finance_coverage(data: object) -> dict[str, object]:
    """Report separate finance freshness without publishing private amounts."""
    if not isinstance(data, dict):
        return {}
    keys = (
        "month_summary",
        "invoices",
        "usage",
        "month_insights_current",
        "month_insights_previous",
    )
    result: dict[str, object] = {}
    for key in keys:
        snapshot = data.get(key)
        result[key] = snapshot is not None
    return result


def _safe_provider_data_freshness(
    data: object, *, local_today: date | None = None
) -> dict[str, object]:
    """Report source data age/content, never usage, costs, IDs or raw dates.

    A successful GraphQL response may contain a summary based on an old meter
    reading, or a PeriodUsageAndCosts object with no actual segment values.
    Preserve that distinction in HA diagnostics without exposing amounts.
    """
    if not isinstance(data, dict):
        return {}
    today = local_today or datetime.now(ZoneInfo("Europe/Brussels")).date()

    def days_ago(value: object) -> int | None:
        if isinstance(value, datetime):
            stamp = value.date()
        elif isinstance(value, date):
            stamp = value
        elif isinstance(value, str):
            try:
                stamp = date.fromisoformat(value[:10])
            except ValueError:
                return None
        else:
            return None
        elapsed = (today - stamp).days
        return elapsed if 0 <= elapsed <= 36500 else None

    def is_current_local_month(value: object) -> bool | None:
        """Report source-period alignment without exposing private dates."""
        if isinstance(value, datetime):
            reading = (
                value.astimezone(ZoneInfo("Europe/Brussels")).date()
                if value.tzinfo is not None
                else value.date()
            )
        elif isinstance(value, date):
            reading = value
        elif isinstance(value, str):
            try:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            reading = (
                timestamp.astimezone(ZoneInfo("Europe/Brussels")).date()
                if timestamp.tzinfo is not None
                else timestamp.date()
            )
        else:
            return None
        return (reading.year, reading.month) == (today.year, today.month)

    summary = data.get("month_summary")
    usage = data.get("usage")
    invoices = data.get("invoices")
    segments: dict[str, object] = {}
    for name, attr in (
        ("electricity", "electricity"),
        ("gas", "gas"),
        ("feed_in", "feed_in"),
    ):
        segment = getattr(usage, attr, None) if usage is not None else None
        if segment is None:
            segments[name] = {"present": False}
            continue
        items = getattr(segment, "items", None)
        segments[name] = {
            "present": True,
            "usage_total_reported": getattr(segment, "usage_total", None) is not None,
            "costs_total_reported": getattr(segment, "costs_total", None) is not None,
            "intervals_reported": len(items)
            if isinstance(items, (list, tuple))
            else None,
        }

    all_invoices = getattr(invoices, "all_periods_invoices", None)
    latest_invoice_age_days = None
    if isinstance(all_invoices, (list, tuple)):
        ages = [
            age
            for invoice in all_invoices
            if (age := days_ago(getattr(invoice, "StartDate", None))) is not None
        ]
        latest_invoice_age_days = min(ages, default=None)
    return {
        "month_summary": {
            "present": summary is not None,
            "last_meter_reading_age_days": (
                days_ago(getattr(summary, "lastMeterReadingDate", None))
                if summary is not None
                else None
            ),
            "last_meter_reading_in_current_local_month": (
                is_current_local_month(getattr(summary, "lastMeterReadingDate", None))
                if summary is not None
                else None
            ),
        },
        "usage": {
            "present": usage is not None,
            "segments": segments,
        },
        "invoices": {
            "present": invoices is not None,
            "invoice_count": (
                len(all_invoices) if isinstance(all_invoices, (list, tuple)) else None
            ),
            "latest_invoice_start_age_days": latest_invoice_age_days,
        },
    }


def _safe_contract_capabilities(settings_data: object) -> dict[str, object]:
    """Describe source-reported tariff and meter metadata without account PII.

    MODEL means a variable commercial contract, not an hourly dynamic
    customer tariff. SMR1/SMR3 are opaque provider codes, not proof of a
    particular physical meter or reading frequency.
    """
    if not isinstance(settings_data, dict):
        return {}

    sites = settings_data.get("user_sites")
    proposition = getattr(sites, "propositionType", None)
    if not isinstance(proposition, str):
        proposition = None

    user = settings_data.get("user")
    connections = getattr(user, "connections", None) or []
    segments: dict[str, object] = {}

    def model_field(value: object, key: str) -> object:
        """Read a whitelisted field from pinned-client dataclasses or test dicts."""
        return value.get(key) if isinstance(value, dict) else getattr(value, key, None)

    for connection in connections:
        segment = model_field(connection, "segment")
        if not isinstance(segment, str) or segment.upper() not in (
            "ELECTRICITY",
            "GAS",
        ):
            continue
        segment_key = segment.lower()
        external = model_field(connection, "externalDetails")
        contract = model_field(external, "contract") or model_field(
            connection, "contract"
        )
        reported_meter_code = model_field(connection, "meterType")
        contract_type = model_field(contract, "contractType")
        segments[segment_key] = {
            "contract_type": (
                contract_type.lower() if isinstance(contract_type, str) else None
            ),
            "reported_meter_code": (
                reported_meter_code.upper()
                if isinstance(reported_meter_code, str)
                else None
            ),
            "last_meter_reading_reported": bool(
                model_field(connection, "lastMeterReadingDate")
            ),
        }

    model = proposition.upper() if proposition else None
    return {
        "proposition_type": model,
        "variable_contract": model == "MODEL" if model else None,
        "meter_code_source": "frank_api_metadata",
        "segments": segments,
    }


def _safe_ha_entity_state_summary(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, object]:
    """Summarize actual HA entity states without names, values or identifiers.

    last_reported tracks state writes, including unchanged values, while
    last_changed cannot be used as an update/freshness indicator.
    """
    # Isolated diagnostics unit tests sometimes use a MagicMock in place of
    # HomeAssistant. Do not make a fake registry look like live HA evidence.
    if not isinstance(hass, HomeAssistant):
        return {"configured": False}
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    recognized_services = {
        SERVICE_NAME_PRICES,
        SERVICE_NAME_ELEC_PRICES,
        SERVICE_NAME_GAS_PRICES,
        SERVICE_NAME_COSTS,
        SERVICE_NAME_USAGE,
        SERVICE_NAME_INVOICES,
        SERVICE_NAME_MONTH_SUMMARY,
        SERVICE_NAME_USER,
    }
    by_service: dict[str, dict[str, int]] = {}
    summary = {
        "registered": 0,
        "disabled": 0,
        "not_loaded": 0,
        "available": 0,
        "unknown": 0,
        "unavailable": 0,
        "not_reported_in_90_minutes": 0,
        "missing_report_timestamp": 0,
    }
    now = datetime.now(ZoneInfo("UTC"))
    for entity in registry.entities.values():
        if getattr(entity, "config_entry_id", None) != entry.entry_id:
            continue
        summary["registered"] += 1
        device = devices.devices.get(getattr(entity, "device_id", None))
        model = getattr(device, "model", None)
        service = model if model in recognized_services else "Other"
        bucket = by_service.setdefault(
            service,
            {
                "registered": 0,
                "available": 0,
                "unavailable": 0,
                "unknown": 0,
                "disabled": 0,
                "not_loaded": 0,
            },
        )
        bucket["registered"] += 1
        if getattr(entity, "disabled_by", None) is not None:
            bucket["disabled"] += 1
            summary["disabled"] += 1
            continue
        state = hass.states.get(entity.entity_id)
        if state is None:
            summary["not_loaded"] += 1
            bucket["not_loaded"] += 1
            continue
        if state.state == STATE_UNAVAILABLE:
            summary["unavailable"] += 1
            bucket["unavailable"] += 1
        elif state.state == STATE_UNKNOWN:
            summary["unknown"] += 1
            bucket["unknown"] += 1
        else:
            summary["available"] += 1
            bucket["available"] += 1

        last_reported = getattr(state, "last_reported", None)
        if not isinstance(last_reported, datetime) or last_reported.tzinfo is None:
            summary["missing_report_timestamp"] += 1
        elif (now - last_reported).total_seconds() >= 5400:
            summary["not_reported_in_90_minutes"] += 1
    summary["by_service"] = by_service
    return summary


# These are *existing* stable sensor description keys, never account values.
# A source GraphQL success cannot prove these entities were registered or
# received a usable HA state, particularly when a site has gas but the account's
# first delivery site does not.
_GAS_ENTITY_GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "current_prices": (
        SERVICE_NAME_GAS_PRICES,
        ("gas_markup", "gas_market", "gas_hourcount"),
    ),
    "forecast": (
        SERVICE_NAME_GAS_PRICES,
        ("gas_tomorrow_avg", "gas_tomorrow_avg_all_in", "gas_tomorrow_min"),
    ),
    "historical_usage": (
        SERVICE_NAME_USAGE,
        ("usage_gas_yesterday", "costs_gas_yesterday"),
    ),
    "contract": (SERVICE_NAME_USER, ("GascontractStatus",)),
}


def _safe_gas_entity_pipeline(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, object]:
    """Check registration, device grouping and real states without leaking values."""
    if not isinstance(hass, HomeAssistant):
        return {"configured": False}

    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    source_entries = tuple(
        entity
        for entity in registry.entities.values()
        if entity.config_entry_id == entry.entry_id
    )

    result: dict[str, object] = {}
    for group, (expected_model, keys) in _GAS_ENTITY_GROUPS.items():
        statuses: dict[str, object] = {}
        for key in keys:
            matches = [
                entity
                for entity in source_entries
                if isinstance(entity.unique_id, str)
                and entity.unique_id.endswith(f".{key}")
            ]
            if not matches:
                statuses[key] = {"state": "not_registered"}
                continue
            if len(matches) != 1:
                statuses[key] = {"state": "duplicate_registration"}
                continue

            entity = matches[0]
            device = devices.devices.get(entity.device_id)
            matching_device = getattr(device, "model", None) == expected_model
            if entity.disabled_by is not None:
                state = "disabled"
            else:
                current = hass.states.get(entity.entity_id)
                if current is None:
                    state = "not_loaded"
                elif current.state == STATE_UNAVAILABLE:
                    state = "unavailable"
                elif current.state == STATE_UNKNOWN:
                    state = "unknown"
                else:
                    state = "available"
            statuses[key] = {
                "state": state,
                "expected_device_group": expected_model,
                "correct_device_group": matching_device,
            }
        result[group] = statuses
    return result


def _safe_gas_delivery_pipeline(
    hass: HomeAssistant,
    entry: ConfigEntry,
    price_coordinator: object,
    statistics_coordinator: object,
    *,
    local_today: date | None = None,
) -> dict[str, object]:
    """Trace gas provider models to selected prices, usage and real HA sensors.

    This does not fetch extra data. No prices, usage amounts, account IDs,
    addresses or raw provider payloads enter the diagnostic.
    """
    local_tz = ZoneInfo("Europe/Brussels")
    local_today = local_today or datetime.now(local_tz).date()
    tomorrow = local_today + timedelta(days=1)
    price_data = getattr(price_coordinator, "data", None)
    gas = price_data.get("gas") if isinstance(price_data, dict) else None

    all_prices = getattr(gas, "all", None) if gas is not None else None
    slots: Counter[str] = Counter()
    if isinstance(all_prices, (list, tuple)):
        for item in all_prices:
            start = getattr(item, "date_from", None)
            if isinstance(start, datetime) and start.tzinfo is not None:
                local_date = start.astimezone(local_tz).date()
                if local_date in (local_today, tomorrow):
                    slots[local_date.isoformat()] += 1

    # Reflect the actual PriceData.asdict projection used by tomorrow-price
    # sensor attributes. Difference vs. parsed slots identifies a lost step.
    attr_slot_count: int | None = None
    if gas is not None and callable(getattr(gas, "asdict", None)):
        try:
            rendered = gas.asdict(
                "total", tomorrow_only=True, timezone="Europe/Amsterdam"
            )
            if isinstance(rendered, list):
                # The pinned API client returns a one-element *message* or
                # *error* list when no tomorrow data can be rendered. Those
                # placeholders are not price slots and must not look valid.
                attr_slot_count = sum(
                    1
                    for row in rendered
                    if isinstance(row, dict)
                    and isinstance(row.get("from"), datetime)
                    and isinstance(row.get("till"), datetime)
                    and isinstance(row.get("price"), (int, float))
                )
        except (AttributeError, ValueError, KeyError, TypeError):
            pass

    next_day_complete: bool | None = None
    if gas is not None and hasattr(price_coordinator, "_covers_local_day"):
        next_day_complete = bool(price_coordinator._covers_local_day(gas, tomorrow))

    stats = getattr(statistics_coordinator, "data", None)
    usage = stats.get("usage") if isinstance(stats, dict) else None
    gas_usage = getattr(usage, "gas", None) if usage is not None else None
    unit = getattr(gas_usage, "unit", None)
    normalized_unit = unit.upper() if isinstance(unit, str) else None

    return {
        "site_gas_subscription": bool(
            getattr(price_coordinator, "user_gas_enabled", False)
        ),
        "prices": {
            "parsed_price_slots": (
                len(all_prices) if isinstance(all_prices, (list, tuple)) else None
            ),
            "today_slots": slots[local_today.isoformat()],
            "tomorrow_slots": slots[tomorrow.isoformat()],
            "tomorrow_attribute_slots": attr_slot_count,
            "tomorrow_complete": next_day_complete,
            "tomorrow_expected_hours": int(
                (
                    datetime.combine(
                        tomorrow + timedelta(days=1), time.min, local_tz
                    ).astimezone(ZoneInfo("UTC"))
                    - datetime.combine(tomorrow, time.min, local_tz).astimezone(
                        ZoneInfo("UTC")
                    )
                ).total_seconds()
                // 3600
            ),
        },
        "historical_usage": {
            "period": "previous_day",
            "api_segment_parsed": gas_usage is not None,
            "unit": (
                normalized_unit
                if normalized_unit in ("KWH", "M3", "M³", "CUBIC_METERS")
                else "unreported"
                if normalized_unit is None
                else "other"
            ),
            "usage_total_reported": (
                getattr(gas_usage, "usage_total", None) is not None
                if gas_usage is not None
                else False
            ),
            "costs_total_reported": (
                getattr(gas_usage, "costs_total", None) is not None
                if gas_usage is not None
                else False
            ),
        },
        "entities": _safe_gas_entity_pipeline(hass, entry),
        "recorder_history_verified": False,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, object]:
    """Return diagnostics for a config entry."""

    runtime_data: FrankEnergieEntryData = entry.runtime_data
    coordinator = runtime_data.coordinator
    stats = getattr(getattr(runtime_data, "statistics_coordinator", None), "data", None)
    finance_stats = stats if isinstance(stats, dict) else {}

    diagnostics: dict[str, object] = {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            # Do not echo option/data blobs: third-party extensions may add
            # fields not covered by name-based redaction. Only safe flags.
            "options": {
                "monthly_finance_enabled": bool(
                    entry.options.get("monthly_finance_enabled", False)
                ),
            },
            "data": {
                "configured": bool(entry.data),
                "site_selected": bool(entry.data.get("site_reference")),
            },
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "country_code": coordinator.country_code,
            "user_country": getattr(coordinator, "_user_country", None),
            "resolution": coordinator.resolution,
            "api_resolution": coordinator.api_resolution,
            "user_electricity_enabled": coordinator.user_electricity_enabled,
            "user_gas_enabled": coordinator.user_gas_enabled,
            "last_fetch_today": coordinator.last_fetch_today,
            "last_fetch_tomorrow": coordinator.last_fetch_tomorrow,
            "has_cached_prices": coordinator.cached_prices is not None,
            "has_cached_today": coordinator.cached_prices_today is not None,
            # Legacy cached_prices_today may be absent after a successful live
            # fetch. _static_prices_today is the actual active daily cache.
            "has_active_today_prices": (
                getattr(coordinator, "_static_prices_today", None) is not None
            ),
            "has_cached_tomorrow": coordinator.cached_prices_tomorrow is not None,
            "resolution_change_pending": getattr(
                coordinator,
                "_resolution_change_pending",
                False,
            ),
        },
        # The original diagnostics dumped complete provider account objects.
        # That leaks billing, connection, referral and other nested personal
        # data despite key-based redaction. Keep only bounded evidence.
        "raw_provider_payloads": "omitted_for_privacy",
        "cached_prices_present": coordinator.cached_prices is not None,
        "battery_session_coordinators": len(
            runtime_data.battery_session_coordinators,
        ),
        "runtime_health": {
            # No private fields; a green coordinator transport is not proof
            # that the provider returned usable account or tariff data.
            "authenticated": bool(
                getattr(getattr(coordinator, "api", None), "is_authenticated", False)
            ),
            "site_metadata_present": bool(
                (
                    getattr(
                        getattr(runtime_data, "settings_coordinator", None),
                        "data",
                        None,
                    )
                    or {}
                ).get("user_sites")
            ),
            "current_electricity_price_present": (
                getattr((coordinator.data or {}).get("electricity"), "current", None)
                is not None
            ),
            "current_gas_price_present": (
                getattr((coordinator.data or {}).get("gas"), "current", None)
                is not None
            ),
            "maintenance_window_utc": datetime.now(ZoneInfo("UTC")).hour == 0,
            "monthly_finance_opted_in": bool(
                entry.options.get("monthly_finance_enabled", False)
            ),
        },
        "ha_entity_state_summary": _safe_ha_entity_state_summary(hass, entry),
        "graphql_request_evidence": (
            trace.snapshot()
            if (trace := getattr(coordinator, "_diagnostic_trace", None)) is not None
            else {"configured": False}
        ),
        "source_check_evidence": source_check_evidence(
            getattr(coordinator, "_diagnostic_trace", None),
            authenticated=bool(
                getattr(getattr(coordinator, "api", None), "is_authenticated", False)
            ),
            site_selected=bool(entry.data.get("site_reference")),
            monthly_finance_enabled=bool(
                entry.options.get("monthly_finance_enabled", False)
            ),
            price_cache_present=(
                getattr(coordinator, "_static_prices_today", None) is not None
            ),
            local_today=datetime.now(ZoneInfo("Europe/Brussels")).date(),
        ),
        # Explicit cross-coordinator evidence: a healthy price coordinator
        # cannot be used to infer invoice, metering or monthly-finance health.
        "coordinator_evidence": {
            "price": _bounded_coordinator_evidence(
                getattr(runtime_data, "price_coordinator", None)
            ),
            "settings": _bounded_coordinator_evidence(
                getattr(runtime_data, "settings_coordinator", None)
            ),
            "statistics": _bounded_coordinator_evidence(
                getattr(runtime_data, "statistics_coordinator", None)
            ),
            # The same service-device timing metadata shown on HA entities.
            # This never exposes customer amounts or a false exact timer.
            "refresh_schedule": {
                service.key: {
                    "device_group": service.device_name,
                    **service_refresh_attributes(
                        service,
                        getattr(coordinator, "_diagnostic_trace", None),
                    ),
                }
                for service in service_refreshes(runtime_data)
            },
            "price_coverage": _safe_price_coverage(coordinator.data),
            "gas_delivery_pipeline": _safe_gas_delivery_pipeline(
                hass,
                entry,
                getattr(runtime_data, "price_coordinator", None),
                getattr(runtime_data, "statistics_coordinator", None),
            ),
            "finance_coverage": _safe_finance_coverage(
                getattr(
                    getattr(runtime_data, "statistics_coordinator", None), "data", None
                )
            ),
            "finance_audit": finance_evidence(
                finance_stats.get("month_insights_current"),
                finance_stats.get("month_insights_previous"),
                finance_stats.get("month_summary"),
            ),
            "provider_data_freshness": _safe_provider_data_freshness(
                getattr(
                    getattr(runtime_data, "statistics_coordinator", None), "data", None
                )
            ),
            "contract_capabilities": _safe_contract_capabilities(
                getattr(
                    getattr(runtime_data, "settings_coordinator", None), "data", None
                )
            ),
        },
    }

    health = diagnostics["runtime_health"]
    warnings: list[str] = []
    if health["authenticated"] and not health["site_metadata_present"]:
        warnings.append("authenticated_site_metadata_missing")
    if (
        not health["current_electricity_price_present"]
        and not health["current_gas_price_present"]
    ):
        warnings.append("current_prices_missing")
    diagnostics["runtime_warnings"] = warnings

    return async_redact_data(diagnostics, TO_REDACT)
