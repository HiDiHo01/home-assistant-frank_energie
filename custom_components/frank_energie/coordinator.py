"""Coordinator implementation for Frank Energie integration.
Fetching the latest data from Frank Energie and updating the states."""

# coordinator.py
# version 2026.10.23
from __future__ import annotations

import asyncio
import copy
import logging
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any, Final, TypedDict, cast
from zoneinfo import ZoneInfo

from aiohttp import ClientError
from homeassistant.components.persistent_notification import async_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import (
    AuthException,
    AuthRequiredException,
    FrankEnergieException,
    NetworkError,
    NoMarketPricesAvailableException,
    RequestException,
)
from python_frank_energie.models import (
    ContractPriceResolutionState,
    EnodeChargers,
    EnodeVehicle,
    EnodeVehicles,
    Invoices,
    MarketPrices,
    MonthInsights,
    MonthSummary,
    PeriodUsageAndCosts,
    Price,
    PriceData,
    Resolution,
    SmartBatteries,
    SmartBatteryDetails,
    SmartBatterySessions,
    SmartPvSystems,
    SmartPvSystemSummary,
    User,
    UserSites,
    UserSmartFeedInStatus,
)

from .const import (
    CONF_INTERVAL_BATTERIES,
    CONF_INTERVAL_BATTERY_SESSIONS,
    CONF_INTERVAL_CHARGERS,
    CONF_INTERVAL_PV,
    CONF_INTERVAL_SETTINGS,
    CONF_INTERVAL_STATISTICS,
    CONF_MONTHLY_FINANCE_ENABLED,
    CONF_INTERVAL_VEHICLES,
    COUNTRY_USES_SIMPLE_MARKET_PRICES,
    DATA_BATTERIES,
    DATA_BATTERY_DETAILS,
    DATA_BATTERY_SESSIONS,
    DATA_CONTRACT_PRICE_RESOLUTION_STATE,
    DATA_ELECTRICITY,
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DATA_GAS,
    DATA_INVOICES,
    DATA_MONTH_SUMMARY,
    DATA_MONTH_INSIGHTS_CURRENT,
    DATA_MONTH_INSIGHTS_PREVIOUS,
    DATA_PV_SUMMARY,
    DATA_PV_SYSTEMS,
    DATA_REFRESH_TOKEN_EXPIRES_AT,
    DATA_TOKEN_EXPIRES_AT,
    DATA_USAGE,
    DATA_USER,
    DATA_USER_SITES,
    DATA_USER_SMART_FEED_IN,
    DEFAULT_INTERVAL_BATTERIES,
    DEFAULT_INTERVAL_BATTERY_SESSIONS,
    DEFAULT_INTERVAL_CHARGERS,
    DEFAULT_INTERVAL_PRICES,
    DEFAULT_INTERVAL_PV,
    DEFAULT_INTERVAL_SETTINGS,
    DEFAULT_INTERVAL_STATISTICS,
    DEFAULT_INTERVAL_VEHICLES,
    DEFAULT_RESOLUTION,
    EVENT_FRANK_ENERGIE,
    SUPPORTED_COUNTRIES,
    SUPPORTED_RESOLUTIONS,
    TIMEZONE_AMSTERDAM,
    TOMORROW_PUBLICATION_HOUR_LOCAL,
)
from .diagnostic_trace import DiagnosticTrace, attach_graphql_trace
from .finance import MonthlyFinanceSnapshot, month_keys
from .helpers import decrypt_password, price_cache_store
from .price_coverage import covered_seconds_in_local_day, covers_local_day
from .mutation_queue import MutationQueue

_LOGGER = logging.getLogger(__name__)
_LOG_AUTH_TOKENS_EXPIRED: Final = (
    "Authentication tokens expired, trying to renew them (%s)"
)


def _get_token_expires_at(api: Any) -> datetime | None:
    """Safely get token expiration timestamp for backward compatibility."""
    if hasattr(api, "token_expires_at"):
        return api.token_expires_at
    if hasattr(api, "_auth") and api._auth and hasattr(api._auth, "expires_at"):
        return api._auth.expires_at
    return None


def _get_refresh_token_expires_at(api: Any) -> datetime | None:
    """Safely get refresh token expiration timestamp for backward compatibility."""
    if hasattr(api, "refresh_token_expires_at"):
        return api.refresh_token_expires_at
    if (
        hasattr(api, "_auth")
        and api._auth
        and hasattr(api._auth, "refresh_token_expires_at")
    ):
        return api._auth.refresh_token_expires_at
    return None


def _amsterdam_date(moment: datetime) -> date:
    """Return the Amsterdam-local calendar date of a timestamp.

    ``last_fetch_today`` / ``last_fetch_tomorrow`` are stored as UTC instants,
    but every freshness check compares them against ``today`` / ``tomorrow``,
    which are Amsterdam-local dates. ``moment.date()`` on the raw UTC value is
    a day behind for the ~1-2h after local midnight (Amsterdam is always ahead
    of UTC), which would wrongly treat a just-completed fetch as stale.
    """
    return moment.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM)).date()


def _price_to_dict(price: Price) -> dict[str, Any]:
    """Convert Price to dict."""
    return {
        "from": price.date_from.isoformat() if price.date_from else None,
        "till": price.date_till.isoformat() if price.date_till else None,
        "marketPrice": price.market_price,
        "marketPriceTax": price.market_price_tax,
        "sourcingMarkupPrice": price.sourcing_markup_price,
        "energyTaxPrice": price.energy_tax_price,
        "perUnit": price.per_unit,
    }


def _market_prices_to_dict(market_prices: MarketPrices) -> dict[str, Any]:
    """Convert MarketPrices to serializable dict."""
    return {
        "electricity": [_price_to_dict(p) for p in market_prices.electricity.all]
        if market_prices.electricity
        else [],
        "gas": [_price_to_dict(p) for p in market_prices.gas.all]
        if market_prices.gas
        else [],
        "energy_country": market_prices.energy_country,
        "energy_type": market_prices.energy_type,
        "electricity_resolution_minutes": (
            market_prices.electricity.resolution_minutes
            if market_prices.electricity
            else None
        ),
        "gas_resolution_minutes": (
            market_prices.gas.resolution_minutes if market_prices.gas else None
        ),
    }


def _dict_to_market_prices(data: dict[str, Any]) -> MarketPrices:
    """Reconstruct MarketPrices from serialized dict."""
    elec_prices = data.get("electricity", [])
    gas_prices = data.get("gas", [])
    country = data.get("energy_country", "NL")
    energy_type = data.get("energy_type")

    if elec_prices and data.get("electricity_resolution_minutes") is None:
        _LOGGER.debug(
            "Cached electricity prices have no persisted resolution_minutes "
            "(older cache format); relying on PriceData to derive it from "
            "entry spacing"
        )
    if gas_prices and data.get("gas_resolution_minutes") is None:
        _LOGGER.debug(
            "Cached gas prices have no persisted resolution_minutes "
            "(older cache format); relying on PriceData to derive it from "
            "entry spacing"
        )

    electricity = PriceData(
        prices=elec_prices,
        energy_type="electricity",
        resolution_minutes=data.get("electricity_resolution_minutes") or 60,
    )
    gas = PriceData(
        prices=gas_prices,
        energy_type="gas",
        resolution_minutes=data.get("gas_resolution_minutes") or 60,
    )

    return MarketPrices(
        electricity=electricity,
        gas=gas,
        energy_country=country,
        energy_type=energy_type,
    )


def _price_counts(market_prices: MarketPrices | None) -> tuple[int, int]:
    """Return (electricity_count, gas_count) for a MarketPrices.

    Explicit attribute access rather than getattr(..., energy_type) so a
    typo'd energy type can't silently resolve to 0 instead of failing —
    there's no string parameter here to misuse. Centralized so any future
    logging that needs these counts reuses this instead of duplicating the
    electricity/gas lookup at each call site.
    """
    if market_prices is None:
        return 0, 0
    # Test doubles and partially parsed responses may be non-MarketPrices
    # sentinel objects. Observability must NEVER break a provider call just
    # because the returned object has no electricity/gas stream.
    electricity = getattr(getattr(market_prices, "electricity", None), "all", None)
    gas = getattr(getattr(market_prices, "gas", None), "all", None)
    return (
        len(electricity) if isinstance(electricity, (list, tuple)) else 0,
        len(gas) if isinstance(gas, (list, tuple)) else 0,
    )


def _price_selection_outcome(
    selected: MarketPrices | None,
    *,
    authenticated: bool,
    customer_electricity: bool = False,
    customer_gas: bool = False,
    fallback_from_cache: bool = False,
) -> str:
    """Classify selected slots, not merely successful API requests.

    Missing customer slots do not prove a public fallback: fallback must
    provide actual data. A cache-only response is not a fresh market feed.
    This affects diagnostic provenance, not selected price values.
    """
    electricity_slots, gas_slots = _price_counts(selected)
    if not electricity_slots and not gas_slots:
        return "not_available"
    if not authenticated:
        return "cached_fallback" if fallback_from_cache else "public"
    has_customer = (customer_electricity and electricity_slots > 0) or (
        customer_gas and gas_slots > 0
    )
    has_fallback = (not customer_electricity and electricity_slots > 0) or (
        not customer_gas and gas_slots > 0
    )
    if has_customer and has_fallback:
        return "mixed"
    if has_fallback:
        return "cached_fallback" if fallback_from_cache else "public_fallback"
    return "customer"


def _site_segment_flags(
    user_sites: UserSites | None, site_reference: str | None
) -> tuple[bool, bool]:
    """Identify the selected site's electricity/gas subscription segments.

    UserSites.segments belongs to the client's first site, so never reuse it
    for a different selected delivery site. Missing/unmatched metadata must
    not enable public price fallback for an unrelated site.
    """
    if user_sites is None or not site_reference:
        return False, False

    segments: object = None
    delivery_sites = getattr(user_sites, "deliverySites", None)
    if isinstance(delivery_sites, (list, tuple)):
        for site in delivery_sites:
            if getattr(site, "reference", None) == site_reference:
                segments = getattr(site, "segments", None)
                break

    # The pinned client also exposes first-site fields at UserSites level.
    if segments is None and getattr(user_sites, "reference", None) == site_reference:
        segments = getattr(user_sites, "segments", None)
    if not isinstance(segments, (list, tuple, set, frozenset)):
        return False, False

    normalized = {item.upper() for item in segments if isinstance(item, str)}
    return "ELECTRICITY" in normalized, "GAS" in normalized


def _resolution_state_to_dict(state: ContractPriceResolutionState) -> dict[str, Any]:
    """Convert ContractPriceResolutionState to serializable dict."""
    return {
        "activeOption": state.active_option,
        "availableOptions": state.available_options,
        "changeRequestEffectiveDate": (
            state.change_request_effective_date.isoformat()
            if isinstance(state.change_request_effective_date, (date, datetime))
            else state.change_request_effective_date
        ),
        "isChangeRequestPossible": state.is_change_request_possible,
        "upcomingChange": (
            state.upcoming_change.isoformat()
            if isinstance(state.upcoming_change, (date, datetime))
            else state.upcoming_change
        ),
        "upcomingChangeEffectiveDate": (
            state.upcoming_change_effective_date.isoformat()
            if isinstance(state.upcoming_change_effective_date, (date, datetime))
            else state.upcoming_change_effective_date
        ),
    }


def _dict_to_resolution_state(data: dict[str, Any]) -> ContractPriceResolutionState:
    """Reconstruct ContractPriceResolutionState from dict."""
    state = ContractPriceResolutionState.from_dict(data)
    if isinstance(state.upcoming_change, str):
        parsed = dt_util.parse_datetime(state.upcoming_change)
        if parsed:
            state.upcoming_change = parsed
    return state


def _extract_electricity_connection_id(user_data: Any) -> str | None:
    """Extract the ELECTRICITY connection ID from user data."""
    if not user_data or not getattr(user_data, "connections", None):
        return None

    for connection in user_data.connections:
        if isinstance(connection, dict):
            cid = connection.get("connectionId")
            segment = connection.get("segment")
        else:
            cid = getattr(connection, "connectionId", None)
            segment = getattr(connection, "segment", None)

        if cid and segment == "ELECTRICITY":
            return str(cid)

    _LOGGER.debug(
        "No ELECTRICITY segment connection found in user data. "
        "Resolution changes are only supported for electricity."
    )
    return None


if sys.platform == "win32" and hasattr(asyncio, "set_event_loop_policy"):
    # Python 3.14-3.16
    try:
        # Proactor is default on Python 3.17+, but explicitly set it for 3.14-3.16 to avoid Selector which is deprecated and can cause issues with aiohttp
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        # fallback for Python 3.17+ where Proactor policy is default and Selector policy is deprecated
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


ERROR_NO_MARKET_PRICES: Final[str] = "No marketprices found"


def _is_no_market_prices_error(ex: Exception) -> bool:
    """Check if the exception represents a 'No market prices available' error."""
    if isinstance(ex, NoMarketPricesAvailableException):
        return True
    return isinstance(ex, RequestException) and ERROR_NO_MARKET_PRICES in str(ex)


class FrankEnergieData(TypedDict):
    """Represents data fetched from Frank Energie API."""

    electricity: PriceData | None
    """Electricity price data."""

    gas: PriceData | None
    """Gas price data."""

    month_summary: MonthSummary | None
    """Optional summary data for the month."""

    month_insights_current: MonthlyFinanceSnapshot | None
    """Optional source-backed current calendar month finance details."""

    month_insights_previous: MonthlyFinanceSnapshot | None
    """Optional source-backed previous calendar month finance details."""

    invoices: Invoices | None
    """Optional invoices data."""

    usage: PeriodUsageAndCosts | None
    """Optional user data."""

    user: User | None
    """Optional user data."""

    user_sites: UserSites | None
    """Optional user sites."""

    enode_chargers: dict[str, EnodeChargers] | None
    """Optional Enode chargers data."""

    enode_vehicles: EnodeVehicles | None
    """Optional Enode vehicles data."""

    batteries: SmartBatteries | None
    """Optional smart batteries data."""

    battery_details: list[SmartBatteryDetails]
    """Optional smart battery details data."""

    battery_sessions: list[SmartBatterySessions]
    """Optional smart battery sessions data."""

    smart_pv_systems: SmartPvSystems | None
    """Optional smart PV systems data."""

    smart_pv_summary: dict[str, SmartPvSystemSummary] | None
    """Optional smart PV system summary data."""

    user_smart_feed_in: UserSmartFeedInStatus | None
    """Optional user smart feed-in status."""

    contract_price_resolution_state: ContractPriceResolutionState | None
    """Optional contract price resolution state."""


@dataclass(frozen=True)
class PricesTodayCache:
    prices_today: MarketPrices | None
    data_invoices: Invoices | None
    data_user: User | None
    user_sites: UserSites | None
    data_period_usage: PeriodUsageAndCosts | None
    data_enode_chargers: dict[str, EnodeChargers] | None
    data_smart_batteries: SmartBatteries | None
    data_smart_battery_details: list[SmartBatteryDetails]
    data_smart_battery_sessions: list[SmartBatterySessions]
    data_enode_vehicles: EnodeVehicles | None
    data_pv_systems: SmartPvSystems | None
    data_pv_summary: dict[str, SmartPvSystemSummary] | None
    data_user_smart_feed_in: UserSmartFeedInStatus | None
    data_contract_price_resolution_state: ContractPriceResolutionState | None
    data_month_summary: MonthSummary | None = None


def _empty_data() -> FrankEnergieData:
    """Create an empty Frank Energie data structure."""

    return {  # type: ignore[typeddict-unknown-key]
        DATA_ELECTRICITY: None,
        DATA_GAS: None,
        DATA_MONTH_SUMMARY: None,
        DATA_MONTH_INSIGHTS_CURRENT: None,
        DATA_MONTH_INSIGHTS_PREVIOUS: None,
        DATA_INVOICES: None,
        DATA_USAGE: None,
        DATA_USER: None,
        DATA_USER_SITES: None,
        DATA_ENODE_CHARGERS: None,
        DATA_BATTERIES: None,
        DATA_BATTERY_DETAILS: [],
        DATA_BATTERY_SESSIONS: [],
        DATA_ENODE_VEHICLES: None,
        DATA_PV_SYSTEMS: None,
        DATA_PV_SUMMARY: None,
        DATA_USER_SMART_FEED_IN: None,
        DATA_CONTRACT_PRICE_RESOLUTION_STATE: None,
        DATA_REFRESH_TOKEN_EXPIRES_AT: None,
        DATA_TOKEN_EXPIRES_AT: None,
    }


