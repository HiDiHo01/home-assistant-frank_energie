"""Frank Energie current electricity and gas price information service."""
# sensor.py
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Final, Optional, Union

from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity,
                                             SensorEntityDescription,
                                             SensorStateClass)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (CURRENCY_EURO, PERCENTAGE, STATE_UNKNOWN,
                                 UnitOfEnergy, UnitOfVolume)
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.helpers import event
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt, utcnow

from .const import (API_CONF_URL, ATTR_TIME, ATTRIBUTION, COMPONENT_TITLE,
                    CONF_COORDINATOR, DATA_ELECTRICITY, DATA_GAS,
                    DATA_INVOICES, DATA_MONTH_SUMMARY, DATA_USAGE, DATA_USER,
                    DATA_USER_SITES, DOMAIN, ICON, SERVICE_NAME_COSTS,
                    SERVICE_NAME_PRICES, SERVICE_NAME_USAGE, SERVICE_NAME_USER,
                    UNIT_ELECTRICITY, UNIT_GAS, VERSION)
from .coordinator import FrankEnergieCoordinator

_LOGGER = logging.getLogger(__name__)

DATA_DELIVERY_SITE: Final[str] = "delivery_site"
FORMAT_DATE = "%d-%m-%Y"


@dataclass
class FrankEnergieEntityDescription(SensorEntityDescription):
    """Describes Frank Energie sensor entity."""

    authenticated: bool = False
    # service_name: Union[str, None] = SERVICE_NAME_PRICES
    service_name: Optional[str] = SERVICE_NAME_PRICES
    # value_fn: Union[Callable[[dict], StateType], None] = None
    # attr_fn: Callable[[dict], dict[str, Union[StateType, list]]] = field(
    #     default_factory=lambda: {}  # type: ignore
    # )
    value_fn: Callable[[dict], StateType] = field(default=lambda _: STATE_UNKNOWN)
    attr_fn: Callable[[dict], dict[str, Union[StateType, list, None]]] = field(default=lambda _: {})

    def __init__(
        self,
        key: str,
        name: str,
        device_class: Optional[str] = None,
        state_class: Optional[str] = None,
        native_unit_of_measurement: Optional[str] = None,
        suggested_display_precision: Optional[int] = None,
        authenticated: Optional[bool] = None,
        service_name: Union[str, None] = None,
        value_fn: Optional[Callable[[dict], StateType]] = None,
        attr_fn: Optional[Callable[[dict],
                                   dict[str, Union[StateType, list]]]] = None,
        entity_registry_enabled_default: bool = True,
        entity_registry_visible_default: bool = True,
        entity_category: Optional[Union[str, EntityCategory]] = None,
        translation_key: Optional[str] = None,
        icon: Optional[str] = None,
    ):
        super().__init__(
            key=key,
            name=name,
            device_class=SensorDeviceClass(device_class) if device_class else None,
            state_class=state_class,
            native_unit_of_measurement=native_unit_of_measurement,
            suggested_display_precision=suggested_display_precision,
            translation_key=translation_key,
            entity_category=EntityCategory(entity_category) if isinstance(entity_category, str) else entity_category
        )
        object.__setattr__(self, 'authenticated', authenticated or False)
        object.__setattr__(self, 'service_name', service_name or SERVICE_NAME_PRICES)
        object.__setattr__(self, 'value_fn', value_fn or STATE_UNKNOWN)
        object.__setattr__(self, 'attr_fn', attr_fn if attr_fn is not None else lambda data: {})
        self.entity_registry_enabled_default = entity_registry_enabled_default
        self.entity_registry_visible_default = entity_registry_visible_default
        self.icon = icon

    def get_state(self, data: dict) -> StateType:
        """Get the state value."""
        return self.value_fn(data)

    def get_attributes(self, data: dict) -> dict[str, Union[StateType, list]]:
        """Get the additional attributes."""
        return self.attr_fn(data)

    @property
    def is_authenticated(self) -> bool:
        """Check if the entity is authenticated."""
        return self.authenticated


def format_user_name(data: dict) -> Optional[str]:
    """
    Formats the user's name from provided data by concatenating the first and last name.

    Parameters:
        data (dict): Dictionary containing user details, specifically `externalDetails` and `person`.

    Returns:
        Optional[str]: The formatted full name or None if data is missing required fields.
    """
    try:
        external_details = data[DATA_USER].get('externalDetails')
        if external_details and 'person' in external_details:
            person = external_details['person']
            return f"{person['firstName']} {person['lastName']}"
    except KeyError as e:
        _LOGGER.error("Missing data key: %s", e)
    return None


