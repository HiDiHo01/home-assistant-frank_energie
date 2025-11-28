""" Coordinator implementation for Frank Energie integration.
    Fetching the latest data from Frank Energie and updating the states."""
# coordinator.py
# version 2025.9.30

import asyncio
import logging
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Final, TypedDict

import aiohttp
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
)

_LOGGER = logging.getLogger(__name__)

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class FrankEnergieData(TypedDict):
    """ Represents data fetched from Frank Energie API. """
    DATA_ELECTRICITY: PriceData | None
    """Electricity price data."""

    DATA_GAS: PriceData | None
    """Gas price data."""

    DATA_MONTH_SUMMARY: MonthSummary | None
    """Optional summary data for the month."""

    DATA_INVOICES: Invoices | None
    """Optional invoices data."""

    DATA_USAGE: PeriodUsageAndCosts | None
    """Optional user data."""

    DATA_USER: User | None
    """Optional user data."""

    DATA_USER_SITES: UserSites | None
    """Optional user sites."""

    DATA_ENODE_CHARGERS: EnodeChargers | None
    """Optional Enode chargers data."""

    DATA_ENODE_VEHICLES: EnodeVehicles | None
    """Optional Enode vehicles data."""

    DATA_BATTERIES: SmartBatteries | None
    """Optional smart batteries data."""

    DATA_BATTERY_DETAILS: SmartBatteryDetails | None
    """Optional smart battery details data."""

    DATA_BATTERY_SESSIONS: SmartBatterySessions | None
    """Optional smart battery sessions data."""


