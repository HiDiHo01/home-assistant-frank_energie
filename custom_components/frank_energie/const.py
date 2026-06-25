"""
Constants used in the Frank Energie integration.
"""

# const.py
# date 2026.6.24

import logging
from dataclasses import dataclass
from typing import Final

from homeassistant.const import CURRENCY_EURO, UnitOfEnergy, UnitOfVolume
from python_frank_energie.models import (
    ContractPriceResolutionState,
    EnodeChargers,
    EnodeVehicles,
    Invoices,
    MarketPrices,
    MonthSummary,
    PeriodUsageAndCosts,
    SmartBatteries,
    SmartBatteryDetails,
    SmartBatterySessions,
    SmartPvSystems,
    SmartPvSystemSummary,
    User,
    UserSites,
    UserSmartFeedInStatus,
)

# --- Logger Setup ---
_LOGGER: logging.Logger = logging.getLogger(__name__)

# --- Domain Information ---
DOMAIN: Final[str] = "frank_energie"
VERSION: Final[str] = "2026.6.21"
ATTRIBUTION: Final[str] = "Data provided by Frank Energie"
UNIQUE_ID: Final[str] = "frank_energie"
TIMEZONE_AMSTERDAM: Final[str] = "Europe/Amsterdam"

# --- URLs ---
# DATA_URL: Final[str] = "https://frank-graphql-prod.graphcdn.app/"
DATA_URL: Final[str] = "https://graphql.frankenergie.nl/"
API_CONF_URL: Final[str] = "https://www.frankenergie.nl/goedkoop"
SITE_NL: Final[str] = "https://www.frankenergie.nl/nl"
SITE_BE: Final[str] = "https://www.frankenergie.be/nl"
CUSTOMER_SERVICE_NL: Final[str] = "klantenservice@frankenergie.nl"
CUSTOMER_SERVICE_BE: Final[str] = "klantenservice@frankenergie.be"
CONTACT_NL_URL: Final[str] = "https://klantenservice.frankenergie.nl/hc/nl-nl"
PHONE_NL: Final[str] = "+31207900114"
WHATSAPP_NL: Final[str] = "+31649163884"

# --- Component Metadata ---
ICON: Final[str] = "mdi:currency-eur"
ICON_CLOCK_OUTLINE: Final[str] = "mdi:clock-outline"
COMPONENT_TITLE: Final[str] = "Frank Energie"
MANUFACTURER_FRANK_ENERGIE: Final[str] = "Frank Energie"

# --- Configuration Constants ---
CONF_COORDINATOR: Final[str] = "coordinator"
CONF_AUTH_TOKEN: Final[str] = "auth_token"
CONF_REFRESH_TOKEN: Final[str] = "refresh_token"
CONF_SITE: Final[str] = "site_reference"
CONF_RESOLUTION: Final[str] = "resolution"  # 15-minute price resolution
CONF_MONTHLY_SUBSCRIPTION_FEE: Final[str] = "monthly_subscription_fee"
CONF_ENERGY_TAX_ODE: Final[str] = "energy_tax_ode"
CONF_ENERGY_TAX_REDUCTION: Final[str] = "energy_tax_reduction"
CONF_NETWORK_CHARGES: Final[str] = "network_charges"

# --- Default values for some config constants ---
DEFAULT_REFRESH_INTERVAL: Final[int] = 900  # 15 minutes
DEFAULT_ROUND: Final[int] = 3  # Default display round value for prices
DEFAULT_RESOLUTION: Final[str] = "PT15M"
DEFAULT_MONTHLY_SUBSCRIPTION_FEE: Final[float] = 7.00
DEFAULT_ENERGY_TAX_ODE: Final[float] = 34.92
DEFAULT_ENERGY_TAX_REDUCTION: Final[float] = -52.42
DEFAULT_NETWORK_CHARGES: Final[float] = 39.87

SUPPORTED_RESOLUTIONS: Final[tuple[str, ...]] = ("PT15M", "PT60M")
SUPPORTED_COUNTRIES: Final[tuple[str, ...]] = ("NL", "BE")

