"""Coordinator implementation for Frank Energie integration.
Fetching the latest data from Frank Energie and updating the states."""

# coordinator.py
# version 2026.06.15
from __future__ import annotations

import asyncio
import logging

# import secrets
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable, Final, TypedDict, cast

from aiohttp import ClientError
from homeassistant.components.persistent_notification import async_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import (
    AuthException,
    AuthRequiredException,
    FrankEnergieException,
    NetworkError,
    RequestException,
)
from python_frank_energie.models import (
    ContractPriceResolutionState,
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
    SmartBatteryDetails,
    SmartBatterySessions,
    SmartPvSystems,
    SmartPvSystemSummary,
    User,
    UserSites,
    UserSmartFeedInStatus,
)

from .const import (
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
    DATA_PV_SUMMARY,
    DATA_PV_SYSTEMS,
    DATA_USAGE,
    DATA_USER,
    DATA_USER_SITES,
    DATA_USER_SMART_FEED_IN,
    DEFAULT_RESOLUTION,
    EVENT_FRANK_ENERGIE,
)
from .mutation_queue import MutationQueue

_LOGGER = logging.getLogger(__name__)
_LOG_AUTH_TOKENS_EXPIRED: Final = (
    "Authentication tokens expired, trying to renew them (%s)"
)

