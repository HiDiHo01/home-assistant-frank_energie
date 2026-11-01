"""Shared service-device refresh controls and truthful polling metadata.

A device groups HA entities; it is not necessarily a separate API source.
Multiple controls can therefore refer to one coordinator. Request timestamps
come only from the privacy-safe GraphQL observer, never HA state changes.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DATA_BATTERIES,
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DATA_PV_SYSTEMS,
    SERVICE_NAME_BATTERIES,
    SERVICE_NAME_COSTS,
    SERVICE_NAME_ENODE_CHARGERS,
    SERVICE_NAME_ENODE_VEHICLES,
    SERVICE_NAME_GAS_PRICES,
    SERVICE_NAME_PRICES,
    SERVICE_NAME_PV_SYSTEMS,
    SERVICE_NAME_USAGE,
    SERVICE_NAME_USER,
)


@dataclass(frozen=True, slots=True)
class RefreshService:
    """One service-device facade over an existing coordinator."""

    key: str
    device_name: str
    coordinator: DataUpdateCoordinator
    operations: tuple[str, ...]


def service_refreshes(runtime_data: object) -> tuple[RefreshService, ...]:
    """Return only services present on this entry; never create ghost devices."""
    price = getattr(runtime_data, "price_coordinator", None) or getattr(
        runtime_data, "coordinator", None
    )
    if price is None:
        return ()
    settings = getattr(runtime_data, "settings_coordinator", None)
    statistics = getattr(runtime_data, "statistics_coordinator", None)
    api_authenticated = bool(
        getattr(getattr(settings, "api", None), "is_authenticated", False)
    )

    services = [
        RefreshService(
            "prices",
            SERVICE_NAME_PRICES,
            price,
            ("MarketPrices", "CustomerMarketPrices", "UserPrices"),
        ),
    ]
    if not api_authenticated or getattr(price, "user_gas_enabled", False):
        services.append(
            RefreshService(
                "gas_prices",
                SERVICE_NAME_GAS_PRICES,
                price,
                ("MarketPrices", "CustomerMarketPrices", "UserPrices"),
            )
        )

    if api_authenticated and statistics is not None and settings is not None:
        services.extend(
            (
                RefreshService(
                    "costs",
                    SERVICE_NAME_COSTS,
                    statistics,
                    ("MonthSummary", "MonthInsights", "Invoices"),
                ),
                RefreshService(
                    "usage",
                    SERVICE_NAME_USAGE,
                    statistics,
                    ("PeriodUsageAndCosts",),
                ),
                RefreshService(
                    "user",
                    SERVICE_NAME_USER,
                    settings,
                    ("Me", "UserSites"),
                ),
            )
        )

        inventory: tuple[tuple[str, str, str, str, str], ...] = (
            (
                "batteries",
                SERVICE_NAME_BATTERIES,
                "battery_coordinator",
                DATA_BATTERIES,
                "batteries",
            ),
            (
                "chargers",
                SERVICE_NAME_ENODE_CHARGERS,
                "charger_coordinator",
                DATA_ENODE_CHARGERS,
                "chargers",
            ),
            (
                "vehicles",
                SERVICE_NAME_ENODE_VEHICLES,
                "vehicle_coordinator",
                DATA_ENODE_VEHICLES,
                "vehicles",
            ),
            (
                "solar",
                SERVICE_NAME_PV_SYSTEMS,
                "pv_coordinator",
                DATA_PV_SYSTEMS,
                "systems",
            ),
        )
        operations = {
            "batteries": ("SmartBatteries",),
            "chargers": ("EnodeChargers",),
            "vehicles": ("EnodeVehicles",),
            "solar": ("SmartPvSystems",),
        }
        for key, name, coordinator_attr, data_key, collection_attr in inventory:
            coordinator = getattr(runtime_data, coordinator_attr, None)
            data = getattr(coordinator, "data", None)
            records = (
                getattr(data.get(data_key), collection_attr, None)
                if isinstance(data, dict)
                else None
            )
            if records:
                services.append(RefreshService(key, name, coordinator, operations[key]))
    return tuple(services)


def service_refresh_interval_minutes(service: RefreshService) -> float | None:
    """Reflect the live coordinator interval; None means no interval timer."""
    interval = getattr(service.coordinator, "update_interval", None)
    if not isinstance(interval, timedelta) or interval.total_seconds() <= 0:
        return None
    return round(interval.total_seconds() / 60, 2)


def service_refresh_attributes(
    service: RefreshService, trace: object | None
) -> dict[str, object]:
    """Return bounded, source-backed timing evidence without account data."""
    minutes = service_refresh_interval_minutes(service)
    result: dict[str, object] = {
        "polling_interval_minutes": minutes,
        "polling_scheduled": minutes is not None,
        "coordinator_update_success": bool(
            getattr(service.coordinator, "last_update_success", False)
        ),
        # This is an estimate, not HA's private internal timer deadline.
        "next_refresh_is_estimate": True,
        "polling_notes": (
            "Coordinator interval, not source publication or an exact HA timer. "
            "May change after each refresh; requests pause 00:00-01:00 UTC."
        ),
        "next_estimated_refresh_utc": None,
        "last_api_request_utc": None,
        "last_successful_api_response_utc": None,
        "last_api_outcome": None,
    }
    if service.key in ("prices", "gas_prices"):
        result["price_slot_updates"] = (
            "local 15/60-minute source slot boundaries; no API request required"
        )
        fetched = getattr(service.coordinator, "last_fetch_today", None)
        result["last_today_price_fetch_utc"] = (
            fetched.astimezone(UTC).isoformat()
            if isinstance(fetched, datetime) and fetched.tzinfo is not None
            else None
        )

    if trace is None or not callable(getattr(trace, "snapshot", None)):
        return result
    events = trace.snapshot().get("events", [])
    if not isinstance(events, list):
        return result

    sources = (
        ("graphql_account", "graphql_public")
        if service.key in ("prices", "gas_prices")
        else ("graphql_account",)
    )
    latest: datetime | None = None
    for event in reversed(events):
        if (
            not isinstance(event, dict)
            or event.get("source") not in sources
            or event.get("operation") not in service.operations
        ):
            continue
        at = event.get("at_utc")
        if not isinstance(at, str):
            continue
        try:
            timestamp = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.tzinfo is None:
            continue
        timestamp = timestamp.astimezone(UTC)
        if latest is None:
            latest = timestamp
            result["last_api_request_utc"] = timestamp.isoformat()
            result["last_api_outcome"] = event.get("outcome")
        if (
            result["last_successful_api_response_utc"] is None
            and event.get("outcome") == "ok"
        ):
            # API response success is not guaranteed financial completeness.
            result["last_successful_api_response_utc"] = timestamp.isoformat()
        if (
            latest is not None
            and result["last_successful_api_response_utc"] is not None
        ):
            break
    if latest is not None and minutes is not None:
        result["next_estimated_refresh_utc"] = (
            latest + timedelta(minutes=minutes)
        ).isoformat()
    return result


def service_api_status(service: RefreshService, trace: object | None) -> str:
    """Report the latest GraphQL transport outcome for this service.

    A connection-level success does not mean every account data source is
    complete, priced, published, or suitable for invoicing. Price cache
    restores without a new request remain explicitly not_checked.
    """
    if trace is None or not callable(getattr(trace, "snapshot", None)):
        return "not_checked"
    snapshot = trace.snapshot()
    events = snapshot.get("events") if isinstance(snapshot, dict) else None
    if not isinstance(events, list):
        return "not_checked"
    sources = (
        ("graphql_public", "graphql_account")
        if service.key in ("prices", "gas_prices")
        else ("graphql_account",)
    )
    for event in reversed(events):
        if (
            not isinstance(event, dict)
            or event.get("source") not in sources
            or event.get("operation") not in service.operations
        ):
            continue
        if event.get("http_error_status") == 429:
            return "rate_limited"
        if event.get("outcome") == "ok":
            return "connected"
        return "error"
    return "not_checked"
