"""Coordinator implementation for Frank Energie integration.
Fetching the latest data from Frank Energie and updating the states."""

# coordinator.py
# version 2026.05.10
from __future__ import annotations

import asyncio
import logging
import secrets
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Awaitable, Callable, Final, TypedDict

from aiohttp import ClientError, ClientSession  # type: ignore
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
    SmartBatteries,
    SmartBatteryDetails,
    SmartBatterySessions,
    User,
    UserSites,
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
    DATA_USAGE,
    DATA_USER,
    DATA_USER_SITES,
    DEFAULT_REFRESH_INTERVAL,
    EVENT_FRANK_ENERGIE,
)

_LOGGER = logging.getLogger(__name__)
_LOG_AUTH_TOKENS_EXPIRED: Final = "Authentication tokens expired, trying to renew them (%s)"

if sys.platform == "win32":
    if hasattr(asyncio, "set_event_loop_policy"):
        # Python 3.14-3.16
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        except Exception:
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

    enode_chargers: EnodeChargers | None
    """Optional Enode chargers data."""

    enode_vehicles: EnodeVehicles | None
    """Optional Enode vehicles data."""

    batteries: SmartBatteries | None
    """Optional smart batteries data."""

    battery_details: SmartBatteryDetails | None
    """Optional smart battery details data."""

    battery_sessions: SmartBatterySessions | None
    """Optional smart battery sessions data."""

    contract_price_resolution_state: ContractPriceResolutionState | None
    """Optional contract price resolution state."""


@dataclass(frozen=True)
class PricesTodayCache:
    prices_today: PriceData
    month_summary: MonthSummary
    invoices: Invoices
    user: User
    user_sites: UserSites
    period_usage: PeriodUsageAndCosts
    enode_chargers: EnodeChargers
    smart_batteries: SmartBatteries
    smart_battery_details: SmartBatteryDetails
    smart_battery_sessions: SmartBatterySessions
    enode_vehicles: EnodeVehicles
    contract_price_resolution_state: ContractPriceResolutionState


