""" Coordinator implementation for Frank Energie integration.
    Fetching the latest data from Frank Energie and updating the states."""
# coordinator.py
# version 2025.6.19

import asyncio
import logging
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Optional, TypedDict

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
    RequestException,
)
from python_frank_energie.models import (
    EnodeChargers,
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
    DATA_GAS,
    DATA_INVOICES,
    DATA_MONTH_SUMMARY,
    DATA_USAGE,
    DATA_USER,
    DATA_USER_SITES,
)

_LOGGER = logging.getLogger(__name__)

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class FrankEnergieData(TypedDict):
    """ Represents data fetched from Frank Energie API. """
    DATA_ELECTRICITY: PriceData
    """Electricity price data."""

    DATA_GAS: PriceData
    """Gas price data."""

    DATA_MONTH_SUMMARY: Optional[MonthSummary]
    """Optional summary data for the month."""

    DATA_INVOICES: Optional[Invoices]
    """Optional invoices data."""

    DATA_USAGE: Optional[PeriodUsageAndCosts]
    """Optional user data."""

    DATA_USER: Optional[User]
    """Optional user data."""

    DATA_USER_SITES: Optional[UserSites]
    """Optional user sites."""

    DATA_ENODE_CHARGERS: Optional[EnodeChargers]
    """Optional Enode chargers data."""

    DATA_BATTERIES: Optional[SmartBatteries]
    """Optional smart batteries data."""

    DATA_BATTERY_DETAILS: Optional[SmartBatteryDetails]
    """Optional smart battery details data."""

    # DATA_BATTERY_SESSIONS: Optional[SmartBatterySessions]
    DATA_BATTERY_SESSIONS: Optional[list[SmartBatterySessions]]
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

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: FrankEnergie
    ) -> None:
        """Initialize the data object."""
        self.hass = hass
        self.entry = entry
        self.api = api
        self.site_reference = entry.data.get("site_reference", None)
        self.country_code: str | None = self.hass.config.country
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
        self._update_interval = timedelta(minutes=60)
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
            update_interval=self._update_interval
        )

    async def _async_update_data(self) -> FrankEnergieData:
        """Get the latest data from Frank Energie."""

        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        tomorrow = today + timedelta(days=1)

        # Fetch today's prices and user data
        prices_today, data_month_summary, data_invoices, data_user, user_sites, data_period_usage, data_enode_chargers, data_smart_batteries, data_smart_battery_details, data_smart_battery_sessions = await self._fetch_today_data(today, tomorrow)

        # Fetch tomorrow's prices if it's after 13:00 UTC
        prices_tomorrow = (
            await self._fetch_tomorrow_data(tomorrow)
            if now_utc.hour >= self.FETCH_TOMORROW_HOUR_UTC
            else None
        )

        return self._aggregate_data(prices_today, prices_tomorrow, data_month_summary, data_invoices, data_user, user_sites, data_period_usage, data_enode_chargers, data_smart_batteries, data_smart_battery_details, data_smart_battery_sessions)

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
                "Fetching Frank Energie data for today %s", self.entry.entry_id)
            prices_today = await self.__fetch_prices_with_fallback(today, tomorrow)

            _LOGGER.debug(
                "Fetching Frank Energie data for site_reference %s", self.site_reference)
            if self.site_reference is not None:
                _LOGGER.debug(
                    "Fetching Frank Energie data_month_summary for today %s", await self.api.month_summary(self.site_reference))

            user_sites = None
            _LOGGER.debug("Fetching Frank Energie user sites for today")
            try:
                if self.api.is_authenticated:
                    user_sites = await self.api.UserSites(self.site_reference)
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
            _LOGGER.debug("Data month_summary: %s", data_month_summary)

            data_invoices = None
            _LOGGER.debug("Fetching Frank Energie data_invoices for today")
            try:
                if self.api.is_authenticated:
                    data_invoices = await self.api.invoices(self.site_reference)
            except AuthException as ex:
                _LOGGER.warning("Authentication failed while fetching invoices: %s", ex)
            _LOGGER.debug("Data invoices: %s", data_invoices)

            data_period_usage = None
            _LOGGER.debug("Fetching Frank Energie data_period_usage for today")
            try:
                if self.api.is_authenticated:
                    data_period_usage = await self.api.period_usage_and_costs(self.site_reference, start_date)
            except AuthException as ex:
                _LOGGER.warning("Authentication failed while fetching period usage: %s", ex)
            _LOGGER.debug("Data period_usage: %s", data_period_usage)

            data_user = None
            _LOGGER.debug("Fetching Frank Energie data_user for today")
            try:
                if self.api.is_authenticated:
                    data_user = await self.api.user(self.site_reference)
            except AuthException as ex:
                _LOGGER.warning("Authentication failed while fetching user data: %s", ex)
            _LOGGER.debug("Data user: %s", data_user)

            if data_user:
                # is_smart_charging = data_user.smartCharging.get("isActivated")
                # Check if smart charging is activated and retrieve smart battery data
                is_smart_charging = self._is_smart_charging_enabled(data_user)
                # is_smart_charging = True

            # Check if smart charging is activated and retrieve smart charging data
            # Gebruik echte of testdata afhankelijk van context
            data_enode_chargers = None
            if self.api.is_authenticated and is_smart_charging:
                data_enode_chargers = await self.api.enode_chargers(self.site_reference, start_date)

            _LOGGER.debug("Data enode chargers: %s", data_enode_chargers)

            data_smart_batteries = None
            if self.api.is_authenticated:
                try:
                    data_smart_batteries = await self.api.smart_batteries()
                except Exception as err:
                    _LOGGER.debug("Failed to fetch smart batteries: %s", err)
                    data_smart_batteries = None
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
                            # data_smart_battery_sessions.extend(sessions)
                            data_smart_battery_sessions.append(sessions)
                        else:
                            _LOGGER.warning(
                                "No valid sessions list found in SmartBatterySessions for battery %s", battery.id)
                    except Exception as err:
                        _LOGGER.error("Failed to fetch sessions for battery %s: %s", battery.id, err)
                        continue
                    _LOGGER.debug("Battery sessions: %s", sessions)
                    _LOGGER.debug("Device ID: %s", battery.id)
            else:
                _LOGGER.debug("No smart batteries found")

            _LOGGER.debug("Data smart battery session: %s", data_smart_battery_sessions)

            return prices_today, data_month_summary, data_invoices, data_user, user_sites, data_period_usage, data_enode_chargers, data_smart_batteries, data_smart_battery_details, data_smart_battery_sessions

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
            await self.__try_renew_token()
            raise ConfigEntryAuthFailed("Authentication is required.") from err

        except AuthException as ex:
            _LOGGER.debug(
                "Authentication tokens expired, trying to renew them (%s)", ex)
            await self.__try_renew_token()
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
            await self.__try_renew_token()
            raise UpdateFailed(ex) from ex

    def _aggregate_data(self, prices_today, prices_tomorrow, data_month_summary, data_invoices, data_user, user_sites, data_period_usage, data_enode_chargers, data_smart_batteries, data_smart_battery_details, data_smart_battery_sessions) -> dict:
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
        }
        result[DATA_ELECTRICITY] = None
        result[DATA_GAS] = None

        if prices_today is not None:
            if prices_today.electricity is not None:
                result[DATA_ELECTRICITY] = prices_today.electricity
            if prices_today.gas is not None:
                result[DATA_GAS] = prices_today.gas

        if prices_tomorrow is not None:
            if prices_tomorrow.electricity is not None:
                result[DATA_ELECTRICITY] += prices_tomorrow.electricity
            if prices_tomorrow.gas is not None:
                result[DATA_GAS] += prices_tomorrow.gas

        return result

    def _is_smart_charging_enabled(self, data_user) -> bool:
        """Check if smart charging is enabled for the user."""
        if not data_user:
            return False
        smart_charging = data_user.smartCharging
        return isinstance(smart_charging, dict) and smart_charging.get("isActivated", False) is True

    async def __fetch_prices_with_fallback(self, start_date: date, end_date: date) -> MarketPrices:
        """ Fetch prices with fallback mechanism.
            This method attempts to fetch user-specific prices first, and if they are not available,
            it falls back to public prices.
        """

        if self.hass.config.country == "BE":
            public_prices: MarketPrices = await self.api.be_prices(start_date, end_date)
        if self.hass.config.country == "NL" or self.hass.config.country is None:
            public_prices: MarketPrices = await self.api.prices(start_date, end_date)

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
        if len(user_prices.gas.all) == 0:
            # if not user_prices.gas.all:
            # if user_prices.gas.all is None:
            _LOGGER.info(
                "No gas prices found for user, falling back to public prices")
            if self.user_gas_enabled:
                user_prices.gas = public_prices.gas
            else:
                # user_prices.gas = None # if user has no gas in users contract you want to reset gas prices
                user_prices.gas = None

        if len(user_prices.electricity.all) == 0:
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

    async def __try_renew_token(self) -> None:
        """Try to renew authentication token."""

        try:
            updated_tokens = await self.api.renew_token()
            data = {
                CONF_ACCESS_TOKEN: updated_tokens.authToken,
                CONF_TOKEN: updated_tokens.refreshToken,
            }
            # Update the config entry with the new tokens
            self.hass.config_entries.async_update_entry(self.entry, data=data)

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


class FrankEnergieBatterySessionCoordinator(DataUpdateCoordinator[SmartBatterySessions]):
    """
    Coordinator to fetch smart battery session data from Frank Energie.

    Retrieves sessions for smart batteries and handles update errors and authentication.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
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
        self.site_reference = entry.data.get("site_reference")
        self.device_id = device_id

        super().__init__(
            hass,
            _LOGGER,
            name="Frank Energie Battery Sessions",
            update_interval=timedelta(minutes=60),
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
#         await asyncio.sleep(interval)


async def hourly_refresh(coordinator: FrankEnergieCoordinator) -> None:
    """Perform hourly refresh of coordinator."""
    await coordinator.async_refresh()


async def start_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start the coordinator."""
    async with aiohttp.ClientSession() as session:
        api = FrankEnergie(session, entry.data["access_token"])
        coordinator = FrankEnergieCoordinator(hass, entry, api)
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