class FrankEnergieCoordinator(DataUpdateCoordinator[FrankEnergieData]):
    """Get the latest data and update the states."""

    # Define the hour at which to fetch tomorrow's prices in UTC
    # This is set to 12 UTC, which corresponds to 14:00 UTC+2
    # If you want to change it to 13:00 UTC, uncomment the line
    # FETCH_TOMORROW_HOUR_UTC = 13  # 13:00 UTC
    # This means that if the current time is after 12:00 UTC, the coordinator will fetch tomorrow's prices
    # at 12:00 UTC,
    # which corresponds to 14:00 in UTC+2 timezone (e.g., Central European Summer Time).
    FETCH_TOMORROW_HOUR_UTC: Final[int] = 11
    PRICE_RELEASE_START_UTC: Final[time] = time(11, 0)
    PRICE_RELEASE_END_UTC: Final[time] = time(13, 0)

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, api: FrankEnergie
    ) -> None:
        """Initialize the data object."""
        super().__init__(
            hass,
            _LOGGER,
            name="Frank Energie coordinator",
            update_interval=None,
            config_entry=config_entry,
        )
        self._mutation_queue = MutationQueue()
        self._auth_lock = self._get_shared_auth_lock(api)
        self.hass = hass
        self.config_entry = config_entry
        self.api = api
        self._diagnostic_trace = getattr(
            api, "_frank_diagnostic_trace", DiagnosticTrace()
        )
        self._today_prices_logged: bool = False
        self._tomorrow_prices_logged: bool = False
        self._cache: dict[str, object] = {}  # <--- hier cache je prijzen
        # self.site_reference = config_entry.data.get("site_reference", None)
        self._site_reference: str | None = config_entry.data.get("site_reference")
        self.country_code: str | None = (
            self.hass.config.country
        )  # replaced by hass_country_code
        self._country_code: str | None = self.hass.config.country
        self.hass_country_code: str | None = self.hass.config.country
        self._user_country: str | None = self.country_code
        # Cached electricity connection ID for contractPriceResolutionState.
        self._connection_id: str | None = None
        self._api_resolution_state: ContractPriceResolutionState | None = None
        self._resolution_change_pending: bool = False
        self.enode_chargers: EnodeChargers | None = None
        self.data: FrankEnergieData = _empty_data()
        self.update_interval = timedelta(
            minutes=15 if self.resolution == "PT15M" else 60
        )
        self._last_update_success = False
        self.user_electricity_enabled = False
        self.user_gas_enabled = False
        # All coordinators inherit price selection for upstream compatibility.
        # Only a live provider reply can establish process-local provenance.
        self._price_sources_by_day: dict[str, dict[str, str]] = {}

        self.cached_prices: FrankEnergieData | None = None
        self.cached_prices_today: PricesTodayCache | None = None
        self.cached_prices_tomorrow: MarketPrices | None = None
        self.last_fetch_today: datetime | None = None
        self._static_prices_today: MarketPrices | None = None
        self._static_month_summary: MonthSummary | None = None
        self._static_invoices: Invoices | None = None
        self._static_user: User | None = None
        self._static_user_sites: UserSites | None = None
        self._static_period_usage: PeriodUsageAndCosts | None = None
        self._static_contract_price_resolution_state: (
            ContractPriceResolutionState | None
        ) = None
        self.last_fetch_tomorrow: datetime | None = None
        self._last_lowest_price_event: date | None = None
        self._last_lowest_4p_event: date | None = None
        self._last_lowest_16p_event: date | None = None
        # None = not yet checked; True/False = confirmed this session.
        # Reset to None daily so PV ownership is re-probed in case of new installation.
        self._has_pv_systems: bool | None = None

    def _remember_price_sources(
        self,
        day: date,
        selected: MarketPrices | None,
        *,
        customer_electricity: bool = False,
        customer_gas: bool = False,
        public_fresh: bool = False,
    ) -> None:
        """Track selected segments separately without persisting false source proof."""
        sources: dict[str, str] = {}
        for segment, is_customer in (
            ("electricity", customer_electricity),
            ("gas", customer_gas),
        ):
            series = getattr(selected, segment, None)
            if series is None or covered_seconds_in_local_day(series, day) <= 0:
                sources[segment] = "unavailable"
                continue
            origin = (
                "customer"
                if is_customer
                else "public"
                if public_fresh
                else "cached_unverified"
            )
            sources[segment] = (
                origin if covers_local_day(series, day) else f"{origin}_partial"
            )
        self._price_sources_by_day[day.isoformat()] = sources
        # Keep only the latest three local dates, not an unbounded history.
        for previous in sorted(self._price_sources_by_day)[:-3]:
            self._price_sources_by_day.pop(previous, None)

    def price_source_for(self, segment: str, day: date) -> str:
        """A public HA consumer can distinguish source evidence from a cache."""
        if segment not in ("electricity", "gas"):
            return "unverified"
        return self._price_sources_by_day.get(day.isoformat(), {}).get(
            segment, "cached_or_unverified"
        )

    def _record_price_fetch(
        self,
        source: str,
        outcome: str,
        period: date,
        prices: MarketPrices | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Record post-parsing source coverage alongside real GraphQL evidence."""
        electricity_slots, gas_slots = _price_counts(prices)
        self._diagnostic_trace.record(
            source,
            outcome,
            period=period,
            electricity_slots=electricity_slots if prices is not None else None,
            gas_slots=gas_slots if prices is not None else None,
            error=error,
        )

    @staticmethod
    def _get_shared_auth_lock(api: FrankEnergie) -> asyncio.Lock:
        """Return an auth lock shared by coordinators using the same API client."""
        lock = getattr(api, "_frank_energie_auth_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            api._frank_energie_auth_lock = lock
        return lock

    @property
    def old_site_reference(self) -> str | None:
        """Return active site reference."""
        return self.config_entry.options.get("site_reference")

    @property
    def site_reference(self) -> str | None:
        """Return active site reference."""
        if self.config_entry and self.config_entry.data:
            return self.config_entry.data.get("site_reference") or self._site_reference
        return self._site_reference

    def _should_skip_api_calls(
        self,
        now_utc: datetime,
    ) -> bool:
        """Return True when API calls should be skipped."""

        return now_utc.hour == 0 and 0 <= now_utc.minute < 60

    def _is_not_in_delivery_site(
        self, data_month_summary, data_invoices, user_sites
    ) -> bool:
        """
        Detect if this is an IN_DELIVERY site based on available data.

        Returns True if the site appears to be in IN_DELIVERY status
        (historical data available).
        """
        # Check for typical IN_DELIVERY indicators
        has_no_user_sites = user_sites is None
        has_no_month_summary = data_month_summary is None
        has_no_invoices = data_invoices is None

        # Additional check: if user_sites exists but has no usage segments
        has_limited_segments = (
            user_sites is not None
            and hasattr(user_sites, "segments")
            and len(user_sites.segments) == 0
        )

        # Site is likely not IN_DELIVERY if it has no historical data
        # Combine conditions explicitly
        if has_no_user_sites:
            return True
        if has_no_month_summary and (has_no_invoices or has_limited_segments):
            return True

        return False

    def _log_not_in_delivery_status(self, is_not_in_delivery: bool) -> None:
        """
        Log a single, clear message about IN_DELIVERY status to keep logs clean.
        """
        if is_not_in_delivery and not hasattr(self, "_not_in_delivery_logged"):
            _LOGGER.info(
                "Frank Energie site appears not to be in IN_DELIVERY status. "
                "Price data is available, but usage and billing data will become "
                "available once your energy delivery begins. This is normal for new customers."
            )
            # Mark that we've logged this to avoid spam
            self._not_in_delivery_logged = True
        elif not is_not_in_delivery and hasattr(self, "_in_delivery_logged"):
            _LOGGER.info(
                "Frank Energie site now has historical data available. "
                "All sensors should be fully functional."
            )
            # Clear the flag so we can log again if status changes back
            delattr(self, "_not_in_delivery_logged")

    @staticmethod
    def _tomorrow_cache_matches_date(
        cached_prices_tomorrow: MarketPrices | None, tomorrow: date
    ) -> bool:
        """Check that cached_prices_tomorrow's entries are all dated tomorrow or later.

        Guards against trusting `last_fetch_tomorrow`'s timestamp alone: a cache
        poisoned by a since-fixed bug (e.g. today's prices mistakenly cached as
        tomorrow's) would otherwise look like a legitimate same-day fetch forever,
        since nothing else ever re-validates its contents against reality.

        Checks every entry, not just the first, so a partially-poisoned or
        unsorted series can't slip a today-dated entry past validation.

        Entries dated *after* tomorrow are accepted too (`>=`, not `==`): the
        API can return multi-day windows, and _price_data_after/
        _build_tomorrow_cache (see promote_tomorrow_prices) deliberately
        preserve that leftover data as the next day's tomorrow cache. None of
        that is stale, so rejecting anything past tomorrow would discard
        legitimately-cached multi-day data.
        """
        if cached_prices_tomorrow is None:
            return False

        # Both segments must be checked. An empty electricity PriceData is
        # truthy, so the old "electricity or gas" expression hid valid gas-only
        # data. It also trusted tomorrow-dated electricity even if the gas
        # portion still contained yesterday's prices.
        prices = [
            price
            for segment in (
                cached_prices_tomorrow.electricity,
                cached_prices_tomorrow.gas,
            )
            if segment is not None
            for price in segment.all
        ]
        if not prices:
            return False

        tz_amsterdam = ZoneInfo(TIMEZONE_AMSTERDAM)
        return all(
            isinstance(price.date_from, datetime)
            and price.date_from.tzinfo is not None
            and price.date_from.astimezone(tz_amsterdam).date() >= tomorrow
            for price in prices
        )

    @staticmethod
    def _covers_local_day(prices: PriceData | None, day: date) -> bool:
        """Require real gap-free interval coverage, including 23/25-hour days."""
        return covers_local_day(prices, day)

    def _today_cache_covers_selected_site(
        self, cached: MarketPrices | None, today: date
    ) -> bool:
        """A partial subscribed gas day cannot freeze today's price cache.

        A selected-site subscription is authoritative. Anonymous or unresolved
        sites retain the existing date-based cache policy until a source
        subscription can be established.
        """
        if cached is None:
            return False
        if not self.api.is_authenticated or not (
            self.user_electricity_enabled or self.user_gas_enabled
        ):
            return True
        return all(
            not enabled or self._covers_local_day(series, today)
            for enabled, series in (
                (self.user_electricity_enabled, cached.electricity),
                (self.user_gas_enabled, cached.gas),
            )
        )

    @staticmethod
    def _preserve_today_source_coverage(
        previous: MarketPrices | None, fetched: MarketPrices, today: date
    ) -> MarketPrices:
        """Keep the segment covering more real time, not the one with more slots.

        Hourly and quarter-hour source intervals can differ, and duplicate
        records must never count as additional coverage. Equal time coverage
        prefers the new provider reply, allowing legitimate price revisions.
        """
        if previous is None:
            return fetched

        electricity = (
            previous.electricity
            if covered_seconds_in_local_day(previous.electricity, today)
            > covered_seconds_in_local_day(fetched.electricity, today)
            else fetched.electricity
        )
        gas = (
            previous.gas
            if covered_seconds_in_local_day(previous.gas, today)
            > covered_seconds_in_local_day(fetched.gas, today)
            else fetched.gas
        )
        if electricity is fetched.electricity and gas is fetched.gas:
            return fetched
        return MarketPrices(
            electricity=electricity,
            gas=gas,
            energy_country=fetched.energy_country,
            energy_type=fetched.energy_type,
        )

    def _tomorrow_cache_covers_selected_site(
        self, cached: MarketPrices, tomorrow: date
    ) -> bool:
        """Do not mistake six gas hours for a complete subscribed gas day."""
        required = (
            (self.user_electricity_enabled, cached.electricity),
            (self.user_gas_enabled, cached.gas),
        )
        # Anonymous accounts do not have site subscription flags. Preserve
        # their existing date-based cache policy.
        return all(
            not enabled or self._covers_local_day(series, tomorrow)
            for enabled, series in required
        )

    async def _refresh_tomorrow_cache(
        self,
        today: date,
        tomorrow: date,
        now_utc: datetime,
    ) -> MarketPrices | None:
        """Fetch and cache tomorrow's prices if the release window has passed."""
        tz_amsterdam = ZoneInfo(TIMEZONE_AMSTERDAM)
        now_local = now_utc.astimezone(tz_amsterdam)

        if now_local.hour < 13:
            _LOGGER.debug(
                "Not fetching tomorrow's prices yet (%02d:00 local time, threshold: 13:00 local time).",
                now_local.hour,
            )
            return None

        # Invalidate stale cache from the previous day before re-fetching.
        # If we skip this and the API returns empty (prices not yet published),
        # cached_prices_tomorrow still holds yesterday's D+1 data, which
        # _aggregate_data would concatenate with today's prices — doubling
        # price periods on sensors between 11:00 and ~14:00 UTC every morning.
        # Not gated on cached_prices_tomorrow being set: promotion can leave
        # last_fetch_tomorrow behind on its own (e.g. cleared to None because
        # there was nothing left to carry over), and a stale timestamp with
        # no cache to match it is exactly the kind of inconsistent state this
        # method exists to clean up before trusting anything.
        if (
            self.last_fetch_tomorrow is not None
            and _amsterdam_date(self.last_fetch_tomorrow) != today
        ):
            _LOGGER.debug(
                "Invalidating stale tomorrow-price cache (was fetched on %s)",
                _amsterdam_date(self.last_fetch_tomorrow),
            )
            self.cached_prices_tomorrow = None
            self.last_fetch_tomorrow = None

        if (
            self.cached_prices_tomorrow is not None
            and self.last_fetch_tomorrow is not None
            and _amsterdam_date(self.last_fetch_tomorrow) == today
        ):
            if self._tomorrow_cache_matches_date(self.cached_prices_tomorrow, tomorrow):
                if self._tomorrow_cache_covers_selected_site(
                    self.cached_prices_tomorrow, tomorrow
                ):
                    _LOGGER.debug(
                        "Tomorrow prices already cached (fetched %s), skipping API call",
                        self.last_fetch_tomorrow,
                    )
                    return self.cached_prices_tomorrow
                _LOGGER.debug(
                    "Tomorrow cache covers only part of the selected site; "
                    "retrying without discarding available prices"
                )
            else:
                # A live fetch now validates itself before caching (see the
                # freshly-fetched check below), so reaching this branch means the
                # bad data came from disk — e.g. state persisted by an older
                # build. Kept at warning: that's a genuine anomaly, not routine.
                _LOGGER.warning(
                    "Cached tomorrow prices claim to be fetched today but are not "
                    "actually dated for %s — discarding stale/incorrect cache and "
                    "fetching fresh data",
                    tomorrow,
                )
                self.cached_prices_tomorrow = None
                self.last_fetch_tomorrow = None

        _LOGGER.debug(
            "Tomorrow cache status: cached=%s last_fetch=%s",
            self.cached_prices_tomorrow is not None,
            self.last_fetch_tomorrow,
        )
        _LOGGER.debug(
            "Fetching tomorrow prices for %s",
            tomorrow,
        )

        prices_tomorrow = await self._fetch_tomorrow_data(tomorrow)

        has_electricity = bool(
            prices_tomorrow
            and prices_tomorrow.electricity
            and prices_tomorrow.electricity.all
        )

        has_gas = bool(
            prices_tomorrow and prices_tomorrow.gas and prices_tomorrow.gas.all
        )

        _LOGGER.debug(
            "Tomorrow prices fetched: electricity=%s gas=%s",
            has_electricity,
            has_gas,
        )

        if (has_electricity or has_gas) and not self._tomorrow_cache_matches_date(
            prices_tomorrow, tomorrow
        ):
            # Same poisoning this method's self-heal check guards against, but
            # caught immediately instead of one cycle later: a freshly fetched
            # response can itself come back non-empty but dated for today
            # instead of tomorrow (e.g. the API echoing the latest available
            # day back for a not-yet-published date — routinely the case in
            # the few minutes right around 13:00, since prices are typically
            # published closer to 13:15-13:20 than exactly on the hour).
            # Accepting it here would cache it as a genuine fetch and (via
            # _adjust_update_interval) go idle for the rest of the day,
            # silently repeating the exact same poisoning next time with no
            # further warning. Logged at debug, not warning: this is the
            # expected shape of "not published yet", not an anomaly.
            _LOGGER.debug(
                "Freshly fetched tomorrow prices are not actually dated for "
                "%s — treating as not yet available and retrying on next "
                "refresh",
                tomorrow,
            )
            prices_tomorrow = None
            has_electricity = has_gas = False

        if has_electricity or has_gas:
            _LOGGER.info(
                "Retrieved Frank Energie tomorrow prices for %s",
                tomorrow,
            )

            self.cached_prices_tomorrow = prices_tomorrow
            self.last_fetch_tomorrow = now_utc

            # A partial day is deliberately retried. Do not emit another
            # "available" event at every retry of the same local day.
            if self.config_entry is not None and not self._tomorrow_prices_logged:
                self.hass.bus.async_fire(
                    EVENT_FRANK_ENERGIE,
                    {
                        "entry_id": self.config_entry.entry_id,
                        "entry_title": self.config_entry.title or "no title",
                        "action": "tomorrow_prices_available",
                        "date": tomorrow.isoformat(),
                        "resolution": self.resolution,
                    },
                )
                self._tomorrow_prices_logged = True
        else:
            _LOGGER.debug("Tomorrow prices not yet available, retrying on next refresh")

        if self.cached_prices_today is not None:
            entry_title = self.config_entry.title if self.config_entry else "Unknown"
            _LOGGER.debug(
                "[%s][%s] Tomorrow electricity periods: %s",
                entry_title,
                self.site_reference,
                (
                    len(prices_tomorrow.electricity.all)
                    if prices_tomorrow and prices_tomorrow.electricity
                    else 0
                ),
            )
            _LOGGER.debug(
                "Listeners: %s",
                len(self._listeners),
            )
            _LOGGER.debug(
                "Coordinator electricity periods: %s",
                (
                    len(self.data[DATA_ELECTRICITY].all)
                    if self.data[DATA_ELECTRICITY]
                    else 0
                ),
            )

        return self.cached_prices_tomorrow

    def _maybe_fire_lowest_price_event(
        self, prices_today: MarketPrices | None, today: date, now_utc: datetime
    ) -> None:
        """Fire the lowest-price event if within the cheapest price slot."""
        if not self._should_fire_lowest_price_event(today):
            return

        lowest = (
            prices_today.electricity.today_min
            if prices_today and prices_today.electricity
            else None
        )
        if lowest is None:
            return

        start = self._ensure_utc(lowest.date_from)
        end = self._ensure_utc(lowest.date_till)

        if not (start <= now_utc < end):
            return

        entry_title = self.config_entry.title or "no title"
        self.hass.bus.async_fire(
            EVENT_FRANK_ENERGIE,
            {
                "entry_id": self.config_entry.entry_id,
                "entry_title": entry_title,
                "action": "lowest_price",
                "resolution": int((end - start).total_seconds() / 60),
                "price": lowest.total,
                "unit": "€/kWh",
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        _LOGGER.debug(
            "Fired frank_energie_event (lowest_price): %s → %s @ %s",
            start,
            end,
            lowest.total,
        )
        self._mark_lowest_price_event_fired(today)

    def _maybe_fire_lowest_4p_event(
        self, prices_today: MarketPrices | None, today: date, now_utc: datetime
    ) -> None:
        """Fire the lowest-4h-window event if within the cheapest consecutive block."""
        if not self._should_fire_lowest_4p_event(today):
            return

        prices = (
            prices_today.electricity.today
            if prices_today and prices_today.electricity
            else None
        )
        if not prices:
            return

        result = self._find_lowest_consecutive_hours(prices, window=4)
        if result is None:
            return

        average_price, start_price, end_price = result

        resolution = (
            int((prices[1].date_from - prices[0].date_from).total_seconds() / 60)
            if len(prices) >= 2
            else 60
        )

        start = self._ensure_utc(start_price.date_from)
        end = self._ensure_utc(end_price.date_till)

        if not (start <= now_utc < end):
            return

        entry_title = self.config_entry.title or "no title"
        self.hass.bus.async_fire(
            EVENT_FRANK_ENERGIE,
            {
                "entry_id": self.config_entry.entry_id,
                "entry_title": entry_title,
                "action": "lowest_4p_price",
                "periods": 4,
                "resolution": resolution,
                "average_price": round(average_price, 3),
                "unit": "€/kWh",
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        _LOGGER.debug(
            "Fired frank_energie_event (lowest_4p_price): %s → %s @ avg %s",
            start,
            end,
            round(average_price, 3),
        )
        self._mark_lowest_4p_event_fired(today)

    def _maybe_fire_lowest_16p_event(
        self, prices_today: MarketPrices | None, today: date, now_utc: datetime
    ) -> None:
        """Fire the lowest-16p-window event if within the cheapest consecutive block."""
        if not self._should_fire_lowest_16p_event(today):
            return

        prices = (
            prices_today.electricity.today
            if prices_today and prices_today.electricity
            else None
        )
        if not prices:
            return

        result = self._find_lowest_consecutive_hours(prices, window=16)
        if result is None:
            return

        average_price, start_price, end_price = result

        # calculate the resolution by looking at the first two price points. Default to 60 if 2 points are not available
        resolution = (
            int((prices[1].date_from - prices[0].date_from).total_seconds() / 60)
            if len(prices) >= 2
            else 60
        )

        # Only fire if the resolution is 15 minutes, else it would fire for 16 hour period.
        if not resolution == 15:
            return

        start = self._ensure_utc(start_price.date_from)
        end = self._ensure_utc(end_price.date_till)

        if not (start <= now_utc < end):
            return

        entry_title = self.config_entry.title or "no title"
        self.hass.bus.async_fire(
            EVENT_FRANK_ENERGIE,
            {
                "entry_id": self.config_entry.entry_id,
                "entry_title": entry_title,
                "action": "lowest_16p_price",
                "periods": 16,
                "resolution": resolution,
                "average_price": round(average_price, 3),
                "unit": "€/kWh",
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )
        _LOGGER.debug(
            "Fired frank_energie_event (lowest_16p_price): %s → %s @ avg %s",
            start,
            end,
            round(average_price, 3),
        )
        self._mark_lowest_16p_event_fired(today)

    async def _fetch_user_sites(self) -> UserSites | None:
        """Fetch user sites from the API."""
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping user sites fetch because the client is not authenticated"
            )
            return None
        try:
            sites = await self.api.UserSites()
            if "ELECTRICITY" in sites.segments:
                self.user_electricity_enabled = True
            if "GAS" in sites.segments:
                self.user_gas_enabled = True
            return sites
        except AuthException as ex:
            _LOGGER.warning("Authentication failed while fetching user sites: %s", ex)
            return None

    async def _fetch_month_summary(self) -> MonthSummary | None:
        """Fetch month summary from the API."""
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping month summary fetch because the client is not authenticated"
            )
            return None

        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch month summary.")
            return None

        try:
            return await self.api.month_summary(self.site_reference)

        except AuthException as ex:
            _LOGGER.debug(
                "Authentication failed while fetching month summary: %s",
                ex,
            )
            await self._try_renew_token()
            return None

        except (RequestException, FrankEnergieException, ClientError) as ex:
            _LOGGER.debug(
                "Failed to fetch month summary: %s",
                ex,
            )
            return None

    async def _fetch_invoices(self) -> Invoices | None:
        """Fetch invoices from the API."""
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping invoices fetch because the client is not authenticated"
            )
            return None
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch invoices.")
            return None
        try:
            return await self.api.invoices(self.site_reference)
        except AuthException as ex:
            _LOGGER.warning("Authentication failed while fetching invoices: %s", ex)
            return None
        except (RequestException, FrankEnergieException, ClientError) as ex:
            error_msg = str(ex).lower()
            if "no reading dates" in error_msg:
                _LOGGER.debug(
                    "No invoice data available yet (typical for none IN_DELIVERY sites): %s",
                    ex,
                )
            else:
                _LOGGER.debug(
                    "No invoice data available (normal for noneIN_DELIVERY sites): %s",
                    ex,
                )
            return None

    async def _fetch_period_usage(self, start_date: date) -> PeriodUsageAndCosts | None:
        """Fetch period usage and costs from the API."""
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping period usage fetch because the client is not authenticated"
            )
            return None
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch period usage.")
            return None
        try:
            return await self.api.period_usage_and_costs(
                self.site_reference, start_date.isoformat()
            )
        except AuthException as ex:
            _LOGGER.warning("Authentication failed while fetching period usage: %s", ex)
            return None
        except (RequestException, FrankEnergieException, ClientError) as ex:
            error_msg = str(ex).lower()
            if "no reading dates" in error_msg:
                _LOGGER.debug(
                    "No usage data available yet (typical for none IN_DELIVERY sites): %s",
                    ex,
                )
            else:
                _LOGGER.debug(
                    "No period usage data available (normal for none IN_DELIVERY sites): %s",
                    ex,
                )
            return None

    async def _fetch_user_data(self) -> User | None:
        """Fetch user data from the API."""
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping user data fetch because the client is not authenticated"
            )
            return None
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch user data.")
            return None
        try:
            user_data = await self.api.user(self.site_reference)
            if user_data is not None:
                try:
                    smart_hvac = await self.api.smart_hvac_status()
                    if smart_hvac:
                        user_data.smartHvac = smart_hvac
                except (RequestException, FrankEnergieException, ClientError) as ex:
                    _LOGGER.debug(
                        "Smart HVAC feature is not supported or accessible on this account: %s",
                        ex,
                    )
            if not self._country_code and user_data:
                country_code_raw = user_data.countryCode
                if isinstance(country_code_raw, str) and country_code_raw:
                    country_code = country_code_raw.upper()
                    if country_code in SUPPORTED_COUNTRIES:
                        self._country_code = country_code
            if not self._connection_id:
                self._connection_id = _extract_electricity_connection_id(user_data)
                _LOGGER.debug(
                    "Extracted electricity connection ID: %s", self._connection_id
                )

            return user_data
        except AuthException as ex:
            _LOGGER.warning("Authentication failed while fetching user data: %s", ex)
            return None
        except (RequestException, FrankEnergieException, ClientError) as ex:
            _LOGGER.warning("No user data available: %s", ex)
            return None

    async def _fetch_contract_price_resolution_state(
        self, connection_id: str | None
    ) -> ContractPriceResolutionState | None:
        """Fetch and process the contract price resolution state."""
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping contract price resolution state fetch: user is not authenticated"
            )
            return None
        if connection_id is None:
            _LOGGER.debug(
                "Skipping contract price resolution state fetch: connection ID is missing"
            )
            return None
        try:
            _LOGGER.debug(
                "Fetching contract price resolution state for connection ID: %s",
                connection_id,
            )
            resolution_state = await self.api.contract_price_resolution_state(
                connection_id
            )

            self._api_resolution_state = resolution_state
            self._resolution_change_pending = (
                False  # change has been processed by API, reset pending flag
            )

            # resolution_state is already a ContractPriceResolutionState dataclass.
            # Keep API state in coordinator data; explicit user commands own persistent options.
            if (
                self.config_entry.options.get("resolution")
                != resolution_state.activeOption
            ):
                _LOGGER.debug(
                    "API resolution differs from configured option (api=%s, option=%s)",
                    resolution_state.activeOption,
                    self.config_entry.options.get("resolution"),
                )

            _LOGGER.debug(
                "Good ContractPriceResolutionState: %s",
                resolution_state,
            )
            return resolution_state

        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.exception("Error fetching ContractPriceResolutionState: %s", err)
            return None

    def _handle_dynamic_fetch_error(self, method_name: str, err: Exception) -> None:
        """Handle errors during dynamic fetches, propagating network errors on initial refresh."""
        if isinstance(
            err, (AuthException, AuthRequiredException, asyncio.CancelledError)
        ):
            raise err
        if isinstance(
            err,
            (
                NetworkError,
                RequestException,
                ClientError,
                ConnectionError,
                asyncio.TimeoutError,
            ),
        ):
            _LOGGER.debug("Network error fetching %s: %s", method_name, err)
            if self.last_fetch_today is None:
                raise err
            return

        # Log generic exceptions (other than network errors or cancel/auth)
        if method_name.startswith("details for battery") or method_name.startswith(
            "sessions for battery"
        ):
            _LOGGER.exception("Failed to fetch %s: %s", method_name, err)
        else:
            _LOGGER.debug("Failed to fetch %s: %s", method_name, err)

    async def _fetch_enode_chargers(
        self, start_date: date, is_smart_charging: bool
    ) -> dict[str, EnodeChargers] | None:
        """Fetch Enode chargers from the API."""
        if not self.api.is_authenticated:
            return None
        if not is_smart_charging:
            _LOGGER.debug("Skipping Enode chargers fetch: smart charging is disabled")
            return None
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch Enode chargers.")
            return None
        try:
            return await self.api.enode_chargers(self.site_reference, start_date)
        except Exception as err:
            self._handle_dynamic_fetch_error("enode chargers", err)
            return None

    async def _fetch_smart_batteries(
        self, is_smart_trading: bool
    ) -> SmartBatteries | None:
        """Fetch smart batteries from the API."""
        if not self.api.is_authenticated:
            return None
        if not is_smart_trading:
            _LOGGER.debug("Skipping smart batteries fetch: smart trading is disabled")
            return None
        try:
            return await self.api.smart_batteries()
        except Exception as err:
            self._handle_dynamic_fetch_error("smart batteries", err)
            return None

    async def _fetch_enode_vehicles(
        self, is_smart_charging: bool
    ) -> EnodeVehicles | None:
        """Fetch Enode vehicles from the API."""
        if not self.api.is_authenticated:
            return None
        if not is_smart_charging:
            _LOGGER.debug("Skipping Enode vehicles fetch: smart charging is disabled")
            return None
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch Enode vehicles.")
            return None
        try:
            vehicles = await self.api.enode_vehicles()
            _LOGGER.debug("Fetched Enode vehicles: %s", vehicles)
            return vehicles
        except Exception as err:
            self._handle_dynamic_fetch_error("enode vehicles", err)
            return None

    async def _fetch_smart_pv_systems(self) -> SmartPvSystems | None:
        """Fetch Smart PV systems; skips call when no systems were found this session.

        No pre-flight feature flag exists for PV ownership, so we probe on
        the first call and cache the result.  An empty response sets
        ``_has_pv_systems = False`` to avoid repeated unnecessary calls;
        the flag resets to ``None`` daily to re-probe in case of new installs.
        Failures leave the flag unchanged so the next cycle retries.
        """
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping smart PV systems fetch because the client is not authenticated"
            )
            return None
        if self._has_pv_systems is False:
            return None
        try:
            result = await self.api.smart_pv_systems()
            self._has_pv_systems = bool(result and result.systems)
            return result
        except Exception as err:
            self._handle_dynamic_fetch_error("smart PV systems", err)
            return None

    async def _fetch_smart_pv_summary(
        self, device_id: str
    ) -> SmartPvSystemSummary | None:
        """Fetch Smart PV system summary from the API."""
        if not self.api.is_authenticated:
            return None
        if not device_id:
            _LOGGER.warning(
                "Device ID is missing, cannot fetch smart PV system summary."
            )
            return None
        try:
            return await self.api.smart_pv_system_summary(device_id)
        except Exception as err:
            self._handle_dynamic_fetch_error(
                f"smart PV system summary for {device_id}", err
            )
            return None

    async def _fetch_user_smart_feed_in(self) -> UserSmartFeedInStatus | None:
        """Fetch user smart feed-in status from the API."""
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping user smart feed-in fetch because the client is not authenticated"
            )
            return None
        try:
            return await self.api.user_smart_feed_in()
        except Exception as err:
            self._handle_dynamic_fetch_error("user smart feed-in status", err)
            return None

    async def _fetch_battery_details(self, battery) -> SmartBatteryDetails | None:
        """Fetch details for a single smart battery."""
        if not self.api.is_authenticated:
            return None
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch battery details.")
            return None
        if not battery or not battery.id:
            _LOGGER.warning(
                "Battery or battery ID is missing, cannot fetch battery details."
            )
            return None
        try:
            details = await self.api.smart_battery_details(battery.id)
            if details:
                # Merge settings from detailed response into battery object
                if details.smart_battery and details.smart_battery.settings:
                    battery.settings = details.smart_battery.settings

                # Merge SUMMARY
                if details.smart_battery_summary:
                    battery.summary = details.smart_battery_summary
                _LOGGER.debug(
                    "Merged battery data %s | settings=%s summary=%s",
                    battery.id,
                    battery.settings,
                    battery.summary,
                )
            return details
        except Exception as err:
            self._handle_dynamic_fetch_error(f"details for battery {battery.id}", err)
            return None

    async def _fetch_battery_sessions(
        self, battery, start_date: date, tomorrow: date
    ) -> SmartBatterySessions | None:
        """Fetch sessions for a single smart battery."""
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch battery sessions.")
            return None
        if not battery or not battery.id:
            _LOGGER.warning(
                "Battery or battery ID is missing, cannot fetch battery sessions."
            )
            return None

        try:
            # Fetch Month-To-Date sessions up to and including today (by querying until 'tomorrow') to get unfinalized live data
            # Missing dynamic totals will be manually reconstructed in sensor.py
            today = datetime.now(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
            mtd_start = min(today.replace(day=1), today - timedelta(days=1))
            sessions = await self.api.smart_battery_sessions(
                battery.id, mtd_start, tomorrow
            )
            if sessions and isinstance(sessions.sessions, list):
                _LOGGER.debug(
                    "Fetched %d session(s) for battery %s",
                    len(sessions.sessions),
                    battery.id,
                )
                return sessions
            else:
                _LOGGER.warning(
                    "No valid sessions list found in SmartBatterySessions for battery %s",
                    battery.id,
                )
                return None
        except Exception as err:
            self._handle_dynamic_fetch_error(f"sessions for battery {battery.id}", err)
            return None

    async def _fetch_battery_details_and_sessions(
        self, battery, start_date: date, tomorrow: date
    ) -> tuple[SmartBatteryDetails | None, SmartBatterySessions | None]:
        """Fetch details and sessions for each smart battery concurrently."""
        if not battery:
            return None, None

        _LOGGER.debug("Fetching details and sessions for battery: %s", battery.id)
        return await asyncio.gather(
            self._fetch_battery_details(battery),
            self._fetch_battery_sessions(battery, start_date, tomorrow),
        )

    async def _get_battery_details_and_sessions(
        self,
        data_smart_batteries: SmartBatteries | None,
        start_date: date,
        tomorrow: date,
    ) -> tuple[list[SmartBatteryDetails], list[SmartBatterySessions]]:
        """Fetch details and sessions for all smart batteries concurrently."""
        details_list: list[SmartBatteryDetails] = []
        sessions_list: list[SmartBatterySessions] = []

        if not (data_smart_batteries and data_smart_batteries.batteries):
            _LOGGER.debug("No smart batteries found")
            return details_list, sessions_list

        battery_tasks = [
            self._fetch_battery_details_and_sessions(battery, start_date, tomorrow)
            for battery in data_smart_batteries.batteries
            if battery
        ]
        if not battery_tasks:
            return details_list, sessions_list

        results = await asyncio.gather(*battery_tasks)
        for details, sessions in results:
            if details:
                details_list.append(details)
            if sessions:
                sessions_list.append(sessions)

        return details_list, sessions_list

    async def _fetch_tomorrow_data(
        self,
        tomorrow: date,
    ) -> MarketPrices | None:
        """Fetch tomorrow's data after 13:00 UTC."""
        try:
            _LOGGER.debug("Fetching Frank Energie data for tomorrow")

            return await self._fetch_prices_with_fallback(
                tomorrow,
                tomorrow + timedelta(days=1),
                use_fallback=False,
            )

        # NoMarketPricesAvailableException not needed here?
        except NoMarketPricesAvailableException:
            _LOGGER.debug(
                "Tomorrow prices are not available yet for %s",
                tomorrow,
            )
            return None

        except UpdateFailed as err:
            _LOGGER.debug(
                "Error fetching Frank Energie data for tomorrow (%s)",
                err,
            )
            return None

        except AuthException as err:
            _LOGGER.debug(_LOG_AUTH_TOKENS_EXPIRED, err)
            await self._try_renew_token()
            raise UpdateFailed(err) from err

    def _combine_price_data(self, today_data: Any, tomorrow_data: Any) -> Any:
        """Combine price data without mutating cached API model instances."""
        if today_data is None:
            return tomorrow_data
        if tomorrow_data is None:
            return today_data

        try:
            combined = copy.copy(today_data)
            combined += tomorrow_data
            return combined
        except TypeError:
            _LOGGER.debug(
                "In-place merge of %s failed, falling back to __add__",
                type(today_data).__name__,
            )
            try:
                return today_data + tomorrow_data
            except ValueError as err:
                return self._resolve_price_data_merge_conflict(
                    today_data, tomorrow_data, err
                )
        except ValueError as err:
            return self._resolve_price_data_merge_conflict(
                today_data, tomorrow_data, err
            )

    @staticmethod
    def _resolve_price_data_merge_conflict(
        today_data: Any, tomorrow_data: Any, err: ValueError
    ) -> Any:
        """Fall back when today's and tomorrow's price data can't be merged.

        A resolution or energy-type mismatch here is expected on a genuine
        contract price resolution change (e.g. PT15M -> PT60M) rather than a
        bug, so this must not crash the whole coordinator update.

        Unlike the midnight-rollover promotion (where tomorrow legitimately
        *becomes* the new today), this runs while today is still today:
        today_data holds the real, currently-active prices, so it must never
        be dropped in favor of tomorrow's forward-looking entries — doing so
        previously made "today" sensors go unavailable the moment a
        resolution-change window began. Each Price entry is self-describing
        via date_from/date_till (see PriceData.current/.today/.tomorrow), so
        a mixed-resolution series is still valid for display; only
        PriceData.__add__'s strict equality guard rejects it. Merge the
        entries directly instead of picking a side.
        """
        _LOGGER.warning(
            "Today's and tomorrow's %s price data have mismatched metadata "
            "(%s); merging entries directly instead of failing the update",
            type(today_data).__name__,
            err,
        )
        merged = copy.copy(today_data)
        unique_prices = {price.date_from: price for price in today_data.price_data}
        unique_prices.update(
            {price.date_from: price for price in tomorrow_data.price_data}
        )
        merged.price_data = sorted(unique_prices.values(), key=lambda p: p.date_from)

        # `merged` is a shallow copy, so resolution_minutes still reflects
        # today_data's entry spacing, not the mixed-resolution price_data we
        # just built. Recompute it the same way PriceData.__post_init__
        # does (smallest positive interval wins) so downstream consumers
        # like _is_cache_resolution_valid() don't see stale metadata.
        intervals = [
            int((price.date_till - price.date_from).total_seconds() // 60)
            for price in merged.price_data
            if price.date_from and price.date_till
        ]
        positive_intervals = [minutes for minutes in intervals if minutes > 0]
        if positive_intervals:
            merged.resolution_minutes = min(positive_intervals)

        return merged

    def _aggregate_data(
        self,
        cache: PricesTodayCache,
        prices_tomorrow: MarketPrices | None,
    ) -> FrankEnergieData:
        """Aggregate today's cache and tomorrow's prices into FrankEnergieData."""
        result: FrankEnergieData = {  # type: ignore[typeddict-unknown-key]
            DATA_TOKEN_EXPIRES_AT: _get_token_expires_at(self.api),
            DATA_REFRESH_TOKEN_EXPIRES_AT: _get_refresh_token_expires_at(self.api),
            DATA_MONTH_SUMMARY: cache.data_month_summary,
            DATA_INVOICES: cache.data_invoices,
            DATA_USAGE: cache.data_period_usage,
            DATA_USER: cache.data_user,
            DATA_USER_SITES: cache.user_sites,
            DATA_ENODE_CHARGERS: cache.data_enode_chargers,
            DATA_ENODE_VEHICLES: cache.data_enode_vehicles,
            DATA_BATTERIES: cache.data_smart_batteries,
            DATA_BATTERY_DETAILS: cache.data_smart_battery_details,
            DATA_BATTERY_SESSIONS: cache.data_smart_battery_sessions,
            DATA_PV_SYSTEMS: cache.data_pv_systems,
            DATA_PV_SUMMARY: cache.data_pv_summary,
            DATA_USER_SMART_FEED_IN: cache.data_user_smart_feed_in,
            DATA_CONTRACT_PRICE_RESOLUTION_STATE: cache.data_contract_price_resolution_state,
            DATA_ELECTRICITY: None,
            DATA_GAS: None,
        }

        if cache.prices_today is not None:
            if cache.prices_today.electricity is not None:
                result[DATA_ELECTRICITY] = cache.prices_today.electricity
            if cache.prices_today.gas is not None:
                result[DATA_GAS] = cache.prices_today.gas

        if prices_tomorrow is not None:
            result[DATA_ELECTRICITY] = self._combine_price_data(
                result[DATA_ELECTRICITY], prices_tomorrow.electricity
            )
            result[DATA_GAS] = self._combine_price_data(
                result[DATA_GAS], prices_tomorrow.gas
            )

        return result

    def _is_smart_charging_enabled(self, data_user) -> bool:
        """Check if smart charging is enabled for the user."""
        if not data_user:
            return False
        smart_charging = data_user.smartCharging
        if isinstance(smart_charging, dict):
            return smart_charging.get("isActivated", False) is True
        return getattr(smart_charging, "isActivated", False) is True

    def _is_smart_trading_enabled(self, data_user) -> bool:
        """Check if smart trading is enabled for the user."""
        if not data_user:
            return False
        smart_trading = data_user.smartTrading
        if isinstance(smart_trading, dict):
            return smart_trading.get("isActivated", False) is True
        return getattr(smart_trading, "isActivated", False) is True

    def _update_today_prices(self, prices: MarketPrices) -> None:
        """Update all internal cache fields representing today's prices."""
        self._static_prices_today = prices
        self._cached_prices = prices
        if self.cached_prices_today is not None:
            self.cached_prices_today = replace(
                self.cached_prices_today, prices_today=prices
            )

    @staticmethod
    def _price_data_after(
        price_data: PriceData | None, cutoff: date
    ) -> PriceData | None:
        """Return a PriceData with entries starting after the given local date."""
        if price_data is None:
            return None

        tz_amsterdam = ZoneInfo(TIMEZONE_AMSTERDAM)
        remaining = [
            price
            for price in price_data.all
            if price.date_from.astimezone(tz_amsterdam).date() > cutoff
        ]
        if not remaining:
            return None

        # `price_data` (the parsed Price list) is set in __post_init__, not a
        # dataclass field, so replace(..., price_data=...) would raise
        # TypeError. Clear `prices` via replace() to keep the other metadata
        # (energy_type, resolution_minutes, ...), then inject the
        # already-filtered, already-parsed entries directly.
        filtered = replace(price_data, prices=[])
        filtered.price_data = remaining
        return filtered

    @staticmethod
    def _merge_prices(
        base: PriceData | None, new_prices: PriceData | None
    ) -> PriceData | None:
        """Merge base and new prices if both exist, otherwise return the one that does."""
        if base and new_prices:
            return base + new_prices
        return base or new_prices

    @staticmethod
    def _build_tomorrow_cache(
        prices_tomorrow: MarketPrices,
        remaining_electricity: PriceData | None,
        remaining_gas: PriceData | None,
    ) -> MarketPrices | None:
        """Build the new tomorrow cache using the remaining prices."""
        if remaining_electricity is None and remaining_gas is None:
            return None

        return MarketPrices(
            electricity=remaining_electricity
            or (
                replace(prices_tomorrow.electricity, prices=[])
                if prices_tomorrow.electricity
                else None
            ),
            gas=remaining_gas
            or (
                replace(prices_tomorrow.gas, prices=[]) if prices_tomorrow.gas else None
            ),
            energy_country=prices_tomorrow.energy_country,
            energy_type=prices_tomorrow.energy_type,
        )

    def promote_tomorrow_prices(self) -> None:
        """Promote cached tomorrow prices to today's prices at local midnight.

        All cached price entries are promoted without any date filtering:
        entries for the new today become today's prices, and entries dated
        after the new today (the API may return multi-day windows) stay
        available as the new tomorrow cache instead of being dropped.
        """
        prices_tomorrow = self.cached_prices_tomorrow
        if prices_tomorrow is None:
            _LOGGER.debug(
                "Midnight rollover: no cached tomorrow prices to promote "
                "(last_fetch_tomorrow=%s), today's prices will be refetched live",
                self.last_fetch_tomorrow,
            )
            return

        new_today = dt_util.now(ZoneInfo(TIMEZONE_AMSTERDAM)).date()

        # Everything else in this file re-validates cached_prices_tomorrow
        # against the date it's meant to represent before trusting it (see
        # _refresh_tomorrow_cache and _adjust_update_interval); promotion is
        # the one remaining place that didn't. It normally can't reach here
        # holding stale data — _refresh_tomorrow_cache clears anything not
        # fetched today before every 13:00+ run — but if a day is ever
        # skipped entirely (HA down across a whole day), or a future bug
        # reintroduces unvalidated writes, this stops days-old prices from
        # getting merged straight into "today" with no warning.
        if not self._tomorrow_cache_matches_date(prices_tomorrow, new_today):
            _LOGGER.warning(
                "Midnight rollover: cached tomorrow prices are not actually "
                "dated for %s — discarding instead of promoting, today's "
                "prices will be refetched live",
                new_today,
            )
            self.cached_prices_tomorrow = None
            self.last_fetch_tomorrow = None
            return

        source_data = self.cached_prices or self.data
        if source_data is None:
            _LOGGER.warning("Cannot promote tomorrow prices: no cached data available")
            return

        # Keep the combined data (yesterday + today) to prevent historical sensors
        # from becoming None at exactly 0:00. Merge the tomorrow cache in case it
        # never made it into an aggregation cycle before midnight.
        #
        # On a ValueError (resolution/energy-type mismatch) this deliberately
        # picks tomorrow's series and drops the rest, unlike
        # _resolve_price_data_merge_conflict's entry-level merge used in
        # _aggregate_data. The two failure sites aren't equivalent: here,
        # "tomorrow" genuinely *becomes* the new "today" at this exact
        # instant, so its resolution is the correct one going forward and
        # source_data's incompatible entries are, by definition, about to be
        # the wrong resolution for the new day. Merging them in anyway would
        # just reintroduce the same mismatch one call later.
        try:
            combined_electricity = self._merge_prices(
                source_data.get(DATA_ELECTRICITY), prices_tomorrow.electricity
            )
            combined_gas = self._merge_prices(
                source_data.get(DATA_GAS), prices_tomorrow.gas
            )
        except ValueError:
            today_elec = source_data.get(DATA_ELECTRICITY)
            today_gas = source_data.get(DATA_GAS)
            _LOGGER.warning(
                "Cannot merge today's and tomorrow's price data: resolution "
                "or type mismatch (today electricity=%s gas=%s, tomorrow "
                "electricity=%s gas=%s). This can happen on a genuine "
                "contract resolution change, or simply because the API "
                "returned different resolutions for the two fetches. Using "
                "tomorrow's prices for the new day instead of merging",
                getattr(today_elec, "resolution_minutes", None),
                getattr(today_gas, "resolution_minutes", None),
                getattr(prices_tomorrow.electricity, "resolution_minutes", None),
                getattr(prices_tomorrow.gas, "resolution_minutes", None),
            )
            # An empty PriceData instance is truthy, so check for actual
            # entries — the tomorrow cache holds an empty series for an
            # energy type the user has no contract for.
            combined_electricity = (
                prices_tomorrow.electricity
                if prices_tomorrow.electricity and prices_tomorrow.electricity.all
                else source_data.get(DATA_ELECTRICITY)
            )
            combined_gas = (
                prices_tomorrow.gas
                if prices_tomorrow.gas and prices_tomorrow.gas.all
                else source_data.get(DATA_GAS)
            )

        updated_data = source_data.copy()
        updated_data[DATA_ELECTRICITY] = combined_electricity
        updated_data[DATA_GAS] = combined_gas

        combined_market_prices = MarketPrices(
            electricity=combined_electricity,
            gas=combined_gas,
            energy_country=prices_tomorrow.energy_country,
            energy_type=prices_tomorrow.energy_type,
        )

        # Prices dated after the new today are still tomorrow's prices:
        # promote them to the tomorrow cache instead of clearing it.
        remaining_electricity = self._price_data_after(combined_electricity, new_today)
        remaining_gas = self._price_data_after(combined_gas, new_today)

        self.cached_prices_tomorrow = self._build_tomorrow_cache(
            prices_tomorrow, remaining_electricity, remaining_gas
        )

        self.cached_prices = updated_data
        self.data = updated_data
        self._update_today_prices(combined_market_prices)

        self.async_update_listeners()

        promoted_elec = (
            len(prices_tomorrow.electricity.all) if prices_tomorrow.electricity else 0
        )
        promoted_gas = len(prices_tomorrow.gas.all) if prices_tomorrow.gas else 0
        kept_elec = len(remaining_electricity.all) if remaining_electricity else 0
        kept_gas = len(remaining_gas.all) if remaining_gas else 0

        _LOGGER.debug(
            "Promoted %s electricity and %s gas price entries from tomorrow to "
            "today's prices (%s electricity and %s gas price entries kept for "
            "the new tomorrow)",
            promoted_elec,
            promoted_gas,
            kept_elec,
            kept_gas,
        )

    def get_pv_system_metadata(self, system_id: str) -> dict[str, Any]:
        """Get PV system metadata (brand, model, display_name, serial_number)."""
        systems_obj = self.data.get(DATA_PV_SYSTEMS)
        pv_system = None
        if systems_obj and systems_obj.systems:
            pv_system = next(
                (s for s in systems_obj.systems if s.id == system_id), None
            )

        brand = pv_system.brand if (pv_system and pv_system.brand) else "Frank Energie"
        model = pv_system.model if (pv_system and pv_system.model) else None
        display_name = (
            pv_system.display_name
            if (pv_system and pv_system.display_name)
            else " ".join(filter(None, [brand, model])) or "Smart PV"
        )
        serial_number = (
            pv_system.inverter_serial_numbers[0]
            if pv_system and pv_system.inverter_serial_numbers
            else None
        )

        return {
            "brand": brand,
            "model": model,
            "display_name": display_name,
            "serial_number": serial_number,
        }

    def _get_public_api(self) -> FrankEnergie:
        """Return a credential-free client for public market price queries.

        Never propagate a customer's bearer token to the public GraphQL query.
        Use the Home Assistant-managed HTTP session; create lazily.
        """
        client = getattr(self, "_public_api", None)
        if client is None:
            client = FrankEnergie(clientsession=async_get_clientsession(self.hass))
            attach_graphql_trace(client, self._diagnostic_trace, "graphql_public")
            self._public_api = client
        return client

    async def _fetch_public_prices_for_range(
        self, start_date: date, end_date: date, country_code: str
    ) -> MarketPrices | None:
        """Fetch public prices for a given date range."""
        _LOGGER.debug(
            "Fetching public prices for country=%s resolution=%s",
            country_code,
            self.resolution,
        )

        public_api = self._get_public_api()
        try:
            if COUNTRY_USES_SIMPLE_MARKET_PRICES.get(country_code, False):
                prices = await public_api.country_prices(
                    country_code, start_date, end_date, self.resolution
                )
            else:
                prices = await public_api.prices(
                    start_date,
                    resolution=self.resolution,
                )
            self._record_price_fetch(
                "public_market",
                "present" if any(_price_counts(prices)) else "empty",
                start_date,
                prices,
            )
            return prices

        except RequestException as ex:
            if not _is_no_market_prices_error(ex):
                raise
            _LOGGER.debug(
                "No market prices available yet for %s (%s - %s)",
                country_code,
                start_date,
                end_date,
            )
            self._record_price_fetch(
                "public_market", "not_published", start_date, error=ex
            )
            return None

        except NetworkError as err:
            _LOGGER.warning("Failed to fetch public prices (%s)", type(err).__name__)
            self._record_price_fetch(
                "public_market", "network_error", start_date, error=err
            )
            return None

    async def _fetch_user_prices_for_range(
        self,
        start_date: date,
        end_date: date,
        default_country: str,
    ) -> MarketPrices | None:
        """Fetch user-specific prices for a given date range."""
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping user prices fetch because the client is not authenticated"
            )
            self._record_price_fetch(
                "customer_tariff", "skipped_unauthenticated", start_date
            )
            return None

        site_reference = self.site_reference
        if site_reference is None:
            _LOGGER.debug(
                "Skipping user prices fetch because site_reference is not set"
            )
            self._record_price_fetch(
                "customer_tariff", "skipped_missing_site", start_date
            )
            return None

        user_country = self._user_country or default_country

        _LOGGER.debug(
            "Fetching user prices for country=%s resolution=%s",
            user_country,
            self.resolution,
        )

        try:
            prices = await self.api.user_prices(
                site_reference,
                user_country,
                start_date,
                end_date,
            )
            self._record_price_fetch(
                "customer_tariff",
                "present" if any(_price_counts(prices)) else "empty",
                start_date,
                prices,
            )
            return prices

        except RequestException as ex:
            if not _is_no_market_prices_error(ex):
                raise
            _LOGGER.debug(
                "No user market prices available yet for %s (%s - %s)",
                user_country,
                start_date,
                end_date,
            )
            self._record_price_fetch(
                "customer_tariff", "not_published", start_date, error=ex
            )
            return None

        except NetworkError as err:
            _LOGGER.warning("Failed to fetch user prices (%s)", type(err).__name__)
            self._record_price_fetch(
                "customer_tariff", "network_error", start_date, error=err
            )
            return None

    async def _fetch_prices_with_fallback(
        self, start_date: date, end_date: date, use_fallback: bool = True
    ) -> MarketPrices | None:
        """Fetch prices with fallback to public prices and cached data.

        When use_fallback=False (e.g. tomorrow's prices), empty user prices are
        returned as-is without substituting public prices.
        This method attempts to fetch user-specific prices first, and if they are not available,
        it falls back to public prices.
        """
        # Ensure country_code is always a str (fallback to "NL" if None)
        country_code = (
            (self.hass.config.country or "NL")
            if self.hass and self.hass.config
            else "NL"
        )

        _LOGGER.debug("Fetching prices concurrently (use_fallback=%s)", use_fallback)
        public_prices, user_prices = await asyncio.gather(
            self._fetch_public_prices_for_range(start_date, end_date, country_code),
            self._fetch_user_prices_for_range(start_date, end_date, country_code),
        )

        fallback_from_cache = public_prices is None
        if public_prices is None:
            if not use_fallback and not self.api.is_authenticated:
                # e.g. tomorrow's prices not published yet — don't substitute
                # today's cached prices, or _refresh_tomorrow_cache will treat
                # today's data as a successful tomorrow fetch and stop retrying.
                _LOGGER.debug(
                    "No public prices available yet, skipping fallback to cached prices"
                )
                self._record_price_fetch("price_selection", "not_available", start_date)
                return None
            public_prices = getattr(
                self,
                "_cached_prices",
                MarketPrices(
                    electricity=PriceData([], "electricity"),
                    gas=PriceData([], "gas"),
                    energy_country=country_code or "NL",
                ),
            )

        if not self.api.is_authenticated:
            self._remember_price_sources(
                start_date, public_prices, public_fresh=not fallback_from_cache
            )
            self._record_price_fetch(
                "price_selection",
                _price_selection_outcome(
                    public_prices,
                    authenticated=False,
                    fallback_from_cache=fallback_from_cache,
                ),
                start_date,
                public_prices,
            )
            return public_prices

        if user_prices is None:
            if not use_fallback:
                _LOGGER.debug("No user prices for tomorrow, skipping fallback")
                return None
            _LOGGER.warning(
                "Failed to fetch user prices, falling back to public prices"
            )
            self._remember_price_sources(
                start_date, public_prices, public_fresh=not fallback_from_cache
            )
            self._record_price_fetch(
                "price_selection",
                _price_selection_outcome(
                    public_prices,
                    authenticated=True,
                    fallback_from_cache=fallback_from_cache,
                ),
                start_date,
                public_prices,
            )
            return public_prices

        # Use user prices if both gas and electricity have data
        has_electricity = user_prices.electricity is not None and getattr(
            user_prices.electricity, "all", None
        )
        has_gas = user_prices.gas is not None and getattr(user_prices.gas, "all", None)

        if has_electricity and has_gas:
            self._remember_price_sources(
                start_date,
                user_prices,
                customer_electricity=True,
                customer_gas=True,
            )
            self._cached_prices = user_prices
            self._record_price_fetch(
                "price_selection", "customer", start_date, user_prices
            )
            return user_prices

        # No fallback for tomorrow — return what we have
        if not use_fallback:
            _LOGGER.debug(
                "No complete user prices for tomorrow (electricity=%s gas=%s), skipping fallback",
                bool(has_electricity),
                bool(has_gas),
            )
            self._remember_price_sources(
                start_date,
                user_prices,
                customer_electricity=bool(has_electricity),
                customer_gas=bool(has_gas),
            )
            self._record_price_fetch(
                "price_selection",
                _price_selection_outcome(
                    user_prices,
                    authenticated=True,
                    customer_electricity=bool(has_electricity),
                    customer_gas=bool(has_gas),
                ),
                start_date,
                user_prices,
            )
            return user_prices

        # Fallback logic for today
        if not has_gas:
            _LOGGER.info("No gas prices for user, falling back to public prices")
            user_prices.gas = (
                public_prices.gas if self.user_gas_enabled else PriceData([], "gas")
            )

        if not has_electricity:
            _LOGGER.info(
                "No electricity prices for user, falling back to public prices"
            )
            user_prices.electricity = (
                public_prices.electricity
                if self.user_electricity_enabled
                else PriceData([], "electricity")
            )

        self._remember_price_sources(
            start_date,
            user_prices,
            customer_electricity=bool(has_electricity),
            customer_gas=bool(has_gas),
            public_fresh=not fallback_from_cache,
        )
        self._cached_prices = user_prices
        outcome = _price_selection_outcome(
            user_prices,
            authenticated=True,
            customer_electricity=bool(has_electricity),
            customer_gas=bool(has_gas),
            fallback_from_cache=fallback_from_cache,
        )
        self._record_price_fetch("price_selection", outcome, start_date, user_prices)
        return user_prices

    async def _handle_fetch_exceptions(self, ex: Exception) -> None:
        """Normalize fetch exceptions for DataUpdateCoordinator callers."""
        if isinstance(ex, (ConfigEntryAuthFailed, AuthRequiredException)):
            raise ex
        if isinstance(ex, UpdateFailed):
            raise ex
        if isinstance(ex, RequestException) and str(ex).startswith("user-error:"):
            raise ConfigEntryAuthFailed from ex
        if isinstance(ex, AuthException):
            _LOGGER.debug(_LOG_AUTH_TOKENS_EXPIRED, ex)
            await self._try_renew_token()
            raise UpdateFailed(ex) from ex

    async def _try_renew_token(self) -> None:
        """Try to renew authentication token with shared-client serialization."""

        async with self._auth_lock:
            try:
                updated_tokens = await self.api.renew_token()
                updated_data = {
                    **self.config_entry.data,
                    CONF_ACCESS_TOKEN: updated_tokens.authToken,
                    CONF_TOKEN: updated_tokens.refreshToken,
                }
                # Clean up old plaintext credentials in data if any
                updated_data.pop(CONF_USERNAME, None)
                updated_data.pop(CONF_PASSWORD, None)

                # Update the config entry with the new tokens
                if (
                    self.config_entry.data.get(CONF_ACCESS_TOKEN)
                    != updated_tokens.authToken
                    or self.config_entry.data.get(CONF_TOKEN)
                    != updated_tokens.refreshToken
                ):
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=updated_data
                    )

                _LOGGER.debug("Successfully renewed token")

            except AuthException as ex:
                _LOGGER.debug(
                    "Token renewal failed, attempting silent re-login using stored credentials"
                )
                username = self.config_entry.options.get(
                    CONF_USERNAME
                ) or self.config_entry.data.get(CONF_USERNAME)
                password = self.config_entry.options.get(
                    CONF_PASSWORD
                ) or self.config_entry.data.get(CONF_PASSWORD)

                if username and password:
                    try:
                        decrypted_password = decrypt_password(self.hass, password)
                        if not decrypted_password:
                            raise AuthException("Decrypted password is empty")
                        auth = await self.api.login(username, decrypted_password)
                        updated_data = {
                            **self.config_entry.data,
                            CONF_ACCESS_TOKEN: auth.authToken,
                            CONF_TOKEN: auth.refreshToken,
                        }
                        updated_data.pop(CONF_USERNAME, None)
                        updated_data.pop(CONF_PASSWORD, None)

                        self.hass.config_entries.async_update_entry(
                            self.config_entry, data=updated_data
                        )
                        _LOGGER.info(
                            "Successfully re-authenticated silently using stored credentials"
                        )
                        return
                    except (AuthException, FrankEnergieException) as login_ex:
                        _LOGGER.warning(
                            "Silent re-login failed with API/Auth error: %s. Proceeding to reauth flow",
                            login_ex,
                        )
                    except Exception as login_ex:
                        _LOGGER.exception(
                            "Unexpected error during silent re-login: %s. Retrying later.",
                            login_ex,
                        )
                        raise UpdateFailed(
                            f"Unexpected error during silent re-login: {login_ex}"
                        ) from login_ex

                _LOGGER.exception(
                    "Failed to renew token: %s. Starting user reauth flow", ex
                )
                raise ConfigEntryAuthFailed from ex

    async def _fetch_authenticated[T](
        self,
        method: Callable[..., Awaitable[T]],
        *args: object,
    ) -> T | None:
        """Execute an authenticated API call with proper logging and error handling."""
        if not self.api.is_authenticated:
            _LOGGER.warning(
                "API not authenticated, skipping call to %s",
                getattr(method, "__name__", repr(method)),
            )
            return None
        try:
            result = await method(*args)
            _LOGGER.debug(
                "Fetched data from %s: %s",
                getattr(method, "__name__", repr(method)),
                result,
            )
            return result
        except asyncio.CancelledError:
            # Required for correct task cancellation handling in Home Assistant
            raise
        except Exception as err:
            _LOGGER.exception(
                "Failed to fetch data using %s: %s",
                getattr(method, "__name__", repr(method)),
                err,
            )
            return None

    def _ensure_utc(self, value: datetime) -> datetime:
        """Ensure datetime is timezone-aware UTC."""
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo("UTC"))
        return value

    def _find_lowest_consecutive_hours(
        self,
        prices: list[Price],
        window: int,
    ) -> tuple[float, Price, Price] | None:
        """Find lowest average price for consecutive hours."""

        if len(prices) < window:
            return None

        lowest_avg: float | None = None
        lowest_start: Price | None = None
        lowest_end: Price | None = None

        for index in range(len(prices) - window + 1):
            window_prices = prices[index : index + window]

            avg_price = sum(price.total for price in window_prices) / window

            if lowest_avg is None or avg_price < lowest_avg:
                lowest_avg = avg_price
                lowest_start = window_prices[0]
                lowest_end = window_prices[-1]

        if lowest_avg is None or lowest_start is None or lowest_end is None:
            return None

        return lowest_avg, lowest_start, lowest_end

    def _should_fire_lowest_price_event(self, today: date) -> bool:
        """Return True if the lowest-price event was not fired today."""
        return self._last_lowest_price_event != today

    def _mark_lowest_price_event_fired(self, today: date) -> None:
        """Mark the lowest-price event as fired for today."""
        self._last_lowest_price_event = today

    def _should_fire_lowest_4p_event(self, today: date) -> bool:
        """Return True if the lowest-4p event has not yet fired today."""
        return self._last_lowest_4p_event != today

    def _mark_lowest_4p_event_fired(self, today: date) -> None:
        """Mark the lowest-4p event as fired for today."""
        self._last_lowest_4p_event = today

    def _should_fire_lowest_16p_event(self, today: date) -> bool:
        """Return True if the lowest-16p event has not yet fired today."""
        return self._last_lowest_16p_event != today

    def _mark_lowest_16p_event_fired(self, today: date) -> None:
        """Mark the lowest-16p event as fired for today."""
        self._last_lowest_16p_event = today

    def _reconcile_resolution(self) -> None:
        """Ensure config and API state are consistent after refresh."""

        if not self._api_resolution_state:
            return

        if self.config_entry is None:
            return

        api_value = self._api_resolution_state.activeOption
        config_value = self.config_entry.options.get("resolution")

        # Only log drift — do NOT overwrite config automatically
        if api_value and config_value and api_value != config_value:
            upcoming_change = self._api_resolution_state.upcomingChange
            if upcoming_change == config_value:
                _LOGGER.debug(
                    "Resolution drift detected but expected due to pending change (config=%s api=%s upcoming=%s)",
                    config_value,
                    api_value,
                    upcoming_change,
                )
            else:
                _LOGGER.warning(
                    "Resolution drift detected (config=%s api=%s)",
                    config_value,
                    api_value,
                )

    async def async_set_resolution(self, value: str) -> None:
        """Update resolution safely via mutation queue."""
        if not self.api.is_authenticated:
            if self.config_entry is not None:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    options={**self.config_entry.options, "resolution": value},
                )
                _LOGGER.debug(
                    "Resolution saved to options (unauthenticated API): %s -> options=%s",
                    value,
                    self.config_entry.options,
                )
                # Invalidate price cache so next refresh fetches with new resolution
                self._static_prices_today = None
                self.last_fetch_today = None
                self.cached_prices_tomorrow = None
                self.last_fetch_tomorrow = None
                await self.async_request_refresh()
            return

        if not self._connection_id:
            _LOGGER.error(
                "Cannot set resolution via API: electricity connection_id not available"
            )
            return

        if self._resolution_change_pending:
            _LOGGER.warning("Cannot set resolution: a change is already pending")
            return

        if (
            self._api_resolution_state is not None
            and not self._api_resolution_state.isChangeRequestPossible
        ):
            _LOGGER.warning(
                "Cannot set resolution via API: isChangeRequestPossible=False"
            )
            return

        self._resolution_change_pending = True

        async def _mutation() -> None:
            try:
                result = await self.api.contract_price_resolution_request_change(
                    self._connection_id,
                    cast(Resolution, value),
                )

                if result is None:
                    raise UpdateFailed("Resolution change request failed: no response")

                if not result.success:
                    if result.reason == "CHANGE_NOT_POSSIBLE":
                        _LOGGER.warning(
                            "Resolution change not possible at this time "
                            "(contract does not allow changes or cooling-off period active)"
                        )
                        async_create(
                            self.hass,
                            message=(
                                "Resolution change is not possible at this time. "
                                "Your contract may not allow changes or a cooling-off period is active. "
                                f"Upcoming change: {self._api_resolution_state.upcomingChange if self._api_resolution_state else 'unknown'} "
                                f"(effective: {self._api_resolution_state.upcomingChangeEffectiveDate if self._api_resolution_state else 'unknown'})"
                            ),
                            title="Frank Energie - Resolution Change Failed",
                            notification_id="frank_energie_resolution_change_failed",
                        )
                        return  # not an error, just not allowed right now
                    raise UpdateFailed(
                        "Resolution change request failed: %s" % (result.reason)
                    )

                _LOGGER.info(
                    "Resolution change accepted (effective: %s)",
                    result.data.effectiveDate if result.data else "unknown",
                )

                if self.config_entry is None:
                    return

                if self.config_entry.options.get("resolution") != value:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        options={**self.config_entry.options, "resolution": value},
                    )

                # Re-fetch resolution state to update our internal tracker immediately
                try:
                    state = await self._fetch_contract_price_resolution_state(
                        self._connection_id
                    )
                    if state:
                        self.data[DATA_CONTRACT_PRICE_RESOLUTION_STATE] = state
                except Exception as err:
                    _LOGGER.debug(
                        "Failed to re-fetch resolution state after successful change: %s",
                        err,
                    )

            finally:
                self._resolution_change_pending = False

        await self._mutation_queue.add(_mutation)
        await self.async_request_refresh()

    @property
    def resolution(self) -> str:
        """Effective price resolution used for API queries."""
        if self.config_entry is None:
            return DEFAULT_RESOLUTION

        configured = self.config_entry.options.get("resolution", DEFAULT_RESOLUTION)

        # An anonymous entry uses its own configured interval. During a pending
        # change, use the selected option until the account API confirms it.
        if (
            self.api.is_authenticated
            and not self._resolution_change_pending
            and self._api_resolution_state is not None
        ):
            active = self._api_resolution_state.activeOption
            if active in SUPPORTED_RESOLUTIONS:
                return active
            _LOGGER.debug(
                "Ignoring unsupported contract price resolution %r; using %s",
                active,
                configured,
            )

        if configured in SUPPORTED_RESOLUTIONS:
            return configured

        _LOGGER.debug(
            "Unsupported configured price resolution %r; falling back to %s",
            configured,
            DEFAULT_RESOLUTION,
        )
        return DEFAULT_RESOLUTION

    @property
    def api_resolution(self) -> str | None:
        """Resolution reported by API (read-only)."""
        return (
            self._api_resolution_state.activeOption
            if self._api_resolution_state
            else None
        )

    def _parse_vehicles(self, data: list[dict]) -> EnodeVehicles:
        vehicles_list = [EnodeVehicle(**vehicle_dict) for vehicle_dict in data]
        return EnodeVehicles(vehicles=vehicles_list)

    async def async_update_enode_charge_settings(
        self, device_id: str, is_vehicle: bool, mutations: dict[str, Any]
    ) -> bool:
        """Serialize charge settings mutations to avoid race conditions.

        The API requires all 13 fields to be present in the mutation payload.
        By serializing updates through the mutation queue, we ensure that
        concurrent interactions (e.g. from sliders and selects) read fresh
        local state and don't overwrite each other with stale values.
        """
        success = False

        async def _update() -> None:
            nonlocal success
            from .helpers import build_charge_settings_input

            # Fetch freshest local state
            if is_vehicle:
                enode_data = self.data.get(DATA_ENODE_VEHICLES)
                item = (
                    next((v for v in enode_data.vehicles if v.id == device_id), None)
                    if enode_data
                    else None
                )
            else:
                enode_data = self.data.get(DATA_ENODE_CHARGERS)
                item = (
                    next((c for c in enode_data.chargers if c.id == device_id), None)
                    if enode_data
                    else None
                )

            if not item or not item.charge_settings:
                _LOGGER.error(
                    "Cannot update charge settings: item %s not found or missing settings",
                    device_id,
                )
                return

            # Construct full payload from fresh state
            input_data = build_charge_settings_input(item.charge_settings)

            # Apply mutations
            for k, v in mutations.items():
                input_data[k] = v

            # Send to API
            try:
                if is_vehicle:
                    success = await self.api.enode_update_vehicle_charge_settings(
                        input_data
                    )
                else:
                    success = await self.api.enode_update_charger_charge_settings(
                        input_data
                    )
            except Exception:
                _LOGGER.exception("Failed to update charge settings for %s", device_id)
                success = False

            if success:
                # Update local cache memory optimistically
                key_map = {
                    "deadline": "deadline",
                    "isSmartChargingEnabled": "is_smart_charging_enabled",
                    "isSolarChargingEnabled": "is_solar_charging_enabled",
                    "minChargeLimit": "min_charge_limit",
                    "maxChargeLimit": "max_charge_limit",
                    "initialCharge": "initial_charge",
                    "hourMonday": "hour_monday",
                    "hourTuesday": "hour_tuesday",
                    "hourWednesday": "hour_wednesday",
                    "hourThursday": "hour_thursday",
                    "hourFriday": "hour_friday",
                    "hourSaturday": "hour_saturday",
                    "hourSunday": "hour_sunday",
                }
                for k, v in mutations.items():
                    if attr := key_map.get(k):
                        if attr == "deadline" and isinstance(v, str):
                            v = datetime.fromisoformat(v)
                        setattr(item.charge_settings, attr, v)

        await self._mutation_queue.add(_update)
        return success