class FrankEnergieCoordinator(DataUpdateCoordinator[FrankEnergieData]):
    """Get the latest data and update the states."""

    # Define the hour at which to fetch tomorrow's prices in UTC
    # This is set to 12 UTC, which corresponds to 14:00 UTC+2
    # If you want to change it to 13:00 UTC, uncomment the line
    # FETCH_TOMORROW_HOUR_UTC = 13  # 13:00 UTC
    # This means that if the current time is after 12:00 UTC, the coordinator will fetch tomorrow's prices
    # at 12:00 UTC,
    # which corresponds to 14:00 in UTC+2 timezone (e.g., Central European Summer Time).
    FETCH_TOMORROW_HOUR_UTC = 12  # 13  # 13:00 UTC 15:00 UTC+2
    PRICE_RELEASE_START_UTC: Final[time] = time(13, 0)  # 13:00 UTC
    PRICE_RELEASE_END_UTC: Final[time] = time(14, 0)  # 14:00 UTC

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, api: FrankEnergie
    ) -> None:
        """Initialize the data object."""
        self.hass = hass
        self.config_entry = config_entry
        self.api = api
        self._today_prices_logged: bool = False
        self._cache: dict = {}  # <--- hier cache je prijzen
        self.site_reference = config_entry.data.get("site_reference", None)
        self.country_code: str | None = (
            self.hass.config.country
        )  # replaced by hass_country_code
        self._country_code: str | None = self.hass.config.country
        self.hass_country_code: str | None = self.hass.config.country
        self._user_country: str | None = self.country_code
        self._connection_id: str | None = (
            None  # cache voor contractPriceResolutionState
        )
        self._resolution_state: ContractPriceResolutionState | None = None
        self.enode_chargers: EnodeChargers | None = None
        self.data: FrankEnergieData = {  # type: ignore[typeddict-unknown-key]
            DATA_ELECTRICITY: None,
            DATA_GAS: None,
            DATA_MONTH_SUMMARY: None,
            DATA_INVOICES: None,
            DATA_USAGE: None,
            DATA_USER: None,
            DATA_USER_SITES: None,
            DATA_ENODE_CHARGERS: None,
            DATA_BATTERIES: None,
            DATA_BATTERY_DETAILS: None,
            DATA_BATTERY_SESSIONS: None,
            DATA_CONTRACT_PRICE_RESOLUTION_STATE: None,
        }
        self._update_interval = timedelta(seconds=DEFAULT_REFRESH_INTERVAL)
        self._last_update_success = False
        self.user_electricity_enabled = False
        self.user_gas_enabled = False
        _LOGGER.debug(
            "Initializing Frank Energie coordinator with country_code: %s",
            self.country_code,
        )

        super().__init__(
            hass,
            _LOGGER,
            name="Frank Energie coordinator",
            update_interval=self._update_interval,
            config_entry=config_entry,
        )

        self.cached_prices_today: MarketPrices | None = None
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
        self._last_lowest_4h_event: date | None = None

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

        self._adjust_update_interval(now_utc)

        # Adjust refresh interval around price release
        # if self.PRICE_RELEASE_START_UTC <= now_utc.time() <= self.PRICE_RELEASE_END_UTC:
        #     self.update_interval = timedelta(minutes=5)
        # else:
        #     self.update_interval = timedelta(seconds=DEFAULT_REFRESH_INTERVAL)

        # ---------------------------------------------------
        # TODAY DATA (all data + prices_today)
        # ---------------------------------------------------
        try:
            (
                prices_today,
                data_month_summary,
                data_invoices,
                data_user,
                user_sites,
                data_period_usage,
                data_enode_chargers,
                data_smart_batteries,
                data_smart_battery_details,
                data_smart_battery_sessions,
                data_enode_vehicles,
                data_contract_price_resolution_state,
            ) = await self._fetch_today_data(today, tomorrow)
            if (
                prices_today is not None
                and prices_today.electricity is not None
                and not self._today_prices_logged
            ):
                _LOGGER.info(
                    "Frank Energie electricity prices available for %s",
                    today,
                )
                self._today_prices_logged = True
        except AuthRequiredException as err:
            raise ConfigEntryAuthFailed from err

        except AuthException as err:
            await self._try_renew_token()
            raise UpdateFailed("Authentication temporarily failed") from err

        except (RequestException, FrankEnergieException, ClientError) as err:
            # FrankEnergieException can wrap AuthException ("Not authorized")
            # Route auth errors to _try_renew_token instead of treating as network error
            if "Not authorized" in str(err) or "Unauthorized" in str(err):
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

        self.cached_prices_today = (
            prices_today,
            data_month_summary,
            data_invoices,
            data_user,
            user_sites,
            data_period_usage,
            data_enode_chargers,
            data_smart_batteries,
            data_smart_battery_details,
            data_smart_battery_sessions,
            data_enode_vehicles,
            data_contract_price_resolution_state,
        )

        self.last_fetch_today = now_utc

        # ---------------------------------------------------
        # TOMORROW PRICES
        # ---------------------------------------------------
        if now_utc.hour >= self.FETCH_TOMORROW_HOUR_UTC:
            if (
                self.cached_prices_tomorrow is None
                or self.last_fetch_tomorrow is None
                or self.last_fetch_tomorrow.date() != today
            ):
                prices_tomorrow = await self._fetch_tomorrow_data(tomorrow)

                _LOGGER.info("Retrieved Frank Energie tomorrow prices for %s", tomorrow)
                self.cached_prices_tomorrow = prices_tomorrow
                self.last_fetch_tomorrow = now_utc
            else:
                prices_tomorrow = self.cached_prices_tomorrow
        else:
            _LOGGER.debug(
                "Not fetching tomorrow's prices yet (current hour: %s:00 UTC, fetch hour: %s:00 UTC).",
                now_utc.hour,
                self.FETCH_TOMORROW_HOUR_UTC,
            )
            prices_tomorrow = None

        # ---------------------------------------------------
        # AGGREGATION
        # ---------------------------------------------------
        self.cached_prices = self._aggregate_data(
            prices_today,
            prices_tomorrow,
            data_month_summary,
            data_invoices,
            data_user,
            user_sites,
            data_period_usage,
            data_enode_chargers,
            data_smart_batteries,
            data_smart_battery_details,
            data_smart_battery_sessions,
            data_enode_vehicles,
            data_contract_price_resolution_state,
        )

        lowest_elec_price = (
            prices_today.electricity.today_min
            if prices_today and prices_today.electricity
            else None
        )

        if lowest_elec_price and self._should_fire_lowest_price_event(today):
            start = lowest_elec_price.date_from
            end = lowest_elec_price.date_till
            self._price_resolution_minutes = int((end - start).total_seconds() / 60)

            # We gaan ervan uit dat de API altijd UTC-tijden teruggeeft, maar we controleren het voor de zekerheid
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)

            if start <= now_utc < end:
                self.hass.bus.async_fire(
                    EVENT_FRANK_ENERGIE,
                    {
                        "entry_id": self.config_entry.entry_id,
                        "action": "lowest_price",
                        "resolution": self._price_resolution_minutes,
                        "price": lowest_elec_price.total,
                        "unit": "€/kWh",
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                    },
                )
                _LOGGER.debug(
                    "Firing frank_energie_event (lowest price): %s → %s : %s",
                    lowest_elec_price.date_from,
                    lowest_elec_price.date_till,
                    lowest_elec_price.total,
                )
                self._mark_lowest_price_event_fired(today)

        prices = (
            prices_today.electricity.today
            if prices_today and prices_today.electricity
            else None
        )

        if prices:
            result = self._find_lowest_consecutive_hours(prices, window=4)

            if result and self._should_fire_lowest_4h_event(today):
                average_price, start_price, end_price = result

                # Correcte price resolution: verschil tussen eerste 2 entries in prices
                if len(prices) >= 2:
                    first_interval = prices[0].date_from
                    second_interval = prices[1].date_from
                    self._price_resolution_minutes = int(
                        (second_interval - first_interval).total_seconds() / 60
                    )
                else:
                    self._price_resolution_minutes = 60  # fallback

                if start_price.date_from <= now_utc < end_price.date_till:
                    self.hass.bus.async_fire(
                        EVENT_FRANK_ENERGIE,
                        {
                            "entry_id": self.config_entry.entry_id,
                            "action": "lowest_4p_price",
                            "periods": 4,
                            "resolution": self._price_resolution_minutes,
                            "average_price": round(average_price, 3),
                            "unit": "€/kWh",
                            "start": start_price.date_from,
                            "end": end_price.date_till,
                        },
                    )

                    self._mark_lowest_4h_event_fired(today)

        return self.cached_prices

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
            _LOGGER.warning(
                "Authentication failed while fetching user sites: %s", ex
            )
            return None

    async def _fetch_month_summary(self) -> MonthSummary | None:
        """Fetch month summary from the API."""
        if not self.api.is_authenticated:
            return None
        try:
            return await self.api.month_summary(self.site_reference)
        except AuthException as ex:
            _LOGGER.warning(
                "Authentication failed while fetching month summary: %s", ex
            )
            return MonthSummary.from_dict({})
        except (RequestException, FrankEnergieException, ClientError) as ex:
            error_msg = str(ex).lower()
            if "no reading dates" in error_msg:
                _LOGGER.debug(
                    "No historical data available yet (typical for IN_DELIVERY sites): %s",
                    ex,
                )
            else:
                _LOGGER.warning("No month summary data available: %s", ex)
            return MonthSummary.from_dict({})

    async def _fetch_invoices(self) -> Invoices | None:
        """Fetch invoices from the API."""
        if not self.api.is_authenticated:
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
                    "No invoice data available yet (typical for IN_DELIVERY sites): %s",
                    ex,
                )
            else:
                _LOGGER.debug(
                    "No invoice data available (normal for IN_DELIVERY sites): %s",
                    ex,
                )
            return None

    async def _fetch_period_usage(self, start_date: date) -> PeriodUsageAndCosts | None:
        """Fetch period usage and costs from the API."""
        if not self.api.is_authenticated:
            return None
        try:
            return await self.api.period_usage_and_costs(
                self.site_reference, start_date.isoformat()
            )
        except AuthException as ex:
            _LOGGER.warning(
                "Authentication failed while fetching period usage: %s", ex
            )
            return None
        except (RequestException, FrankEnergieException, ClientError) as ex:
            error_msg = str(ex).lower()
            if "no reading dates" in error_msg:
                _LOGGER.debug(
                    "No usage data available yet (typical for IN_DELIVERY sites): %s",
                    ex,
                )
            else:
                _LOGGER.debug(
                    "No period usage data available (normal for IN_DELIVERY sites): %s",
                    ex,
                )
            return None

    async def _fetch_user_data(self) -> User | None:
        """Fetch user data from the API."""
        if not self.api.is_authenticated:
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
                and user_data.connections[0].get("connectionId")
            ):
                self._connection_id = user_data.connections[0].get(
                    "connectionId"
                )
            return user_data
        except AuthException as ex:
            _LOGGER.warning(
                "Authentication failed while fetching user data: %s", ex
            )
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
                resolution_state = (
                    await self.api.contract_price_resolution_state(
                        connection_id
                    )
                )
                self._resolution_state = resolution_state

                # resolution_state is already a ContractPriceResolutionState dataclass
                if (
                    resolution_state
                    and resolution_state.activeOption
                ):
                    # Update options using async_update_entry instead of direct assignment
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
            _LOGGER.exception(
                "Error fetching ContractPriceResolutionState: %s", err
            )
            return None

    async def _fetch_enode_chargers(
        self, start_date: date, is_smart_charging: bool
    ) -> dict[str, EnodeChargers] | None:
        """Fetch Enode chargers from the API."""
        if not (self.api.is_authenticated and is_smart_charging):
            return None
        try:
            return await self.api.enode_chargers(self.site_reference, start_date)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Failed to fetch enode chargers: %s", err)
            return None

    async def _fetch_smart_batteries(
        self, is_smart_trading: bool
    ) -> SmartBatteries | None:
        """Fetch smart batteries from the API."""
        if not (self.api.is_authenticated and is_smart_trading):
            return None
        try:
            return await self.api.smart_batteries()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Failed to fetch smart batteries: %s", err)
            return None

    async def _fetch_enode_vehicles(
        self, is_smart_charging: bool
    ) -> EnodeVehicles | None:
        """Fetch Enode vehicles from the API."""
        if not (self.api.is_authenticated and is_smart_charging):
            return None
        try:
            vehicles = await self.api.enode_vehicles()
            _LOGGER.debug("Fetched Enode vehicles: %s", vehicles)
            return vehicles
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("Failed to fetch enode vehicles: %s", err)
            return None

    async def _fetch_battery_details(self, battery) -> SmartBatteryDetails | None:
        """Fetch details for a single smart battery."""
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
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.error(
                "Failed to fetch sessions for battery %s: %s",
                battery.id,
                err,
            )
            return None

    async def _fetch_battery_details_and_sessions(
        self, battery, start_date: date, tomorrow: date
    ) -> tuple[SmartBatteryDetails | None, SmartBatterySessions | None]:
        """Fetch details and sessions for each smart battery concurrently."""
        if not battery:
            return None, None

        _LOGGER.debug(
            "Fetching details and sessions for battery: %s", battery.id
        )
        return await asyncio.gather(
            self._fetch_battery_details(battery),
            self._fetch_battery_sessions(battery, start_date, tomorrow),
        )

    async def _fetch_today_data(
        self, today: date, tomorrow: date
    ) -> tuple[
        MarketPrices | None,
        MonthSummary | None,
        Invoices | None,
        User | None,
        UserSites | None,
        PeriodUsageAndCosts | None,
        dict[str, EnodeChargers] | None,
        SmartBatteries | None,
        list[SmartBatteryDetails | None],
        list[SmartBatterySessions | None],
        EnodeVehicles | None,
        ContractPriceResolutionState | None,
    ]:
        """
        Fetches all relevant Frank Energie data for the current day, including prices, user sites, monthly summaries, invoices, usage, user info, Enode chargers, smart batteries, battery details, and battery sessions.

        Attempts to retrieve user-specific and public data as available, handling authentication failures and falling back to cached data if necessary. Also manages token renewal on authentication errors.

        Parameters:
            today (date): The current date for which data is being fetched.
            tomorrow (date): The next day, used for price and session range queries.

        Returns:
            Tuple containing today's prices, month summary, invoices, user data, user sites, period usage, Enode chargers, smart batteries, smart battery details, and smart battery sessions.
        """
        # --- Initialiseer alle variabelen ---
        prices_today: MarketPrices | None = None
        data_month_summary: MonthSummary | None = None
        data_invoices: Invoices | None = None
        data_user: User | None = None
        user_sites: UserSites | None = None
        data_period_usage: PeriodUsageAndCosts | None = None
        data_enode_chargers: dict[str, EnodeChargers] | None = None
        data_smart_batteries: SmartBatteries | None = None
        data_smart_battery_details: list[SmartBatteryDetails | None] = []
        data_smart_battery_sessions: list[SmartBatterySessions | None] = []
        data_enode_vehicles: EnodeVehicles | None = None
        data_contract_price_resolution_state: ContractPriceResolutionState | None = None

        yesterday = today - timedelta(days=1)
        start_date = yesterday

        try:
            _LOGGER.debug(
                "Fetching Frank Energie data for today %s", self.config_entry.entry_id
            )

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
                    self._fetch_prices_with_fallback(today, tomorrow),
                    self._fetch_user_sites(),
                    self._fetch_month_summary(),
                    self._fetch_invoices(),
                    self._fetch_period_usage(start_date),
                    self._fetch_user_data(),
                )

                # --- Haal contractPriceResolutionState op ---
                data_contract_price_resolution_state = (
                    await self._fetch_contract_price_resolution_state(
                        self._connection_id
                    )
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
            ) = await asyncio.gather(
                self._fetch_enode_chargers(start_date, is_smart_charging),
                self._fetch_smart_batteries(is_smart_trading),
                self._fetch_enode_vehicles(is_smart_charging),
            )

            data_smart_battery_details = []
            data_smart_battery_sessions = []

            if data_smart_batteries and data_smart_batteries.batteries:
                battery_tasks = [
                    self._fetch_battery_details_and_sessions(
                        battery, start_date, tomorrow
                    )
                    for battery in data_smart_batteries.batteries
                    if battery
                ]
                if battery_tasks:
                    results = await asyncio.gather(*battery_tasks)
                    for details, sessions in results:
                        if details:
                            data_smart_battery_details.append(details)
                        if sessions:
                            data_smart_battery_sessions.append(sessions)
            else:
                _LOGGER.debug("No smart batteries found")

            # Detect and log IN_DELIVERY status for clean user experience
            if self.api.is_authenticated:
                is_not_in_delivery = self._is_not_in_delivery_site(
                    data_month_summary, data_invoices, user_sites
                )
                self._log_not_in_delivery_status(is_not_in_delivery)

            return (
                prices_today,
                data_month_summary,
                data_invoices,
                data_user,
                user_sites,
                data_period_usage,
                data_enode_chargers,
                data_smart_batteries,
                data_smart_battery_details,
                data_smart_battery_sessions,
                data_enode_vehicles,
                data_contract_price_resolution_state,
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
                tomorrow, tomorrow + timedelta(days=1)
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
        prices_today,
        prices_tomorrow,
        data_month_summary,
        data_invoices,
        data_user,
        user_sites,
        data_period_usage,
        data_enode_chargers,
        data_smart_batteries,
        data_smart_battery_details,
        data_smart_battery_sessions,
        data_enode_vehicles,
        data_contract_price_resolution_state,
    ) -> FrankEnergieData:
        """Aggregate the fetched data into a single returnable dictionary."""

        # Aggregate the data into a single dictionary
        result: FrankEnergieData = {  # type: ignore[typeddict-unknown-key]
            DATA_MONTH_SUMMARY: data_month_summary,
            DATA_INVOICES: data_invoices,
            DATA_USAGE: data_period_usage,
            DATA_USER: data_user,
            DATA_USER_SITES: user_sites,
            DATA_ENODE_CHARGERS: data_enode_chargers,
            DATA_BATTERIES: data_smart_batteries,
            DATA_BATTERY_DETAILS: data_smart_battery_details,
            DATA_BATTERY_SESSIONS: data_smart_battery_sessions,
            DATA_ENODE_VEHICLES: data_enode_vehicles,
            DATA_CONTRACT_PRICE_RESOLUTION_STATE: data_contract_price_resolution_state,
            DATA_ELECTRICITY: None,
            DATA_GAS: None,
        }

        if prices_today is not None:
            if prices_today.electricity is not None:
                result[DATA_ELECTRICITY] = prices_today.electricity
            if prices_today.gas is not None:
                result[DATA_GAS] = prices_today.gas

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
        return (
            isinstance(smart_charging, dict)
            and smart_charging.get("isActivated", False) is True
        )

    def _is_smart_trading_enabled(self, data_user) -> bool:
        """Check if smart trading is enabled for the user."""
        if not data_user:
            return False
        smart_trading = data_user.smartTrading
        return (
            isinstance(smart_trading, dict)
            and smart_trading.get("isActivated", False) is True
        )

    async def _fetch_prices_with_fallback(
        self, start_date: date, end_date: date
    ) -> MarketPrices:
        """Fetch prices with fallback to public prices and cached data.
        This method attempts to fetch user-specific prices first, and if they are not available,
        it falls back to public prices.
        """
        country_code = (
            self.hass.config.country if self.hass and self.hass.config else "NL"
        )

        async def fetch_public():
            try:
                # For Belgium, we need to use a different endpoint for public prices
                if country_code == "BE":
                    return await self.api.be_prices(start_date, end_date)
                # Determine resolution option for public prices based on contract price resolution state
                resolution_active_option = (
                    self._resolution_state.activeOption
                    if self._resolution_state
                    else "PT60M"
                )
                _LOGGER.debug(
                    "Using contractPriceResolutionState active option: %s",
                    resolution_active_option,
                )
                return await self.api.prices(
                    start_date, end_date, resolution_active_option
                )
            except NetworkError as err:
                _LOGGER.warning("Failed to fetch public prices: %s", err)
                return None

        async def fetch_user():
            if not self.api.is_authenticated:
                return None
            resolution_active_option = (
                self._resolution_state.activeOption
                if self._resolution_state
                else "PT60M"
            )
            _LOGGER.debug(
                "Fetching user prices for site_reference %s with country %s and resolution option %s",
                self.site_reference,
                self._user_country,
                resolution_active_option,
            )
            user_country = self._user_country or country_code
            try:
                return await self.api.user_prices(
                    self.site_reference, user_country, start_date, end_date
                )
            except NetworkError as err:
                _LOGGER.warning("Failed to fetch user prices: %s", err)
                return None

        _LOGGER.debug("Fetching prices concurrently")
        public_prices, user_prices = await asyncio.gather(fetch_public(), fetch_user())

        if public_prices is None:
            # Use cached prices if available, otherwise create empty MarketPrices
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
            _LOGGER.warning(
                "Failed to fetch user prices, falling back to public prices"
            )
            return public_prices

        # Use user prices if both gas and electricity have data
        if (
            user_prices.gas is not None
            and getattr(user_prices.gas, "all", None)
            and user_prices.electricity is not None
            and getattr(user_prices.electricity, "all", None)
        ):
            self._cached_prices = user_prices
            return user_prices

        # Fallback logic
        if user_prices.gas is None or not getattr(user_prices.gas, "all", None):
            _LOGGER.info("No gas prices found for user, falling back to public prices")
            user_prices.gas = (
                public_prices.gas if self.user_gas_enabled else PriceData([], "gas")
            )

        if user_prices.electricity is None or not getattr(
            user_prices.electricity, "all", None
        ):
            _LOGGER.info(
                "No electricity prices found for user, falling back to public prices"
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
            _LOGGER.exception("Failed to renew token: %s. Starting user reauth flow", ex)
            # Consider setting the coordinator to an error state or handling the error appropriately
            raise ConfigEntryAuthFailed from ex

    # async def _fetch_authenticated(self, method: Callable, *args) -> Any:
    async def _fetch_authenticated(
        self, method: Callable[..., Awaitable[object]], *args: object
    ) -> object | None:
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
        """Adjust coordinator update interval around price release windows with jitter to prevent thundering herd."""
        if self.PRICE_RELEASE_START_UTC <= now_utc.time() <= self.PRICE_RELEASE_END_UTC:
            # 5 minutes + 5 to 45 seconds jitter
            new_interval = timedelta(seconds=300 + secrets.randbelow(41) + 5)
        else:
            # 15 minutes + 10 to 80 seconds jitter
            # Positive jitter ensures sensor clock-aligned refreshes (exactly at 15m)
            # do not trigger the coordinator API call early, keeping users desynchronized.
            new_interval = timedelta(
                seconds=DEFAULT_REFRESH_INTERVAL + secrets.randbelow(71) + 10
            )

        if self.update_interval != new_interval:
            _LOGGER.debug("Update interval changed to %s", new_interval)
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

    def _should_fire_lowest_4h_event(self, today: date) -> bool:
        return self._last_lowest_4h_event != today

    def _mark_lowest_4h_event_fired(self, today: date) -> None:
        self._last_lowest_4h_event = today

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
        # self.device_id = config_entry.data.get("device_id")
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
            raise ex
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
                "Failed to fetch battery session data from Frank Energie: %s" % ex
            ) from ex

        except ConfigEntryAuthFailed as ex:
            _LOGGER.error("Authentication failed: %s", ex)
            raise ex
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            raise UpdateFailed(
                "Unexpected error while fetching battery session data: %s" % ex
            ) from ex


