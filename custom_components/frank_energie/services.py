"""Read-only actions for Frank Energie tariffs and Smart Battery results."""

import logging
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import probatio
from aiohttp import ClientError
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.selector import ConfigEntrySelector
from python_frank_energie.exceptions import (
    AuthException,
    AuthRequiredException,
    FrankEnergieException,
    RateLimitException,
)

from .battery_results import battery_range_result
from .const import (
    DATA_BATTERIES,
    DATA_DELIVERY_SITE,
    DATA_ELECTRICITY,
    DATA_GAS,
    DOMAIN,
    TIMEZONE_AMSTERDAM,
    UNIT_ELECTRICITY,
)
from .device_inventory import validated_inventory_ids
from .helpers import (
    api_maintenance_active,
    known_site_contracts,
    price_date_window,
    prices_covering_window,
    resolve_gas_unit,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from python_frank_energie.models import Price

    from . import FrankEnergieEntryData
    from .coordinator import FrankEnergieBatteryCoordinator


_LOGGER = logging.getLogger(__name__)
SERVICE_GET_PRICES_FOR_DATE: Final = "get_prices_for_date"
SERVICE_GET_BATTERY_RESULTS: Final = "get_battery_results"
_GET_PRICES_SCHEMA: Final = probatio.Schema(
    {
        probatio.Required("config_entry"): ConfigEntrySelector({"integration": DOMAIN}),
        probatio.Required("date"): cv.date,
        probatio.Required("energy_type"): probatio.In((DATA_ELECTRICITY, DATA_GAS)),
    }
)


def _calendar_date(value: object) -> date:
    """Require a calendar date so the SDK never sends a timestamp."""
    if isinstance(value, datetime):
        raise probatio.Invalid("Expected a calendar date without a time")
    return cv.date(value)


_BATTERY_RESULTS_SCHEMA: Final = probatio.Schema(
    {
        probatio.Required("config_entry"): ConfigEntrySelector({"integration": DOMAIN}),
        probatio.Required("start_date"): _calendar_date,
        probatio.Required("end_date"): _calendar_date,
    }
)


def _unavailable_prices(day: date) -> ServiceValidationError:
    """Explain that the selected published window has no usable cached prices."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="prices_for_date_unavailable",
        translation_placeholders={"date": day.isoformat()},
    )


def _gas_price_unit(periods: list[Price], fallback: str | None) -> str | None:
    """Require one supported gas unit across all returned intervals."""
    units = {resolve_gas_unit(price.per_unit or fallback) for price in periods}
    return units.pop() if len(units) == 1 else None


@callback
def _async_get_prices_for_date(call: ServiceCall) -> ServiceResponse:
    """Return validated cached price intervals for a single configuration and date."""
    entry: ConfigEntry[FrankEnergieEntryData] = service.async_get_config_entry(
        call.hass, DOMAIN, call.data["config_entry"]
    )
    coordinator = entry.runtime_data.price_coordinator
    settings_data = entry.runtime_data.settings_coordinator.data or {}
    energy_type = call.data["energy_type"]
    contracts = known_site_contracts(settings_data.get(DATA_DELIVERY_SITE))
    if (
        coordinator.api.is_authenticated
        and contracts is not None
        and energy_type.upper() not in contracts
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="price_contract_unavailable"
        )

    day: date = call.data["date"]
    tomorrow = datetime.now(UTC).astimezone(
        ZoneInfo(TIMEZONE_AMSTERDAM)
    ).date() + timedelta(days=1)
    if day > tomorrow:
        raise _unavailable_prices(day)

    is_gas = energy_type == DATA_GAS
    try:
        start, end = price_date_window(day, tomorrow_gas=is_gas and day == tomorrow)
    except OverflowError as err:
        raise _unavailable_prices(day) from err

    series = (coordinator.data or {}).get(DATA_GAS if is_gas else DATA_ELECTRICITY)
    if series is None or not (periods := prices_covering_window(series, start, end)):
        raise _unavailable_prices(day)
    if any(
        not isfinite(price.market_price) or not isfinite(price.total)
        for price in periods
    ):
        raise _unavailable_prices(day)

    unit = _gas_price_unit(periods, series.per_unit) if is_gas else UNIT_ELECTRICITY
    if unit is None:
        raise _unavailable_prices(day)

    return {
        "date": day.isoformat(),
        "energy_type": energy_type,
        "unit": unit,
        "coverage": {"start": start.isoformat(), "end": end.isoformat()},
        "prices": [
            {
                "start": price.date_from.isoformat(),
                "end": price.date_till.isoformat(),
                "market_price": price.market_price,
                "all_in_price": price.total,
            }
            for price in periods
        ],
    }


def _battery_ids(coordinator: FrankEnergieBatteryCoordinator) -> list[str]:
    """Read the selected configuration's last successful battery inventory."""
    if not coordinator.api.is_authenticated or not coordinator.site_reference:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="battery_results_auth_required"
        )
    ids = validated_inventory_ids(
        "batteries", (coordinator.data or {}).get(DATA_BATTERIES)
    )
    if ids is None or not coordinator.last_update_success:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="battery_inventory_unavailable"
        )
    return sorted(ids)


async def _async_get_battery_results(call: ServiceCall) -> ServiceResponse:
    """Fetch one inclusive range per battery without changing polling caches."""
    start: date = call.data["start_date"]
    end: date = call.data["end_date"]
    if end < start:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="battery_results_reversed_dates"
        )
    entry: ConfigEntry[FrankEnergieEntryData] = service.async_get_config_entry(
        call.hass, DOMAIN, call.data["config_entry"]
    )
    coordinator = entry.runtime_data.battery_coordinator
    results: dict[str, dict[str, object] | None] = dict.fromkeys(
        _battery_ids(coordinator)
    )
    for battery_id in results:
        if api_maintenance_active(datetime.now(UTC)):
            break
        try:
            data = await coordinator.async_api_call(
                "smart_battery_sessions",
                coordinator.api.smart_battery_sessions,
                battery_id,
                start,
                end,
            )
        except (RateLimitException, AuthException, AuthRequiredException) as err:
            _LOGGER.debug(
                "Stopping Smart Battery date-range requests (%s)", type(err).__name__
            )
            break
        except (
            FrankEnergieException,
            ClientError,
            TimeoutError,
            ValueError,
            TypeError,
        ) as err:
            _LOGGER.debug(
                "Smart Battery date-range results unavailable (%s)", type(err).__name__
            )
            continue
        results[battery_id] = battery_range_result(data, battery_id, start, end)

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "batteries": results,
    }


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the domain's response-only actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_PRICES_FOR_DATE,
        _async_get_prices_for_date,
        schema=_GET_PRICES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_BATTERY_RESULTS,
        _async_get_battery_results,
        schema=_BATTERY_RESULTS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
