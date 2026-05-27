# frank_energie/api.py

import logging
from datetime import date
from typing import Optional

from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import AuthException, RequestException
from python_frank_energie.models import MarketPrices

_LOGGER = logging.getLogger(__name__)


class FrankEnergieAPI:
    """Wrapper class for interacting with the Frank Energie API."""

    def __init__(
        self, access_token: Optional[str] = None, refresh_token: Optional[str] = None
    ):
        self.api = FrankEnergie(auth_token=access_token, refresh_token=refresh_token)

    async def authenticate(self, username: str, password: str) -> None:
        """Authenticate with the Frank Energie API using the provided username and password."""
        try:
            await self.api.login(username, password)
            _LOGGER.info("Successfully authenticated with Frank Energie API")
        except AuthException as ex:
            _LOGGER.error("Failed to authenticate with Frank Energie API: %s", ex)
            raise

    async def get_prices(self, start_date: date, end_date: date) -> MarketPrices:
        """
        Fetch the electricity and gas prices from the Frank Energie API for the specified date range.

        Args:
            start_date: The start date of the price range.
            end_date: The end date of the price range.

        Returns:
            The MarketPrices object containing the electricity and gas prices.

        Raises:
            RequestException: If an error occurs while fetching prices from the API.
        """
        try:
            return await self.api.prices(start_date, end_date)
        except RequestException as ex:
            _LOGGER.error("Failed to fetch prices from Frank Energie API: %s", ex)
            raise
