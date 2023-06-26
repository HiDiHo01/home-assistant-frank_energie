from __future__ import annotations
""" const.py """
"""Constants used in the Frank Energie integration."""

import logging
from homeassistant.const import (
    CURRENCY_EURO,
    ENERGY_KILO_WATT_HOUR,
    VOLUME_CUBIC_METERS,
)
_LOGGER = logging.getLogger(__name__)

ATTRIBUTION = "Data provided by Frank Energie"
DOMAIN = "frank_energie"
DATA_URL = "https://frank-graphql-prod.graphcdn.app/"
ICON = "mdi:currency-eur"
COMPONENT_TITLE = "Frank Energie"

CONF_COORDINATOR = "coordinator"
ATTR_TIME = "from_time"

DATA_ELECTRICITY = "electricity"
DATA_GAS = "gas"
DATA_MONTH_SUMMARY = "month_summary"
DATA_INVOICES = "invoices"
DATA_USER = "user"

UNIT_ELECTRICITY = f"{CURRENCY_EURO}/{ENERGY_KILO_WATT_HOUR}"
UNIT_GAS = f"{CURRENCY_EURO}/{VOLUME_CUBIC_METERS}"

SERVICE_NAME_PRICES = "Prices"
SERVICE_NAME_COSTS = "Costs"
SERVICE_NAME_USER = "User"

_LOGGER.info("Constants loaded for %s", DOMAIN)