SENSOR_TYPES: tuple[FrankEnergieEntityDescription, ...] = (
    FrankEnergieEntityDescription(
        key="elec_markup",
        name="Current electricity price (All-in)",
        translation_key="current_electricity_price",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data[DATA_ELECTRICITY].current_hour.total
        if data[DATA_ELECTRICITY].current_hour else None,
        attr_fn=lambda data: {"prices": data[DATA_ELECTRICITY].asdict(
            "total", timezone="Europe/Amsterdam")
        }
    ),
    FrankEnergieEntityDescription(
        key="elec_market",
        name="Current electricity market price",
        translation_key="current_electricity_marketprice",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: data[DATA_ELECTRICITY].current_hour.market_price
        if data[DATA_ELECTRICITY].current_hour else None,
        attr_fn=lambda data: {
            "prices": data[DATA_ELECTRICITY].asdict("market_price")
        }
    ),
    FrankEnergieEntityDescription(
        key="elec_tax",
        name="Current electricity price including tax",
        translation_key="current_electricity_price_incl_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.market_price_with_tax
            if data[DATA_ELECTRICITY].current_hour else None
        ),
        attr_fn=lambda data: {
            "prices": data[DATA_ELECTRICITY].asdict("market_price_with_tax")
        }
    ),
    FrankEnergieEntityDescription(
        key="elec_tax_vat",
        name="Current electricity VAT price",
        translation_key="current_electricity_tax_price",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.market_price_tax
            if data[DATA_ELECTRICITY].current_hour else None
        ),
        attr_fn=lambda data: {
            'prices': data[DATA_ELECTRICITY].asdict('market_price_tax')
        },
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_sourcing",
        name="Current electricity sourcing markup",
        translation_key="current_electricity_sourcing_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data:
            data[DATA_ELECTRICITY].current_hour.sourcing_markup_price
        if data[DATA_ELECTRICITY].current_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_tax_only",
        name="Current electricity tax only",
        translation_key="elec_tax_only",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=5,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data:
            data[DATA_ELECTRICITY].current_hour.energy_tax_price
        if data[DATA_ELECTRICITY].current_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_fixed_kwh",
        name="Fixed electricity cost kWh",
        translation_key="elec_fixed_kwh",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=6,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.sourcing_markup_price
            + data[DATA_ELECTRICITY].current_hour.energy_tax_price  # noqa: W503
        ) if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_var_kwh",
        name="Variable electricity cost kWh",
        translation_key="elec_var_kwh",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=6,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.market_price_with_tax
        ) if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_markup",
        name="Current gas price (All-in)",
        translation_key="gas_markup",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: data[DATA_GAS].current_hour.total
        if data[DATA_GAS].current_hour else None,
        attr_fn=lambda data: {"prices": data[DATA_GAS].asdict("total")}
    ),
    FrankEnergieEntityDescription(
        key="gas_market",
        name="Current gas market price",
        translation_key="gas_market",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: data[DATA_GAS].current_hour.market_price
        if data[DATA_GAS].current_hour else None,
        attr_fn=lambda data: {"prices": data[DATA_GAS].asdict("market_price")}
    ),
    FrankEnergieEntityDescription(
        key="gas_tax",
        name="Current gas price including tax",
        translation_key="gas_tax",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: data[DATA_GAS].current_hour.market_price_with_tax
        if data[DATA_GAS].current_hour else None,
        attr_fn=lambda data: {
            "prices": data[DATA_GAS].asdict("market_price_with_tax")},
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_tax_vat",
        name="Current gas VAT price",
        translation_key="gas_tax_vat",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: data[DATA_GAS].current_hour.market_price_tax
        if data[DATA_GAS].current_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_sourcing",
        name="Current gas sourcing price",
        translation_key="gas_sourcing",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: data[DATA_GAS].current_hour.sourcing_markup_price
        if data[DATA_GAS].current_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_tax_only",
        name="Current gas tax only",
        translation_key="gas_tax_only",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: data[DATA_GAS].current_hour.energy_tax_price
        if data[DATA_GAS].current_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_min",
        name="Lowest gas price today (All-in)",
        translation_key="gas_min",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        value_fn=lambda data: data[DATA_GAS].today_min.total
        if data[DATA_GAS].today_min else None,
        attr_fn=lambda data: {ATTR_TIME: data[DATA_GAS].today_min.date_from}
    ),
    FrankEnergieEntityDescription(
        key="gas_max",
        name="Highest gas price today (All-in)",
        translation_key="gas_max",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        value_fn=lambda data: data[DATA_GAS].today_max.total
        if data[DATA_GAS].today_max else None,
        attr_fn=lambda data: {ATTR_TIME: data[DATA_GAS].today_max.date_from}
    ),
    FrankEnergieEntityDescription(
        key="elec_min",
        name="Lowest electricity price today (All-in)",
        translation_key="elec_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_min.total
        if data[DATA_ELECTRICITY].today_min else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].today_min.date_from}
    ),
    FrankEnergieEntityDescription(
        key="elec_max",
        name="Highest electricity price today (All-in)",
        translation_key="elec_max",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_max.total
        if data[DATA_ELECTRICITY].today_max else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].today_max.date_from}
    ),
    FrankEnergieEntityDescription(
        key="elec_avg",
        name="Average electricity price today (All-in)",
        translation_key="average_electricity_price_today_all_in",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].today_avg
        ),
        attr_fn=lambda data: {
            'prices': data[DATA_ELECTRICITY].asdict(
                'total', today_only=True, timezone="Europe/Amsterdam"
            )
        }
    ),
    FrankEnergieEntityDescription(
        key="elec_previoushour",
        name="Previous hour electricity price (All-in)",
        translation_key="elec_previoushour",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].previous_hour.total
            if data[DATA_ELECTRICITY].previous_hour else None
        ),
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_nexthour",
        name="Next hour electricity price (All-in)",
        translation_key="elec_nexthour",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].next_hour.total
            if data[DATA_ELECTRICITY].next_hour else None
        ),
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_market_percent_tax",
        name="Electricity market percent tax",
        translation_key="elec_market_percent_tax",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:percent",
        value_fn=lambda data: (
            100 / (
                data[DATA_ELECTRICITY].current_hour.market_price /
                data[DATA_ELECTRICITY].current_hour.market_price_tax
            )
            if (
                data[DATA_ELECTRICITY].current_hour and
                data[DATA_ELECTRICITY].current_hour.market_price != 0 and
                data[DATA_ELECTRICITY].current_hour.market_price_tax != 0
            ) else 21
        ),
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_market_percent_tax",
        name="Gas market percent tax",
        translation_key="gas_market_percent_tax",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:percent",
        value_fn=lambda data: (
            100 / (
                data[DATA_GAS].current_hour.market_price /
                data[DATA_GAS].current_hour.market_price_tax
            )
            if (
                data[DATA_GAS].current_hour and
                data[DATA_GAS].current_hour.market_price != 0 and
                data[DATA_GAS].current_hour.market_price_tax != 0
            ) else 21
        ),
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_all_min",
        name="Lowest electricity price all hours (All-in)",
        translation_key="elec_all_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].all_min.total,
        suggested_display_precision=4,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].all_min.date_from}
    ),
    FrankEnergieEntityDescription(
        key="elec_all_max",
        name="Highest electricity price all hours (All-in)",
        translation_key="elec_all_max",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].all_max.total,
        suggested_display_precision=4,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].all_max.date_from}
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_min",
        name="Lowest electricity price tomorrow (All-in)",
        translation_key="elec_tomorrow_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_min.total
        if data[DATA_ELECTRICITY].tomorrow_min else None,
        suggested_display_precision=4,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].tomorrow_min.date_from}
        if data[DATA_ELECTRICITY].tomorrow_min else {}
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_max",
        name="Highest electricity price tomorrow (All-in)",
        translation_key="elec_tomorrow_max",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_max.total
        if data[DATA_ELECTRICITY].tomorrow_max else None,
        suggested_display_precision=4,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].tomorrow_max.date_from}
        if data[DATA_ELECTRICITY].tomorrow_max else {}
    ),
    FrankEnergieEntityDescription(
        key="elec_upcoming_min",
        name="Lowest electricity price upcoming hours (All-in)",
        translation_key="elec_upcoming_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_min.total,
        suggested_display_precision=4,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].upcoming_min.date_from}
    ),
    FrankEnergieEntityDescription(
        key="elec_upcoming_max",
        name="Highest electricity price upcoming hours (All-in)",
        translation_key="elec_upcoming_max",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_max.total,
        suggested_display_precision=4,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].upcoming_max.date_from}
    ),
    FrankEnergieEntityDescription(
        key="elec_avg_tax",
        name="Average electricity price today including tax",
        translation_key="average_electricity_price_today_including_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_tax_avg,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_avg_tax_markup",
        name="Average electricity price today including tax and markup",
        translation_key="average_electricity_price_today_including_tax_and_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_tax_markup_avg,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_avg_market",
        name="Average electricity market price today",
        translation_key="average_electricity_market_price_today",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_market_avg,
        suggested_display_precision=3
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg_tax_markup",
        name="Average electricity price tomorrow including tax and markup",
        translation_key="average_electricity_price_tomorrow_including_tax_and_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_average_price_including_tax_and_markup
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg",
        name="Average electricity price tomorrow (All-in)",
        translation_key="average_electricity_price_tomorrow_all_in",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_average_price
        # value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_avg.total
        if data[DATA_ELECTRICITY].tomorrow_avg else None,
        attr_fn=lambda data: {'tomorrow_prices': data[DATA_ELECTRICITY].asdict(
            'total', tomorrow_only=True, timezone="Europe/Amsterdam")}
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg_tax",
        name="Average electricity price tomorrow including tax",
        translation_key="average_electricity_price_tomorrow_including_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_average_price_including_tax
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg_market",
        name="Average electricity market price tomorrow",
        translation_key="average_electricity_market_price_tomorrow",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_average_market_price
    ),
    FrankEnergieEntityDescription(
        key="elec_market_upcoming",
        name="Average electricity market price upcoming",
        translation_key="average_electricity_market_price_upcoming",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_avg.marketPrice
        if data[DATA_ELECTRICITY].upcoming_avg else None,
        attr_fn=lambda data: {'upcoming_prices': data[DATA_ELECTRICITY].asdict(
            'marketPrice', upcoming_only=True)
        }
        if data[DATA_ELECTRICITY].upcoming_avg else {}
    ),
    FrankEnergieEntityDescription(
        key="elec_upcoming",
        name="Average electricity price upcoming (All-in)",
        translation_key="average_electricity_price_upcoming_market",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_avg.total
        if data[DATA_ELECTRICITY].upcoming_avg else None,
        attr_fn=lambda data: {'upcoming_prices': data[DATA_ELECTRICITY].asdict(
            'total', upcoming_only=True, timezone="Europe/Amsterdam")},
        # attr_fn=lambda data: data[DATA_ELECTRICITY].upcoming_attr,
    ),
    FrankEnergieEntityDescription(
        key="elec_all",
        name="Average electricity price all hours (All-in)",
        translation_key="elec_all",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_ELECTRICITY].all_avg.total
        if data[DATA_ELECTRICITY].all_avg else None,
        attr_fn=lambda data: {'all_prices': data[DATA_ELECTRICITY].asdict(
            'total', timezone="Europe/Amsterdam")}
        if data[DATA_ELECTRICITY].all_avg else {},
        # attr_fn=lambda data: data[DATA_ELECTRICITY].all_attr,
    ),
    FrankEnergieEntityDescription(
        key="elec_tax_markup",
        name="Current electricity price including tax and markup",
        translation_key="current_electricity_price_incl_tax_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].current_hour.market_price_including_tax_and_markup
        if data[DATA_ELECTRICITY].current_hour else None,
        suggested_display_precision=3,
        attr_fn=lambda data: {'prices': data[DATA_ELECTRICITY].asdict(
            'market_price_including_tax_and_markup')
        }
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg",
        name="Average gas price tomorrow (All-in)",
        translation_key="gas_tomorrow_avg_all_in",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].tomorrow_average_price,
        # value_fn=lambda data: data[DATA_GAS].tomorrow_avg.total,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_tax_markup",
        name="Current gas price including tax and markup",
        translation_key="gas_tax_markup",
        suggested_display_precision=3,
        native_unit_of_measurement=UNIT_GAS,
        value_fn=lambda data: data[DATA_GAS].current_hour.market_price_including_tax_and_markup
        if data[DATA_ELECTRICITY].current_hour else None,
        attr_fn=lambda data: {'prices': data[DATA_GAS].asdict(
            'market_price_including_tax_and_markup')},
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_hourcount",
        name="Number of hours with electricity prices loaded",
        translation_key="elec_hourcount",
        icon="mdi:numeric-0-box-multiple",
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data[DATA_ELECTRICITY].length,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_hourcount",
        name="Number of hours with gas prices loaded",
        translation_key="gas_hourcount",
        icon="mdi:numeric-0-box-multiple",
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data[DATA_GAS].length,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_previoushour_market",
        name="Previous hour electricity market price",
        translation_key="elec_previoushour_market",
        suggested_display_precision=3,
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].previous_hour.market_price
        if data[DATA_ELECTRICITY].previous_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_nexthour_market",
        name="Next hour electricity market price",
        translation_key="elec_nexthour_market",
        suggested_display_precision=3,
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].next_hour.market_price
        if data[DATA_ELECTRICITY].next_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_previoushour_all_in",
        name="Previous hour gas price (All-in)",
        translation_key="gas_previoushour_all_in",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].previous_hour.total
        if data[DATA_GAS].previous_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_nexthour_all_in",
        name="Next hour gas price (All-in)",
        translation_key="gas_nexthour_all_in",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].next_hour.total
        if data[DATA_GAS].next_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_previoushour_market",
        name="Previous hour gas market price",
        translation_key="gas_previoushour_market",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].previous_hour.marketPrice
        if data[DATA_GAS].previous_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_nexthour_market",
        name="Next hour gas market price",
        translation_key="gas_nexthour_market",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].next_hour.marketPrice
        if data[DATA_GAS].next_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg_market",
        name="Average gas market price tomorrow",
        translation_key="gas_tomorrow_avg_market",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].tomorrow_prices_market
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg_market_tax",
        name="Average gas market price incl tax tomorrow",
        translation_key="gas_tomorrow_avg_market_tax",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].tomorrow_prices_market_tax
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg_market_tax_markup",
        name="Average gas market price incl tax and markup tomorrow",
        translation_key="gas_tomorrow_avg_market_tax_markup",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].tomorrow_prices_market_tax_markup
    ),
    FrankEnergieEntityDescription(
        key="gas_today_avg_all_in",
        name="Average gas price today (All-in)",
        translation_key="gas_today_avg_all_in",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].today_prices_total
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg_all_in",
        name="Average gas price tomorrow (All-in)",
        translation_key="gas_tomorrow_avg_all_in",
        native_unit_of_measurement=UNIT_GAS,
        value_fn=lambda data: data[DATA_GAS].tomorrow_prices_total
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_min",
        name="Lowest gas price tomorrow (All-in)",
        translation_key="gas_tomorrow_min",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        value_fn=lambda data: data[DATA_GAS].tomorrow_min.total
        if data[DATA_GAS].tomorrow_min
        else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_GAS].tomorrow_min.date_from
            if data[DATA_GAS].tomorrow_min
            else None
        }
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_max",
        name="Highest gas price tomorrow (All-in)",
        translation_key="gas_tomorrow_max",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        value_fn=lambda data: data[DATA_GAS].tomorrow_max.total
        if data[DATA_GAS].tomorrow_max
        else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_GAS].tomorrow_max.date_from
            if data[DATA_GAS].tomorrow_max
            else None
        }
    ),
    FrankEnergieEntityDescription(
        key="gas_market_upcoming",
        name="Average gas market price upcoming hours",
        translation_key="gas_market_upcoming",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: data[DATA_GAS].upcoming_avg.marketPrice
        if data[DATA_GAS].upcoming_avg else None,
        attr_fn=lambda data: {
            'prices': data[DATA_GAS].asdict('marketPrice')
        }
    ),
    FrankEnergieEntityDescription(
        key="gas_upcoming_min",
        name="Lowest gas price upcoming hours (All-in)",
        translation_key="gas_upcoming_min",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        value_fn=lambda data: data[DATA_GAS].upcoming_min.total,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_GAS].upcoming_min.date_from
        }
        if data[DATA_ELECTRICITY].upcoming_min else {},
    ),
    FrankEnergieEntityDescription(
        key="gas_upcoming_max",
        name="Highest gas price upcoming hours (All-in)",
        translation_key="gas_upcoming_max",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        value_fn=lambda data: data[DATA_GAS].upcoming_max.total,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_GAS].upcoming_max.date_from
        }
    ),
    FrankEnergieEntityDescription(
        key="average_electricity_price_upcoming_all_in",
        name="Average electricity price upcoming (All-in)",
        translation_key="average_electricity_price_upcoming_all_in",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].upcoming_avg.total
            if data[DATA_ELECTRICITY].upcoming_avg else None
        ),
        attr_fn=lambda data: (
            {
                "Number of hours": len(data[DATA_ELECTRICITY].upcoming_avg.values),
                'average_electricity_price_upcoming_all_in': data[DATA_ELECTRICITY].upcoming_avg.total,
                'average_electricity_market_price_including_tax_and_markup_upcoming': (
                    data[DATA_ELECTRICITY].upcoming_avg.market_price_with_tax_and_markup
                ),
                'average_electricity_market_markup_price': (
                    data[DATA_ELECTRICITY].upcoming_avg.market_markup_price
                ),
                'average_electricity_market_price_including_tax_upcoming': (
                    data[DATA_ELECTRICITY].upcoming_avg.market_price_with_tax
                ),
                'average_electricity_market_price_tax_upcoming': (
                    data[DATA_ELECTRICITY].upcoming_avg.marketPriceTax
                ),
                'average_electricity_market_price_upcoming': (
                    data[DATA_ELECTRICITY].upcoming_avg.marketPrice
                ),
                'upcoming_prices': data[DATA_ELECTRICITY].asdict(
                    'total', upcoming_only=True, timezone="Europe/Amsterdam"
                ),
            }
            if data[DATA_ELECTRICITY].upcoming_avg else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="average_electricity_price_upcoming_market",
        name="Average electricity price (upcoming, market)",
        translation_key="average_electricity_price_upcoming_market",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_market_avg
        if data[DATA_ELECTRICITY].upcoming_avg else None,
        suggested_display_precision=3,
        attr_fn=lambda data: {
            'average_electricity_price_upcoming_market': data[DATA_ELECTRICITY].upcoming_market_avg,
            'upcoming_market_prices': data[DATA_ELECTRICITY].asdict('marketPrice', upcoming_only=True)
        }
    ),
    FrankEnergieEntityDescription(
        key="average_electricity_price_upcoming_market_tax",
        name="Average electricity price (upcoming, market and tax)",
        translation_key="average_electricity_price_upcoming_market_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_market_tax_avg
        if data[DATA_ELECTRICITY].upcoming_avg else None,
        suggested_display_precision=3,
        attr_fn=lambda data: {
            'average_electricity_price_upcoming_market_tax': data[DATA_ELECTRICITY].upcoming_market_tax_avg,
            'upcoming_market_tax_prices': data[DATA_ELECTRICITY].asdict('market_price_with_tax', upcoming_only=True)
        }
    ),
    FrankEnergieEntityDescription(
        key="average_electricity_price_upcoming_market_tax_markup",
        name="Average electricity price (upcoming, market, tax and markup)",
        translation_key="average_electricity_price_upcoming_market_tax_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_market_tax_markup_avg
        if data[DATA_ELECTRICITY] else None,
        suggested_display_precision=3,
        attr_fn=lambda data: {
            'average_electricity_price_upcoming_market_tax_markup':
                data[DATA_ELECTRICITY].upcoming_market_tax_markup_avg}
        if data[DATA_ELECTRICITY].upcoming_market_tax_markup_avg else {},
    ),
    FrankEnergieEntityDescription(
        key="gas_markup_before6am",
        name="Gas price before 6AM (All-in)",
        translation_key="gas_markup_before6am",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: sum(
            data[DATA_GAS].today_gas_before6am) / len(data[DATA_GAS].today_gas_before6am),
        attr_fn=lambda data: {"Number of hours": len(
            data[DATA_GAS].today_gas_before6am)}
    ),
    FrankEnergieEntityDescription(
        key="gas_markup_after6am",
        name="Gas price after 6AM (All-in)",
        translation_key="gas_markup_after6am",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: sum(
            data[DATA_GAS].today_gas_after6am) / len(data[DATA_GAS].today_gas_after6am),
        attr_fn=lambda data: {"Number of hours": len(
            data[DATA_GAS].today_gas_after6am)}
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_before6am",
        name="Gas price tomorrow before 6AM (All-in)",
        translation_key="gas_price_tomorrow_before6am_allin",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: (sum(data[DATA_GAS].tomorrow_gas_before6am) / len(
            data[DATA_GAS].tomorrow_gas_before6am))
        if data[DATA_GAS].tomorrow_gas_before6am else None,
        attr_fn=lambda data: {"Number of hours": len(
            data[DATA_GAS].tomorrow_gas_before6am)}
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_after6am",
        name="Gas price tomorrow after 6AM (All-in)",
        translation_key="gas_price_tomorrow_after6am_allin",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        value_fn=lambda data: (sum(data[DATA_GAS].tomorrow_gas_after6am) / len(
            data[DATA_GAS].tomorrow_gas_after6am))
        if data[DATA_GAS].tomorrow_gas_after6am else None,
        attr_fn=lambda data: {"Number of hours": len(
            data[DATA_GAS].tomorrow_gas_after6am)}
    ),
    # FrankEnergieEntityDescription(
    #     key="energy_consumption_sensor",
    #     name="Energy consumption (todo)",
    #     translation_key="",
    #     device_class=SensorDeviceClass.ENERGY,
    #     state_class=SensorStateClass.TOTAL,
    #     native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    #     suggested_display_precision=1,
    #     authenticated=True,
    #     service_name=SERVICE_NAME_USAGE,
    #     entity_registry_enabled_default=False,
    #     value_fn=lambda data: async_get_energy_consumption_data(
    #         hass, data[USER_ID]
    #     ),
    #     attr_fn=lambda data: {
    #         "Energy usage": async_get_energy_consumption_data(hass, data[USER_ID])
    #     },
    # ),
    FrankEnergieEntityDescription(
        key="actual_costs_until_last_meter_reading_date",
        name="Actual monthly cost",
        translation_key="actual_costs_until_last_meter_reading_date",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[
            DATA_MONTH_SUMMARY
        ].actualCostsUntilLastMeterReadingDate,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate
        }
    ),
    FrankEnergieEntityDescription(
        key="expected_costs_until_last_meter_reading_date",
        name="Expected monthly cost until now",
        translation_key="expected_costs_until_last_meter_reading_date",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[
            DATA_MONTH_SUMMARY
        ].expectedCostsUntilLastMeterReadingDate,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate
        }
    ),
    FrankEnergieEntityDescription(
        key="difference_costs_until_last_meter_reading_date",
        name="Difference expected and actual monthly cost until now",
        translation_key="difference_costs_until_last_meter_reading_date",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[
            DATA_MONTH_SUMMARY
        ].differenceUntilLastMeterReadingDate,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate
        }
    ),
    FrankEnergieEntityDescription(
        key="difference_costs_per_day",
        name="Difference expected and actual cost per day",
        translation_key="difference_costs_per_day",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[
            DATA_MONTH_SUMMARY
        ].differenceUntilLastMeterReadingDateAvg,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate
        }
    ),
    FrankEnergieEntityDescription(
        key="expected_costs_this_month",
        name="Expected cost this month",
        translation_key="expected_costs_this_month",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].expectedCosts,
        attr_fn=lambda data: {
            "Description": data[DATA_INVOICES].currentPeriodInvoice.PeriodDescription,
        }
    ),
    FrankEnergieEntityDescription(
        key="expected_costs_per_day_this_month",
        name="Expected cost per day this month",
        translation_key="expected_costs_per_day_this_month",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].expectedCostsPerDay,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate,
            "Description": data[DATA_INVOICES].currentPeriodInvoice.PeriodDescription,
        }
    ),
    FrankEnergieEntityDescription(
        key="costs_per_day_till_now_this_month",
        name="Cost per day till now this month",
        translation_key="costs_per_day_till_now_this_month",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].CostsPerDayTillNow,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate,
            "Description": data[DATA_INVOICES].currentPeriodInvoice.PeriodDescription,
        }
    ),
    FrankEnergieEntityDescription(
        key="invoice_previous_period",
        name="Invoice previous period",
        translation_key="invoice_previous_period",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].previousPeriodInvoice.TotalAmount
        if data[DATA_INVOICES].previousPeriodInvoice
        else None,
        attr_fn=lambda data: {
            "Start date": data[DATA_INVOICES].previousPeriodInvoice.StartDate,
            "Description": data[DATA_INVOICES].previousPeriodInvoice.PeriodDescription,
        }
    ),
    FrankEnergieEntityDescription(
        key="invoice_current_period",
        name="Invoice current period",
        translation_key="invoice_current_period",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].currentPeriodInvoice.TotalAmount
        if data[DATA_INVOICES].currentPeriodInvoice
        else None,
        attr_fn=lambda data: {
            "Start date": data[DATA_INVOICES].currentPeriodInvoice.StartDate,
            "Description": data[DATA_INVOICES].currentPeriodInvoice.PeriodDescription,
        }
    ),
    FrankEnergieEntityDescription(
        key="invoice_upcoming_period",
        name="Invoice upcoming period",
        translation_key="invoice_upcoming_period",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].upcomingPeriodInvoice.TotalAmount
        if data[DATA_INVOICES].upcomingPeriodInvoice
        else None,
        attr_fn=lambda data: {
            "Start date": data[DATA_INVOICES].upcomingPeriodInvoice.StartDate,
            "Description": data[DATA_INVOICES].upcomingPeriodInvoice.PeriodDescription,
        }
    ),
    FrankEnergieEntityDescription(
        key="costs_this_year",
        name="Costs this year",
        translation_key="costs_this_year",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].TotalCostsThisYear
        if data[DATA_INVOICES].TotalCostsThisYear
        else None,
        attr_fn=lambda data: {
            'Invoices': data[DATA_INVOICES].AllInvoicesDictForThisYear
        }
    ),
    FrankEnergieEntityDescription(
        key="total_costs",
        name="Total costs",
        translation_key="total_costs",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: sum(
            invoice.TotalAmount for invoice in data[DATA_INVOICES].allPeriodsInvoices
        )
        if data[DATA_INVOICES].allPeriodsInvoices
        else None,
        attr_fn=lambda data: {
            "Invoices": data[DATA_INVOICES].AllInvoicesDict,
            **{
                label: parsed_date.strftime(FORMAT_DATE)
                for label, field in {
                    "First meter reading": "firstMeterReadingDate",
                    "Last meter reading": "lastMeterReadingDate",
                }.items()
                if (value := getattr(data[DATA_USER], field, None))
                and (parsed_date := dt.parse_date(value)) is not None
            },
        },
    ),
    FrankEnergieEntityDescription(
        key="average_costs_per_month",
        name="Average costs per month",
        translation_key="average_costs_per_month",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].calculate_average_costs_per_month(
        )
        if data[DATA_INVOICES].allPeriodsInvoices
        else None
    ),
    FrankEnergieEntityDescription(
        key="average_costs_per_year",
        name="Average costs per year",
        translation_key="average_costs_per_year",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: (
            data[DATA_INVOICES].calculate_average_costs_per_year()
            if data[DATA_INVOICES].allPeriodsInvoices
            else None
        ),
        attr_fn=lambda data: {
            'Total amount': sum(invoice.TotalAmount for invoice in data[DATA_INVOICES].allPeriodsInvoices),
            'Number of years': len(data[DATA_INVOICES].get_all_invoices_dict_per_year()),
            'Invoices': data[DATA_INVOICES].get_all_invoices_dict_per_year(),
        },
    ),
    FrankEnergieEntityDescription(
        key="average_costs_per_year_corrected",
        name="Average costs per year (corrected)",
        translation_key="average_costs_per_year_corrected",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].calculate_average_costs_per_month(
        ) * 12
        if data[DATA_INVOICES].allPeriodsInvoices
        else None,
        attr_fn=lambda data: {
            'Month average': data[DATA_INVOICES].calculate_average_costs_per_month(),
            'Invoices': data[DATA_INVOICES].get_all_invoices_dict_per_year()}
    ),
    FrankEnergieEntityDescription(
        key="average_costs_per_month_previous_year",
        name="Average costs per month previous year",
        translation_key="average_costs_per_month_previous_year",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].calculate_average_costs_per_month(
            dt.now().year - 1)
        if data[DATA_INVOICES].allPeriodsInvoices
        else None
    ),
    FrankEnergieEntityDescription(
        key="average_costs_per_month_this_year",
        name="Average costs per month this year",
        translation_key="average_costs_per_month_this_year",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].calculate_average_costs_per_month(
            dt.now().year)
        if data[DATA_INVOICES].allPeriodsInvoices
        else None
    ),
    FrankEnergieEntityDescription(
        key="expected_costs_this_year",
        name="Expected costs this year",
        translation_key="expected_costs_this_year",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].calculate_expected_costs_this_year(
        )
        if data[DATA_INVOICES].allPeriodsInvoices
        else None
    ),
    FrankEnergieEntityDescription(
        key="costs_previous_year",
        name="Costs previous year",
        translation_key="costs_previous_year",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: data[DATA_INVOICES].TotalCostsPreviousYear
        if data[DATA_INVOICES].TotalCostsPreviousYear
        else None,
        attr_fn=lambda data: {
            'Invoices': data[DATA_INVOICES].AllInvoicesDictForPreviousYear}
    ),
    FrankEnergieEntityDescription(
        key="costs_elelectricity_yesterday",
        name="Costs elelectricity yesterday",
        translation_key="costs_elelectricity_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        value_fn=lambda data: data[DATA_USAGE].electricity.costs_total
        if data[DATA_USAGE].electricity
        else None,
        attr_fn=lambda data: {
            "Electricity costs yesterday": data[DATA_USAGE].electricity
        } if data[DATA_USAGE].electricity else {}
    ),
    FrankEnergieEntityDescription(
        key="usage_elelectricity_yesterday",
        name="Usage elelectricity yesterday",
        translation_key="usage_elelectricity_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        value_fn=lambda data: data[DATA_USAGE].electricity.usage_total
        if data[DATA_USAGE].electricity
        else None,
        attr_fn=lambda data: {
            "Electricity usage yesterday": data[DATA_USAGE].electricity
        } if data[DATA_USAGE].electricity else {}
    ),
    FrankEnergieEntityDescription(
        key="costs_gas_yesterday",
        name="Costs gas yesterday",
        translation_key="costs_elelectricity_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        value_fn=lambda data: data[DATA_USAGE].gas.costs_total
        if data[DATA_USAGE].gas
        else None,
        attr_fn=lambda data: {
            "Gas costs yesterday": data[DATA_USAGE].gas
        } if data[DATA_USAGE].gas else {}
    ),
    FrankEnergieEntityDescription(
        key="usage_gas_yesterday",
        name="Usage gas yesterday",
        translation_key="usage_gas_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        value_fn=lambda data: data[DATA_USAGE].gas.usage_total
        if data[DATA_USAGE].gas
        else None,
        attr_fn=lambda data: {
            "Gas usage yesterday": data[DATA_USAGE].gas
        } if data[DATA_USAGE].gas else {}
    ),
    FrankEnergieEntityDescription(
        key="advanced_payment_amount",
        name="Advanced payment amount",
        translation_key="advanced_payment_amount",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].advancedPaymentAmount
        if data[DATA_USER].advancedPaymentAmount
        else None
    ),
    FrankEnergieEntityDescription(
        key="has_CO2_compensation",
        name="Has CO₂ compensation",
        translation_key="co2_compensation",
        icon="mdi:molecule-co2",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].hasCO2Compensation
        if data[DATA_USER].hasCO2Compensation
        else False
    ),
    FrankEnergieEntityDescription(
        key="reference",
        name="Reference",
        translation_key="reference",
        icon="mdi:numeric",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].reference
        if data[DATA_USER].reference
        else None,
        # attr_fn=lambda data: data[DATA_USER_SITES].delivery_sites
    ),
    FrankEnergieEntityDescription(
        key="status",
        name="Status",
        translation_key="status",
        icon="mdi:connection",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER_SITES].status
        if data[DATA_USER_SITES].status
        else None,
        attr_fn=lambda data: {
            'Connections status': next((connection['status']
                                        for connection in data[DATA_USER].connections
                                        if connection.get('status')), None
                                       )
        }
    ),
    FrankEnergieEntityDescription(
        key="propositionType",
        name="Proposition type",
        translation_key="proposition_type",
        icon="mdi:file-document-check",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER_SITES].propositionType
        if data[DATA_USER_SITES].propositionType
        else None
    ),
    FrankEnergieEntityDescription(
        key="countryCode",
        name="Country code",
        translation_key="country_code",
        icon="mdi:flag",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].countryCode
        if data[DATA_USER].countryCode
        else None
    ),
    FrankEnergieEntityDescription(
        key="bankAccountNumber",
        name="Bankaccount Number",
        translation_key="bank_account_number",
        icon="mdi:bank",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].externalDetails.debtor.bankAccountNumber
        if data[DATA_USER].externalDetails and data[DATA_USER].externalDetails.debtor else None
    ),
    FrankEnergieEntityDescription(
        key="preferredAutomaticCollectionDay",
        name="Preferred Automatic Collection Day",
        translation_key="preferred_automatic_collection_day",
        icon="mdi:bank",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].externalDetails.debtor.preferredAutomaticCollectionDay
        if data[DATA_USER].externalDetails and data[DATA_USER].externalDetails.debtor else None
    ),
    FrankEnergieEntityDescription(
        key="fullName",
        name="Full Name",
        translation_key="full_name",
        icon="mdi:form-textbox",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            f"{data[DATA_USER].externalDetails.person.firstName} {data[DATA_USER].externalDetails.person.lastName}"
            if data[DATA_USER].externalDetails and data[DATA_USER].externalDetails.person else None
        )
    ),
    FrankEnergieEntityDescription(
        key="phoneNumber",
        name="Phonenumber",
        translation_key="phone_number",
        icon="mdi:phone",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].externalDetails.contact.phoneNumber
            if data[DATA_USER].externalDetails and data[DATA_USER].externalDetails.contact else None
        )
    ),
    FrankEnergieEntityDescription(
        key="segments",
        name="Segments",
        translation_key="segments",
        icon="mdi:segment",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: ', '.join(
            data[DATA_USER_SITES].segments) if data[DATA_USER_SITES].segments else None
        if data[DATA_USER_SITES].segments
        else None
    ),
    FrankEnergieEntityDescription(
        key="gridOperator",
        name="Gridoperator",
        translation_key="grid_operator",
        icon="mdi:transmission-tower",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: next(
            (connection['externalDetails']['gridOperator']
                for connection in data[DATA_USER].connections
                if connection.get('externalDetails') and connection['externalDetails'].get('gridOperator')), None
        )
        if data[DATA_USER].connections
        else None
    ),
    FrankEnergieEntityDescription(
        key="EAN",
        name="EAN (Energy Account Number)",
        translation_key="EAN",
        icon="mdi:meter-electric",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: next(
            (connection['EAN'] for connection in data[DATA_USER].connections
                if connection.get('EAN')), None
        )
        if data[DATA_USER].connections
        else None
    ),
    FrankEnergieEntityDescription(
        key="meterType",
        name="Meter Type",
        translation_key="meter_type",
        icon="mdi:meter-electric",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: next(
            (connection['meterType'] for connection in data[DATA_USER].connections
                if connection.get('meterType')), None
        )
        if data[DATA_USER].connections
        else None
    ),
    FrankEnergieEntityDescription(
        key="contractStatus",
        name="Contract Status",
        translation_key="contract_status",
        icon="mdi:file-document-outline",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: next(
            (connection['contractStatus'] for connection in data[DATA_USER].connections
                if connection.get('contractStatus')), None
        )
        if data[DATA_USER].connections
        else None
    ),
    FrankEnergieEntityDescription(
        key="deliveryStartDate",
        name="Delivery start date",
        translation_key="delivery_start_date",
        icon="mdi:calendar-clock",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            parsed_date.strftime(FORMAT_DATE)
            if (parsed_date := dt.parse_date(data[DATA_USER_SITES].deliveryStartDate)) is not None
            else None
        )
    ),
    FrankEnergieEntityDescription(
        key="deliveryEndDate",
        name="Delivery end date",
        translation_key="delivery_end_date",
        icon="mdi:calendar-clock",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            parsed_date.strftime(FORMAT_DATE)
            if (parsed_date := dt.parse_date(data[DATA_USER_SITES].deliveryEndDate)) is not None
            else None
        )
    ),
    FrankEnergieEntityDescription(
        key="firstMeterReadingDate",
        name="First meter reading date",
        translation_key="first_meter_reading_date",
        icon="mdi:calendar-clock",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            parsed_date.strftime(FORMAT_DATE)
            if (parsed_date := dt.parse_date(data[DATA_USER_SITES].firstMeterReadingDate)) is not None
            else None
        )
    ),
    FrankEnergieEntityDescription(
        key="lastMeterReadingDate",
        name="Last meter reading date",
        translation_key="last_meter_reading_date",
        icon="mdi:calendar-clock",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            parsed_date.strftime(FORMAT_DATE)
            if (parsed_date := dt.parse_date(data[DATA_USER_SITES].lastMeterReadingDate)) is not None
            else None
        )
    ),
    FrankEnergieEntityDescription(
        key="treesCount",
        name="Trees count",
        translation_key="trees_count",
        icon="mdi:tree-outline",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].treesCount
        if data[DATA_USER].treesCount is not None
        else 0
    ),
    FrankEnergieEntityDescription(
        key="friendsCount",
        name="Friends count",
        translation_key="friends_count",
        icon="mdi:account-group",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].friendsCount
        if data[DATA_USER].friendsCount is not None
        else 0
    ),
    FrankEnergieEntityDescription(
        key="deliverySite",
        name="Delivery Site",
        translation_key="delivery_site",
        icon="mdi:home",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER_SITES].format_delivery_site_as_dict[0],
        # attr_fn=lambda data: next(
        #     iter(data[DATA_USER_SITES].delivery_site_as_dict.values()))
    ),
    FrankEnergieEntityDescription(
        key="rewardPayoutPreference",
        name="Reward payout preference",
        translation_key="reward_payout_preference",
        icon="mdi:trophy",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].UserSettings.get(
            "rewardPayoutPreference")
        if data[DATA_USER].UserSettings
        else None
    ),
    FrankEnergieEntityDescription(
        key="PushNotificationPriceAlerts",
        name="Push notification price alerts",
        translation_key="push_notification_price_alerts",
        icon="mdi:bell-alert",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].PushNotificationPriceAlerts[0]["isEnabled"]
        if data[DATA_USER].PushNotificationPriceAlerts
        else None
    ),
    FrankEnergieEntityDescription(
        key="smartPushNotifications",
        name="Smart Push notification price alerts",
        translation_key="smart_push_notification_price_alerts",
        icon="mdi:bell-alert",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].UserSettings.get(
            "smartPushNotifications")
        if data[DATA_USER].UserSettings.get(
            "smartPushNotifications")
        else None
    ),
    FrankEnergieEntityDescription(
        key="smartChargingisActivated",
        name="Smart Charging Activated",
        translation_key="smartcharging_isactivated",
        icon="mdi:ev-station",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].smartCharging.get('isActivated')
        if data[DATA_USER].smartCharging
        else None,
        attr_fn=lambda data: {
            'Provider': data[DATA_USER].smartCharging.get('provider'),
            'Available In Country': data[DATA_USER].smartCharging.get('isAvailableInCountry')
            if data[DATA_USER].smartCharging
            else []
        }
    )
)


class FrankEnergieSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Frank Energie sensor."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_icon = ICON
    _unsub_update: Callable[[], None] | None = None
    # _attr_suggested_display_precision = DEFAULT_ROUND
    # _attr_device_class = SensorDeviceClass.MONETARY
    # _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: FrankEnergieEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        self.entity_description: FrankEnergieEntityDescription = description
        # if description.translation_key:
        #    self._attr_name = _(description.translation_key)
        self._attr_unique_id = f"{entry.unique_id}.{description.key}"
        # Do not set extra identifier for default service, backwards compatibility
        device_info_identifiers: set[tuple[str, str]] = (
            {(DOMAIN, f"{entry.entry_id}")}
            if description.service_name is SERVICE_NAME_PRICES
            else {(DOMAIN, f"{entry.entry_id}_{description.service_name}")}
        )

        self._attr_device_info = DeviceInfo(
            identifiers=device_info_identifiers,
            name=f"{COMPONENT_TITLE} - {description.service_name}",
            translation_key=f"{COMPONENT_TITLE} - {description.service_name}",
            manufacturer=COMPONENT_TITLE,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=API_CONF_URL,
            model=description.service_name,
            sw_version=VERSION,
        )

        # Set defaults or exceptions for non default sensors.
        # self._attr_device_class = description.device_class or self._attr_device_class
        # self._attr_state_class = description.state_class or self._attr_state_class
        # self._attr_suggested_display_precision = description.suggested_display_precision
        # or self._attr_suggested_display_precision
        self._attr_icon = description.icon or self._attr_icon

        self._update_job = HassJob(self._handle_scheduled_update)
        self._unsub_update = None

        super().__init__(coordinator)

    async def async_update(self):
        """Get the latest data and updates the states."""
        try:
            data = self.coordinator.data
            self._attr_native_value = self.entity_description.value_fn(data)
        except (TypeError, IndexError, ValueError):
            # No data available
            self._attr_native_value = None
        except ZeroDivisionError as e:
            _LOGGER.error(
                "Division by zero error in FrankEnergieSensor: %s", e)
            self._attr_native_value = None
