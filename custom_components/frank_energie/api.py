# frank_energie/api.py

import logging
from datetime import datetime, timedelta, date
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import RequestException, AuthException
from python_frank_energie.models import MarketPrices

_LOGGER = logging.getLogger(__name__)

class FrankEnergieAPI:
    def __init__(self, access_token=None, refresh_token=None):
        self.api = FrankEnergie(auth_token=access_token, refresh_token=refresh_token)
    
    async def authenticate(self, username, password):
        try:
            await self.api.login(username, password)
            _LOGGER.info("Successfully authenticated with Frank Energie API")
        except AuthException as ex:
            _LOGGER.error("Failed to authenticate with Frank Energie API: %s", ex)
            raise

    async def get_prices(self, start_date: date, end_date: date) -> MarketPrices:
        try:
            return await self.api.prices(start_date, end_date)
        except RequestException as ex:
            _LOGGER.error("Failed to fetch prices from Frank Energie API: %s", ex)
            raise