class FrankEnergieCoordinator(DataUpdateCoordinator[FrankEnergieData]):
    """ Get the latest data and update the states. """

    # Define the hour at which to fetch tomorrow's prices in UTC
    # This is set to 12 UTC, which corresponds to 14:00 UTC+2
    # If you want to change it to 13:00 UTC, uncomment the line
    # FETCH_TOMORROW_HOUR_UTC = 13  # 13:00 UTC
    # FETCH_TOMORROW_HOUR_UTC = 12  # 12:00 UTC
    # This means that if the current time is after 13:00 UTC, the coordinator will fetch tomorrow's prices
    # at 12:00 UTC,
    # which corresponds to 14:00 in UTC+2 timezone (e.g., Central European Summer Time).
    FETCH_TOMORROW_HOUR_UTC = 12  # 13  # 13:00 UTC 15:00 UTC+2
    PRICE_RELEASE_START_UTC: Final[time] = time(13, 0)  # 13:00 UTC
    PRICE_RELEASE_END_UTC: Final[time] = time(14, 0)    # 14:00 UTC

    def __init__(
        self, hass: HomeAssistant, config_entry: ConfigEntry, api: FrankEnergie
    ) -> None:
        """Initialize the data object."""
        self.hass = hass
        self.config_entry = config_entry
        self.api = api
        self._cache: dict = {}  # <--- hier cache je prijzen
        self.site_reference = config_entry.data.get("site_reference", None)
        self.country_code: str | None = self.hass.config.country
        self._connection_id: str | None = None  # cache voor contractPriceResolutionState
        self._resolution_state: ContractPriceResolutionState | None = None
        self.enode_chargers: EnodeChargers | None = None
        self.data: FrankEnergieData = {
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

        self.cached_prices_today: dict | None = None
        self.cached_prices_tomorrow: dict | None = None
        self.last_fetch_today: datetime | None = None
        self.last_fetch_tomorrow: datetime | None = None

    def _is_in_delivery_site(self, data_month_summary, data_invoices, user_sites) -> bool:
        """
        Detect if this is an IN_DELIVERY site based on available data.

        Returns True if the site appears to be in IN_DELIVERY status
        (no historical data available yet).
        """
        # Check for typical IN_DELIVERY indicators
        has_no_month_summary = data_month_summary is None
        has_no_invoices = data_invoices is None

        # Additional check: if user_sites exists but has no usage segments
        has_limited_segments = (
            user_sites is not None
            and hasattr(user_sites, 'segments')
            and len(user_sites.segments) == 0
        )

        # Site is likely IN_DELIVERY if it has no historical data
        return has_no_month_summary and (has_no_invoices or has_limited_segments)

    def _log_in_delivery_status(self, is_in_delivery: bool) -> None:
        """
        Log a single, clear message about IN_DELIVERY status to keep logs clean.
        """
        if is_in_delivery and not hasattr(self, '_in_delivery_logged'):
            _LOGGER.info(
                "Frank Energie site appears to be in IN_DELIVERY status. "
                "Price data is available, but usage and billing data will become "
                "available once your energy delivery begins. This is normal for new customers."
            )
            # Mark that we've logged this to avoid spam
            self._in_delivery_logged = True
        elif not is_in_delivery and hasattr(self, '_in_delivery_logged'):
            _LOGGER.info(
                "Frank Energie site now has historical data available. "
                "All sensors should be fully functional."
            )
            # Clear the flag so we can log again if status changes back
            delattr(self, '_in_delivery_logged')

    async def _async_update_data(self) -> FrankEnergieData:
        """Fetch and cache data from Frank Energie with smart interval logic."""

        now_utc = datetime.now(timezone.utc)

        # Adjust refresh interval around price release
        if self.PRICE_RELEASE_START_UTC <= now_utc.time() <= self.PRICE_RELEASE_END_UTC:
            self.update_interval = timedelta(minutes=5)
        else:
            self.update_interval = timedelta(seconds=DEFAULT_REFRESH_INTERVAL)

        today = now_utc.date()
        tomorrow = today + timedelta(days=1)

        # ---------------------------------------------------
        # TODAY DATA (all data + prices_today)
        # ---------------------------------------------------
        if (
            self.cached_prices_today is None
            or self.last_fetch_today is None
            or self.last_fetch_today.date() != today
        ):
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
            ) = await self._fetch_today_data(today, tomorrow)

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
            )

            self.last_fetch_today = now_utc

        else:
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
            ) = self.cached_prices_today

        # ---------------------------------------------------
        # TOMORROW PRICES
        # ---------------------------------------------------
        if now_utc.hour >= self.FETCH_TOMORROW_HOUR_UTC:
            if (
                self.cached_prices_tomorrow is None
                or self.last_fetch_tomorrow is None
                or self.last_fetch_tomorrow.date() != tomorrow
            ):
                prices_tomorrow = await self._fetch_tomorrow_data(tomorrow)
                self.cached_prices_tomorrow = prices_tomorrow
                self.last_fetch_tomorrow = now_utc
            else:
                prices_tomorrow = self.cached_prices_tomorrow
        else:
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
        )

        return self.cached_prices

    async def _fetch_today_data(self, today: date, tomorrow: date):
        """
        Fetches all relevant Frank Energie data for the current day, including prices, user sites, monthly summaries, invoices, usage, user info, Enode chargers, smart batteries, battery details, and battery sessions.

        Attempts to retrieve user-specific and public data as available, handling authentication failures and falling back to cached data if necessary. Also manages token renewal on authentication errors.

        Parameters:
            today (date): The current date for which data is being fetched.
            tomorrow (date): The next day, used for price and session range queries.

        Returns:
            Tuple containing today's prices, month summary, invoices, user data, user sites, period usage, Enode chargers, smart batteries, smart battery details, and smart battery sessions.
        """
        # current_date = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        start_date = yesterday

        try:
            _LOGGER.debug(
                "Fetching Frank Energie data for today %s", self.config_entry.entry_id)

            prices_today = await self.__fetch_prices_with_fallback(today, tomorrow)

            _LOGGER.debug(
                "Fetching Frank Energie data for site_reference %s", self.site_reference)
            if self.site_reference is not None:
                _LOGGER.debug(
                    "Preparing to fetch Frank Energie data_month_summary for site %s", self.site_reference)

            user_sites = None
            _LOGGER.debug("Fetching Frank Energie user sites for today")
            try:
                if self.api.is_authenticated:
                    user_sites = await self.api.UserSites()
                    if "ELECTRICITY" in user_sites.segments:
                        self.user_electricity_enabled = True
                    if "GAS" in user_sites.segments:
                        self.user_gas_enabled = True
            except AuthException as ex:
                _LOGGER.warning("Authentication failed while fetching user sites: %s", ex)
            _LOGGER.debug("User sites: %s", user_sites)

            data_month_summary = None
            _LOGGER.debug("Fetching Frank Energie data_month_summary for today")
            try:
                if self.api.is_authenticated:
                    data_month_summary = await self.api.month_summary(self.site_reference)
            except AuthException as ex:
                _LOGGER.warning("Authentication failed while fetching month summary: %s", ex)
            except (RequestException, FrankEnergieException) as ex:
                # Check if this looks like an IN_DELIVERY "No reading dates" error
                error_msg = str(ex).lower()
                if "no reading dates" in error_msg:
                    _LOGGER.debug("No historical data available yet (typical for IN_DELIVERY sites): %s", ex)
                else:
                    _LOGGER.warning("No month summary data available: %s", ex)
            except Exception as ex:
                # Check for GraphQL errors that might contain "No reading dates found"
                error_msg = str(ex).lower()
                if "no reading dates found" in error_msg:
                    _LOGGER.debug("No historical data available yet (typical for IN_DELIVERY sites): %s", ex)
                else:
                    _LOGGER.error("Unexpected error while fetching month summary: %s", ex)
            _LOGGER.debug("Data month_summary: %s", data_month_summary)

            data_invoices = None
            _LOGGER.debug("Fetching Frank Energie data_invoices for today")
            try:
                if self.api.is_authenticated:
                    data_invoices = await self.api.invoices(self.site_reference)
            except AuthException as ex:
                _LOGGER.warning("Authentication failed while fetching invoices: %s", ex)
            except (RequestException, FrankEnergieException) as ex:
                # For IN_DELIVERY sites, missing invoice data is expected
                error_msg = str(ex).lower()
                if "no reading dates" in error_msg:
                    _LOGGER.debug("No invoice data available yet (typical for IN_DELIVERY sites): %s", ex)
                else:
                    _LOGGER.debug("No invoice data available (normal for IN_DELIVERY sites): %s", ex)
            except Exception as ex:
                # Check for any other errors that might contain "No reading dates"
                error_msg = str(ex).lower()
                if "no reading dates" in error_msg:
                    _LOGGER.debug("No invoice data available yet (typical for IN_DELIVERY sites): %s", ex)
                else:
                    _LOGGER.error("Unexpected error while fetching invoices: %s", ex)
            _LOGGER.debug("Data invoices: %s", data_invoices)

            data_period_usage = None
            _LOGGER.debug("Fetching Frank Energie data_period_usage for today")
            try:
                if self.api.is_authenticated:
                    data_period_usage = await self.api.period_usage_and_costs(self.site_reference, start_date)
            except AuthException as ex:
                _LOGGER.warning("Authentication failed while fetching period usage: %s", ex)
            except (RequestException, FrankEnergieException) as ex:
                # For IN_DELIVERY sites, missing usage data is expected
                error_msg = str(ex).lower()
                if "no reading dates" in error_msg:
                    _LOGGER.debug("No usage data available yet (typical for IN_DELIVERY sites): %s", ex)
                else:
                    _LOGGER.debug("No period usage data available (normal for IN_DELIVERY sites): %s", ex)
            except Exception as ex:
                # Check for any other errors that might contain "No reading dates"
                error_msg = str(ex).lower()
                if "no reading dates" in error_msg:
                    _LOGGER.debug("No usage data available yet (typical for IN_DELIVERY sites): %s", ex)
                else:
                    _LOGGER.error("Unexpected error while fetching period usage: %s", ex)
            _LOGGER.debug("Data period_usage: %s", data_period_usage)

            data_user = None
            _LOGGER.debug("Fetching Frank Energie data_user for today")
            try:
                if self.api.is_authenticated:
                    data_user = await self.api.user(self.site_reference)
                    if not self._connection_id:
                        if data_user.sites and data_user.sites[0].contracts:
                            self._connection_id = data_user.connections[0].get("connectionId")
            except AuthException as ex:
                _LOGGER.warning("Authentication failed while fetching user data: %s", ex)
            except (RequestException, FrankEnergieException) as ex:
                _LOGGER.warning("No user data available: %s", ex)
            except Exception as ex:
                _LOGGER.error("Unexpected error while fetching user data: %s", ex)
            _LOGGER.debug("Data user: %s", data_user)

            try:
                if self.api.is_authenticated and self._connection_id:
                    self._resolution_state = await self.api.contract_price_resolution_state(self._connection_id)
                    _LOGGER.debug("ContractPriceResolutionState: %s", self._resolution_state)
            except Exception as err:
                _LOGGER.error("Error fetching ContractPriceResolutionState: %s", err)
                pass

            # Initialize feature flags
            is_smart_charging = False
            is_smart_trading = False
            if data_user:
                # Check if smart charging and trading are activated
                is_smart_charging = self._is_smart_charging_enabled(data_user)
                is_smart_trading = self._is_smart_trading_enabled(data_user)

            # Check if smart charging is activated and retrieve smart charging data
            data_enode_chargers = None
            try:
                if self.api.is_authenticated and is_smart_charging:
                    data_enode_chargers = await self.api.enode_chargers(self.site_reference, start_date)
            except Exception as err:
                _LOGGER.debug("Failed to fetch enode chargers: %s", err)
                data_enode_chargers = None
            _LOGGER.debug("Data enode chargers: %s", data_enode_chargers)

            data_smart_batteries = None
            if self.api.is_authenticated and is_smart_trading:
                # Only fetch smart batteries if smart trading is enabled
                try:
                    data_smart_batteries = await self.api.smart_batteries()
                except Exception as err:
                    _LOGGER.debug("Failed to fetch smart batteries: %s", err)
                    data_smart_batteries = None
            elif self.api.is_authenticated and not is_smart_trading:
                _LOGGER.debug("Smart trading not enabled, skipping smart batteries fetch")
            _LOGGER.debug("Data smart batteries: %s", data_smart_batteries)

            data_smart_battery_details = []
            if self.api.is_authenticated and data_smart_batteries:
                _LOGGER.debug("Data smart batteries: %s", data_smart_batteries.smart_batteries)

            if data_smart_batteries and data_smart_batteries.smart_batteries:
                for battery in data_smart_batteries.smart_batteries:
                    if not battery:
                        continue

                    _LOGGER.debug("Smart battery ID: %s", battery.id)
                    if not self.api.is_authenticated:
                        _LOGGER.warning("API not authenticated. Skipping battery ID: %s", battery.id)
                        continue
                    try:
                        details = await self.api.smart_battery_details(
                            battery.id
                        )
                        if details:
                            data_smart_battery_details.extend(details)
                    except Exception as err:
                        _LOGGER.error("Failed to fetch details for battery %s: %s", battery.id, err)
                        continue
                    _LOGGER.debug("Battery details: %s", details)
                    _LOGGER.debug("Device ID: %s", battery.id)
            else:
                _LOGGER.debug("No smart batteries found")

            _LOGGER.debug("Data smart battery details: %s", data_smart_battery_details)

            data_smart_battery_sessions = []
            # if self.api.is_authenticated and data_user:
            # _LOGGER.debug("Data user Batteries: %s", data_user.smartCharging.get("isActivated"))
            if data_smart_batteries and data_smart_batteries.smart_batteries:
                for battery in data_smart_batteries.smart_batteries:
                    if not battery:
                        continue

                    _LOGGER.debug("Smart battery ID coord: %s", battery.id)
                    if not self.api.is_authenticated:
                        _LOGGER.warning("API not authenticated. Skipping battery ID: %s", battery.id)
                        continue
                    try:
                        sessions = await self.api.smart_battery_sessions(
                            battery.id, start_date, tomorrow
                        )
                        if sessions and isinstance(sessions.sessions, list):
                            _LOGGER.debug("Appending %d session(s) for battery %s", len(sessions.sessions), battery.id)
                            data_smart_battery_sessions.append(sessions)
                        else:
                            _LOGGER.warning(
                                "No valid sessions list found in SmartBatterySessions for battery %s", battery.id)
                    except Exception as err:
                        _LOGGER.error("Failed to fetch sessions for battery %s: %s", battery.id, err)
                        sessions = None
                        continue
                    _LOGGER.debug("Battery sessions: %s", sessions)
                    _LOGGER.debug("Device ID: %s", battery.id)
            else:
                _LOGGER.debug("No smart batteries found")

            _LOGGER.debug("Data smart battery session: %s", data_smart_battery_sessions)

            data_enode_vehicles = []
            # Fetch Enode vehicles if smart trading is enabled
            try:
                if self.api.is_authenticated and is_smart_charging:
                    data_enode_vehicles = await self.api.enode_vehicles()
                    _LOGGER.debug("Fetched Enode vehicles: %s", data_enode_vehicles)
            except Exception as err:
                _LOGGER.debug("Failed to fetch enode vehicles: %s", err)
                data_enode_vehicles = None

            # Detect and log IN_DELIVERY status for clean user experience
            is_in_delivery = self._is_in_delivery_site(data_month_summary, data_invoices, user_sites)
            self._log_in_delivery_status(is_in_delivery)

            return prices_today, data_month_summary, data_invoices, data_user, user_sites, data_period_usage, data_enode_chargers, data_smart_batteries, data_smart_battery_details, data_smart_battery_sessions, data_enode_vehicles

        except UpdateFailed as err:
            electricity = self.data.get(DATA_ELECTRICITY)
            gas = self.data.get(DATA_GAS)

            electricity_prices = electricity.future_prices.get_future_prices() if electricity and electricity.future_prices else None
            gas_prices = gas.future_prices.get_future_prices() if gas and gas.future_prices else None

            if electricity_prices and gas_prices:
                _LOGGER.warning("Update failed but using cached data: %s", err)
                return self.data

            raise err

        except RequestException as ex:
            if str(ex).startswith("user-error:"):
                raise ConfigEntryAuthFailed from ex
            raise UpdateFailed(ex) from ex

        except AuthRequiredException as err:
            _LOGGER.warning("Authentication failed: %s", err)
            await self._try_renew_token()
            raise ConfigEntryAuthFailed("Authentication is required.") from err

        except AuthException as ex:
            _LOGGER.debug(
                "Authentication tokens expired, trying to renew them (%s)", ex)
            await self._try_renew_token()
            raise UpdateFailed(ex) from ex

    async def _fetch_tomorrow_data(self, tomorrow: date):
        """Fetch tomorrow's data after 13:00 UTC."""
        try:
            _LOGGER.debug("Fetching Frank Energie data for tomorrow")
            return await self.__fetch_prices_with_fallback(tomorrow, tomorrow + timedelta(days=1))
        except UpdateFailed as err:
            _LOGGER.debug(
                "Error fetching Frank Energie data for tomorrow (%s)", err)
            return None
        except AuthException as ex:
            _LOGGER.debug(
                "Authentication tokens expired, trying to renew them (%s)", ex)
            await self._try_renew_token()
            raise UpdateFailed(ex) from ex

    def _aggregate_data(self, prices_today, prices_tomorrow, data_month_summary, data_invoices, data_user, user_sites, data_period_usage, data_enode_chargers, data_smart_batteries, data_smart_battery_details, data_smart_battery_sessions, data_enode_vehicles) -> dict:
        """Aggregate the fetched data into a single returnable dictionary."""

        # Aggregate the data into a single dictionary
        result = {
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
        }
        result[DATA_ELECTRICITY] = None
        result[DATA_GAS] = None

        if prices_today is not None:
            if prices_today.electricity is not None:
                result[DATA_ELECTRICITY] = prices_today.electricity
            if prices_today.gas is not None:
                result[DATA_GAS] = prices_today.gas

        if prices_tomorrow is not None:
            if result[DATA_ELECTRICITY] is not None and prices_tomorrow.electricity is not None:
                result[DATA_ELECTRICITY] += prices_tomorrow.electricity
            if result[DATA_GAS] is not None and prices_tomorrow.gas is not None:
                result[DATA_GAS] += prices_tomorrow.gas

        return result

    def _is_smart_charging_enabled(self, data_user) -> bool:
        """Check if smart charging is enabled for the user."""
        if not data_user:
            return False
        smart_charging = data_user.smartCharging
        return isinstance(smart_charging, dict) and smart_charging.get("isActivated", False) is True

    def _is_smart_trading_enabled(self, data_user) -> bool:
        """Check if smart trading is enabled for the user."""
        if not data_user:
            return False
        smart_trading = data_user.smartTrading
        return isinstance(smart_trading, dict) and smart_trading.get("isActivated", False) is True

    async def __fetch_prices_with_fallback(self, start_date: date, end_date: date) -> MarketPrices:
        """ Fetch prices with fallback mechanism.
            This method attempts to fetch user-specific prices first, and if they are not available,
            it falls back to public prices.
        """

        if self.hass.config.country == "BE":
            public_prices: MarketPrices = await self.api.be_prices(start_date, end_date)
        if self.hass.config.country == "NL" or self.hass.config.country is None:
            active_option = "PT60M"
            active_option = self._resolution_state.activeOption if self._resolution_state else "PT60M"
            _LOGGER.debug("Using contractPriceResolutionState active option: %s", active_option)
            public_prices: MarketPrices = await self.api.prices(start_date, end_date, active_option)
            # public_prices: MarketPrices = await self.api.prices(start_date, end_date, "PT15M")

        # If not logged in, return public prices
        if not self.api.is_authenticated:
            return public_prices

        user_prices: MarketPrices = await self.api.user_prices(self.site_reference, start_date, end_date)

        # if len(user_prices.gas.all) > 0 and len(user_prices.electricity.all) > 0:
        # if user_prices.gas.all and user_prices.electricity.all:
        if user_prices.gas is not None and user_prices.gas.all and user_prices.electricity is not None and user_prices.electricity.all:
            # If user_prices are available for both gas and electricity return them
            return user_prices

        # Use public prices if no user prices are available as fallback
        if not getattr(user_prices.gas, "all", None) or len(user_prices.gas.all) == 0:
            # if not user_prices.gas.all:
            # if user_prices.gas.all is None:
            _LOGGER.info(
                "No gas prices found for user, falling back to public prices")
            if self.user_gas_enabled:
                user_prices.gas = public_prices.gas
            else:
                # user_prices.gas = None # if user has no gas in users contract you want to reset gas prices
                user_prices.gas = None

        if not getattr(user_prices.electricity, "all", None) or len(user_prices.electricity.all) == 0:
            # if user_prices.electricity.all is None:
            _LOGGER.info(
                "No electricity prices found for user, falling back to public prices")
            if self.user_electricity_enabled:
                user_prices.electricity = public_prices.electricity
            else:
                # user_prices.electricity = None # if user has no electricity in users contract you want to reset electricity prices
                user_prices.electricity = None

        return user_prices

    async def _handle_fetch_exceptions(self, ex):
        if isinstance(ex, UpdateFailed):
            if self.data[DATA_ELECTRICITY].get_future_prices() and self.data[DATA_GAS].get_future_prices():
                _LOGGER.warning(str(ex))
                return self.data
            raise ex
        if isinstance(ex, RequestException) and str(ex).startswith("user-error:"):
            raise ConfigEntryAuthFailed from ex
        if isinstance(ex, AuthException):
            _LOGGER.debug("Authentication tokens expired, trying to renew them (%s)", ex)
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
            _LOGGER.error(
                "Failed to renew token: %s. Starting user reauth flow", ex)
            # Consider setting the coordinator to an error state or handling the error appropriately
            raise ConfigEntryAuthFailed from ex

    async def _fetch_authenticated(self, method: Callable, *args) -> Any:
        """Execute authenticated API call with logging."""
        if not self.api.is_authenticated:
            _LOGGER.warning("API not authenticated. Skipping %s", method.__name__)
            return None
        try:
            result = await method(*args)
            _LOGGER.debug("Fetched data from %s: %s", method.__name__, result)
            return result
        except Exception as err:
            _LOGGER.error("Failed to fetch %s: %s", method.__name__, err)
            return None

    def _parse_vehicles(self, data: list[dict]) -> EnodeVehicles:
        vehicles_list = [EnodeVehicle(**vehicle_dict) for vehicle_dict in data]
        return EnodeVehicles(vehicles=vehicles_list)


