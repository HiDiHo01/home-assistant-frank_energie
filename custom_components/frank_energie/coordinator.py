""" Coordinator implementation for Frank Energie integration.
    Fetching the latest data from Frank Energie and updating the states."""
# coordinator.py

import asyncio
import sys
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Optional, TypedDict

import aiohttp
from homeassistant.config_entries import ConfigEntry  # type: ignore
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN  # type: ignore
from homeassistant.core import HomeAssistant  # type: ignore
from homeassistant.exceptions import ConfigEntryAuthFailed  # type: ignore
from homeassistant.helpers.update_coordinator import \
    DataUpdateCoordinator  # type: ignore
from homeassistant.helpers.update_coordinator import UpdateFailed
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import AuthException, RequestException
from python_frank_energie.models import (Invoices, MarketPrices, MonthSummary,
                                         PeriodUsageAndCosts, PriceData, User,
                                         UserSites)

from .const import (_LOGGER, DATA_ELECTRICITY, DATA_GAS, DATA_INVOICES,
                    DATA_MONTH_SUMMARY, DATA_USAGE, DATA_USER, DATA_USER_SITES)

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


class FrankEnergieCoordinator(DataUpdateCoordinator[FrankEnergieData]):
    """ Get the latest data and update the states. """

    FETCH_TOMORROW_HOUR_UTC = 13

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, api: FrankEnergie
    ) -> None:
        """Initialize the data object."""
        self.hass = hass
        self.entry = entry
        self.api = api
        self.site_reference = entry.data.get("site_reference", None)

        super().__init__(
            hass,
            _LOGGER,
            name="Frank Energie coordinator",
            update_interval=timedelta(minutes=60),
        )

    async def _async_update_data(self) -> FrankEnergieData:
        """Get the latest data from Frank Energie."""

        today = datetime.now(timezone.utc).date()
        tomorrow = today + timedelta(days=1)

        # Fetch today's prices and user data
        prices_today, data_month_summary, data_invoices, data_user, user_sites, data_period_usage = await self._fetch_today_data(today, tomorrow)

        # Fetch tomorrow's prices if it's after 13:00 UTC
        prices_tomorrow = await self._fetch_tomorrow_data(tomorrow) if datetime.now(timezone.utc).hour >= self.FETCH_TOMORROW_HOUR_UTC else None

        return self._aggregate_data(prices_today, prices_tomorrow, data_month_summary, data_invoices, data_user, user_sites, data_period_usage)

    async def _fetch_today_data(self, today: date, tomorrow: date):
        """Fetch today's data."""
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

            user_sites = (
                await self.api.UserSites()
                if self.api.is_authenticated
                else None
            )
            _LOGGER.debug("User sites: %s", user_sites)

            data_month_summary = (
                await self.api.month_summary(self.site_reference)
                if self.api.is_authenticated
                else None
            )
            _LOGGER.debug("Data month_summary: %s", data_month_summary)

            data_invoices = (
                await self.api.invoices(self.site_reference)
                if self.api.is_authenticated
                else None
            )
            _LOGGER.debug("Data invoices: %s", data_invoices)

            data_period_usage = (
                await self.api.period_usage_and_costs(self.site_reference, start_date)
                if self.api.is_authenticated
                else None
            )
            _LOGGER.debug("Data period_usage: %s", data_period_usage)

            data_user = (
                await self.api.user(self.site_reference)
                if self.api.is_authenticated
                else None
            )
            _LOGGER.debug("Data user: %s", data_user)

            return prices_today, data_month_summary, data_invoices, data_user, user_sites, data_period_usage

        except UpdateFailed as err:
            if (self.data["DATA_ELECTRICITY"].future_prices is not None and
                    self.data["DATA_GAS"].future_prices is not None):
                _LOGGER.warning(str(err))
                return self.data
            raise err

        except RequestException as ex:
            if str(ex).startswith("user-error:"):
                raise ConfigEntryAuthFailed from ex
            raise UpdateFailed(ex) from ex

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

    def _aggregate_data(self, prices_today, prices_tomorrow, data_month_summary, data_invoices, data_user, user_sites, data_period_usage):
        """Aggregate the fetched data into a single returnable dictionary."""

        result = {
            DATA_ELECTRICITY: prices_today.electricity,
            DATA_GAS: prices_today.gas,
            DATA_MONTH_SUMMARY: data_month_summary,
            DATA_INVOICES: data_invoices,
            DATA_USAGE: data_period_usage,
            DATA_USER: data_user,
            DATA_USER_SITES: user_sites,
        }

        if prices_tomorrow is not None:
            result[DATA_ELECTRICITY] += prices_tomorrow.electricity
            result[DATA_GAS] += prices_tomorrow.gas

        return result

    async def __fetch_prices_with_fallback(self, start_date: date, end_date: date) -> MarketPrices:
        """Fetch prices with fallback mechanism."""

        if not self.api.is_authenticated:
            return await self.api.prices(start_date, end_date)

        # user_prices = await self.api.user_prices(start_date, end_date)
        user_prices = await self.api.user_prices(start_date, self.site_reference, end_date)

        # if len(user_prices.gas.all) > 0 and len(user_prices.electricity.all) > 0:
        # if user_prices.gas.all and user_prices.electricity.all:
        if user_prices.gas is not None and user_prices.gas.all and user_prices.electricity is not None and user_prices.electricity.all:
            # If user_prices are available for both gas and electricity return them
            return user_prices

        public_prices = await self.api.prices(start_date, end_date)

        # Use public prices if no user prices are available
        if len(user_prices.gas.all) == 0:
            # if not user_prices.gas.all:
            # if user_prices.gas.all is None:
            _LOGGER.info(
                "No gas prices found for user, falling back to public prices")
            user_prices.gas = public_prices.gas

        if len(user_prices.electricity.all) == 0:
            # if user_prices.electricity.all is None:
            _LOGGER.info(
                "No electricity prices found for user, falling back to public prices")
            user_prices.electricity = public_prices.electricity

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
    await coordinator.async_refresh()