# --- Data Fields ---
DATA_CONTRACT_PRICE_RESOLUTION_STATE: Final[str] = "contract_price_resolution_state"
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
DATA_ENODE_VEHICLES: Final[str] = "enode_vehicles"
DATA_PV_SYSTEMS: Final[str] = "smart_pv_systems"
DATA_PV_SUMMARY: Final[str] = "smart_pv_summary"
DATA_USER_SMART_FEED_IN: Final[str] = "user_smart_feed_in"
DATA_TOKEN_EXPIRES_AT: Final[str] = "auth_token_expires_at"
DATA_REFRESH_TOKEN_EXPIRES_AT: Final[str] = "refresh_token_expires_at"

# --- Attribute Constants ---
ATTR_FROM_TIME: Final[str] = "from_time"
ATTR_TILL_TIME: Final[str] = "till_time"
ATTR_LAST_UPDATE: Final[str] = "Last update"
ATTR_START_DATE: Final[str] = "Start date"

# --- Event Attribute Constants ---
EVENT_FRANK_ENERGIE = "frank_energie_event"

# --- Unit Constants ---
UNIT_ELECTRICITY: Final[str] = f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}"
UNIT_GAS: Final[str] = f"{CURRENCY_EURO}/{UnitOfVolume.CUBIC_METERS}"
UNIT_GAS_NL: Final[str] = f"{CURRENCY_EURO}/{UnitOfVolume.CUBIC_METERS}"
UNIT_GAS_BE: Final[str] = f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}"

PER_UNIT_TO_UNIT: Final[dict[str, str]] = {
    "M3": UNIT_GAS,
    "KWH": UNIT_GAS_BE,
}

# --- Service Names ---
SERVICE_NAME_PRICES: Final[str] = "Prices"
SERVICE_NAME_GAS_PRICES: Final[str] = "Gasprices"
SERVICE_NAME_ELEC_PRICES: Final[str] = "Electricityprices"
SERVICE_NAME_COSTS: Final[str] = "Costs"
SERVICE_NAME_USAGE: Final[str] = "Usage"
SERVICE_NAME_USER: Final[str] = "User"
SERVICE_NAME_SETTINGS: Final[str] = "Settings"
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
SERVICE_NAME_ENODE_VEHICLES: Final[str] = "Vehicles"
SERVICE_NAME_PV_SYSTEMS: Final[str] = "Solar Systems"
SERVICE_NAME_PV_SUMMARY: Final[str] = "Solar Summary"

# --- Device Response Data Class ---


@dataclass
class DeviceResponseEntry:
    """Data class describing a single response entry."""

    # Electricity prices and details
    electricity: MarketPrices | None

    # Gas prices and details
    gas: MarketPrices | None

    # Monthly summary (if available)
    month_summary: MonthSummary | None = None

    # Invoice details (if available)
    invoices: Invoices | None = None

    # Usage information (if available)
    usage: PeriodUsageAndCosts | None = None

    # User information (if available)
    user: User | None = None

    # User Sites information (if available. this replaces delivery site)
    user_sites: UserSites | None = None

    # Smart battery details (if available)
    smart_batteries: SmartBatteries | None = None

    # Smart battery session details (if available)
    smart_battery_sessions: SmartBatterySessions | None = None

    # Smart battery details (if available)
    smart_battery_details: SmartBatteryDetails | None = None

    # Enode chargers details (if available)
    enode_chargers: EnodeChargers | None = None

    # Vehicles details (if available)
    vehicles: EnodeVehicles | None = None  # Placeholder for vehicle data, if any

    # Smart PV systems (if available)
    smart_pv_systems: SmartPvSystems | None = None

    # Smart PV system summary (if available)
    smart_pv_summary: dict[str, SmartPvSystemSummary] | None = None

    # User smart feed-in status (if available)
    user_smart_feed_in: UserSmartFeedInStatus | None = None

    # Contract price resolution state (if available)
    contract_price_resolution_state: ContractPriceResolutionState | None = None


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