#        except Exception as e:
#            _LOGGER.error("Error updating FrankEnergieSensor: %s", e)
#            self._attr_native_value = None

        # Cancel the currently scheduled event if there is any
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None

        # Schedule the next update at exactly the next whole hour sharp
        next_update_time = utcnow().replace(minute=0, second=0) + timedelta(hours=1)
        self._unsub_update = event.async_track_point_in_utc_time(
            self.hass,
            self._update_job,
            next_update_time,
        )

    async def _handle_scheduled_update(self, _) -> None:
        """Handle a scheduled update."""
        # Only handle the scheduled update for entities which have a reference to hass,
        # which disabled sensors don't have.
        if self.hass is None:
            return

        self.async_schedule_update_ha_state(True)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the cached state attributes, if available."""
        if self.coordinator.data:
            return self.entity_description.attr_fn(self.coordinator.data)
        return {}

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Frank Energie sensor entries."""
    _LOGGER.debug("Setting up Frank Energie sensors for entry: %s",
                  config_entry.entry_id)

    coordinator: FrankEnergieCoordinator = hass.data[DOMAIN][config_entry.entry_id][CONF_COORDINATOR]
    # timezone = hass.config.time_zone
    # Add an entity for each sensor type, when authenticated is True,
    # only add the entity if the user is authenticated
    async_add_entities(
        [
            FrankEnergieSensor(coordinator, description, config_entry)
            for description in SENSOR_TYPES
            if not description.authenticated or coordinator.api.is_authenticated
        ],
        True,
    )
    _LOGGER.debug("Frank Ejsonsensors added for entry: %s",
                  config_entry.entry_id)

    # Add the service to update the data
    """Set up Frank Energie sensor entries."""
    _LOGGER.debug("Setting up Frank Energie sensors for entry: %s",
                  config_entry.entry_id)

    coordinator: FrankEnergieCoordinator = hass.data[DOMAIN][config_entry.entry_id][CONF_COORDINATOR]
    # timezone = hass.config.time_zone
    # Add an entity for each sensor type, when authenticated is True,
    # only add the entity if the user is authenticated
    async_add_entities(
        [
            FrankEnergieSensor(coordinator, description, config_entry)
            for description in SENSOR_TYPES
            if not description.authenticated or coordinator.api.is_authenticated
        ],
        True,
    )
    _LOGGER.debug("Frank Ejsonsensors added for entry: %s",
                  config_entry.entry_id)

    # Add the service to update the data
