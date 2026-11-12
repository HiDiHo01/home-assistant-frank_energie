"""Coordinator implementation for Frank Energie integration.
Fetching the latest data from Frank Energie and updating the states."""

# coordinator.py
# version 2026.10.23

import asyncio
import copy
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from datetime import UTC, date, datetime, time, timedelta
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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.exceptions import ConfigEntryAuthFailed
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
    RateLimitException,
)
from python_frank_energie.models import (
    ContractPriceResolutionState,
    DeliverySite,
    EnodeChargers,
    EnodeVehicle,
    EnodeVehicles,
    Invoices,
    MarketPrices,
    MonthSummary,
    PeriodUsageAndCosts,
    Price,
    PriceData,
    Resolution,
    SmartBatteries,
    SmartBattery,
    SmartBatteryDetails,
    SmartBatterySessions,
    SmartPvSystems,
    SmartPvSystemSummary,
    User,
    UserSites,
    UserSmartFeedInStatus,
)

from . import helpers
from .const import (
    CONF_INTERVAL_BATTERIES,
    CONF_INTERVAL_BATTERY_SESSIONS,
    CONF_INTERVAL_CHARGERS,
    CONF_INTERVAL_PV,
    CONF_INTERVAL_SETTINGS,
    CONF_INTERVAL_STATISTICS,
    CONF_INTERVAL_VEHICLES,
    COUNTRY_USES_SIMPLE_MARKET_PRICES,
    DATA_BATTERIES,
    DATA_BATTERY_DETAILS,
    DATA_BATTERY_SESSIONS,
    DATA_BATTERY_SETTINGS,
    DATA_CONTRACT_PRICE_RESOLUTION_STATE,
    DATA_DELIVERY_SITE,
    DATA_ELECTRICITY,
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DATA_GAS,
    DATA_INVOICES,
    DATA_MONTH_SUMMARY,
    DATA_PV_SUMMARY,
    DATA_PV_SYSTEMS,
    DATA_REFRESH_TOKEN_EXPIRES_AT,
    DATA_TOKEN_EXPIRES_AT,
    DATA_USAGE,
    DATA_USER,
    DATA_USER_SITES,
    DATA_USER_SMART_FEED_IN,
    DATA_USER_SMART_FEED_IN_FETCHED,
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
from .device_inventory import async_identify_inventory_device
from .helpers import (
    api_maintenance_active,
    decrypt_password,
    known_site_contracts,
    price_cache_store,
)
from .rate_limit import get_rate_limit_state, rate_limit_update_failed
from .mutation_queue import MutationQueue
from .repairs import async_process_device_inventory

_LOGGER = logging.getLogger(__name__)

# Battery options and change windows change rarely; refetch them at most this often.
BATTERY_SETTINGS_REFRESH_INTERVAL: Final[timedelta] = timedelta(minutes=30)
# Retry ambiguous empty responses and transient failures without discarding cache.
BATTERY_SETTINGS_FAILURE_RETRY_INTERVAL: Final[timedelta] = timedelta(minutes=15)
_LOG_AUTH_TOKENS_EXPIRED: Final = (
    "Authentication tokens expired, trying to renew them (%s)"
)
_DEFAULT_ENTRY_TITLE: Final[str] = "no title"


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
    electricity_count = (
        len(market_prices.electricity.all) if market_prices.electricity else 0
    )
    gas_count = len(market_prices.gas.all) if market_prices.gas else 0
    return electricity_count, gas_count


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

    invoices: Invoices | None
    """Optional invoices data."""

    usage: PeriodUsageAndCosts | None
    """Optional user data."""

    user: User | None
    """Optional user data."""

    user_sites: UserSites | None
    """Optional user sites."""

    delivery_site: DeliverySite | None
    """Delivery site matching the config entry's site reference."""

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

    smart_battery_settings: dict[str, object]
    """Per-battery adjustable settings and options, keyed by battery ID."""

    smart_pv_systems: SmartPvSystems | None
    """Optional smart PV systems data."""

    smart_pv_summary: dict[str, SmartPvSystemSummary] | None
    """Optional smart PV system summary data."""

    user_smart_feed_in: UserSmartFeedInStatus | None
    """Optional user smart feed-in status."""

    user_smart_feed_in_fetched: bool
    """Whether the user smart feed-in API request succeeded, including a null response."""

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
        DATA_INVOICES: None,
        DATA_USAGE: None,
        DATA_USER: None,
        DATA_USER_SITES: None,
        DATA_DELIVERY_SITE: None,
        DATA_ENODE_CHARGERS: None,
        DATA_BATTERIES: None,
        DATA_BATTERY_DETAILS: [],
        DATA_BATTERY_SESSIONS: [],
        DATA_BATTERY_SETTINGS: {},
        DATA_ENODE_VEHICLES: None,
        DATA_PV_SYSTEMS: None,
        DATA_PV_SUMMARY: None,
        DATA_USER_SMART_FEED_IN: None,
        DATA_USER_SMART_FEED_IN_FETCHED: False,
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
        self.rate_limits = get_rate_limit_state(hass, config_entry)
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
        self._connection_id: str | None = (
            None  # cache voor contractPriceResolutionState
        )
        self._api_resolution_state: ContractPriceResolutionState | None = None
        self._resolution_change_pending: bool = False
        self.enode_chargers: EnodeChargers | None = None
        self.data: FrankEnergieData = _empty_data()
        self.verified_inventory_ids: set[str] | None = None
        self.update_interval = timedelta(
            minutes=15 if self.resolution == "PT15M" else 60
        )
        self._last_update_success = False
        self.user_electricity_enabled = False
        self.user_gas_enabled = False
        self._price_fetch_used_cache: bool = False

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

    async def async_api_call[**P, T](
        self,
        resource: str,
        method: Callable[P, Awaitable[T]],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        """Apply the entry's cooldown before making a public client method call."""
        return await self.rate_limits.async_call(
            resource,
            lambda: method(*args, **kwargs),
            authenticated=self.api.is_authenticated and resource != "login",
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

        return api_maintenance_active(now_utc)

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
    def _price_series_for_contracts(
        prices: MarketPrices | None, required_contracts: set[str] | None = None
    ) -> list[PriceData | None]:
        """Select required series, or populated series when contracts are unknown."""
        if prices is None:
            return []
        return [
            series
            for kind, series in (
                ("ELECTRICITY", prices.electricity),
                ("GAS", prices.gas),
            )
            if (
                kind in required_contracts
                if required_contracts
                else series is not None and series.all
            )
        ]

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
        price_series = FrankEnergieCoordinator._price_series_for_contracts(
            cached_prices_tomorrow
        )
        if not price_series:
            return False

        tz_amsterdam = ZoneInfo(TIMEZONE_AMSTERDAM)
        return all(
            price.date_from.astimezone(tz_amsterdam).date() >= tomorrow
            for series in price_series
            if series is not None
            for price in series.all
        )

    def _price_contracts(self) -> set[str]:
        """Return known energy contracts for the selected site's price fetch."""
        if not self.api.is_authenticated:
            return set()
        settings = getattr(self, "settings_coordinator", self)
        settings_data = settings.data
        site = settings_data.get(DATA_DELIVERY_SITE) if settings_data else None
        contracts = known_site_contracts(site)
        if contracts is not None:
            return contracts
        return {
            kind
            for kind, enabled in (
                ("ELECTRICITY", self.user_electricity_enabled),
                ("GAS", self.user_gas_enabled),
            )
            if enabled
        }

    @staticmethod
    def _prices_cover_date(
        prices: MarketPrices | None,
        day: date,
        *,
        required_contracts: set[str] | None = None,
    ) -> bool:
        """Check that `prices` holds at least one entry dated `day` (Amsterdam-local).

        The today-side counterpart of `_tomorrow_cache_matches_date`:
        `last_fetch_today` only records *when* a fetch happened, not *which
        day* it returned. When both the user and public price fetches fail,
        `_fetch_prices_with_fallback` returns `_cached_prices`, which at
        startup is the previous day's disk cache. Trusting the timestamp
        alone would stamp that as today's fetch and keep every price sensor
        unavailable until the midnight promotion.

        Known site contracts require a non-empty series for each commodity.
        Without contract metadata, validate every non-empty series so public
        prices and single-contract sites remain supported.
        """
        if prices is None:
            return False

        price_series_list = FrankEnergieCoordinator._price_series_for_contracts(
            prices, required_contracts
        )
        tz_amsterdam = ZoneInfo(TIMEZONE_AMSTERDAM)
        return bool(price_series_list) and all(
            price_series is not None
            and any(
                price.date_from.astimezone(tz_amsterdam).date() == day
                for price in price_series.all
            )
            for price_series in price_series_list
        )

    def _tomorrow_prices_complete(
        self, prices: MarketPrices | None, tomorrow: date
    ) -> bool:
        """Check tomorrow's dates and every commodity required by the site."""
        return self._tomorrow_cache_matches_date(
            prices, tomorrow
        ) and self._prices_cover_date(
            prices, tomorrow, required_contracts=self._price_contracts()
        )

    @staticmethod
    def _filter_tomorrow_prices(
        prices: MarketPrices | None, tomorrow: date
    ) -> MarketPrices | None:
        """Discard stale commodities independently, retaining published prices."""
        if prices is None:
            return None
        tz_amsterdam = ZoneInfo(TIMEZONE_AMSTERDAM)

        def valid_series(series: PriceData | None) -> PriceData | None:
            """Reject a commodity if any entry precedes tomorrow's local date."""
            if series is None or all(
                price.date_from.astimezone(tz_amsterdam).date() >= tomorrow
                for price in series.all
            ):
                return series
            return None

        electricity = valid_series(prices.electricity)
        gas = valid_series(prices.gas)
        if not (
            (electricity is not None and electricity.all)
            or (gas is not None and gas.all)
        ):
            return None
        if electricity is prices.electricity and gas is prices.gas:
            return prices
        return replace(prices, electricity=electricity, gas=gas)

    def _cached_prices_active(
        self, prices: MarketPrices | None, moment: datetime
    ) -> bool:
        """Require an active interval for each contracted commodity, in UTC."""
        series = self._price_series_for_contracts(prices, self._price_contracts())
        return bool(series) and all(
            item is not None and any(price.contains_time(moment) for price in item.all)
            for item in series
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
            and not self._tomorrow_cache_matches_date(
                self.cached_prices_tomorrow, tomorrow
            )
        ):
            # A live fetch now validates itself before caching (see the
            # freshly-fetched check below), so reaching this branch means the
            # bad data came from disk — e.g. state persisted by an older
            # build. Kept at warning: that's a genuine anomaly, not routine.
            _LOGGER.warning(
                "Cached tomorrow prices claim to be fetched today but are not "
                "actually dated for %s — discarding stale/incorrect commodities and "
                "fetching fresh data",
                tomorrow,
            )
            self.cached_prices_tomorrow = self._filter_tomorrow_prices(
                self.cached_prices_tomorrow, tomorrow
            )
            self.last_fetch_tomorrow = None

        if (
            self.last_fetch_tomorrow is not None
            and _amsterdam_date(self.last_fetch_tomorrow) == today
            and self._tomorrow_prices_complete(self.cached_prices_tomorrow, tomorrow)
        ):
            _LOGGER.debug(
                "Tomorrow prices already cached (fetched %s), skipping API call",
                self.last_fetch_tomorrow,
            )
            return self.cached_prices_tomorrow

        _LOGGER.debug(
            "Tomorrow cache status: cached=%s last_fetch=%s",
            self.cached_prices_tomorrow is not None,
            self.last_fetch_tomorrow,
        )
        _LOGGER.debug(
            "Fetching tomorrow prices for %s",
            tomorrow,
        )

        # The provider can echo today's gas while electricity is already
        # published for tomorrow, or vice versa. Keep the valid commodity and
        # retry the other rather than discarding the complete response.
        prices_tomorrow = self._filter_tomorrow_prices(
            await self._fetch_tomorrow_data(tomorrow), tomorrow
        )

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

        if has_electricity or has_gas:
            self._cache_tomorrow_prices(prices_tomorrow, tomorrow, now_utc)
        else:
            _LOGGER.debug("Tomorrow prices not yet available, retrying on next refresh")

        if self.cached_prices_today is not None:
            entry_id = self.config_entry.entry_id if self.config_entry else "Unknown"
            _LOGGER.debug(
                "[%s] Tomorrow electricity periods: %s",
                entry_id,
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

    def _cache_tomorrow_prices(
        self, prices: MarketPrices, tomorrow: date, fetched_at: datetime
    ) -> None:
        """Keep partial commodities available while retrying missing contracts."""
        resolved = prices
        previous = self.cached_prices_tomorrow
        if previous is not None:
            resolved = replace(
                prices,
                electricity=(
                    prices.electricity
                    if prices.electricity is not None and prices.electricity.all
                    else previous.electricity
                ),
                gas=(
                    prices.gas
                    if prices.gas is not None and prices.gas.all
                    else previous.gas
                ),
            )
        self.cached_prices_tomorrow = resolved
        if not self._tomorrow_prices_complete(resolved, tomorrow):
            self.last_fetch_tomorrow = None
            _LOGGER.debug(
                "Tomorrow's prices are partial; waiting for remaining contracts"
            )
            return

        self.last_fetch_tomorrow = fetched_at
        _LOGGER.info("Retrieved Frank Energie tomorrow prices for %s", tomorrow)
        if self.config_entry is not None:
            self.hass.bus.async_fire(
                EVENT_FRANK_ENERGIE,
                {
                    "entry_id": self.config_entry.entry_id,
                    "entry_title": self.config_entry.title or _DEFAULT_ENTRY_TITLE,
                    "action": "tomorrow_prices_available",
                    "date": tomorrow.isoformat(),
                    "resolution": self.resolution,
                },
            )

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

        entry_title = self.config_entry.title or _DEFAULT_ENTRY_TITLE
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

        entry_title = self.config_entry.title or _DEFAULT_ENTRY_TITLE
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

        entry_title = self.config_entry.title or _DEFAULT_ENTRY_TITLE
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
            return await self.async_api_call("UserSites", self.api.UserSites)
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
            return await self.async_api_call(
                "month_summary", self.api.month_summary, self.site_reference
            )

        except AuthException as ex:
            _LOGGER.debug(
                "Authentication failed while fetching month summary: %s",
                ex,
            )
            await self._try_renew_token()
            return None

        except RateLimitException:
            raise
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
        # An API exception is a failed refresh, never a successful empty invoice.
        # The invoice coordinator translates failures into unavailable state
        # while retaining the last successful result in its data cache.
        return await self.async_api_call(
            "invoices", self.api.invoices, self.site_reference
        )

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
            return await self.async_api_call(
                "period_usage_and_costs",
                self.api.period_usage_and_costs,
                self.site_reference,
                start_date.isoformat(),
            )
        except RateLimitException:
            raise
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

    async def _fetch_user_smart_hvac(self, user_data: User) -> None:
        """Populate optional UserSmartHvac without invalidating other user data."""
        # Me.user_smart_hvac is a root-level GraphQL field on python-frank-energie main.
        previous_user = self.data.get(DATA_USER) if self.data else None
        user_data.smartHvac = None
        try:
            me_data = await self.async_api_call("me", self.api.me, self.site_reference)
        except RateLimitException:
            # Smart HVAC is optional; a throttle must not discard valid user data.
            user_data.smartHvac = getattr(previous_user, "smartHvac", None)
        except AuthRequiredException:
            raise
        except (
            AuthException,
            RequestException,
            FrankEnergieException,
            ClientError,
            TimeoutError,
        ):
            _LOGGER.debug("UserSmartHvac status is unavailable for this site")
        else:
            user_data.smartHvac = getattr(me_data, "user_smart_hvac", None)

    def _update_user_metadata(self, user_data: User | None) -> None:
        """Update country and connection metadata from the user response."""
        if not self._country_code and user_data:
            country_code_raw = user_data.countryCode
            if isinstance(country_code_raw, str) and country_code_raw:
                country_code = country_code_raw.upper()
                if country_code in SUPPORTED_COUNTRIES:
                    self._country_code = country_code
        if user_data is not None:
            selected_connection_id = _extract_electricity_connection_id(user_data)
            if selected_connection_id != self._connection_id:
                self._connection_id = selected_connection_id
                _LOGGER.debug(
                    "Electricity connection for the selected site %s",
                    "available" if selected_connection_id else "missing",
                )

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
            user_data = await self.async_api_call(
                "user", self.api.user, self.site_reference
            )
            if user_data is not None:
                await self._fetch_user_smart_hvac(user_data)
            self._update_user_metadata(user_data)
            return user_data
        except AuthRequiredException:
            raise ConfigEntryAuthFailed(
                "Frank Energie authentication is required"
            ) from None
        except AuthException:
            _LOGGER.warning("Authentication failed while fetching user data")
            return None
        except RateLimitException:
            raise
        except (RequestException, FrankEnergieException, ClientError) as ex:
            _LOGGER.warning("No user data available: %s", ex)
            return None

    async def _fetch_contract_price_resolution_state(
        self, connection_id: str | None
    ) -> ContractPriceResolutionState | None:
        """Fetch and process the contract price resolution state."""
        if self._price_contracts() == {"GAS"}:
            return None
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
            resolution_state = await self.async_api_call(
                "contract_price_resolution_state",
                self.api.contract_price_resolution_state,
                connection_id,
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

        except RateLimitException as err:
            _LOGGER.debug("Contract price resolution state rate limited: %s", err)
            return self._api_resolution_state
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.exception("Error fetching ContractPriceResolutionState: %s", err)
            return None

    def _handle_dynamic_fetch_error(self, method_name: str, err: Exception) -> None:
        """Handle errors during dynamic fetches, propagating network errors on initial refresh."""
        if isinstance(
            err,
            (
                AuthException,
                AuthRequiredException,
                RateLimitException,
                asyncio.CancelledError,
            ),
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
            return await self.async_api_call(
                "enode_chargers",
                self.api.enode_chargers,
                self.site_reference,
                start_date,
            )
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
            return await self.async_api_call(
                "smart_batteries",
                self.api.smart_batteries,
            )
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
            vehicles = await self.async_api_call(
                "enode_vehicles", self.api.enode_vehicles
            )
            _LOGGER.debug("Fetched Enode vehicles")
            return vehicles
        except Exception as err:
            self._handle_dynamic_fetch_error("enode vehicles", err)
            return None

    def _process_device_inventory(self, kind: str, inventory: object) -> None:
        """Record only validated live inventories without breaking hardware polling."""
        if inventory is None:
            return
        try:
            self.verified_inventory_ids = async_process_device_inventory(
                self.hass, self.config_entry, kind, inventory
            )
        except Exception:  # noqa: BLE001 - Repairs must not break polling
            self.verified_inventory_ids = None
            _LOGGER.exception("Failed to process the %s inventory for Repairs", kind)

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
        if self._has_pv_systems is False and not any(
            async_identify_inventory_device(
                self.hass, self.config_entry, device, expected_kind="pv"
            )
            for device in dr.async_entries_for_config_entry(
                dr.async_get(self.hass), self.config_entry.entry_id
            )
        ):
            return None
        try:
            result = await self.async_api_call(
                "smart_pv_systems", self.api.smart_pv_systems
            )
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
            return await self.async_api_call(
                "smart_pv_system_summary", self.api.smart_pv_system_summary, device_id
            )
        except Exception as err:
            self._handle_dynamic_fetch_error(
                f"smart PV system summary for {device_id}", err
            )
            return None

    async def _fetch_user_smart_feed_in(
        self,
    ) -> tuple[UserSmartFeedInStatus | None, bool]:
        """Fetch feed-in status and record whether a null response is authoritative."""
        if not self.api.is_authenticated:
            _LOGGER.debug(
                "Skipping user smart feed-in fetch because the client is not authenticated"
            )
            return None, False
        try:
            status = await self.async_api_call(
                "user_smart_feed_in", self.api.user_smart_feed_in
            )
            return status, True
        except RateLimitException:
            # Preserve independent PV data while the feed-in request cools down.
            return None, False
        except Exception as err:
            self._handle_dynamic_fetch_error("user smart feed-in status", err)
            return None, False

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
            details = await self.async_api_call(
                "smart_battery_details", self.api.smart_battery_details, battery.id
            )
            if details:
                # Merge settings from detailed response into battery object
                if details.smart_battery and details.smart_battery.settings:
                    battery.settings = details.smart_battery.settings

                # Merge SUMMARY
                if details.smart_battery_summary:
                    battery.summary = details.smart_battery_summary
                _LOGGER.debug("Merged smart battery settings and summary")
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
            sessions = await self.async_api_call(
                "smart_battery_sessions",
                self.api.smart_battery_sessions,
                battery.id,
                mtd_start,
                tomorrow,
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

        except RateLimitException as err:
            _LOGGER.debug("Tomorrow prices rate limited: %s", err)
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

    async def _fetch_public_prices_unauthenticated(
        self, start_date: date, end_date: date, country_code: str
    ) -> MarketPrices | None:
        """Recover public prices without triggering token renewal.

        An authenticated client's implicit renewal may be rate-limited while
        the provider's public price endpoints remain accessible.
        """
        async with FrankEnergie(
            clientsession=async_get_clientsession(self.hass)
        ) as public_api:
            if COUNTRY_USES_SIMPLE_MARKET_PRICES.get(country_code, False):
                return await self.rate_limits.async_call(
                    "country_prices",
                    lambda: public_api.country_prices(
                        country_code, start_date, end_date, self.resolution
                    ),
                    authenticated=False,
                )
            return await self.rate_limits.async_call(
                "prices",
                lambda: public_api.prices(start_date, resolution=self.resolution),
                authenticated=False,
            )

    async def _fetch_public_prices_for_range(
        self, start_date: date, end_date: date, country_code: str
    ) -> MarketPrices | None:
        """Fetch public prices for a given date range."""
        _LOGGER.debug(
            "Fetching public prices for country=%s resolution=%s",
            country_code,
            self.resolution,
        )

        try:
            if COUNTRY_USES_SIMPLE_MARKET_PRICES.get(country_code, False):
                return await self.async_api_call(
                    "country_prices",
                    self.api.country_prices,
                    country_code,
                    start_date,
                    end_date,
                    self.resolution,
                )

            return await self.async_api_call(
                "prices",
                self.api.prices,
                start_date,
                resolution=self.resolution,
            )

        except RateLimitException as err:
            if err.operation_name != FrankEnergie.RENEW_TOKEN_OPERATIONNAME:
                raise
            return await self._fetch_public_prices_unauthenticated(
                start_date, end_date, country_code
            )
        except RequestException as ex:
            if not _is_no_market_prices_error(ex):
                raise
            _LOGGER.debug(
                "No market prices available yet for %s (%s - %s)",
                country_code,
                start_date,
                end_date,
            )
            return None

        except NetworkError as err:
            _LOGGER.warning("Failed to fetch public prices: %s", err)
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
            return None

        site_reference = self.site_reference
        if site_reference is None:
            _LOGGER.debug(
                "Skipping user prices fetch because site_reference is not set"
            )
            return None

        user_country = self._user_country or default_country

        _LOGGER.debug(
            "Fetching user prices for country=%s resolution=%s",
            user_country,
            self.resolution,
        )

        try:
            return await self.async_api_call(
                "user_prices",
                self.api.user_prices,
                site_reference,
                user_country,
                start_date,
                end_date,
            )

        except RequestException as ex:
            if not _is_no_market_prices_error(ex):
                raise
            _LOGGER.debug(
                "No user market prices available yet for %s (%s - %s)",
                user_country,
                start_date,
                end_date,
            )
            return None

        except NetworkError as err:
            _LOGGER.warning("Failed to fetch user prices: %s", err)
            return None

    @staticmethod
    async def _price_source_or_throttle(
        source: Awaitable[MarketPrices | None],
    ) -> tuple[MarketPrices | None, RateLimitException | None]:
        """Capture only a resource throttle without discarding the other source."""
        try:
            return await source, None
        except RateLimitException as error:
            return None, error

    @staticmethod
    def _has_price_entries(prices: MarketPrices | None) -> bool:
        """Reject empty market responses, even when the wrapper exists."""
        return bool(
            prices is not None
            and (
                (prices.electricity and prices.electricity.all)
                or (prices.gas and prices.gas.all)
            )
        )

    async def _fetch_price_sources(
        self, start_date: date, end_date: date, country_code: str
    ) -> tuple[MarketPrices | None, MarketPrices | None]:
        """Fetch both price sources independently and preserve useful results."""
        public_result, user_result = await asyncio.gather(
            self._price_source_or_throttle(
                self._fetch_public_prices_for_range(start_date, end_date, country_code)
            ),
            self._price_source_or_throttle(
                self._fetch_user_prices_for_range(start_date, end_date, country_code)
            ),
        )
        public_prices, public_error = public_result
        user_prices, user_error = user_result
        if not self._has_price_entries(public_prices) and not self._has_price_entries(
            user_prices
        ):
            if rate_limit_error := public_error or user_error:
                raise rate_limit_error
        return public_prices, user_prices

    async def _fetch_prices_with_fallback(
        self, start_date: date, end_date: date, use_fallback: bool = True
    ) -> MarketPrices | None:
        """Fetch prices with fallback to public prices and cached data.

        When use_fallback=False (e.g. tomorrow's prices), empty user prices are
        returned as-is without substituting public prices.
        This method attempts to fetch user-specific prices first, and if they are not available,
        it falls back to public prices.
        """
        self._price_fetch_used_cache = False
        # Ensure country_code is always a str (fallback to "NL" if None)
        country_code = (
            (self.hass.config.country or "NL")
            if self.hass and self.hass.config
            else "NL"
        )

        _LOGGER.debug("Fetching prices concurrently (use_fallback=%s)", use_fallback)
        public_prices, user_prices = await self._fetch_price_sources(
            start_date, end_date, country_code
        )
        public_is_live = public_prices is not None

        if public_prices is None:
            if not use_fallback and not self.api.is_authenticated:
                # e.g. tomorrow's prices not published yet — don't substitute
                # today's cached prices, or _refresh_tomorrow_cache will treat
                # today's data as a successful tomorrow fetch and stop retrying.
                _LOGGER.debug(
                    "No public prices available yet, skipping fallback to cached prices"
                )
                return None
            public_prices = getattr(self, "_cached_prices", None) or MarketPrices(
                electricity=PriceData([], "electricity"),
                gas=PriceData([], "gas"),
                energy_country=country_code or "NL",
            )

        if not self.api.is_authenticated:
            self._price_fetch_used_cache = not public_is_live
            return public_prices

        if user_prices is None:
            if not use_fallback:
                _LOGGER.debug("No user prices for tomorrow, skipping fallback")
                return None
            _LOGGER.warning(
                "Failed to fetch user prices, falling back to public prices"
            )
            self._price_fetch_used_cache = not public_is_live
            return public_prices

        # Use user prices if both gas and electricity have data
        has_electricity = user_prices.electricity is not None and getattr(
            user_prices.electricity, "all", None
        )
        has_gas = user_prices.gas is not None and getattr(user_prices.gas, "all", None)

        if has_electricity and has_gas:
            self._cached_prices = user_prices
            return user_prices

        # No fallback for tomorrow — return what we have
        if not use_fallback:
            _LOGGER.debug(
                "No complete user prices for tomorrow (electricity=%s gas=%s), skipping fallback",
                bool(has_electricity),
                bool(has_gas),
            )
            return user_prices

        # Fallback logic for today
        contracts = self._price_contracts()
        if not has_gas:
            _LOGGER.info("No gas prices for user, falling back to public prices")
            user_prices.gas = (
                public_prices.gas if "GAS" in contracts else PriceData([], "gas")
            )

        if not has_electricity:
            _LOGGER.info(
                "No electricity prices for user, falling back to public prices"
            )
            user_prices.electricity = (
                public_prices.electricity
                if "ELECTRICITY" in contracts
                else PriceData([], "electricity")
            )

        self._price_fetch_used_cache = not public_is_live and (
            (not has_gas and "GAS" in contracts)
            or (not has_electricity and "ELECTRICITY" in contracts)
        )
        self._cached_prices = user_prices
        return user_prices

    async def _handle_fetch_exceptions(self, ex: Exception) -> None:
        """Normalize fetch exceptions for DataUpdateCoordinator callers."""
        if isinstance(ex, (ConfigEntryAuthFailed, AuthRequiredException)):
            raise ex
        if isinstance(ex, RateLimitException):
            raise rate_limit_update_failed(ex, self.update_interval) from ex
        if isinstance(ex, UpdateFailed):
            raise ex
        if isinstance(ex, RequestException) and str(ex).startswith("user-error:"):
            raise ConfigEntryAuthFailed from ex
        if isinstance(ex, AuthException):
            _LOGGER.debug(_LOG_AUTH_TOKENS_EXPIRED, ex)
            try:
                await self._try_renew_token()
            except RateLimitException as err:
                raise rate_limit_update_failed(err, self.update_interval) from err
            raise UpdateFailed(ex) from ex

    async def _try_renew_token(self) -> None:
        """Try to renew authentication token with shared-client serialization."""

        async with self._auth_lock:
            # A failed silent login must not trigger another token renewal
            # until the provider's authentication cooldown has expired.
            self.rate_limits.check_authentication_recovery()
            try:
                updated_tokens = await self.async_api_call(
                    "renew_token",
                    self.api.renew_token,
                )
                updated_data = {
                    **self.config_entry.data,
                    CONF_ACCESS_TOKEN: updated_tokens.authToken,
                    CONF_TOKEN: updated_tokens.refreshToken,
                }
                # Clean up old plaintext credentials in data if any
                updated_data.pop(CONF_USERNAME, None)
                updated_data.pop(CONF_PASSWORD, None)

                # Update the config entry with the new tokens
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=updated_data
                )

                _LOGGER.debug("Successfully renewed token")

            except AuthException as ex:
                await self._async_reauthenticate(ex)

    async def _async_reauthenticate(self, renewal_error: AuthException) -> None:
        """Try stored credentials while the caller holds the shared auth lock."""
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
                auth = await self.async_api_call(
                    "login", self.api.login, username, decrypted_password
                )
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
            except RateLimitException:
                raise
            except (AuthException, FrankEnergieException):
                _LOGGER.warning(
                    "Silent re-login failed with API/Auth error; starting reauthentication"
                )
            except Exception as login_ex:
                _LOGGER.warning(
                    "Unexpected silent re-login failure (%s); retrying later",
                    type(login_ex).__name__,
                )
                raise UpdateFailed(
                    "Unexpected silent re-login failure; retry later"
                ) from None

        _LOGGER.warning("Token renewal failed; starting reauthentication")
        raise ConfigEntryAuthFailed("Authentication has expired") from None

    async def _fetch_authenticated[T](
        self,
        method: Callable[..., Awaitable[T]],
        *args: object,
    ) -> T | None:
        """Execute an authenticated API call while redacting private response data."""
        operation = getattr(method, "__name__", type(method).__name__)
        if not self.api.is_authenticated:
            _LOGGER.warning("API not authenticated, skipping call to %s", operation)
            return None
        try:
            result = await self.async_api_call(operation, method, *args)
            _LOGGER.debug("Fetched data from %s", operation)
            return result
        except (asyncio.CancelledError, RateLimitException):
            raise
        except Exception as err:
            helpers.log_api_exception(
                _LOGGER, "Failed to fetch data using %s", err, operation
            )
            return None

    def _ensure_utc(self, value: datetime) -> datetime:
        """Ensure datetime is timezone-aware UTC."""
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo("UTC"))
        return value

    @staticmethod
    def _slide_price_window_totals(
        incoming: float,
        outgoing: float,
        current_sum: float,
        invalid_count: int,
        exact_sum: Decimal | None,
    ) -> tuple[float, int, Decimal | None]:
        """Advance the float and optional decimal totals by one price slot."""
        if math.isfinite(incoming):
            current_sum += incoming
            if exact_sum is not None:
                exact_sum += Decimal(str(incoming))
        else:
            invalid_count += 1

        if math.isfinite(outgoing):
            current_sum -= outgoing
            if exact_sum is not None:
                exact_sum -= Decimal(str(outgoing))
        else:
            invalid_count -= 1

        return current_sum, invalid_count, exact_sum

    @staticmethod
    def _decimal_price_window_total(
        prices: list[Price],
        start_index: int,
        window: int,
    ) -> Decimal:
        """Sum a finite price window exactly as decimal price values."""
        return sum(
            (
                Decimal(str(prices[index].total))
                for index in range(start_index, start_index + window)
            ),
            Decimal(0),
        )

    @staticmethod
    def _compare_close_price_windows(
        prices: list[Price],
        window: int,
        candidate_index: int,
        lowest_index: int,
        candidate_exact: Decimal | None,
        lowest_exact: Decimal | None,
    ) -> tuple[bool, Decimal, Decimal]:
        """Resolve close float totals without moving an exactly tied window."""
        if candidate_exact is None:
            candidate_exact = FrankEnergieCoordinator._decimal_price_window_total(
                prices, candidate_index, window
            )
        if lowest_exact is None:
            lowest_exact = FrankEnergieCoordinator._decimal_price_window_total(
                prices, lowest_index, window
            )
        return candidate_exact < lowest_exact, candidate_exact, lowest_exact

    def _find_lowest_consecutive_hours(
        self,
        prices: list[Price],
        window: int,
    ) -> tuple[float, Price, Price] | None:
        """Find the earliest cheapest finite price window in linear time.

        Maintain a sliding total without allocating window slices. Resolve
        nearly equal totals with decimal comparisons to avoid floating-point
        drift. Skip non-finite price windows and resume when they become valid.
        """
        if window <= 0 or len(prices) < window:
            return None

        current_sum = 0.0
        invalid_count = 0
        for index in range(window):
            value = prices[index].total
            if math.isfinite(value):
                current_sum += value
            else:
                invalid_count += 1

        lowest_sum: float | None = current_sum if invalid_count == 0 else None
        lowest_index: int | None = 0 if invalid_count == 0 else None
        exact_sum: Decimal | None = None
        lowest_exact: Decimal | None = None

        for index in range(window, len(prices)):
            current_sum, invalid_count, exact_sum = self._slide_price_window_totals(
                prices[index].total,
                prices[index - window].total,
                current_sum,
                invalid_count,
                exact_sum,
            )
            if invalid_count:
                continue

            start_index = index - window + 1
            if lowest_sum is None:
                lowest_sum = current_sum
                lowest_index = start_index
                lowest_exact = exact_sum
                continue

            assert lowest_index is not None
            if math.isclose(current_sum, lowest_sum, rel_tol=1e-12, abs_tol=1e-12):
                better, exact_sum, lowest_exact = self._compare_close_price_windows(
                    prices, window, start_index, lowest_index, exact_sum, lowest_exact
                )
            else:
                better = current_sum < lowest_sum

            if better:
                lowest_sum = current_sum
                lowest_index = start_index
                lowest_exact = exact_sum

        if lowest_index is None:
            return None

        lowest_avg = (
            math.fsum(
                prices[position].total
                for position in range(lowest_index, lowest_index + window)
            )
            / window
        )
        return (
            lowest_avg,
            prices[lowest_index],
            prices[lowest_index + window - 1],
        )

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

    def _selected_electricity_connection_id(self) -> str | None:
        """Read the selected connection from authoritative settings when available."""
        settings = getattr(self, "settings_coordinator", self)
        settings_data = settings.data
        user = settings_data.get(DATA_USER) if settings_data else None
        if user is not None:
            return _extract_electricity_connection_id(user)
        return self._connection_id

    async def _refresh_resolution_state_after_change(self, connection_id: str) -> None:
        """Update the published status after a successful resolution request."""
        state = await self._fetch_contract_price_resolution_state(connection_id)
        if (
            state is not None
            and self._selected_electricity_connection_id() == connection_id
        ):
            self._static_contract_price_resolution_state = state
            self.data[DATA_CONTRACT_PRICE_RESOLUTION_STATE] = state

    def _can_change_resolution(self) -> bool:
        """Check the selected connection and known change restrictions."""
        if not self._connection_id or self._price_contracts() == {"GAS"}:
            _LOGGER.error(
                "Cannot set resolution via API: electricity connection_id not available"
            )
            return False
        if self._resolution_change_pending:
            _LOGGER.warning("Cannot set resolution: a change is already pending")
            return False
        if (
            self._api_resolution_state is not None
            and not self._api_resolution_state.isChangeRequestPossible
        ):
            _LOGGER.warning(
                "Cannot set resolution via API: isChangeRequestPossible=False"
            )
            return False
        return True

    async def async_set_resolution(self, value: str) -> None:
        """Update resolution safely via mutation queue."""
        if not self.api.is_authenticated:
            if self.config_entry is not None:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    options={**self.config_entry.options, "resolution": value},
                )
                _LOGGER.debug(
                    "Resolution saved to options (unauthenticated API): %s",
                    value,
                )
                # Invalidate price cache so next refresh fetches with new resolution
                self._static_prices_today = None
                self.last_fetch_today = None
                self.cached_prices_tomorrow = None
                self.last_fetch_tomorrow = None
                await self.async_request_refresh()
            return

        if not self._can_change_resolution():
            return

        self._resolution_change_pending = True
        requested_connection_id = self._connection_id

        async def _mutation() -> None:
            try:
                if (
                    self._selected_electricity_connection_id()
                    != requested_connection_id
                ):
                    raise UpdateFailed(
                        "Electricity connection changed before the resolution request; retry"
                    )
                result = await self.async_api_call(
                    "contract_price_resolution_request_change",
                    self.api.contract_price_resolution_request_change,
                    requested_connection_id,
                    cast(Resolution, value),
                )
                if (
                    self._selected_electricity_connection_id()
                    != requested_connection_id
                ):
                    _LOGGER.warning(
                        "Electricity connection changed while a resolution request was "
                        "in flight; not applying the previous connection's response"
                    )
                    return

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

                # Publish only a status verified for the current connection.
                try:
                    await self._refresh_resolution_state_after_change(
                        requested_connection_id
                    )
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
        """Prefer a valid API resolution, then configuration, then the default.

        Normalize API values to uppercase for query and cache selection.
        Authentication and pending changes do not override an active API value.
        """
        settings_data = getattr(self, "settings_coordinator", self).data
        site = settings_data.get(DATA_DELIVERY_SITE) if settings_data else None
        if self.api.is_authenticated and known_site_contracts(site) == {"GAS"}:
            return "PT60M"
        if self.config_entry is None:
            return DEFAULT_RESOLUTION

        configured = self.config_entry.options.get("resolution", DEFAULT_RESOLUTION)

        if self._api_resolution_state is not None:
            active = self._api_resolution_state.activeOption
            if isinstance(active, str):
                normalized_active = active.upper()
                if normalized_active in SUPPORTED_RESOLUTIONS:
                    return normalized_active
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
                    success = await self.async_api_call(
                        "enode_update_vehicle_charge_settings",
                        self.api.enode_update_vehicle_charge_settings,
                        input_data,
                    )
                else:
                    success = await self.async_api_call(
                        "enode_update_charger_charge_settings",
                        self.api.enode_update_charger_charge_settings,
                        input_data,
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
            result[DATA_USER] = data_user
            result[DATA_USER_SITES] = user_sites
            if user_sites is not None and (site_reference := self.site_reference):
                result[DATA_DELIVERY_SITE] = next(
                    (
                        site
                        for site in user_sites.deliverySites
                        if isinstance(site, DeliverySite)
                        and site.reference == site_reference
                    ),
                    None,
                )
            contracts = known_site_contracts(result[DATA_DELIVERY_SITE]) or set()
            self.user_electricity_enabled = "ELECTRICITY" in contracts
            self.user_gas_enabled = "GAS" in contracts
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

        self._resolution_state_connection_id: str | None = None

    def _clear_resolution_state(self) -> None:
        """Discard status that is not verified for the selected connection."""
        self._resolution_state_connection_id = None
        self._api_resolution_state = None
        self._static_contract_price_resolution_state = None
        self._resolution_change_pending = False
        if self.data is not None:
            self.data[DATA_CONTRACT_PRICE_RESOLUTION_STATE] = None

    def _sync_connection_id(self) -> None:
        """Synchronize from selected-site settings without clearing on outages."""
        settings_data = self.settings_coordinator.data
        user_data = settings_data.get(DATA_USER) if settings_data else None
        if user_data is None:
            return

        connection_id = _extract_electricity_connection_id(user_data)
        if connection_id != self._connection_id:
            previous_connection_id = self._connection_id
            self._connection_id = connection_id
            self._clear_resolution_state()
            if previous_connection_id is not None:
                # Prices from the previous connection may use another resolution.
                self._static_prices_today = None
                self.last_fetch_today = None
                self.cached_prices_tomorrow = None
                self.last_fetch_tomorrow = None

    async def _refresh_resolution_state_after_change(self, connection_id: str) -> None:
        """Refresh published change status only for the selected connection."""
        self._sync_connection_id()
        if self._connection_id == connection_id:
            await self._refresh_current_resolution_state()

    async def _refresh_current_resolution_state(
        self,
    ) -> ContractPriceResolutionState | None:
        """Fetch current connection status and avoid reusing another site's state."""
        connection_id = self._connection_id
        if connection_id is None:
            self._clear_resolution_state()
            return None

        state = await self._fetch_contract_price_resolution_state(connection_id)
        # Account settings may change while the API request is in flight.
        self._sync_connection_id()
        if connection_id != self._connection_id:
            self._clear_resolution_state()
            return None
        if state is None:
            # A failed fetch says nothing about the connection: keep a state
            # already verified for it, so the effective resolution can't flip.
            if self._resolution_state_connection_id != connection_id:
                self._clear_resolution_state()
            return None

        self._resolution_state_connection_id = connection_id
        self._static_contract_price_resolution_state = state
        if self.data is not None:
            self.data[DATA_CONTRACT_PRICE_RESOLUTION_STATE] = state
        return state

    async def async_set_resolution(self, value: str) -> None:
        """Validate change eligibility against this connection's current state."""
        self._sync_connection_id()
        if (
            self.api.is_authenticated
            and self._connection_id is not None
            and self._price_contracts() != {"GAS"}
            and not self._resolution_change_pending
            and self._resolution_state_connection_id != self._connection_id
            and await self._refresh_current_resolution_state() is None
        ):
            raise UpdateFailed(
                "Cannot verify resolution change eligibility for the "
                "selected electricity connection; retry after the next refresh"
            )
        await super().async_set_resolution(value)

    def _parse_cached_data(self, cached_data: dict[str, Any]) -> None:
        """Parse cached data dictionary into coordinator state."""
        if prices_today_dict := cached_data.get("prices_today"):
            self._static_prices_today = _dict_to_market_prices(prices_today_dict)

        if prices_tomorrow_dict := cached_data.get("prices_tomorrow"):
            self.cached_prices_tomorrow = _dict_to_market_prices(prices_tomorrow_dict)

        if resolution_state_dict := cached_data.get("contract_price_resolution_state"):
            # A disk cache does not prove the connection still has these rights.
            self._resolution_state_connection_id = None
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
        """Check cached price intervals against the effective query resolution."""
        if not self._static_prices_today or not self.config_entry:
            return True

        price_series = self._static_prices_today.electricity
        if (
            self._price_contracts() == {"GAS"}
            or price_series is None
            or not price_series.all
        ):
            return True

        cached_res = price_series.resolution_minutes
        expected_res = 15 if self.resolution == "PT15M" else 60

        if cached_res != expected_res:
            _LOGGER.debug(
                "Cache resolution (%s) differs from effective resolution (%s), invalidating cache",
                cached_res,
                expected_res,
            )
            return False

        return True

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

        if not self._prices_cover_date(
            self._static_prices_today,
            today,
            required_contracts=self._price_contracts(),
        ):
            _LOGGER.debug(
                "Cached today prices claim to be fetched today but hold no "
                "entries for %s — cache is not fresh",
                today,
            )
            return False

        if (
            self.last_fetch_tomorrow
            and _amsterdam_date(self.last_fetch_tomorrow) == today
        ):
            tomorrow = today + timedelta(days=1)
            if self._tomorrow_prices_complete(self.cached_prices_tomorrow, tomorrow):
                _LOGGER.debug(
                    "Cached tomorrow prices (fetched today) validated as "
                    "genuinely dated for %s — cache is fresh",
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
        """Restore valid prices, then verify the selected connection's status."""
        self._sync_connection_id()
        cached_data_loaded = await self._async_setup()
        now_utc = dt_util.utcnow()

        if cached_data_loaded:
            cache_is_fresh = self._is_cache_fresh(now_utc)
            if (
                cache_is_fresh
                and self.api.is_authenticated
                and self._connection_id is not None
                and self._price_contracts() != {"GAS"}
                and not self._should_skip_api_calls(now_utc)
            ):
                await self._refresh_current_resolution_state()
            else:
                self._clear_resolution_state()

            if not self._is_cache_resolution_valid():
                self._static_prices_today = None
                self.last_fetch_today = None
                self.cached_prices_tomorrow = None
                self.last_fetch_tomorrow = None
                cached_data_loaded = False

        if not cached_data_loaded or not self._is_cache_fresh(now_utc):
            _LOGGER.debug("No fresh cache found, performing initial API fetch")
            await super().async_config_entry_first_refresh()
        else:
            _LOGGER.debug("Fresh cache loaded, skipping initial API fetch")

    def _adjust_update_interval(self, now_utc: datetime) -> None:
        """Adjust the coordinator update interval based on cache and publication state."""
        now_local = now_utc.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM))
        today = now_local.date()
        tomorrow = today + timedelta(days=1)

        tomorrow_cache_valid = (
            self.cached_prices_tomorrow is not None
            and self.last_fetch_tomorrow is not None
            and _amsterdam_date(self.last_fetch_tomorrow) == today
            and self._tomorrow_prices_complete(
                self.cached_prices_tomorrow,
                tomorrow,
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

    def _carry_forward_previous_day(
        self, new_prices: MarketPrices | None, today: date
    ) -> MarketPrices | None:
        """Preserve omitted commodities and history across local midnight.

        An empty series in a partial response must not erase usable cached
        prices for that commodity, including when resolutions differ.

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

        # Keep yesterday + today only; anything older would just accumulate.
        cutoff = today - timedelta(days=2)

        def preserve_series(
            cached: PriceData | None, fetched: PriceData | None
        ) -> PriceData | None:
            """Preserve an omitted commodity independently of other series."""
            if fetched is None or not fetched.all:
                return cached if cached is not None and cached.all else fetched
            if (
                cached is None
                or not cached.all
                or cached.all[-1].date_from.astimezone(tz_amsterdam).date() >= today
            ):
                return fetched
            try:
                merged = self._merge_prices(cached, fetched)
            except ValueError:
                _LOGGER.debug(
                    "Cannot carry yesterday's prices over local midnight "
                    "(resolution or energy-type mismatch); using freshly "
                    "fetched prices for this commodity"
                )
                return fetched
            return self._price_data_after(merged, cutoff) or merged

        merged_electricity = preserve_series(
            previous.electricity, new_prices.electricity
        )
        merged_gas = preserve_series(previous.gas, new_prices.gas)
        if (
            merged_electricity is new_prices.electricity
            and merged_gas is new_prices.gas
        ):
            return new_prices
        return MarketPrices(
            electricity=merged_electricity,
            gas=merged_gas,
            energy_country=new_prices.energy_country,
            energy_type=new_prices.energy_type,
        )

    async def _use_cached_prices_on_fetch_failure(
        self, error: Exception, now_utc: datetime
    ) -> MarketPrices:
        """Preserve a live price cache on fetch failures without hiding throttles."""
        if not isinstance(error, RateLimitException):
            await self._handle_fetch_exceptions(error)
        cached = self._static_prices_today
        if cached is not None and self._cached_prices_active(cached, now_utc):
            _LOGGER.warning(
                "Failed to fetch today prices, falling back to cached data: %s",
                error,
            )
            return cached
        if isinstance(error, RateLimitException):
            raise rate_limit_update_failed(error, self.update_interval) from error
        raise UpdateFailed(f"Failed to fetch today prices: {error}") from error

    def _invalidate_cache_on_resolution_change(self) -> None:
        """Drop cached price data only when its effective resolution changed."""
        if self._is_cache_resolution_valid():
            return
        _LOGGER.debug("Invalidating cached prices after an effective resolution change")
        self._static_prices_today = None
        self.last_fetch_today = None
        self.cached_prices_tomorrow = None
        self.last_fetch_tomorrow = None

    async def _get_today_prices(
        self,
        today: date,
        tomorrow: date,
        now_utc: datetime,
        required_contracts: set[str],
    ) -> MarketPrices | None:
        """Fetch today's prices if necessary, preserving live cache on failures."""
        cached = self._static_prices_today
        if (
            cached is not None
            and self.last_fetch_today is not None
            and _amsterdam_date(self.last_fetch_today) == today
            and self._prices_cover_date(
                cached, today, required_contracts=required_contracts
            )
        ):
            return cached

        try:
            fetched = await self._fetch_prices_with_fallback(
                today, tomorrow, use_fallback=True
            )
            prices_today = self._carry_forward_previous_day(fetched, today)
            self._update_today_prices(prices_today)
            if not self._price_fetch_used_cache and self._prices_cover_date(
                fetched, today, required_contracts=required_contracts
            ):
                self.last_fetch_today = now_utc
                self._today_prices_logged = True
            else:
                self.last_fetch_today = None
                self._today_prices_logged = False
                _LOGGER.warning(
                    "Today's prices for %s are incomplete or came from cache; "
                    "retrying on next refresh",
                    today,
                )
            return prices_today
        except Exception as err:
            return await self._use_cached_prices_on_fetch_failure(err, now_utc)

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch price data."""
        now_utc = datetime.now(ZoneInfo("UTC"))
        today = now_utc.astimezone(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
        tomorrow = today + timedelta(days=1)

        skip_api_calls = self._should_skip_api_calls(now_utc)
        required_contracts = self._price_contracts()
        self._sync_connection_id()
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
            # Verify the account's interval before reusing prices from the cache.
            # On transient errors, retain state verified for this connection.
            fetched_resolution_state = await self._refresh_current_resolution_state()
            data_contract_price_resolution_state = (
                fetched_resolution_state
                if fetched_resolution_state is not None
                else self._static_contract_price_resolution_state
            )
            self._invalidate_cache_on_resolution_change()

            prices_today = await self._get_today_prices(
                today, tomorrow, now_utc, required_contracts
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
        # Last known ``smartBatterySettings`` per battery, when each was fetched,
        # when a failed or empty request may be retried, and how often each was
        # invalidated, so a request that overlaps a change is not marked fresh.
        self._battery_settings: dict[str, object] = {}
        self._battery_settings_fetched_at: dict[str, datetime] = {}
        self._battery_settings_retry_after: dict[str, datetime] = {}
        self._battery_settings_generation: dict[str, int] = {}
        # Per-battery request numbers: the last one issued and the last one
        # recorded, so an older response can never overwrite a newer one.
        self._battery_settings_requested: dict[str, int] = {}
        self._battery_settings_recorded: dict[str, int] = {}

    async def async_update_battery_settings(
        self, battery_id: str, settings: dict[str, object]
    ) -> bool:
        """Send a smart battery setting change through the mutation queue.

        Changes are serialized, so overlapping changes are applied in the order
        they were requested and the last requested value wins. A successful
        change invalidates the battery's settings before the next queued change
        starts, then requests a refresh to show the pending change.
        """
        outcome: list[bool] = []

        async def _update() -> None:
            applied = bool(
                await self.api.smart_battery_update_settings(battery_id, settings)
            )
            if applied:
                self.invalidate_battery_settings(battery_id)
            outcome.append(applied)

        await self._mutation_queue.add(_update)
        success = bool(outcome and outcome[0])
        if success:
            await self.async_request_refresh()
        return success

    def invalidate_battery_settings(self, battery_id: str) -> None:
        """Refetch a battery's settings on the next refresh, e.g. after a change."""
        self._battery_settings_fetched_at.pop(battery_id, None)
        self._battery_settings_retry_after.pop(battery_id, None)
        self._battery_settings_generation[battery_id] = (
            self._battery_settings_generation.get(battery_id, 0) + 1
        )

    def _battery_settings_are_fresh(self, battery_id: str, now: datetime) -> bool:
        """Skip requests while settings are fresh or a retry cooldown is active."""
        retry_after = self._battery_settings_retry_after.get(battery_id)
        if retry_after is not None:
            return now < retry_after

        fetched_at = self._battery_settings_fetched_at.get(battery_id)
        return (
            battery_id in self._battery_settings
            and fetched_at is not None
            and now - fetched_at < BATTERY_SETTINGS_REFRESH_INTERVAL
        )

    async def _fetch_battery_settings(
        self, battery: SmartBattery, now: datetime
    ) -> tuple[bool, object | None, timedelta | None]:
        """Fetch the adjustable settings and allowed options of one smart battery.

        Returns ``(fetched, settings, retry_delay)``. ``fetched`` is False when
        the settings are still fresh (see ``BATTERY_SETTINGS_REFRESH_INTERVAL``
        and ``invalidate_battery_settings``), when the request failed, and on
        library releases without ``smart_battery_settings``, where the battery
        entities keep their static option lists. ``retry_delay`` is set when
        the request did not produce settings. The library returns None both
        for a battery without settings and for GraphQL errors or malformed
        responses, so None keeps the known settings and is retried at the
        normal refresh interval. Only authentication failures propagate: the
        settings only refine which options the entities offer, so they must
        never fail the battery update.
        """
        if not hasattr(self.api, "smart_battery_settings"):
            return False, None, None
        if self._battery_settings_are_fresh(battery.id, now):
            return False, None, None

        try:
            settings = await self.api.smart_battery_settings(battery.id)
        except (AuthException, AuthRequiredException):
            raise
        except Exception as err:
            # Same authentication shape that ``_handle_fetch_exceptions`` handles.
            if isinstance(err, RequestException) and str(err).startswith("user-error:"):
                raise
            # Deliberately not ``_handle_dynamic_fetch_error``: it re-raises
            # network errors on the first refresh, and settings must never fail
            # the update.
            retry_delay = BATTERY_SETTINGS_FAILURE_RETRY_INTERVAL
            retry_after = getattr(err, "retry_after", None)
            if (
                isinstance(err, RequestException)
                and isinstance(retry_after, int | float)
                and not isinstance(retry_after, bool)
                and math.isfinite(retry_after)
                and retry_after > 0
            ):
                # New clients have a rate-limit error; older pinned clients do
                # not export that class. Respect a bounded Retry-After hint.
                retry_delay = max(
                    retry_delay, timedelta(seconds=min(retry_after, 86400))
                )

            # Upstream error messages can contain private account data.
            _LOGGER.debug(
                "Failed to fetch settings for battery %s: %s",
                battery.id,
                type(err).__name__,
            )
            return False, None, retry_delay

        if settings is None:
            return False, None, BATTERY_SETTINGS_REFRESH_INTERVAL
        return True, settings, None

    async def _fetch_battery_data(
        self, battery: SmartBattery, start_date: date, tomorrow: date, now: datetime
    ) -> tuple[
        SmartBatteryDetails | None,
        SmartBatterySessions | None,
        tuple[bool, object | None, timedelta | None],
    ]:
        """Fetch details, sessions and settings for one smart battery concurrently."""
        _LOGGER.debug("Fetching details and sessions for battery: %s", battery.id)
        return await asyncio.gather(
            self._fetch_battery_details(battery),
            self._fetch_battery_sessions(battery, start_date, tomorrow),
            self._fetch_battery_settings(battery, now),
        )

    def _record_battery_settings(
        self,
        battery_id: str,
        fetched: bool,
        settings: object | None,
        request: tuple[int, int],
        now: datetime,
        retry_delay: timedelta | None,
    ) -> object | None:
        """Keep valid settings and guard both success and retry timestamps.

        Generation and request ordering prevent an older response from changing
        the cache or its cooldown after an overlapping mutation or newer fetch.
        A request without settings keeps the last known settings and schedules
        a retry after ``retry_delay``.
        """
        generation, request_number = request
        current = self._battery_settings_generation.get(battery_id, 0) == generation
        newest = request_number > self._battery_settings_recorded.get(battery_id, 0)
        if current and newest:
            if retry_delay is not None:
                self._battery_settings_retry_after[battery_id] = now + retry_delay
                self._battery_settings_recorded[battery_id] = request_number
            elif fetched:
                self._battery_settings[battery_id] = settings
                self._battery_settings_recorded[battery_id] = request_number
                self._battery_settings_fetched_at[battery_id] = now
                self._battery_settings_retry_after.pop(battery_id, None)

        return self._battery_settings.get(battery_id)

    def _start_battery_settings_request(self, battery_id: str) -> tuple[int, int]:
        """Return the battery's generation and a new, increasing request number."""
        request_number = self._battery_settings_requested.get(battery_id, 0) + 1
        self._battery_settings_requested[battery_id] = request_number
        return (self._battery_settings_generation.get(battery_id, 0), request_number)

    async def _get_battery_data(
        self,
        data_smart_batteries: SmartBatteries | None,
        start_date: date,
        tomorrow: date,
    ) -> tuple[
        list[SmartBatteryDetails], list[SmartBatterySessions], dict[str, object]
    ]:
        """Fetch details, sessions and settings for all smart batteries concurrently.

        Fetched settings and their timestamps are only recorded once every
        battery's requests completed, and a result is only marked fresh when the
        battery was not invalidated while it was in flight. Batteries whose
        settings were not refetched keep their last known settings, including
        batteries that were briefly missing from the battery list.
        """
        details_list: list[SmartBatteryDetails] = []
        sessions_list: list[SmartBatterySessions] = []
        settings_by_battery: dict[str, object] = {}

        batteries = [
            battery
            for battery in (
                data_smart_batteries.batteries if data_smart_batteries else []
            )
            if battery and battery.id
        ]
        if not batteries:
            _LOGGER.debug("No smart batteries found")
            return details_list, sessions_list, settings_by_battery

        now = datetime.now(UTC)
        requests = {
            battery.id: self._start_battery_settings_request(battery.id)
            for battery in batteries
        }
        results = await asyncio.gather(
            *(
                self._fetch_battery_data(battery, start_date, tomorrow, now)
                for battery in batteries
            )
        )
        for battery, (details, sessions, (fetched, settings, retry_delay)) in zip(
            batteries, results
        ):
            if details:
                details_list.append(details)
            if sessions:
                sessions_list.append(sessions)
            known = self._record_battery_settings(
                battery.id, fetched, settings, requests[battery.id], now, retry_delay
            )
            if known is not None:
                settings_by_battery[battery.id] = known

        return details_list, sessions_list, settings_by_battery

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch battery data."""
        self.verified_inventory_ids = None
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
                data_smart_battery_settings,
            ) = await self._get_battery_data(data_smart_batteries, start_date, tomorrow)

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
            result[DATA_BATTERY_SETTINGS] = data_smart_battery_settings
            self._process_device_inventory("battery", data_smart_batteries)
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
        self.verified_inventory_ids = None
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
            self._process_device_inventory("charger", data_enode_chargers)
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
        """Fetch PV data and validate freshly retrieved physical system IDs."""
        self.verified_inventory_ids = None
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
            data_pv_systems, feed_in_result = await asyncio.gather(
                self._fetch_smart_pv_systems(),
                self._fetch_user_smart_feed_in(),
            )
            data_user_smart_feed_in, feed_in_fetched = feed_in_result

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
            self._process_device_inventory("pv", data_pv_systems)
            result[DATA_PV_SUMMARY] = data_pv_summary
            result[DATA_USER_SMART_FEED_IN] = data_user_smart_feed_in
            result[DATA_USER_SMART_FEED_IN_FETCHED] = feed_in_fetched
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
        self.verified_inventory_ids = None
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
            self._process_device_inventory("vehicle", enode_vehicles)
            return result
        except Exception as err:
            await self._handle_fetch_exceptions(err)
            raise UpdateFailed(f"Failed to fetch vehicle data: {err}") from err


class FrankEnergieStatisticsCoordinator(FrankEnergieCoordinator):
    """Coordinator for monthly statistics and usage, independent of invoices."""

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
            data_month_summary, data_period_usage = await asyncio.gather(
                self._fetch_month_summary(),
                self._fetch_period_usage(start_date),
            )
            result = _empty_data()
            if self.settings_coordinator.data:
                result[DATA_USER] = self.settings_coordinator.data.get(DATA_USER)
                result[DATA_USER_SITES] = self.settings_coordinator.data.get(
                    DATA_USER_SITES
                )
            result[DATA_MONTH_SUMMARY] = data_month_summary
            result[DATA_USAGE] = data_period_usage
            return result
        except Exception as err:
            await self._handle_fetch_exceptions(err)
            raise UpdateFailed(f"Failed to fetch statistics data: {err}") from err


class FrankEnergieInvoiceCoordinator(FrankEnergieStatisticsCoordinator):
    """Poll invoices independently while using the existing statistics interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: FrankEnergie,
        settings_coordinator: FrankEnergieSettingsCoordinator,
    ) -> None:
        """Initialize invoice polling without adding another polling setting."""
        super().__init__(hass, config_entry, api, settings_coordinator)
        self.name = "Frank Energie invoice coordinator"

    async def _async_update_data(self) -> FrankEnergieData:
        """Keep previous invoices on a failed refresh and report unavailability."""
        if self._should_skip_api_calls(datetime.now(ZoneInfo("UTC"))):
            if self.last_update_success:
                return self.data
            raise UpdateFailed("Maintenance window active")
        try:
            invoices = await self._fetch_invoices()
            result = _empty_data()
            result[DATA_USER] = self.settings_coordinator.data.get(DATA_USER)
            result[DATA_USER_SITES] = self.settings_coordinator.data.get(
                DATA_USER_SITES
            )
            result[DATA_INVOICES] = invoices
            return result
        except Exception as err:
            await self._handle_fetch_exceptions(err)
            raise UpdateFailed("Failed to fetch invoices") from err


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
        self.rate_limits = get_rate_limit_state(hass, config_entry)
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

    def _session_date_window(self) -> tuple[date, date]:
        """Validate access and determine the month-to-date session time window."""
        today = datetime.now(ZoneInfo(TIMEZONE_AMSTERDAM)).date()
        if not self.api.is_authenticated:
            raise UpdateFailed("API client is not authenticated.")
        if not self.device_id:
            raise UpdateFailed("No device ID provided for smart battery sessions.")
        start = min(today.replace(day=1), today - timedelta(days=1))
        return start, today + timedelta(days=1)

    def _cached_sessions_or_raise(
        self, error: UpdateFailed
    ) -> SmartBatterySessions | None:
        """Reuse the last successful sessions only after a failed refresh."""
        if self.last_update_success:
            _LOGGER.warning("%s", error)
            return self.data
        raise error

    async def _async_update_data(self) -> SmartBatterySessions | None:
        """
        Fetch smart battery session data.

        Returns:
            SmartBatterySessions: Session data for the specified smart battery device.

        Raises:
            UpdateFailed: If an error occurs during data fetching.
        """
        try:
            mtd_start, tomorrow = self._session_date_window()
            return await self.rate_limits.async_call(
                "smart_battery_sessions",
                lambda: self.api.smart_battery_sessions(
                    self.device_id, mtd_start, tomorrow
                ),
            )

        except UpdateFailed as ex:
            return self._cached_sessions_or_raise(ex)
        except AuthException as ex:
            _LOGGER.debug(
                "Authentication tokens expired, attempting token renewal: %s", ex
            )
            try:
                await self.rate_limits.async_call("renew_token", self.api.renew_token)
            except RateLimitException as err:
                raise rate_limit_update_failed(err, self.update_interval) from err
            raise UpdateFailed(
                "Authentication failed and token was renewed. Retry update."
            ) from ex

        except RateLimitException as ex:
            raise rate_limit_update_failed(ex, self.update_interval) from ex

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