async def run_hourly(
    start_time: datetime, end_time: datetime, interval: timedelta, method: Callable
) -> None:
    """Run the specified method at regular intervals between start_time and end_time."""
    while True:
        now = datetime.now(timezone.utc)
        if start_time <= now <= end_time:
            await method()
        await asyncio.sleep(interval.total_seconds())


async def hourly_refresh(coordinator: FrankEnergieCoordinator) -> None:
    """Perform hourly refresh of coordinator."""
    await coordinator.async_refresh()


async def start_coordinator(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Start the coordinator."""
    async with ClientSession() as session:
        api = FrankEnergie(session, config_entry.data["access_token"])
        coordinator = FrankEnergieCoordinator(
            hass,
            config_entry,
            api,
        )
        await coordinator.async_refresh()

        today = datetime.now(timezone.utc)
        start_time = datetime.combine(today.date(), time(15, 0), tzinfo=timezone.utc)
        end_time = datetime.combine(today.date(), time(16, 0), tzinfo=timezone.utc)
        interval = timedelta(minutes=5)

        await run_hourly(
            start_time, end_time, interval, lambda: hourly_refresh(coordinator)
        )


def _parse_resolution(resolution: str) -> int:
    """Convert ISO8601 duration (PT60M) to minutes."""
    if resolution.startswith("PT") and resolution.endswith("M"):
        return int(resolution[2:-1])
    return 0