class FrankEnergieBatterySessionCoordinator(DataUpdateCoordinator[SmartBatterySessions]):
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

    async def _async_update_data(self) -> SmartBatterySessions:
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

            _LOGGER.debug("Fetching smart battery sessions for device %s", self.device_id)

            return await self.api.smart_battery_sessions(self.device_id, today, tomorrow)

        except AuthException as ex:
            _LOGGER.debug("Authentication tokens expired, attempting token renewal: %s", ex)
            await self.api.renew_token()
            raise UpdateFailed("Authentication failed and token was renewed. Retry update.") from ex

        except RequestException as ex:
            raise UpdateFailed("Failed to fetch battery session data from Frank Energie: %s" % ex) from ex

        except Exception as ex:
            raise UpdateFailed("Unexpected error while fetching battery session data: %s" % ex) from ex
        except UpdateFailed as ex:
            if self.data.get(DATA_BATTERY_SESSIONS):
                _LOGGER.warning(str(ex))
                return self.data
            raise ex
        except ConfigEntryAuthFailed as ex:
            _LOGGER.error("Authentication failed: %s", ex)
            raise ex


async def run_hourly(start_time: datetime, end_time: datetime, interval: timedelta, method: Callable) -> None:
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
    async with aiohttp.ClientSession() as session:
        api = FrankEnergie(session, config_entry.data["access_token"])
        coordinator = FrankEnergieCoordinator(hass, config_entry, api)
        await coordinator.async_refresh()

        today = datetime.now(timezone.utc)
        start_time = datetime.combine(today.date(), time(15, 0), tzinfo=timezone.utc)
        end_time = datetime.combine(today.date(), time(16, 0), tzinfo=timezone.utc)
        interval = timedelta(minutes=5)

        await run_hourly(start_time,
                         end_time,
                         interval,
                         lambda: hourly_refresh(coordinator)
                         )