class FrankEnergieSettingsCoordinator(FrankEnergieCoordinator):
    """Coordinator for user configuration and site settings."""

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, api: FrankEnergie
    ) -> None:
        """Initialize the settings coordinator."""
        super().__init__(hass, config_entry, api)
        self.name = "Frank Energie settings coordinator"
        _LOGGER.debug("Initializing %s (country_code=%s)", self.name, self.country_code)
        self.update_interval = timedelta(
            hours=config_entry.options.get(
                CONF_INTERVAL_SETTINGS, DEFAULT_INTERVAL_SETTINGS
            )
        )

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch settings data."""
        now_utc = datetime.now(ZoneInfo("UTC"))
        if self._should_skip_api_calls(now_utc):
            _LOGGER.debug("Skipping settings fetch during maintenance window")
            if self.last_update_success:
                return self.data
            raise UpdateFailed("Maintenance window active")

        try:
            user_sites, data_user = await asyncio.gather(
                self._fetch_user_sites(),
                self._fetch_user_data(),
            )
            result = _empty_data()
            # A transient empty UserSites response must not silently turn an
            # authenticated electricity/gas contract into no subscription.
            # Keep earlier source-backed settings and retry on the next tick.
            if self.api.is_authenticated and user_sites is None:
                raise UpdateFailed("Authenticated delivery sites not available")
            result[DATA_USER] = data_user or (self.data or {}).get(DATA_USER)
            result[DATA_USER_SITES] = user_sites
            result[DATA_TOKEN_EXPIRES_AT] = _get_token_expires_at(self.api)
            result[DATA_REFRESH_TOKEN_EXPIRES_AT] = _get_refresh_token_expires_at(
                self.api
            )
            return result
        except Exception as err:
            await self._handle_fetch_exceptions(err)
            raise UpdateFailed(f"Failed to fetch settings data: {err}") from err


class FrankEnergiePriceCoordinator(FrankEnergieCoordinator):
    """Coordinator for energy prices and resolution state."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: FrankEnergie,
        settings_coordinator: FrankEnergieSettingsCoordinator,
    ) -> None:
        """Initialize the price coordinator."""
        super().__init__(hass, config_entry, api)
        self.name = "Frank Energie price coordinator"
        _LOGGER.debug("Initializing %s (country_code=%s)", self.name, self.country_code)
        self.settings_coordinator = settings_coordinator
        self.update_interval = None
        self.store = price_cache_store(hass, config_entry.entry_id)

    def _selected_customer_source_needs_retry(self, day: date) -> bool:
        """A complete public fallback is not a complete customer tariff."""
        if not self.api.is_authenticated:
            return False
        sources = self._price_sources_by_day.get(day.isoformat(), {})
        return any(
            enabled and sources.get(segment) != "customer"
            for segment, enabled in (
                ("electricity", self.user_electricity_enabled),
                ("gas", self.user_gas_enabled),
            )
        )

    def _parse_cached_data(self, cached_data: dict[str, Any]) -> None:
        """Parse cached data dictionary into coordinator state."""
        if prices_today_dict := cached_data.get("prices_today"):
            self._static_prices_today = _dict_to_market_prices(prices_today_dict)

        if prices_tomorrow_dict := cached_data.get("prices_tomorrow"):
            self.cached_prices_tomorrow = _dict_to_market_prices(prices_tomorrow_dict)

        if resolution_state_dict := cached_data.get("contract_price_resolution_state"):
            data_contract_price_resolution_state = _dict_to_resolution_state(
                resolution_state_dict
            )
            self._static_contract_price_resolution_state = (
                data_contract_price_resolution_state
            )
            self._api_resolution_state = data_contract_price_resolution_state
            self._resolution_change_pending = (
                bool(data_contract_price_resolution_state.upcomingChange)
                if data_contract_price_resolution_state
                else False
            )

        if last_fetch_today_str := cached_data.get("last_fetch_today"):
            self.last_fetch_today = dt_util.parse_datetime(last_fetch_today_str)

        if last_fetch_tomorrow_str := cached_data.get("last_fetch_tomorrow"):
            self.last_fetch_tomorrow = dt_util.parse_datetime(last_fetch_tomorrow_str)

    async def _async_setup(self) -> bool:
        """Load cached prices into data before first refresh."""
        try:
            cached_data = await self.store.async_load()
            if not cached_data:
                return False

            _LOGGER.debug("Loading cached prices from disk into coordinator data")

            self._parse_cached_data(cached_data)

            today_elec_count, today_gas_count = _price_counts(self._static_prices_today)
            tomorrow_elec_count, tomorrow_gas_count = _price_counts(
                self.cached_prices_tomorrow
            )
            _LOGGER.debug(
                "Loaded from disk: today electricity=%s gas=%s, "
                "tomorrow electricity=%s gas=%s",
                today_elec_count,
                today_gas_count,
                tomorrow_elec_count,
                tomorrow_gas_count,
            )

            cache = PricesTodayCache(
                prices_today=self._static_prices_today,
                data_month_summary=None,
                data_invoices=None,
                data_user=None,
                user_sites=None,
                data_period_usage=None,
                data_enode_chargers=None,
                data_smart_batteries=None,
                data_smart_battery_details=[],
                data_smart_battery_sessions=[],
                data_enode_vehicles=None,
                data_pv_systems=None,
                data_pv_summary=None,
                data_user_smart_feed_in=None,
                data_contract_price_resolution_state=self._static_contract_price_resolution_state,
            )

            self.data = self._aggregate_data(cache, self.cached_prices_tomorrow)
            self.cached_prices = self.data

            if not self._is_cache_resolution_valid():
                self._static_prices_today = None
                self.last_fetch_today = None
                self.cached_prices_tomorrow = None
                self.last_fetch_tomorrow = None
                return False

            return True

        except Exception as err:
            _LOGGER.warning(
                "Failed to load cached prices from disk: %s", err, exc_info=True
            )
        return False

    def _is_cache_resolution_valid(self) -> bool:
        """Validate cache granularity without confusing market query and BE tariff.

        Belgian MODEL customer prices can legitimately be hourly even when the
        public market query is configured for 15-minute resolution. Only a
        verified MODEL contract for the selected site allows that difference.
        """
        if self._static_prices_today is None or self.config_entry is None:
            return True

        series = (
            self._static_prices_today.electricity,
            self._static_prices_today.gas,
        )
        resolutions = {
            segment.resolution_minutes
            for segment in series
            if segment is not None and segment.all
        }
        if not resolutions:
            return True

        expected_res = 15 if self.resolution == "PT15M" else 60
        if resolutions == {expected_res}:
            return True

        selected_site = None
        sites = (self.settings_coordinator.data or {}).get(DATA_USER_SITES)
        if sites is not None:
            for site in getattr(sites, "deliverySites", ()) or ():
                if getattr(site, "reference", None) == self.site_reference:
                    selected_site = site
                    break
            if (
                selected_site is None
                and getattr(sites, "reference", None) == self.site_reference
            ):
                selected_site = sites

        proposition = getattr(selected_site, "propositionType", None)
        if (
            self.api.is_authenticated
            and isinstance(proposition, str)
            and proposition.upper() == "MODEL"
            and resolutions <= {15, 60}
        ):
            # These are source intervals, not the requested public resolution.
            return True

        _LOGGER.debug(
            "Cached source resolutions %s do not match query resolution %s",
            sorted(str(value) for value in resolutions),
            expected_res,
        )
        return False

    def _is_cache_fresh(self, now_utc: datetime) -> bool:
        """Check if the loaded disk cache is fresh enough to skip API fetches."""
        if not self.last_fetch_today:
            return False

        if not self._is_cache_resolution_valid():
            return False

        now_local = now_utc.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM))
        today = now_local.date()
        if _amsterdam_date(self.last_fetch_today) != today:
            return False
        if not self._today_cache_covers_selected_site(self._static_prices_today, today):
            return False

        if (
            self.last_fetch_tomorrow
            and _amsterdam_date(self.last_fetch_tomorrow) == today
        ):
            tomorrow = today + timedelta(days=1)
            if self._tomorrow_cache_matches_date(
                self.cached_prices_tomorrow, tomorrow
            ) and self._tomorrow_cache_covers_selected_site(
                self.cached_prices_tomorrow, tomorrow
            ):
                _LOGGER.debug(
                    "Cached tomorrow prices have full required coverage for %s",
                    tomorrow,
                )
                return True
            _LOGGER.debug(
                "Cached tomorrow prices claim to be fetched today but are not "
                "actually dated for %s — cache is not fresh",
                tomorrow,
            )
            return False

        return now_local.time() < time(TOMORROW_PUBLICATION_HOUR_LOCAL, 0)

    async def async_config_entry_first_refresh(self) -> None:
        """Perform first refresh."""
        # Cache completeness is site-specific. Resolve subscribed segments
        # before deciding whether a cached partial day can skip the first
        # live refresh; _async_update_data has not executed yet on this path.
        self.user_electricity_enabled, self.user_gas_enabled = _site_segment_flags(
            (self.settings_coordinator.data or {}).get(DATA_USER_SITES),
            self.site_reference,
        )
        cached_data_loaded = await self._async_setup()
        now_utc = dt_util.utcnow()
        # A fresh disk cache still needs a live HA polling schedule.
        # Previously this return path left update_interval at None forever.
        self._adjust_update_interval(now_utc)

        if not cached_data_loaded or not self._is_cache_fresh(now_utc):
            _LOGGER.debug("No fresh cache found, performing initial API fetch")
            await super().async_config_entry_first_refresh()
        else:
            _LOGGER.debug("Fresh cache loaded, skipping initial API fetch")
            # A validated cached snapshot is usable HA state. Publish it with
            # the supported coordinator API so entities are available at
            # startup even though no provider request was needed yet.
            self.async_set_updated_data(self.data)

    def _adjust_update_interval(self, now_utc: datetime) -> None:
        """Adjust the coordinator update interval based on cache and publication state."""
        now_local = now_utc.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM))
        today = now_local.date()
        tomorrow = today + timedelta(days=1)

        tomorrow_cache_valid = (
            self.cached_prices_tomorrow is not None
            and self.last_fetch_tomorrow is not None
            and _amsterdam_date(self.last_fetch_tomorrow) == today
            and self._tomorrow_cache_matches_date(
                self.cached_prices_tomorrow,
                tomorrow,
            )
            and self._tomorrow_cache_covers_selected_site(
                self.cached_prices_tomorrow, tomorrow
            )
        )

        if tomorrow_cache_valid:
            # Keep the coordinator alive as a safety fallback. The aligned
            # scheduler remains responsible for exact slot-boundary updates.
            new_interval = timedelta(minutes=15 if self.resolution == "PT15M" else 60)
        elif now_local.time() < time(TOMORROW_PUBLICATION_HOUR_LOCAL):
            # Keep a low-frequency fallback alive before publication.
            new_interval = timedelta(hours=1)
        elif now_local.time() < time(15, 0):
            # Poll frequently while tomorrow's prices are expected to become
            # available.
            new_interval = timedelta(minutes=5)
        elif now_local.time() < time(18, 0):
            new_interval = timedelta(minutes=15)
        else:
            new_interval = timedelta(minutes=DEFAULT_INTERVAL_PRICES)

        if self.update_interval == new_interval:
            return

        _LOGGER.debug(
            "Price coordinator update interval changed to %s",
            new_interval,
        )
        self.update_interval = new_interval

    def publish_price_slot(self, now_utc: datetime) -> None:
        """Advance local tariff state and cheapest-price events without API I/O.

        The aligned clock owns business transitions, while this coordinator's
        update_interval is the only routine price-provider poller.
        """
        now_local = now_utc.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM))
        prices = self._static_prices_today
        electricity = getattr(prices, "electricity", None)
        gas = getattr(prices, "gas", None)
        # Both fuels can have a different valid customer resolution. Publish
        # at the shortest source interval so neither series misses a slot.
        published_resolution = min(
            (
                minutes
                for series in (electricity, gas)
                if getattr(series, "all", None)
                and (minutes := getattr(series, "resolution_minutes", None)) in (15, 60)
            ),
            default=15 if self.resolution == "PT15M" else 60,
        )
        if now_local.minute % published_resolution:
            return

        today = now_local.date()
        self._maybe_fire_lowest_price_event(prices, today, now_utc)
        self._maybe_fire_lowest_4p_event(prices, today, now_utc)
        self._maybe_fire_lowest_16p_event(prices, today, now_utc)
        self.async_update_listeners()

    def _carry_forward_previous_day(
        self, new_prices: MarketPrices | None, today: date
    ) -> MarketPrices | None:
        """Preserve yesterday's cached prices when a live fetch crosses local midnight.

        Normally `promote_tomorrow_prices` merges the previous day's prices
        with the already-cached tomorrow prices at exactly 00:00 local time,
        so `previous_hour`/history-dependent sensors don't go unavailable.
        But if tomorrow's prices weren't cached yet when midnight hit (late
        API publication, a failed fetch, a restart), promotion has nothing to
        promote, and this method's caller ends up doing a live, today-only
        fetch instead. Without this, that fetch would replace `today - 1`'s
        data outright and `previous_hour` would have no matching entry for
        the entire first hour of the new day.
        """
        previous = self._static_prices_today
        if previous is None or new_prices is None:
            return new_prices

        tz_amsterdam = ZoneInfo(TIMEZONE_AMSTERDAM)

        def is_from_before_today(price_data: PriceData | None) -> bool:
            return bool(
                price_data
                and price_data.all
                and price_data.all[-1].date_from.astimezone(tz_amsterdam).date() < today
            )

        if not (
            is_from_before_today(previous.electricity)
            or is_from_before_today(previous.gas)
        ):
            return new_prices

        try:
            merged_electricity = self._merge_prices(
                previous.electricity, new_prices.electricity
            )
            merged_gas = self._merge_prices(previous.gas, new_prices.gas)
        except ValueError:
            _LOGGER.debug(
                "Cannot carry yesterday's prices over local midnight "
                "(resolution or energy-type mismatch); using freshly "
                "fetched today prices only"
            )
            return new_prices

        # Keep yesterday + today only; anything older would just accumulate.
        cutoff = today - timedelta(days=2)
        return MarketPrices(
            electricity=self._price_data_after(merged_electricity, cutoff)
            or merged_electricity,
            gas=self._price_data_after(merged_gas, cutoff) or merged_gas,
            energy_country=new_prices.energy_country,
            energy_type=new_prices.energy_type,
        )

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch price data."""
        now_utc = datetime.now(ZoneInfo("UTC"))
        today = now_utc.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
        tomorrow = today + timedelta(days=1)

        skip_api_calls = self._should_skip_api_calls(now_utc)
        self._reconcile_resolution()
        self._adjust_update_interval(now_utc)

        # Reset daily flags on new day
        if (
            self.last_fetch_today is None
            or _amsterdam_date(self.last_fetch_today) != today
        ):
            self._today_prices_logged = False

        if (
            self.last_fetch_tomorrow is None
            or _amsterdam_date(self.last_fetch_tomorrow) != today
        ):
            self._tomorrow_prices_logged = False

        user_data = self.settings_coordinator.data.get(DATA_USER)
        # The settings coordinator fetches UserSites; the price coordinator
        # does not. Derive flags from that authoritative, selected-site object
        # instead of retaining the price coordinator's default False values.
        self.user_electricity_enabled, self.user_gas_enabled = _site_segment_flags(
            self.settings_coordinator.data.get(DATA_USER_SITES),
            self.site_reference,
        )
        if extracted_conn_id := _extract_electricity_connection_id(user_data):
            self._connection_id = extracted_conn_id

        if skip_api_calls:
            _LOGGER.debug("Skipping price API calls during maintenance window")
            if not self.last_update_success:
                raise UpdateFailed("Maintenance window active")
            prices_today = self._static_prices_today
            data_contract_price_resolution_state = (
                self._static_contract_price_resolution_state
            )
            prices_tomorrow = self.cached_prices_tomorrow
        else:
            if (
                self._static_prices_today is None
                or self.last_fetch_today is None
                or _amsterdam_date(self.last_fetch_today) != today
                or not self._today_cache_covers_selected_site(
                    self._static_prices_today, today
                )
                or self._selected_customer_source_needs_retry(today)
            ):
                try:
                    fetched_prices_today = await self._fetch_prices_with_fallback(
                        today, tomorrow, use_fallback=True
                    )
                    if fetched_prices_today is None or not any(
                        _price_counts(fetched_prices_today)
                    ):
                        # A failed retry must not erase partial but still usable
                        # current-day prices. The next poll retries again.
                        prices_today = self._static_prices_today
                        if prices_today is None:
                            raise UpdateFailed("No current-day prices available")
                    else:
                        current_prices = self._carry_forward_previous_day(
                            fetched_prices_today, today
                        )
                        previous_prices = self._static_prices_today
                        prices_today = self._preserve_today_source_coverage(
                            previous_prices, current_prices, today
                        )
                        # If preservation chooses an older interval series, its
                        # original source cannot be inferred from the new reply.
                        if previous_prices is not None:
                            selected_sources = self._price_sources_by_day.get(
                                today.isoformat(), {}
                            )
                            for segment in ("electricity", "gas"):
                                retained = getattr(prices_today, segment, None)
                                prior = getattr(previous_prices, segment, None)
                                fresh = getattr(current_prices, segment, None)
                                if retained is prior and retained is not fresh:
                                    selected_sources[segment] = "cached_unverified"
                        self._update_today_prices(prices_today)
                        sources = self._price_sources_by_day.get(today.isoformat())
                        cache_only = bool(sources) and not any(
                            source.startswith(("customer", "public"))
                            for source in sources.values()
                        )
                        # Never treat a restored/cache-only selection as an API
                        # success. Keep retrying until real provider evidence.
                        if not cache_only:
                            self.last_fetch_today = now_utc
                            self._today_prices_logged = True
                except Exception as err:
                    await self._handle_fetch_exceptions(err)
                    if (
                        self._static_prices_today is not None
                        and self._static_prices_today.electricity is not None
                        and self._static_prices_today.electricity.current is not None
                        and self._static_prices_today.gas is not None
                        and self._static_prices_today.gas.current is not None
                    ):
                        _LOGGER.warning(
                            "Failed to fetch today prices, falling back to cached data: %s",
                            err,
                        )
                        prices_today = self._static_prices_today
                    else:
                        raise UpdateFailed(
                            f"Failed to fetch today prices: {err}"
                        ) from err
            else:
                prices_today = self._static_prices_today

            data_contract_price_resolution_state = (
                await self._fetch_contract_price_resolution_state(self._connection_id)
            )
            self._static_contract_price_resolution_state = (
                data_contract_price_resolution_state
            )
            prices_tomorrow = await self._refresh_tomorrow_cache(
                today, tomorrow, now_utc
            )

        result = _empty_data()
        result[DATA_CONTRACT_PRICE_RESOLUTION_STATE] = (
            data_contract_price_resolution_state
        )

        cache = PricesTodayCache(
            prices_today=prices_today,
            data_month_summary=None,
            data_invoices=None,
            data_user=None,
            user_sites=None,
            data_period_usage=None,
            data_enode_chargers=None,
            data_smart_batteries=None,
            data_smart_battery_details=[],
            data_smart_battery_sessions=[],
            data_enode_vehicles=None,
            data_pv_systems=None,
            data_pv_summary=None,
            data_user_smart_feed_in=None,
            data_contract_price_resolution_state=data_contract_price_resolution_state,
        )

        aggregated = self._aggregate_data(cache, prices_tomorrow)
        result[DATA_ELECTRICITY] = aggregated.get(DATA_ELECTRICITY)
        result[DATA_GAS] = aggregated.get(DATA_GAS)

        if self.settings_coordinator.data:
            result[DATA_USER] = self.settings_coordinator.data.get(DATA_USER)
            result[DATA_USER_SITES] = self.settings_coordinator.data.get(
                DATA_USER_SITES
            )
            result[DATA_TOKEN_EXPIRES_AT] = self.settings_coordinator.data.get(
                DATA_TOKEN_EXPIRES_AT
            )
            result[DATA_REFRESH_TOKEN_EXPIRES_AT] = self.settings_coordinator.data.get(
                DATA_REFRESH_TOKEN_EXPIRES_AT
            )

        self.cached_prices = result

        # Events
        self._maybe_fire_lowest_price_event(prices_today, today, now_utc)
        self._maybe_fire_lowest_4p_event(prices_today, today, now_utc)
        self._maybe_fire_lowest_16p_event(prices_today, today, now_utc)

        if not skip_api_calls:
            try:
                await self.store.async_save(
                    {
                        "prices_today": _market_prices_to_dict(prices_today)
                        if prices_today
                        else None,
                        "prices_tomorrow": _market_prices_to_dict(prices_tomorrow)
                        if prices_tomorrow
                        else None,
                        "contract_price_resolution_state": _resolution_state_to_dict(
                            data_contract_price_resolution_state
                        )
                        if data_contract_price_resolution_state
                        else None,
                        "last_fetch_today": self.last_fetch_today.isoformat()
                        if self.last_fetch_today
                        else None,
                        "last_fetch_tomorrow": self.last_fetch_tomorrow.isoformat()
                        if self.last_fetch_tomorrow
                        else None,
                    }
                )
            except Exception as err:
                _LOGGER.warning(
                    "Failed to save prices to disk cache: %s", err, exc_info=True
                )

        return result


class FrankEnergieBatteryCoordinator(FrankEnergieCoordinator):
    """Coordinator for smart battery data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: FrankEnergie,
        settings_coordinator: FrankEnergieSettingsCoordinator,
    ) -> None:
        """Initialize the battery coordinator."""
        super().__init__(hass, config_entry, api)
        self.name = "Frank Energie battery coordinator"
        _LOGGER.debug("Initializing %s (country_code=%s)", self.name, self.country_code)
        self.settings_coordinator = settings_coordinator
        self.update_interval = timedelta(
            minutes=config_entry.options.get(
                CONF_INTERVAL_BATTERIES, DEFAULT_INTERVAL_BATTERIES
            )
        )

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch battery data."""
        now_utc = datetime.now(ZoneInfo("UTC"))
        if self._should_skip_api_calls(now_utc):
            if self.last_update_success:
                return self.data
            raise UpdateFailed("Maintenance window active")

        today = now_utc.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
        tomorrow = today + timedelta(days=1)
        start_date = today - timedelta(days=1)

        user_data = self.settings_coordinator.data.get(DATA_USER)
        is_smart_trading = self._is_smart_trading_enabled(user_data)

        try:
            data_smart_batteries = await self._fetch_smart_batteries(is_smart_trading)

            (
                data_smart_battery_details,
                data_smart_battery_sessions,
            ) = await self._get_battery_details_and_sessions(
                data_smart_batteries, start_date, tomorrow
            )

            if data_smart_batteries and data_smart_batteries.batteries:
                self.update_interval = timedelta(
                    minutes=self.config_entry.options.get(
                        CONF_INTERVAL_BATTERIES, DEFAULT_INTERVAL_BATTERIES
                    )
                )
            else:
                self.update_interval = timedelta(
                    minutes=self.config_entry.options.get(CONF_INTERVAL_BATTERIES, 15)
                )

            result = _empty_data()
            if self.settings_coordinator.data:
                result[DATA_USER] = self.settings_coordinator.data.get(DATA_USER)
                result[DATA_USER_SITES] = self.settings_coordinator.data.get(
                    DATA_USER_SITES
                )
            result[DATA_BATTERIES] = data_smart_batteries
            result[DATA_BATTERY_DETAILS] = data_smart_battery_details
            result[DATA_BATTERY_SESSIONS] = data_smart_battery_sessions
            return result
        except Exception as err:
            await self._handle_fetch_exceptions(err)
            raise UpdateFailed(f"Failed to fetch battery data: {err}") from err


class FrankEnergieChargerCoordinator(FrankEnergieCoordinator):
    """Coordinator for EV charger data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: FrankEnergie,
        settings_coordinator: FrankEnergieSettingsCoordinator,
    ) -> None:
        """Initialize the charger coordinator."""
        super().__init__(hass, config_entry, api)
        self.name = "Frank Energie charger coordinator"
        _LOGGER.debug("Initializing %s (country_code=%s)", self.name, self.country_code)
        self.settings_coordinator = settings_coordinator
        self.update_interval = timedelta(
            minutes=config_entry.options.get(
                CONF_INTERVAL_CHARGERS, DEFAULT_INTERVAL_CHARGERS
            )
        )

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch charger data."""
        now_utc = datetime.now(ZoneInfo("UTC"))
        if self._should_skip_api_calls(now_utc):
            if self.last_update_success:
                return self.data
            raise UpdateFailed("Maintenance window active")

        start_date = now_utc.astimezone(
            ZoneInfo(TIMEZONE_AMSTERDAM)
        ).date() - timedelta(days=1)

        user_data = self.settings_coordinator.data.get(DATA_USER)
        is_smart_charging = self._is_smart_charging_enabled(user_data)

        try:
            data_enode_chargers = await self._fetch_enode_chargers(
                start_date, is_smart_charging
            )

            has_active_charger = False
            if data_enode_chargers and data_enode_chargers.chargers:
                for charger in data_enode_chargers.chargers:
                    if charger.charge_state and charger.charge_state.is_charging:
                        has_active_charger = True
                        break

            if has_active_charger:
                self.update_interval = timedelta(minutes=2)
            else:
                self.update_interval = timedelta(
                    minutes=self.config_entry.options.get(
                        CONF_INTERVAL_CHARGERS, DEFAULT_INTERVAL_CHARGERS
                    )
                )

            result = _empty_data()
            if self.settings_coordinator.data:
                result[DATA_USER] = self.settings_coordinator.data.get(DATA_USER)
                result[DATA_USER_SITES] = self.settings_coordinator.data.get(
                    DATA_USER_SITES
                )
            result[DATA_ENODE_CHARGERS] = data_enode_chargers
            return result
        except Exception as err:
            await self._handle_fetch_exceptions(err)
            raise UpdateFailed(f"Failed to fetch charger data: {err}") from err


class FrankEnergiePVCoordinator(FrankEnergieCoordinator):
    """Coordinator for smart PV data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: FrankEnergie,
        settings_coordinator: FrankEnergieSettingsCoordinator,
    ) -> None:
        """Initialize the PV coordinator."""
        super().__init__(hass, config_entry, api)
        self.name = "Frank Energie PV coordinator"
        _LOGGER.debug("Initializing %s (country_code=%s)", self.name, self.country_code)
        self.settings_coordinator = settings_coordinator
        self.update_interval = timedelta(
            minutes=config_entry.options.get(CONF_INTERVAL_PV, DEFAULT_INTERVAL_PV)
        )

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch PV data."""
        now_utc = datetime.now(ZoneInfo("UTC"))
        if self._should_skip_api_calls(now_utc):
            if self.last_update_success:
                return self.data
            raise UpdateFailed("Maintenance window active")

        today = now_utc.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
        if (
            self.last_fetch_today is None
            or _amsterdam_date(self.last_fetch_today) != today
        ):
            self._has_pv_systems = None
        self.last_fetch_today = now_utc

        try:
            data_pv_systems, data_user_smart_feed_in = await asyncio.gather(
                self._fetch_smart_pv_systems(),
                self._fetch_user_smart_feed_in(),
            )

            data_pv_summary = {}
            if data_pv_systems and data_pv_systems.systems:
                systems = [s for s in data_pv_systems.systems if s]
                pv_tasks = [
                    self._fetch_smart_pv_summary(system.id) for system in systems
                ]
                pv_summaries = await asyncio.gather(*pv_tasks)
                for system, summary in zip(systems, pv_summaries):
                    if summary:
                        data_pv_summary[system.id] = summary

            if data_pv_systems and data_pv_systems.systems:
                self.update_interval = timedelta(
                    minutes=self.config_entry.options.get(
                        CONF_INTERVAL_PV, DEFAULT_INTERVAL_PV
                    )
                )
            else:
                self.update_interval = timedelta(
                    minutes=self.config_entry.options.get(CONF_INTERVAL_PV, 15)
                )

            result = _empty_data()
            if self.settings_coordinator.data:
                result[DATA_USER] = self.settings_coordinator.data.get(DATA_USER)
                result[DATA_USER_SITES] = self.settings_coordinator.data.get(
                    DATA_USER_SITES
                )
            result[DATA_PV_SYSTEMS] = data_pv_systems
            result[DATA_PV_SUMMARY] = data_pv_summary
            result[DATA_USER_SMART_FEED_IN] = data_user_smart_feed_in
            return result
        except Exception as err:
            await self._handle_fetch_exceptions(err)
            raise UpdateFailed(f"Failed to fetch PV data: {err}") from err


class FrankEnergieVehicleCoordinator(FrankEnergieCoordinator):
    """Coordinator for EV vehicles."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: FrankEnergie,
        settings_coordinator: FrankEnergieSettingsCoordinator,
    ) -> None:
        """Initialize the vehicle coordinator."""
        super().__init__(hass, config_entry, api)
        self.name = "Frank Energie vehicle coordinator"
        _LOGGER.debug("Initializing %s (country_code=%s)", self.name, self.country_code)
        self.settings_coordinator = settings_coordinator
        self.update_interval = timedelta(
            minutes=config_entry.options.get(
                CONF_INTERVAL_VEHICLES, DEFAULT_INTERVAL_VEHICLES
            )
        )

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch vehicle data."""
        now_utc = datetime.now(ZoneInfo("UTC"))
        if self._should_skip_api_calls(now_utc):
            _LOGGER.debug("Skipping vehicle fetch during maintenance window")
            if self.last_update_success:
                return self.data
            raise UpdateFailed("Maintenance window active")

        user_data = self.settings_coordinator.data.get(DATA_USER)
        is_smart_charging = self._is_smart_charging_enabled(user_data)

        try:
            enode_vehicles = await self._fetch_enode_vehicles(is_smart_charging)

            is_active = False
            if enode_vehicles and enode_vehicles.vehicles:
                for vehicle in enode_vehicles.vehicles:
                    if vehicle.is_reachable or (
                        vehicle.charge_state
                        and (
                            vehicle.charge_state.is_charging
                            or vehicle.charge_state.is_plugged_in
                        )
                    ):
                        is_active = True
                        break

            if is_active:
                self.update_interval = timedelta(minutes=1)
            else:
                self.update_interval = timedelta(
                    minutes=self.config_entry.options.get(
                        CONF_INTERVAL_VEHICLES, DEFAULT_INTERVAL_VEHICLES
                    )
                )

            result = _empty_data()
            if self.settings_coordinator.data:
                result[DATA_USER] = self.settings_coordinator.data.get(DATA_USER)
                result[DATA_USER_SITES] = self.settings_coordinator.data.get(
                    DATA_USER_SITES
                )
            result[DATA_ENODE_VEHICLES] = enode_vehicles
            return result
        except Exception as err:
            await self._handle_fetch_exceptions(err)
            raise UpdateFailed(f"Failed to fetch vehicle data: {err}") from err


class FrankEnergieStatisticsCoordinator(FrankEnergieCoordinator):
    """Coordinator for historical statistics, billing, and invoices."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: FrankEnergie,
        settings_coordinator: FrankEnergieSettingsCoordinator,
    ) -> None:
        """Initialize the statistics coordinator."""
        super().__init__(hass, config_entry, api)
        self.name = "Frank Energie statistics coordinator"
        _LOGGER.debug("Initializing %s (country_code=%s)", self.name, self.country_code)
        self.settings_coordinator = settings_coordinator
        self.update_interval = timedelta(
            minutes=config_entry.options.get(
                CONF_INTERVAL_STATISTICS, DEFAULT_INTERVAL_STATISTICS
            )
        )
        # Never share prior-month cache between delivery sites or months.
        self._previous_finance_cache_key: tuple[str | None, str, date] | None = None
        self._previous_finance_snapshot: MonthlyFinanceSnapshot | None = None

    async def _fetch_monthly_insights(
        self, period: str
    ) -> MonthlyFinanceSnapshot | None:
        """Get a named calendar month without exposing account identifiers."""
        if not self.api.is_authenticated or not self.site_reference:
            return None
        try:
            result: MonthInsights | None = await self.api.month_insights(
                self.site_reference, period
            )
        except (
            AuthException,
            RequestException,
            FrankEnergieException,
            ClientError,
            TimeoutError,
            ValueError,
            TypeError,
            KeyError,
        ) as ex:
            # This optional endpoint may return incomplete or malformed data
            # during publication lag. Do not take down existing daily usage,
            # invoice, or month-summary sensors because parsing it failed.
            _LOGGER.debug(
                "Month insights unavailable for %s (%s)", period, type(ex).__name__
            )
            return None
        if result is None:
            return None
        selected_electricity: bool | None = None
        selected_gas: bool | None = None
        if self.settings_coordinator.data:
            selected_electricity, selected_gas = _site_segment_flags(
                self.settings_coordinator.data.get(DATA_USER_SITES),
                self.site_reference,
            )
        return MonthlyFinanceSnapshot(
            month=period,
            insights=result,
            retrieved_at=datetime.now(ZoneInfo("UTC")),
            source_states=self._diagnostic_trace.source_states_for_month(period),
            gas_expected=selected_gas,
            electricity_expected=selected_electricity,
        )

    async def _fetch_previous_finance(
        self, period: str, today: date
    ) -> MonthlyFinanceSnapshot | None:
        """Refresh the historical month at most daily per site and month."""
        key = (self.site_reference, period, today)
        if key == self._previous_finance_cache_key:
            return self._previous_finance_snapshot
        snapshot = await self._fetch_monthly_insights(period)
        if snapshot is None:
            # Keep a previously observed month's provider reading across
            # temporary outages, scoped to exactly this site and period.
            # Do not cache the failed refresh as successful; retry next poll.
            if (
                self._previous_finance_cache_key is not None
                and self._previous_finance_cache_key[:2] == key[:2]
            ):
                return self._previous_finance_snapshot
            return None
        self._previous_finance_snapshot = snapshot
        self._previous_finance_cache_key = key
        return snapshot

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch historical statistics data."""
        now_utc = datetime.now(ZoneInfo("UTC"))
        if self._should_skip_api_calls(now_utc):
            _LOGGER.debug("Skipping statistics fetch during maintenance window")
            if self.last_update_success:
                return self.data
            raise UpdateFailed("Maintenance window active")

        today = now_utc.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
        yesterday = today - timedelta(days=1)
        start_date = yesterday

        try:
            data_month_summary, data_invoices, data_period_usage = await asyncio.gather(
                self._fetch_month_summary(),
                self._fetch_invoices(),
                self._fetch_period_usage(start_date),
            )
            result = _empty_data()
            if self.settings_coordinator.data:
                result[DATA_USER] = self.settings_coordinator.data.get(DATA_USER)
                result[DATA_USER_SITES] = self.settings_coordinator.data.get(
                    DATA_USER_SITES
                )
            result[DATA_MONTH_SUMMARY] = data_month_summary
            result[DATA_INVOICES] = data_invoices
            result[DATA_USAGE] = data_period_usage
            # Explicit opt-in. Never increase API traffic for legacy installs.
            # A period is carried with each snapshot so previous-month data
            # cannot be displayed as the new month after local midnight.
            if self.config_entry.options.get(CONF_MONTHLY_FINANCE_ENABLED, False):
                current_month, previous_month = month_keys(today)
                current_finance, previous_finance = await asyncio.gather(
                    self._fetch_monthly_insights(current_month),
                    self._fetch_previous_finance(previous_month, today),
                )
                result[DATA_MONTH_INSIGHTS_CURRENT] = current_finance
                result[DATA_MONTH_INSIGHTS_PREVIOUS] = previous_finance
            return result
        except Exception as err:
            await self._handle_fetch_exceptions(err)
            raise UpdateFailed(f"Failed to fetch statistics data: {err}") from err


class FrankEnergieBatterySessionCoordinator(
    DataUpdateCoordinator[SmartBatterySessions | None]
):
    """
    Coordinator to fetch smart battery session data from Frank Energie.

    Retrieves sessions for smart batteries and handles update errors and authentication.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: FrankEnergie,
        device_id: str,
    ) -> None:
        """
        Initialize the battery session coordinator.

        Args:
            hass (HomeAssistant): Home Assistant instance.
            entry (ConfigEntry): Config entry containing integration settings.
            api (FrankEnergie): Instance of the FrankEnergie API.
            device_id (str): The smart battery device ID.
        """
        self.api = api
        self.site_reference = config_entry.data.get("site_reference")
        self.device_id = device_id

        super().__init__(
            hass,
            _LOGGER,
            name="Frank Energie Battery Sessions",
            update_interval=timedelta(
                minutes=config_entry.options.get(
                    CONF_INTERVAL_BATTERY_SESSIONS, DEFAULT_INTERVAL_BATTERY_SESSIONS
                )
            ),
            config_entry=config_entry,
        )

    async def _async_update_data(self) -> SmartBatterySessions | None:
        """
        Fetch smart battery session data.

        Returns:
            SmartBatterySessions: Session data for the specified smart battery device.

        Raises:
            UpdateFailed: If an error occurs during data fetching.
        """
        try:
            today = datetime.now(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
            tomorrow = today + timedelta(days=1)

            if not self.api.is_authenticated:
                raise UpdateFailed("API client is not authenticated.")

            if not self.device_id:
                raise UpdateFailed("No device ID provided for smart battery sessions.")

            # Fetch Month-To-Date sessions up to and including today (by querying until 'tomorrow') to get unfinalized live data
            mtd_start = min(today.replace(day=1), today - timedelta(days=1))
            return await self.api.smart_battery_sessions(
                self.device_id, mtd_start, tomorrow
            )

        except UpdateFailed as ex:
            if self.last_update_success:
                _LOGGER.warning(str(ex))
                return self.data
            raise
        except AuthException as ex:
            _LOGGER.debug(
                "Authentication tokens expired, attempting token renewal: %s", ex
            )
            await self.api.renew_token()
            raise UpdateFailed(
                "Authentication failed and token was renewed. Retry update."
            ) from ex

        except RequestException as ex:
            raise UpdateFailed(
                f"Failed to fetch battery session data from Frank Energie: {ex!s}"
            ) from ex

        except ConfigEntryAuthFailed as ex:
            _LOGGER.exception("Authentication failed")
            raise ex

        except asyncio.CancelledError:
            raise

        except Exception as ex:
            raise UpdateFailed(
                f"Unexpected error while fetching battery session data: {ex}"
            ) from ex


def _parse_resolution(resolution: str) -> int:
    """Convert ISO8601 duration (PT60M) to minutes."""
    if resolution.startswith("PT") and resolution.endswith("M"):
        return int(resolution[2:-1])
    return 0
