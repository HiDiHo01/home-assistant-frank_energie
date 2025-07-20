"""
Constants used in the Frank Energie integration.
"""
# const.py

import logging
from dataclasses import dataclass
from typing import Final, Optional

from homeassistant.const import CURRENCY_EURO, UnitOfEnergy, UnitOfVolume
from python_frank_energie.models import (
    EnodeChargers,
    Invoices,
    MarketPrices,
    MonthSummary,
    PeriodUsageAndCosts,
    SmartBatteries,
    SmartBatteryDetails,
    SmartBatterySessions,
    User,
    UserSites,
)

# --- Logger Setup ---
_LOGGER: logging.Logger = logging.getLogger(__name__)

# --- Domain Information ---
DOMAIN: Final[str] = "frank_energie"
VERSION: Final[str] = "2025.7.20"
ATTRIBUTION: Final[str] = "Data provided by Frank Energie"
UNIQUE_ID: Final[str] = "frank_energie"

# --- URLs ---
DATA_URL: Final[str] = "https://frank-graphql-prod.graphcdn.app/"
# DATA_URL: Final[str] = "https://graphql.frankenergie.nl/"
API_CONF_URL: Final[str] = "https://www.frankenergie.nl/goedkoop"

# --- Component Metadata ---
ICON: Final[str] = "mdi:currency-eur"
COMPONENT_TITLE: Final[str] = "Frank Energie"

# --- Configuration Constants ---
CONF_COORDINATOR: Final[str] = "coordinator"
CONF_AUTH_TOKEN: Final[str] = "auth_token"
CONF_REFRESH_TOKEN: Final[str] = "refresh_token"
CONF_SITE: Final[str] = "site_reference"

# --- Default values for some config constants ---
DEFAULT_REFRESH_INTERVAL: Final[int] = 3600

# --- Data Fields ---
DATA_ELECTRICITY: Final[str] = "electricity"
DATA_GAS: Final[str] = "gas"
DATA_MONTH_SUMMARY: Final[str] = "month_summary"
DATA_INVOICES: Final[str] = "invoices"
DATA_USAGE: Final[str] = "usage"
DATA_USER: Final[str] = "user"
DATA_USER_SITES: Final[str] = "user_sites"
DATA_DELIVERY_SITE: Final[str] = "delivery_site"
DATA_BATTERIES: Final[str] = "smart_batteries"
DATA_BATTERY_DETAILS: Final[str] = "smart_battery_details"
DATA_BATTERY_SESSIONS: Final[str] = "smart_battery_sessions"
DATA_ENODE_CHARGERS: Final[str] = "enode_chargers"

# --- Attribute Constants ---
ATTR_TIME: Final[str] = "from_time"

# --- Unit Constants ---
UNIT_ELECTRICITY: Final[str] = f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}"
UNIT_GAS: Final[str] = f"{CURRENCY_EURO}/{UnitOfVolume.CUBIC_METERS}"
UNIT_GAS_BE: Final[str] = f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}"

# --- Service Names ---
SERVICE_NAME_PRICES: Final[str] = "Prices"
SERVICE_NAME_GAS_PRICES: Final[str] = "Gasprices"
SERVICE_NAME_ELEC_PRICES: Final[str] = "Electricityprices"
SERVICE_NAME_COSTS: Final[str] = "Costs"
SERVICE_NAME_USAGE: Final[str] = "Usage"
SERVICE_NAME_USER: Final[str] = "User"
SERVICE_NAME_ACTIVE_DELIVERY_SITE: Final[str] = "Active_Delivery_Site"
SERVICE_NAME_ELEC_CONN: Final[str] = "Electricity connection"
SERVICE_NAME_GAS_CONN: Final[str] = "Gas connection"
SERVICE_NAME_BATTERIES: Final[str] = "Batteries"
SERVICE_NAME_BATTERY_SESSIONS: Final[str] = "Battery Sessions"
SERVICE_NAME_BATTERY_SUMMARY: Final[str] = "Battery Summary"
SERVICE_NAME_BATTERY_DETAILS: Final[str] = "Battery Details"
SERVICE_NAME_INVOICES: Final[str] = "Invoices"
SERVICE_NAME_MONTH_SUMMARY: Final[str] = "Month Summary"
SERVICE_NAME_USER_SITES: Final[str] = "User Sites"
SERVICE_NAME_ENODE_CHARGERS: Final[str] = "Chargers"

# --- Display Constants ---
DEFAULT_ROUND: Final[int] = 3  # Default display round value for prices

# --- Device Response Data Class ---


@dataclass
class DeviceResponseEntry:
    """Data class describing a single response entry."""

    # Electricity prices and details
    electricity: MarketPrices

    # Gas prices and details
    gas: MarketPrices

    # Monthly summary (if available)
    month_summary: Optional[MonthSummary] = None

    # Invoice details (if available)
    invoices: Optional[Invoices] = None

    # Usage information (if available)
    usage: Optional[PeriodUsageAndCosts] = None

    # User information (if available)
    user: Optional[User] = None

    # User Sites information (if available. this replaces delivery site)
    user_sites: Optional[UserSites] = None

    # Smart battery details (if available)
    smart_batteries: Optional[SmartBatteries] = None

    # Smart battery session details (if available)
    smart_battery_sessions: Optional[SmartBatterySessions] = None

    # Smart battery details (if available)
    smart_battery_details: Optional[SmartBatteryDetails] = None

    # Enode chargers details (if available)
    enode_chargers: Optional[EnodeChargers] = None


# Log loading of constants (move to init.py for better practice)
_LOGGER.debug("Constants loaded for %s", DOMAIN)

# --- Example of how to use the DeviceResponseEntry class ---
# Example usage of the DeviceResponseEntry class
# device_response = DeviceResponseEntry(
#     electricity=MarketPrices(),
#     gas=MarketPrices(),
#     month_summary=MonthSummary(),
#     invoices=Invoices(),
#     usage=PeriodUsageAndCosts(),
#     user=User(),
#     user_sites=UserSites(),
#     smart_batteries=SmartBatteries(),
#     smart_battery_sessions=SmartBatterySessions(),
#     enode_chargers=EnodeChargers(),
# )
# This is just a placeholder for the actual data that would be populated
# in a real-world scenario. The actual data would be fetched from the API
# and populated into the DeviceResponseEntry instance.
# The above example is commented out to avoid execution errors since
# the classes are not fully implemented in this snippet.
# The DeviceResponseEntry class can be used to hold the response data
# from the Frank Energie API calls, making it easier to manage and
# access the data in a structured way.