if sys.platform == "win32" and hasattr(asyncio, "set_event_loop_policy"):
    # Python 3.14-3.16
    try:
        # Proactor is default on Python 3.17+, but explicitly set it for 3.14-3.16 to avoid Selector which is deprecated and can cause issues with aiohttp
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        # fallback for Python 3.17+ where Proactor policy is default and Selector policy is deprecated
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


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
        self.hass = hass
        self.config_entry = config_entry
        self.api = api
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
        # self._update_interval = timedelta(seconds=DEFAULT_REFRESH_INTERVAL)
        self._update_interval = (
            None  # Start with no update interval; will be set after first fetch
        )
        self._last_update_success = False
        self.user_electricity_enabled = False
        self.user_gas_enabled = False
        _LOGGER.debug(
            "Initializing Frank Energie coordinator with country_code: %s",
            self.country_code,
        )

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

    @property
    def old_site_reference(self) -> str | None:
        """Return active site reference."""
        return self.config_entry.options.get("site_reference")

    @property
    def site_reference(self) -> str | None:
        """Return active site reference."""
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

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch and cache data from Frank Energie with smart interval logic."""

        _LOGGER.debug(
            "Starting data update for Frank Energie coordinator (user: %s).",
            self.config_entry.title,
        )

        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        tomorrow = today + timedelta(days=1)

        # skip API calls between 00:00 and 01:00 UTC to avoid maintenance window
        skip_api_calls = self._should_skip_api_calls(now_utc)

        self._reconcile_resolution()
        self._adjust_update_interval(now_utc)

        # Reset daily log flag on new day
        if self.last_fetch_today is None or self.last_fetch_today.date() != today:
            self._today_prices_logged = False
            self._has_pv_systems = None  # re-probe PV ownership once per day

        if self.last_fetch_tomorrow is None or self.last_fetch_tomorrow.date() != today:
            self._tomorrow_prices_logged = False

        # ---------------------------------------------------
        # TODAY DATA
        # ---------------------------------------------------
        if skip_api_calls:
            _LOGGER.debug(
                "Skipping Frank Energie API calls between 00:00 and 01:00 UTC"
            )

            if self.cached_prices_today is None:
                _LOGGER.info(
                    "No cached Frank Energie data available during API maintenance window. Sensors will be unavailable until the first successful data fetch after 01:00 UTC."
                )
        else:
            await self._refresh_today_cache(
                today,
                tomorrow,
                now_utc,
            )

        if self.cached_prices_today is None:
            if skip_api_calls:
                if self.cached_prices is not None:
                    return self.cached_prices

                if self.data is not None:
                    return self.data

                raise UpdateFailed(
                    "No cached data available during maintenance window."
                )

            raise UpdateFailed(
                "No cached Frank Energie data available after attempting update."
            )

        # ---------------------------------------------------
        # TOMORROW PRICES
        # ---------------------------------------------------
        if skip_api_calls:
            prices_tomorrow = self.cached_prices_tomorrow
        else:
            prices_tomorrow = await self._refresh_tomorrow_cache(
                today,
                tomorrow,
                now_utc,
            )

        assert self.cached_prices_today is not None

        # ---------------------------------------------------
        # AGGREGATION
        # ---------------------------------------------------
        _LOGGER.debug(
            "Today electricity periods: %s",
            len(self.cached_prices_today.prices_today.electricity.all)
            if self.cached_prices_today
            and self.cached_prices_today.prices_today
            and self.cached_prices_today.prices_today.electricity
            else 0,
        )

        _LOGGER.debug(
            "Tomorrow electricity periods: %s",
            len(prices_tomorrow.electricity.all)
            if prices_tomorrow and prices_tomorrow.electricity
            else 0,
        )
        result = self._aggregate_data(self.cached_prices_today, prices_tomorrow)
        self.cached_prices = result

        # ---------------------------------------------------
        # EVENTS
        # ---------------------------------------------------
        self._maybe_fire_lowest_price_event(
            self.cached_prices_today.prices_today, today, now_utc
        )
        self._maybe_fire_lowest_4p_event(
            self.cached_prices_today.prices_today, today, now_utc
        )
        self._maybe_fire_lowest_16p_event(
            self.cached_prices_today.prices_today, today, now_utc
        )

        _LOGGER.debug(
            "Returning coordinator data with %s electricity periods",
            len(result[DATA_ELECTRICITY].all) if result[DATA_ELECTRICITY] else 0,
        )
        return result

    async def _refresh_today_cache(
        self, today: date, tomorrow: date, now_utc: datetime
    ) -> None:
        """Fetch and cache today's data if stale or missing."""
        if (
            self.cached_prices_today is not None
            and self.last_fetch_today is not None
            and self.last_fetch_today.date() == today
        ):
            return

        try:
            self.cached_prices_today = await self._fetch_today_data(today, tomorrow)
            self.last_fetch_today = now_utc

            if (
                self.cached_prices_today.prices_today is not None
                and self.cached_prices_today.prices_today.electricity is not None
                and not self._today_prices_logged
            ):
                _LOGGER.info("Frank Energie electricity prices available for %s", today)
                self._today_prices_logged = True
        except AuthRequiredException as err:
            raise ConfigEntryAuthFailed from err
        except AuthException as err:
            await self._try_renew_token()
            raise UpdateFailed("Authentication temporarily failed") from err
        except FrankEnergieException as err:
            # FrankEnergieException can wrap AuthException ("Not authorized")
            # Route auth errors to _try_renew_token instead of treating as network error
            err_msg = str(err).casefold()
            if "not authorized" in err_msg or "unauthorized" in err_msg:
                _LOGGER.warning(
                    "Auth error wrapped as FrankEnergieException: %s. Attempting token renewal.",
                    err,
                )
                await self._try_renew_token()
                raise UpdateFailed(
                    "Authentication temporarily failed, token renewal attempted"
                ) from err
            _LOGGER.warning(
                "Temporary network error while fetching Frank Energie data: %s",
                err,
            )
            raise UpdateFailed from err

        except (RequestException, ClientError) as err:
            _LOGGER.warning(
                "Temporary network error while fetching Frank Energie data: %s",
                err,
            )
            raise UpdateFailed from err

    async def _refresh_tomorrow_cache(
        self,
        today: date,
        tomorrow: date,
        now_utc: datetime,
    ) -> MarketPrices | None:
        """Fetch and cache tomorrow's prices if the release window has passed."""
        if now_utc.hour < self.FETCH_TOMORROW_HOUR_UTC:
            _LOGGER.debug(
                "Not fetching tomorrow's prices yet (%02d:00 UTC, threshold: %02d:00 UTC).",
                now_utc.hour,
                self.FETCH_TOMORROW_HOUR_UTC,
            )
            return None

        # Invalidate stale cache from the previous day before re-fetching.
        # If we skip this and the API returns empty (prices not yet published),
        # cached_prices_tomorrow still holds yesterday's D+1 data, which
        # _aggregate_data would concatenate with today's prices — doubling
        # price periods on sensors between 11:00 and ~14:00 UTC every morning.
        if (
            self.cached_prices_tomorrow is not None
            and self.last_fetch_tomorrow is not None
            and self.last_fetch_tomorrow.date() != today
        ):
            _LOGGER.debug(
                "Invalidating stale tomorrow-price cache (was fetched on %s)",
                self.last_fetch_tomorrow.date(),
            )
            self.cached_prices_tomorrow = None
            self.last_fetch_tomorrow = None

        if (
            self.cached_prices_tomorrow is not None
            and self.last_fetch_tomorrow is not None
            and self.last_fetch_tomorrow.date() == today
        ):
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

        if has_electricity or has_gas:
            _LOGGER.info(
                "Retrieved Frank Energie tomorrow prices for %s",
                tomorrow,
            )

            self.cached_prices_tomorrow = prices_tomorrow
            self.last_fetch_tomorrow = now_utc

            if self.config_entry is not None:
                self.hass.bus.async_fire(
                    EVENT_FRANK_ENERGIE,
                    {
                        "entry_id": self.config_entry.entry_id,
                        "entry_title": self.config_entry.title,
                        "action": "tomorrow_prices_available",
                        "date": tomorrow.isoformat(),
                        "resolution": self.resolution,
                    },
                )
        else:
            _LOGGER.debug("Tomorrow prices not yet available, retrying on next refresh")

        if self.cached_prices_today is not None:
            _LOGGER.debug(
                "[%s][%s] Tomorrow electricity periods: %s",
                self.config_entry.title,
                self.site_reference,
                len(prices_tomorrow.electricity.all)
                if prices_tomorrow and prices_tomorrow.electricity
                else 0,
            )
            _LOGGER.debug(
                "Listeners: %s",
                len(self._listeners),
            )
            _LOGGER.debug(
                "Coordinator electricity periods: %s",
                len(self.data[DATA_ELECTRICITY].all)
                if self.data[DATA_ELECTRICITY]
                else 0,
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

        self.hass.bus.async_fire(
            EVENT_FRANK_ENERGIE,
            {
                "entry_id": self.config_entry.entry_id,
                "entry_title": self.config_entry.title,
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

        self.hass.bus.async_fire(
            EVENT_FRANK_ENERGIE,
            {
                "entry_id": self.config_entry.entry_id,
                "entry_title": self.config_entry.title,
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

        self.hass.bus.async_fire(
            EVENT_FRANK_ENERGIE,
            {
                "entry_id": self.config_entry.entry_id,
                "entry_title": self.config_entry.title,
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
            return None
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch user data.")
            return None
        try:
            user_data = await self.api.user(self.site_reference)
            if not self._country_code:
                country_code_raw = user_data.countryCode
                if isinstance(country_code_raw, str) and country_code_raw:
                    country_code = country_code_raw.upper()
                    if country_code in {"NL", "BE"}:
                        self._country_code = country_code
            if (
                not self._connection_id
                and user_data
                and user_data.connections
                and getattr(user_data.connections[0], "connectionId", None)
            ):
                self._connection_id = getattr(
                    user_data.connections[0], "connectionId", None
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
        try:
            _LOGGER.debug(
                "Fetching contract price resolution state for connection ID: %s",
                connection_id,
            )
            if self.api.is_authenticated and connection_id:
                resolution_state = await self.api.contract_price_resolution_state(
                    connection_id
                )

                self._api_resolution_state = resolution_state
                self._resolution_change_pending = (
                    False  # change has been processed by API, reset pending flag
                )

                # resolution_state is already a ContractPriceResolutionState dataclass
                if (
                    self.config_entry.options.get("resolution")
                    != resolution_state.activeOption
                ):
                    options = dict(self.config_entry.options)
                    options["resolution"] = resolution_state.activeOption
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, options=options
                    )

                _LOGGER.debug(
                    "Good ContractPriceResolutionState: %s",
                    resolution_state,
                )
                return resolution_state
            return None

        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.exception("Error fetching ContractPriceResolutionState: %s", err)
            return None

    async def _fetch_enode_chargers(
        self, start_date: date, is_smart_charging: bool
    ) -> dict[str, EnodeChargers] | None:
        """Fetch Enode chargers from the API.

        Chargers are fetched for all authenticated users regardless of whether
        smart charging is activated.  A user may have a physical charger
        registered without having the smart-charging feature enabled — the
        API will simply return the charger with `canSmartCharge=False` or
        `isSmartChargingEnabled=False` in chargeSettings.

        The ``is_smart_charging`` parameter is retained in the signature to
        avoid changing the call site; it is intentionally not used as a gate.
        """
        if not self.api.is_authenticated:
            return None
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch Enode chargers.")
            return None
        try:
            return await self.api.enode_chargers(self.site_reference, start_date)
        except (AuthException, AuthRequiredException):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Failed to fetch enode chargers: %s", err)
            return None

    async def _fetch_smart_batteries(
        self, is_smart_trading: bool
    ) -> SmartBatteries | None:
        """Fetch smart batteries from the API."""
        if not self.api.is_authenticated:
            return None
        try:
            return await self.api.smart_batteries()
        except (AuthException, AuthRequiredException):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Failed to fetch smart batteries: %s", err)
            return None

    async def _fetch_enode_vehicles(
        self, is_smart_charging: bool
    ) -> EnodeVehicles | None:
        """Fetch Enode vehicles from the API.

        Previously ``is_smart_charging`` was required but now chargers and vehicles
        are always fetched when authenticated to ensure sensors are visible.
        """
        if not self.api.is_authenticated:
            return None
        if not self.site_reference:
            _LOGGER.warning("Site reference is missing, cannot fetch Enode vehicles.")
            return None
        try:
            vehicles = await self.api.enode_vehicles()
            _LOGGER.debug("Fetched Enode vehicles: %s", vehicles)
            return vehicles
        except (AuthException, AuthRequiredException):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Failed to fetch enode vehicles: %s", err)
            return None

    async def old_fetch_smart_pv_systems(self) -> SmartPvSystems | None:
        """Fetch Smart PV systems from the API."""
        if not self.api.is_authenticated:
            return None
        try:
            return await self.api.smart_pv_systems()
        except (AuthException, AuthRequiredException):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch smart PV systems: %s", err)
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
            return None
        if self._has_pv_systems is False:
            return None
        try:
            result = await self.api.smart_pv_systems()
            self._has_pv_systems = bool(result and result.systems)
            return result
        except (AuthException, AuthRequiredException):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch smart PV systems: %s", err)
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
        except (AuthException, AuthRequiredException):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Failed to fetch smart PV system summary for %s: %s", device_id, err
            )
            return None

    async def _fetch_user_smart_feed_in(self) -> UserSmartFeedInStatus | None:
        """Fetch user smart feed-in status from the API."""
        if not self.api.is_authenticated:
            return None
        try:
            return await self.api.user_smart_feed_in()
        except (AuthException, AuthRequiredException):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to fetch user smart feed-in status: %s", err)
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
        except (AuthException, AuthRequiredException):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.exception(
                "Failed to fetch details for battery %s: %s",
                battery.id,
                err,
            )
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
            sessions = await self.api.smart_battery_sessions(
                battery.id, start_date, tomorrow
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
        except (AuthException, AuthRequiredException):
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "Failed to fetch sessions for battery %s",
                battery.id,
            )
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

    async def _get_static_data(
        self, today: date, tomorrow: date, start_date: date
    ) -> tuple[
        MarketPrices | None,
        UserSites | None,
        MonthSummary | None,
        Invoices | None,
        PeriodUsageAndCosts | None,
        User | None,
        ContractPriceResolutionState | None,
    ]:
        """Fetch daily static data concurrently or return from cache."""
        if (
            self._static_prices_today is None
            or self.last_fetch_today is None
            or self.last_fetch_today.date() != today
        ):
            _LOGGER.debug("Fetching Frank Energie static daily data concurrently")
            (
                prices_today,
                user_sites,
                data_month_summary,
                data_invoices,
                data_period_usage,
                data_user,
            ) = await asyncio.gather(
                self._fetch_prices_with_fallback(today, tomorrow, use_fallback=True),
                self._fetch_user_sites(),
                self._fetch_month_summary(),
                self._fetch_invoices(),
                self._fetch_period_usage(start_date),
                self._fetch_user_data(),
            )

            # --- Haal contractPriceResolutionState op ---
            data_contract_price_resolution_state = (
                await self._fetch_contract_price_resolution_state(self._connection_id)
            )

            self._static_prices_today = prices_today
            self._static_month_summary = data_month_summary
            self._static_invoices = data_invoices
            self._static_user = data_user
            self._static_user_sites = user_sites
            self._static_period_usage = data_period_usage
            self._static_contract_price_resolution_state = (
                data_contract_price_resolution_state
            )
        else:
            prices_today = self._static_prices_today
            data_month_summary = self._static_month_summary
            data_invoices = self._static_invoices
            user_sites = self._static_user_sites
            data_period_usage = self._static_period_usage
            data_contract_price_resolution_state = (
                self._static_contract_price_resolution_state
            )
            data_user = self._static_user

        return (
            prices_today,
            user_sites,
            data_month_summary,
            data_invoices,
            data_period_usage,
            data_user,
            data_contract_price_resolution_state,
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

    async def _fetch_today_data(self, today: date, tomorrow: date) -> PricesTodayCache:
        """
        Fetches all relevant Frank Energie data for the current day, including prices, user sites, monthly summaries, invoices, usage, user info, Enode chargers, smart batteries, battery details, battery sessions, and smart PV systems.

        Attempts to retrieve user-specific and public data as available, handling authentication failures and falling back to cached data if necessary. Also manages token renewal on authentication errors.

        Parameters:
            today (date): The current date for which data is being fetched.
            tomorrow (date): The next day, used for price and session range queries.

        Returns:
            Tuple containing today's data components including Smart PV systems and summaries.
        """
        yesterday = today - timedelta(days=1)
        start_date = yesterday

        try:
            _LOGGER.debug(
                "Fetching Frank Energie data for today %s", self.config_entry.entry_id
            )

            (
                prices_today,
                user_sites,
                data_month_summary,
                data_invoices,
                data_period_usage,
                data_user,
                data_contract_price_resolution_state,
            ) = await self._get_static_data(today, tomorrow, start_date)

            # Initialize feature flags
            is_smart_charging = False
            is_smart_trading = False
            if data_user:
                # Check if smart charging and trading are activated
                is_smart_charging = self._is_smart_charging_enabled(data_user)
                is_smart_trading = self._is_smart_trading_enabled(data_user)

            _LOGGER.debug("Fetching dynamic interval data concurrently")
            (
                data_enode_chargers,
                data_smart_batteries,
                data_enode_vehicles,
                data_pv_systems,
                data_user_smart_feed_in,
            ) = await asyncio.gather(
                self._fetch_enode_chargers(start_date, is_smart_charging),
                self._fetch_smart_batteries(is_smart_trading),
                self._fetch_enode_vehicles(is_smart_charging),
                self._fetch_smart_pv_systems(),
                self._fetch_user_smart_feed_in(),
            )

            (
                data_smart_battery_details,
                data_smart_battery_sessions,
            ) = await self._get_battery_details_and_sessions(
                data_smart_batteries, start_date, tomorrow
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

            # Detect and log IN_DELIVERY status for clean user experience
            if self.api.is_authenticated:
                is_not_in_delivery = self._is_not_in_delivery_site(
                    data_month_summary, data_invoices, user_sites
                )
                self._log_not_in_delivery_status(is_not_in_delivery)

            return PricesTodayCache(
                prices_today=prices_today,
                data_month_summary=data_month_summary,
                data_invoices=data_invoices,
                data_user=data_user,
                user_sites=user_sites,
                data_period_usage=data_period_usage,
                data_enode_chargers=data_enode_chargers,
                data_smart_batteries=data_smart_batteries,
                data_smart_battery_details=data_smart_battery_details,
                data_smart_battery_sessions=data_smart_battery_sessions,
                data_enode_vehicles=data_enode_vehicles,
                data_pv_systems=data_pv_systems,
                data_pv_summary=data_pv_summary,
                data_user_smart_feed_in=data_user_smart_feed_in,
                data_contract_price_resolution_state=data_contract_price_resolution_state,
            )

        except UpdateFailed as err:
            if self.cached_prices_today:
                _LOGGER.warning(
                    "Update failed, but prices are cached: %s",
                    err,
                )
                return self.cached_prices_today

            raise

        except RequestException as ex:
            if str(ex).startswith("user-error:"):
                raise ConfigEntryAuthFailed from ex
            raise UpdateFailed(ex) from ex

        except AuthRequiredException as err:
            _LOGGER.warning("Authentication failed: %s", err)
            await self._try_renew_token()
            raise ConfigEntryAuthFailed("Authentication is required.") from err

        except AuthException as ex:
            _LOGGER.debug(_LOG_AUTH_TOKENS_EXPIRED, ex)
            await self._try_renew_token()
            raise UpdateFailed(ex) from ex

    async def _fetch_tomorrow_data(self, tomorrow: date):
        """Fetch tomorrow's data after 13:00 UTC."""
        try:
            _LOGGER.debug("Fetching Frank Energie data for tomorrow")
            return await self._fetch_prices_with_fallback(
                tomorrow, tomorrow + timedelta(days=1), use_fallback=False
            )
        except UpdateFailed as err:
            _LOGGER.debug("Error fetching Frank Energie data for tomorrow (%s)", err)
            return None
        except AuthException as ex:
            _LOGGER.debug(_LOG_AUTH_TOKENS_EXPIRED, ex)
            await self._try_renew_token()
            raise UpdateFailed(ex) from ex

    def _aggregate_data(
        self,
        cache: PricesTodayCache,
        prices_tomorrow: MarketPrices | None,
    ) -> FrankEnergieData:
        """Aggregate today's cache and tomorrow's prices into FrankEnergieData."""
        result: FrankEnergieData = {  # type: ignore[typeddict-unknown-key]
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
            if (
                result[DATA_ELECTRICITY] is not None
                and prices_tomorrow.electricity is not None
            ):
                result[DATA_ELECTRICITY] += prices_tomorrow.electricity
            if result[DATA_GAS] is not None and prices_tomorrow.gas is not None:
                result[DATA_GAS] += prices_tomorrow.gas

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
            if country_code == "BE":
                return await self.api.be_prices(start_date, end_date)
            return await self.api.prices(start_date, end_date, self.resolution)
        except NetworkError as err:
            _LOGGER.warning("Failed to fetch public prices: %s", err)
            return None

    async def _fetch_user_prices_for_range(
        self, start_date: date, end_date: date, default_country: str
    ) -> MarketPrices | None:
        """Fetch user-specific prices for a given date range."""
        if not self.api.is_authenticated:
            return None

        site_reference = self.site_reference
        if site_reference is None:
            _LOGGER.debug(
                "Skipping user prices fetch because site_reference is not set"
            )
            return None

        user_country = self._user_country or default_country

        _LOGGER.debug(
            "Fetching user prices for site_reference=%s country=%s resolution=%s",
            site_reference,
            user_country,
            self.resolution,
        )

        try:
            return await self.api.user_prices(
                site_reference, user_country, start_date, end_date
            )
        except NetworkError as err:
            _LOGGER.warning("Failed to fetch user prices: %s", err)
            return None

    async def _fetch_prices_with_fallback(
        self, start_date: date, end_date: date, use_fallback: bool = True
    ) -> MarketPrices:
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

        if public_prices is None:
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
            return public_prices

        if user_prices is None:
            if not use_fallback:
                _LOGGER.debug("No user prices for tomorrow, skipping fallback")
                return public_prices
            _LOGGER.warning(
                "Failed to fetch user prices, falling back to public prices"
            )
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

        self._cached_prices = user_prices
        return user_prices

    async def _handle_fetch_exceptions(self, ex):
        if isinstance(ex, UpdateFailed):
            if (
                self.data[DATA_ELECTRICITY] is not None
                and self.data[DATA_ELECTRICITY].get_future_prices()
                and self.data[DATA_GAS] is not None
                and self.data[DATA_GAS].get_future_prices()
            ):
                _LOGGER.warning(str(ex))
                return self.data
            raise ex
        if isinstance(ex, RequestException) and str(ex).startswith("user-error:"):
            raise ConfigEntryAuthFailed from ex
        if isinstance(ex, AuthException):
            _LOGGER.debug(_LOG_AUTH_TOKENS_EXPIRED, ex)
            await self._try_renew_token()
            raise UpdateFailed(ex) from ex

    async def _try_renew_token(self) -> None:
        """Try to renew authentication token."""

        try:
            updated_tokens = await self.api.renew_token()
            data = {
                CONF_ACCESS_TOKEN: updated_tokens.authToken,
                CONF_TOKEN: updated_tokens.refreshToken,
            }
            # Update the config entry with the new tokens
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)

            _LOGGER.debug("Successfully renewed token")

        except AuthException as ex:
            _LOGGER.exception(
                "Failed to renew token: %s. Starting user reauth flow", ex
            )
            # Consider setting the coordinator to an error state or handling the error appropriately
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

    def _adjust_update_interval(self, now_utc: datetime) -> None:
        """Adjust coordinator update interval around price release windows."""
        # default_interval is None outside the release window — polling is
        # event-driven (e.g. HA restart, button press) outside 11:00–13:00 UTC.
        default_interval = None

        new_interval = (
            timedelta(minutes=5)
            if self.PRICE_RELEASE_START_UTC
            <= now_utc.time()
            <= self.PRICE_RELEASE_END_UTC
            else default_interval
        )

        if self.update_interval != new_interval:
            _LOGGER.debug("Update interval (old method) changed to %s", new_interval)
            self.update_interval = new_interval

    def _ensure_utc(self, value: datetime) -> datetime:
        """Ensure datetime is timezone-aware UTC."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
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
                    "Resolution saved to options (not authenticated): %s -> options=%s",
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
            _LOGGER.warning(
                "Cannot set resolution via API: connection_id not available"
            )
            return

        if (
            self._api_resolution_state is not None
            and not self._api_resolution_state.isChangeRequestPossible
        ):
            _LOGGER.warning(
                "Cannot set resolution via API: isChangeRequestPossible=False"
            )
            return

        async def _mutation() -> None:
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
            self._resolution_change_pending = (
                True  # disable select until change is effective
            )

            if self.config_entry is None:
                return

            if self.config_entry.options.get("resolution") != value:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    options={**self.config_entry.options, "resolution": value},
                )

        await self._mutation_queue.add(_mutation)
        await self.async_request_refresh()

    async def old_async_set_resolution(self, value: str) -> None:
        """Update resolution safely via mutation queue."""
        if not self.api.is_authenticated:
            # Not authenticated — save to options only, no API call
            if self.config_entry is not None:
                _LOGGER.warning(  # use warning so it's visible in logs
                    "Resolution saved to options (not authenticated): %s -> options=%s",
                    value,
                    self.config_entry.options,
                )
            if self.config_entry is not None:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    options={**self.config_entry.options, "resolution": value},
                )
                await self.async_request_refresh()
                _LOGGER.debug(
                    "Resolution saved to options (not authenticated): %s", value
                )
            return

        if not self._connection_id:
            _LOGGER.warning(
                "Cannot set resolution via API: connection_id not available"
            )
            return

        if (
            self._api_resolution_state is not None
            and not self._api_resolution_state.isChangeRequestPossible
        ):
            _LOGGER.warning(
                "Cannot set resolution via API: isChangeRequestPossible=False"
            )
            return

        async def _mutation() -> None:
            result = await self.api.contract_price_resolution_request_change(
                self._connection_id,
                cast(Resolution, value),
            )

            if result is None or not result.success:
                raise UpdateFailed(
                    "Resolution change request failed: %s"
                    % (result.reason if result else "no response")
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

        await self._mutation_queue.add(_mutation)
        await self.async_request_refresh()

    @property
    def resolution(self) -> str:
        """Effective price resolution used for API queries."""
        if self.config_entry is None:
            return DEFAULT_RESOLUTION
        return self.config_entry.options.get("resolution", DEFAULT_RESOLUTION)

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
            update_interval=timedelta(minutes=60),
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
            today = date.today()
            tomorrow = today + timedelta(days=1)

            if not self.api.is_authenticated:
                raise UpdateFailed("API client is not authenticated.")

            if not self.device_id:
                raise UpdateFailed("No device ID provided for smart battery sessions.")

            _LOGGER.debug(
                "Fetching smart battery sessions for device %s", self.device_id
            )

            return await self.api.smart_battery_sessions(
                self.device_id, today, tomorrow
            )

        except UpdateFailed as ex:
            if self.data:
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
