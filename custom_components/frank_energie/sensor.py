"""Frank Energie current electricity and gas price information service.
Sensor platform for Frank Energie integration."""

# sensor.py
# -*- coding: utf-8 -*-
# VERSION = "2026.6.21"
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ClassVar, Final, Generic, Optional, TypeVar, Union
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CURRENCY_EURO,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import event
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import dt as dt_util
from python_frank_energie.models import EnodeCharger

from .const import (
    API_CONF_URL,
    ATTR_FROM_TIME,
    ATTR_LAST_UPDATE,
    ATTR_START_DATE,
    ATTR_TILL_TIME,
    ATTRIBUTION,
    COMPONENT_TITLE,
    DATA_BATTERIES,
    DATA_BATTERY_SESSIONS,
    DATA_CONTRACT_PRICE_RESOLUTION_STATE,
    DATA_ELECTRICITY,
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DATA_GAS,
    DATA_INVOICES,
    DATA_MONTH_SUMMARY,
    DATA_PV_SUMMARY,
    DATA_PV_SYSTEMS,
    DATA_REFRESH_TOKEN_EXPIRES_AT,
    DATA_TOKEN_EXPIRES_AT,
    DATA_USAGE,
    DATA_USER,
    DATA_USER_SITES,
    DOMAIN,
    ICON,
    PER_UNIT_TO_UNIT,
    SERVICE_NAME_BATTERIES,
    SERVICE_NAME_BATTERY_SESSIONS,
    SERVICE_NAME_COSTS,
    SERVICE_NAME_ENODE_CHARGERS,
    SERVICE_NAME_ENODE_VEHICLES,
    SERVICE_NAME_GAS_PRICES,
    SERVICE_NAME_PRICES,
    SERVICE_NAME_USAGE,
    SERVICE_NAME_USER,
    TIMEZONE_AMSTERDAM,
    UNIT_ELECTRICITY,
    UNIT_GAS,
    UNIT_GAS_NL,
    VERSION,
)
from .coordinator import (
    FrankEnergieBatterySessionCoordinator,
    FrankEnergieCoordinator,
    FrankEnergieData,
    SmartBatterySessions,
)
from .helpers import device_translation_key
from .statistics import lowest_window

_DataT = TypeVar("_DataT")
_LOGGER = logging.getLogger(__name__)
FORMAT_DATE = "%d-%m-%Y"


def _get_gas_unit(per_unit: str | None) -> str:
    """Return the Home Assistant gas unit for an API perUnit value."""

    if per_unit is None:
        return UNIT_GAS_NL

    unit = PER_UNIT_TO_UNIT.get(per_unit.upper())

    if unit is None:
        _LOGGER.warning(
            "Unsupported gas perUnit value received from API: %s",
            per_unit,
        )
        return UNIT_GAS_NL

    return unit


def _format_battery_date(date_val) -> datetime | None:
    """Parse a battery session date value (str or date) into a timezone-aware datetime.

    Accepts either a raw ISO date string (e.g. "2024-01-15") or a date/datetime
    object that already has a .replace() method.  Returns None when date_val is
    falsy so callers can use a simple ``if data`` guard.

    Returns a datetime (not a str) so that SensorDeviceClass.TIMESTAMP sensors
    receive a value with a .tzinfo attribute as required by Home Assistant.
    """
    tz = ZoneInfo(TIMEZONE_AMSTERDAM)
    if not date_val:
        return None
    if isinstance(date_val, str):
        return datetime.strptime(date_val, "%Y-%m-%d").replace(tzinfo=tz)
    if isinstance(date_val, datetime):
        return (
            date_val.astimezone(tz)
            if date_val.tzinfo is not None
            else date_val.replace(tzinfo=tz)
        )
    return datetime.combine(date_val, datetime.min.time(), tz)


def _parse_site_date(date_str: str | None) -> str | None:
    """Parse a delivery-site date string into FORMAT_DATE, or return None.

    Uses ``dt_util.parse_date`` to accept any ISO-8601 date string and formats
    the result as ``dd-mm-yyyy``.  Returns None when date_str is falsy or when
    parsing fails (parse_date returns None).
    """
    if not date_str:
        return None
    parsed = dt_util.parse_date(date_str)
    return parsed.strftime(FORMAT_DATE) if parsed is not None else None


def _parse_contract_date(date_val: datetime | str | None) -> str | None:
    """Parse a contract start date (str, datetime, or None) into FORMAT_DATE.

    Supports both pre-parsed datetime objects and raw date/datetime strings.
    """
    if not date_val:
        return None
    if isinstance(date_val, datetime):
        return dt_util.as_local(date_val).strftime(FORMAT_DATE)
    try:
        parsed = datetime.fromisoformat(str(date_val).replace("Z", "+00:00"))
        return dt_util.as_local(parsed).strftime(FORMAT_DATE)
    except (ValueError, TypeError):
        return None


# Battery session data type
BatterySessionData = SmartBatterySessions | None
BatterySessionCoordinator = DataUpdateCoordinator[BatterySessionData]

# Regular FrankEnergie data
FrankEnergieDataCoordinator = DataUpdateCoordinator[FrankEnergieData]


class FrankEnergieBaseSensor(
    CoordinatorEntity[DataUpdateCoordinator[_DataT]],
    SensorEntity,
    Generic[_DataT],
):
    """Base class for Frank Energie sensors with generic coordinator data."""

    _entity_description: FrankEnergieEntityDescription[_DataT]

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[_DataT],
        description: FrankEnergieEntityDescription[_DataT],
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._entity_description = description

    @callback
    def _handle_coordinator_update(self) -> None:
        _LOGGER.debug(
            "%s received coordinator update",
            self.entity_id,
        )
        super()._handle_coordinator_update()

    @property
    def entity_description(self) -> SensorEntityDescription:  # type: ignore[override]
        """Return the HA-compatible entity description."""
        return self._entity_description

    @property
    def native_value(self) -> StateType:  # type: ignore[override]
        """Return the sensor value from coordinator data."""
        value_fn = self._entity_description.value_fn
        assert value_fn is not None
        value = value_fn(self.coordinator.data)
        if isinstance(value, (str, int, float, datetime)) or value is None:
            return value
        raise TypeError(
            f"Invalid state type returned: {type(value).__name__}. "
            "Expected str | int | float | datetime | None."
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:  # type: ignore[override]
        """Return sensor attributes from coordinator data."""
        attr_fn = self._entity_description.attr_fn
        assert attr_fn is not None
        return attr_fn(self.coordinator.data)


@dataclass(frozen=True, slots=True)
class FrankEnergieBinaryEntityDescription(Generic[_DataT]):
    """Describes a dynamic Frank Energie binary sensor."""

    key: str
    """Unique key for the sensor."""

    name: str
    """Friendly name."""

    authenticated: bool = True
    """Whether the sensor requires authentication."""

    service_name: str | None = None
    """Service category or name."""

    icon: str | None = None
    """Material Design Icon."""

    device_class: BinarySensorDeviceClass | None = None
    """HA BinarySensorDeviceClass."""

    value_fn: Callable[[Any, _DataT], bool] | None = None
    """Callable returning sensor value."""

    attr_fn: Callable[[Any, _DataT], dict] | None = None
    """Callable returning additional attributes."""

    entity_registry_enabled_default: bool = True
    """Default entity registry visibility."""


@dataclass(frozen=True, slots=True)
class FrankEnergieEntityDescription(
    SensorEntityDescription,
    Generic[_DataT],
):
    """Describes a Frank Energie sensor entity.

    This description is used to define static sensor metadata.
    All statistics-relevant fields MUST be stable and defined upfront.
    """

    # Core metadata
    authenticated: bool = False
    service_name: str | None = SERVICE_NAME_PRICES

    # Function to extract the sensor value from the coordinator's data
    value_fn: Callable[[_DataT], StateType] = lambda _: STATE_UNKNOWN
    attr_fn: Callable[[_DataT], dict[str, object]] = lambda _: {}

    # Flags for filtering
    is_gas: bool = False
    is_electricity: bool = False
    is_feed_in: bool = False  # used to filter based on estimatedFeedIn
    is_battery_session: bool = False

    def __post_init__(self):
        # Convert string device_class to enum if necessary
        if isinstance(self.device_class, str):
            object.__setattr__(
                self, "device_class", SensorDeviceClass(self.device_class)
            )

        # Convert string entity_category to enum if necessary
        if isinstance(self.entity_category, str):
            object.__setattr__(
                self, "entity_category", EntityCategory(self.entity_category)
            )

        if not self.translation_key and not (self.key and self.key.startswith("test_")):
            object.__setattr__(self, "translation_key", self.key)

        # Provide default value_fn and attr_fn if not set, to ensure they are always callable
        if self.value_fn is None:
            object.__setattr__(self, "value_fn", lambda _: STATE_UNKNOWN)
        if self.attr_fn is None:
            object.__setattr__(self, "attr_fn", lambda data: {})

    def get_state(self, data: _DataT) -> StateType:
        """Get the state value from coordinator data."""
        return self.value_fn(data)

    def get_attributes(self, data: _DataT) -> dict[str, object]:
        """Get the additional attributes from coordinator data."""
        return self.attr_fn(data)

    @property
    def _attr_should_record(self) -> bool:
        """Prevent Recorder from storing large attributes."""
        return True  # main state recorded, attributes ignored

    @property
    def is_authenticated(self) -> bool:
        """Return whether this entity requires authentication."""
        return self.authenticated


@dataclass(frozen=True, kw_only=True)
class EnodeVehicleEntityDescription(SensorEntityDescription):
    """Describes a sensor for an Enode vehicle."""

    value_fn: Callable[[dict], object]
    attr_fn: Callable[[dict], dict] = field(default_factory=lambda: lambda _: {})
    unique_id_fn: Callable[[dict], str] | None = None
    authenticated: bool = False
    service_name: str = SERVICE_NAME_ENODE_VEHICLES
    translation_key: str | None = None

    def __init__(
        self,
        key: str,
        name: str,
        device_class: SensorDeviceClass | BinarySensorDeviceClass | None = None,
        state_class: SensorStateClass | None = None,
        native_unit_of_measurement: str | None = None,
        suggested_display_precision: int | None = None,
        authenticated: bool | None = False,
        service_name: Union[str, None] = None,
        value_fn: Callable[[dict[str, StateType]], StateType] | None = None,
        unique_id_fn: Callable[[dict], str] | None = None,
        attr_fn: Callable[[dict], dict[str, StateType | list | None]] = field(
            default_factory=lambda: lambda _: {}
        ),
        entity_registry_enabled_default: bool = True,
        entity_registry_visible_default: bool = True,
        entity_category: Union[str, EntityCategory] | None = None,
        translation_key: str | None = None,
        icon: str | None = None,
        options: list[str] | None = None,
        is_gas: bool = False,  # used externally for gas filtering
        is_electricity: bool = False,  # used externally for electricity filtering
        is_feed_in: bool = False,  # used to filter based on estimatedFeedIn
        is_battery_session: bool = False,  # used to indicate battery session sensors
    ) -> None:
        super().__init__(
            key=key,
            name=name,
            device_class=device_class,
            state_class=state_class,
            native_unit_of_measurement=native_unit_of_measurement,
            suggested_display_precision=suggested_display_precision,
            translation_key=translation_key or key,
            options=options,
            entity_category=EntityCategory(entity_category)
            if isinstance(entity_category, str)
            else entity_category,
        )
        object.__setattr__(self, "value_fn", value_fn or (lambda _: STATE_UNKNOWN))

    def get_state(self, data: dict[str, object]) -> StateType:
        """Return validated state value."""
        if self.value_fn is None:
            raise ValueError("value_fn is not configured")

        value = self.value_fn(data)

        if isinstance(value, (str, int, float)) or value is None:
            return value

        raise TypeError(
            f"Invalid state type returned: {type(value).__name__}. "
            "Expected str | int | float | None."
        )


@dataclass(frozen=True, kw_only=True)
class ChargerSensorDescription(SensorEntityDescription):
    """Describes a charger sensor entity."""

    value_fn: Callable[[EnodeCharger], StateType]
    authenticated: bool = False

    def __post_init__(self):
        if not self.translation_key:
            object.__setattr__(self, "translation_key", self.key)


class FrankEnergieBatterySessionSensor(
    CoordinatorEntity[DataUpdateCoordinator[SmartBatterySessions | None]],
    SensorEntity,
):
    """Sensor voor een enkele smart battery sessie."""

    def __init__(
        self,
        coordinator: FrankEnergieBatterySessionCoordinator | FrankEnergieCoordinator,
        description: FrankEnergieEntityDescription,
        battery_id: str | None = None,
        is_total: bool = False,
    ) -> None:
        """Initialiseer de sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._battery_id = battery_id
        self._attr_unique_id = (
            description.key if is_total else f"{battery_id}_{description.key}"
        )
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._is_total = is_total

    @property
    def native_value(self) -> StateType:
        """Return the native value of the sensor."""
        try:
            value = self.entity_description.value_fn(self.coordinator.data)

            if value is None:
                return None

            if isinstance(value, (int, float)):
                precision = self.entity_description.suggested_display_precision
                if precision is not None:
                    return round(value, precision)

            return value

        except Exception as err:
            self._logger().error(
                "Failed to get native value for %s: %s",
                self.entity_description.key,
                err,
            )
            return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        if not self.coordinator.data:
            return {}
        try:
            return self.entity_description.attr_fn(self.coordinator.data) or {}
        except Exception:
            _LOGGER.exception(
                "Failed to get attributes for %s", self.entity_description.key
            )
            return {}

    @property
    def device_info(self) -> dict | None:
        """Return device info."""
        if self._is_total:
            entry = self.coordinator.config_entry
            return {
                "identifiers": {
                    (DOMAIN, f"{entry.entry_id}_{self.entity_description.service_name}")
                },
                "name": f"Frank Energie - {self.entity_description.service_name}",
            }

        if not self._battery_id:
            return None
        return {
            "identifiers": {(DOMAIN, self._battery_id)},
            "name": f"Smart Battery {self._battery_id}",
            "manufacturer": "Frank Energie",
            "model": "SmartBattery",
        }

    def _logger(self):
        import logging

        return logging.getLogger(f"{DOMAIN}.sensor")


class EnodeVehicleSensor(CoordinatorEntity, SensorEntity):
    """Representation of an Enode vehicle sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True  # Allow entity name to be set in the UI
    _attr_entity_registry_enabled_default = (
        True  # Default to enabled in entity registry
    )

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: FrankEnergieCoordinator,
        description: EnodeVehicleEntityDescription,
        vehicle_data: dict,
        vehicle_index: int,
    ) -> None:
        """Initialize the Enode vehicle sensor."""
        super().__init__(coordinator)

        self.hass = hass
        self.coordinator = coordinator
        self.entity_description = description
        self._vehicle_id = vehicle_data["id"]
        self._vehicle_data = vehicle_data
        self._vehicle_index = vehicle_index

        info = vehicle_data.get("information") or {}
        vehicle_name = (
            f"{info.get('brand', '')} {info.get('model', '')}".strip() or None
        )
        if description.unique_id_fn is not None:
            self._attr_unique_id = description.unique_id_fn(vehicle_data)
        else:
            self._attr_unique_id = f"{DOMAIN}_{self._vehicle_id}_{description.key}"
        self._attr_name = description.name
        self._attr_translation_key = description.translation_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._vehicle_id)},
            manufacturer=info.get("brand"),
            model=info.get("model"),
            serial_number=info.get("vin", None),
            name=vehicle_name,
            hw_version=str(info.get("year")),
        )

    @property
    def native_value(self) -> StateType:
        """Return the current value from the latest coordinator data."""
        vehicles_obj = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not vehicles_obj:
            return None

        latest_vehicle_data = next(
            (v for v in vehicles_obj.vehicles if v["id"] == self._vehicle_id), None
        )
        if not latest_vehicle_data:
            return None
        return self.entity_description.value_fn(latest_vehicle_data)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return extra attributes from the latest coordinator data."""
        vehicles_obj = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not vehicles_obj:
            return {}

        latest_vehicle_data = next(
            (v for v in vehicles_obj.vehicles if v["id"] == self._vehicle_id), None
        )
        if not latest_vehicle_data:
            return {}

        try:
            if (
                hasattr(self.entity_description, "attr_fn")
                and self.entity_description.attr_fn
            ):
                return self.entity_description.attr_fn(latest_vehicle_data) or {}
            return {}
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Could not get attributes for %s", self.entity_id)
            return {}

    @property
    def old_available(self) -> bool:
        """Return True if the native_value is valid."""
        try:
            value = self.native_value
            return value not in (STATE_UNAVAILABLE, STATE_UNKNOWN, None)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Error checking availability for %s: %s", self.entity_id, err)
            return False

    @property
    def available(self) -> bool:
        """Return True if the sensor value is valid."""
        try:
            if self.coordinator.data is not None:
                value = self.native_value
        except (KeyError, AttributeError, TypeError) as err:
            _LOGGER.debug(
                "Availability check failed for %s: %s",
                self.entity_id,
                err,
            )
            return False
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Error checking availability for %s: %s", self.entity_id, err)
            return False

        return value not in (STATE_UNAVAILABLE, STATE_UNKNOWN, None)

    async def async_update_data(self, data: dict) -> None:
        """Update stored data for the sensor."""
        self._vehicle_data = data
        self.async_write_ha_state()


class FrankEnergiePvSensor(CoordinatorEntity[FrankEnergieCoordinator], SensorEntity):
    """Representation of a Frank Energie Smart PV sensor."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        system_id: str,
        sensor_type: str,  # "total_bonus", "operational_status", "steering_status"
    ) -> None:
        """Initialize the PV sensor."""
        super().__init__(coordinator)
        self._system_id = system_id
        self._sensor_type = sensor_type

        # Get PV system metadata (brand, model, name, serial_number)
        metadata = coordinator.get_pv_system_metadata(system_id)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, system_id)},
            manufacturer=metadata["brand"],
            model=metadata["model"],
            name=metadata["display_name"],
            serial_number=metadata["serial_number"],
        )

        self._attr_unique_id = f"{DOMAIN}_{system_id}_{sensor_type}"
        self._attr_translation_key = f"pv_{sensor_type}"

        if sensor_type == "total_bonus":
            self._attr_name = "Total bonus"
            self._attr_native_unit_of_measurement = CURRENCY_EURO
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_state_class = SensorStateClass.TOTAL
            self._attr_icon = "mdi:currency-eur"
            self._attr_suggested_display_precision = 2
        elif sensor_type == "operational_status":
            self._attr_name = "Operational status"
            self._attr_icon = "mdi:information-outline"
        elif sensor_type == "steering_status":
            self._attr_name = "Steering status"
            self._attr_icon = "mdi:solar-power"

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        summary_dict = self.coordinator.data.get(DATA_PV_SUMMARY)
        if not summary_dict:
            return None
        summary = summary_dict.get(self._system_id)
        if not summary:
            return None

        if self._sensor_type == "total_bonus":
            return summary.total_bonus
        if self._sensor_type == "operational_status":
            return summary.operational_status
        if self._sensor_type == "steering_status":
            if summary.steering_status is not None:
                return summary.steering_status
            systems_obj = self.coordinator.data.get(DATA_PV_SYSTEMS)
            if systems_obj and systems_obj.systems:
                pv_system = next(
                    (s for s in systems_obj.systems if s.id == self._system_id), None
                )
                if pv_system:
                    return pv_system.steering_status
            return None

        return None


def format_user_name(data: dict) -> str | None:
    """
    Formats the user's name from provided data by concatenating the first and last name.

    Parameters:
        data (dict): Dictionary containing user details, specifically `externalDetails` and `person`.

    Returns:
        Optional[str]: The formatted full name or None if data is missing required fields.
    """
    try:
        user = data.get(DATA_USER) or {}
        external = user.get("externalDetails") or {}
        person = external.get("person") or {}
        first = person.get("firstName")
        last = person.get("lastName")
        return f"{first} {last}".strip() if first or last else None
    except KeyError:
        _LOGGER.exception("Missing data key")
    return None


STATIC_ENODE_SENSOR_TYPES: tuple[FrankEnergieEntityDescription, ...] = (
    FrankEnergieEntityDescription(
        key="enode_total_chargers",
        name="Total Chargers",
        native_unit_of_measurement=None,
        state_class=None,
        device_class=None,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_CHARGERS,
        icon="mdi:ev-station",
        value_fn=lambda data: (
            len(data[DATA_ENODE_CHARGERS].chargers)
            if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers
            else STATE_UNKNOWN
        ),
        attr_fn=lambda data: {
            "chargers": [
                asdict(charger)
                for charger in getattr(data.get(DATA_ENODE_CHARGERS), "chargers", [])
            ]
        },
    ),
    FrankEnergieEntityDescription(
        key="total_charge_capacity",
        name="Total Charge Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_CHARGERS,
        icon="mdi:flash",
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda data: (
            sum(
                charger.charge_settings.capacity
                for charger in getattr(data.get(DATA_ENODE_CHARGERS), "chargers", [])
                if charger.charge_settings
                and charger.charge_settings.capacity is not None
            )
            if DATA_ENODE_CHARGERS in data
            and getattr(data[DATA_ENODE_CHARGERS], "chargers", None)
            else None
        ),
        attr_fn=lambda data: {
            "chargers capacity": {
                charger.id: charger.charge_settings.capacity
                for charger in getattr(data.get(DATA_ENODE_CHARGERS), "chargers", [])
                if charger.charge_settings
                and charger.charge_settings.capacity is not None
            }
            if DATA_ENODE_CHARGERS in data
            and getattr(data[DATA_ENODE_CHARGERS], "chargers", None)
            else {}
        },
    ),
    FrankEnergieEntityDescription(
        key="total_charge_rate",
        name="Total Charge Rate",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_CHARGERS,
        icon="mdi:flash",
        device_class=SensorDeviceClass.POWER,
        value_fn=lambda data: (
            sum(
                charger.charge_state.charge_rate
                for charger in getattr(data.get(DATA_ENODE_CHARGERS), "chargers", [])
                if charger.charge_state and charger.charge_state.charge_rate is not None
            )
            if DATA_ENODE_CHARGERS in data
            and getattr(data[DATA_ENODE_CHARGERS], "chargers", None)
            else None
        ),
        attr_fn=lambda data: {
            "chargers charge rate": {
                charger.id: charger.charge_state.charge_rate
                for charger in getattr(data.get(DATA_ENODE_CHARGERS), "chargers", [])
                if charger.charge_state and charger.charge_state.charge_rate is not None
            }
            if DATA_ENODE_CHARGERS in data
            and getattr(data[DATA_ENODE_CHARGERS], "chargers", None)
            else {}
        },
    ),
)


ENODE_CHARGER_SENSOR_TYPES: tuple[ChargerSensorDescription, ...] = (
    ChargerSensorDescription(
        key="charger_brand",
        name="Brand",
        icon="mdi:ev-station",
        authenticated=True,
        value_fn=lambda charger: (
            charger.information.get("brand") if charger.information else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ChargerSensorDescription(
        key="charger_model",
        name="Model",
        icon="mdi:ev-station",
        authenticated=True,
        value_fn=lambda charger: (
            charger.information.get("model") if charger.information else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ChargerSensorDescription(
        key="can_smart_charge",
        name="Can Smart Charge",
        icon="mdi:flash",
        authenticated=True,
        value_fn=lambda charger: charger.can_smart_charge,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ChargerSensorDescription(
        key="charge_capacity",
        name="Charge Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:flash",
        device_class=SensorDeviceClass.ENERGY,
        authenticated=True,
        value_fn=lambda charger: (
            charger.charge_settings.capacity if charger.charge_settings else None
        ),
    ),
    ChargerSensorDescription(
        key="is_plugged_in",
        name="Is Plugged In",
        icon="mdi:flash",
        authenticated=True,
        value_fn=lambda charger: (
            charger.charge_state.is_plugged_in if charger.charge_state else None
        ),
    ),
    ChargerSensorDescription(
        key="power_delivery_state",
        name="Power Delivery State",
        icon="mdi:flash",
        authenticated=True,
        value_fn=lambda charger: (
            charger.charge_state.power_delivery_state if charger.charge_state else None
        ),
    ),
    ChargerSensorDescription(
        key="is_reachable",
        name="Is Reachable",
        icon="mdi:ev-station",
        authenticated=True,
        value_fn=lambda charger: charger.is_reachable,
    ),
    ChargerSensorDescription(
        key="is_charging",
        name="Is Charging",
        icon="mdi:ev-station",
        authenticated=True,
        value_fn=lambda charger: (
            charger.charge_state.is_charging if charger.charge_state else None
        ),
    ),
    ChargerSensorDescription(
        key="charge_rate",
        name="Charge Rate",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        icon="mdi:flash",
        device_class=SensorDeviceClass.POWER,
        authenticated=True,
        value_fn=lambda charger: (
            charger.charge_state.charge_rate if charger.charge_state else None
        ),
    ),
    ChargerSensorDescription(
        key="is_smart_charging_enabled",
        name="Is Smart Charging Enabled",
        icon="mdi:flash",
        authenticated=True,
        value_fn=lambda charger: (
            charger.charge_settings.is_smart_charging_enabled
            if charger.charge_settings
            else None
        ),
    ),
    ChargerSensorDescription(
        key="is_solar_charging_enabled",
        name="Is Solar Charging Enabled",
        icon="mdi:flash",
        authenticated=True,
        value_fn=lambda charger: (
            charger.charge_settings.is_solar_charging_enabled
            if charger.charge_settings
            else None
        ),
    ),
)

STATIC_BATTERY_SENSOR_TYPES: tuple[FrankEnergieEntityDescription, ...] = (
    FrankEnergieEntityDescription(
        key="total_batteries",
        name="Total Batteries",
        native_unit_of_measurement=None,
        state_class=None,
        device_class=None,
        authenticated=True,
        service_name=SERVICE_NAME_BATTERIES,
        icon="mdi:battery",
        value_fn=lambda data: (
            len(batteries.batteries)
            if (batteries := data.get(DATA_BATTERIES)) and batteries.batteries
            else None
        ),
        attr_fn=lambda data: {
            "batteries": (
                [asdict(battery) for battery in batteries.batteries]
                if (batteries := data.get(DATA_BATTERIES)) and batteries.batteries
                else []
            )
        },
    ),
)

WEEKDAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

ENODE_VEHICLE_SENSOR_TYPES: list[EnodeVehicleEntityDescription] = [
    EnodeVehicleEntityDescription(
        key="vehicle_name",
        name="Vehicle Name",
        translation_key="vehicle_name",
        icon="mdi:car",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            ((data.get("information") or {}).get("brand") or "")
            + " "
            + ((data.get("information") or {}).get("model") or "")
            if data.get("information")
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="can_smart_charge",
        name="Can Smart Charge",
        icon="mdi:car-electric",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            bool(data.get("canSmartCharge")) if "canSmartCharge" in data else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="is_reachable",
        name="Vehicle Reachable",
        icon="mdi:car-connected",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: (
            bool(data.get("isReachable")) if "isReachable" in data else None
        ),
    ),
    EnodeVehicleEntityDescription(
        key="battery_capacity",
        name="Battery Capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery",
        device_class=SensorDeviceClass.ENERGY,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            data.get("chargeState", {}).get("batteryCapacity")
            if isinstance(data, dict)
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="battery_level",
        name="Battery Level",
        icon="mdi:battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: data.get("chargeState", {}).get("batteryLevel"),
    ),
    EnodeVehicleEntityDescription(
        key="charge_limit",
        name="Charge Limit",
        icon="mdi:battery-charging-70",
        native_unit_of_measurement=PERCENTAGE,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: _get_nested(data, "chargeState", "chargeLimit"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="charge_rate",
        name="Charge Rate",
        icon="mdi:flash",
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        device_class=SensorDeviceClass.POWER,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: data.get("chargeState", {}).get("chargeRate"),
    ),
    EnodeVehicleEntityDescription(
        key="charge_time_remaining",
        name="Charge Time Remaining",
        icon="mdi:clock-fast",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            int(data.get("chargeState", {}).get("chargeTimeRemaining"))
            if data.get("chargeState", {}).get("chargeTimeRemaining") is not None
            else None
        ),
    ),
    EnodeVehicleEntityDescription(
        key="vehicle_range",
        name="Estimated Range",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            int(data.get("chargeState", {}).get("range"))
            if data.get("chargeState", {}).get("range") is not None
            else None
        ),
    ),
    EnodeVehicleEntityDescription(
        key="is_charging",
        name="Is Charging",
        icon="mdi:ev-station",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: (
            bool(data.get("chargeState", {}).get("isCharging"))
            if "isCharging" in data.get("chargeState", {})
            else None
        ),
    ),
    EnodeVehicleEntityDescription(
        key="charge_last_updated",
        name="Charge Last Updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: _parse_iso_datetime(
            data.get("chargeState", {}).get("lastUpdated")
        ),
    ),
    EnodeVehicleEntityDescription(
        key="is_fully_charged",
        name="Fully Charged",
        icon="mdi:battery-check",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: (
            bool(data.get("chargeState", {}).get("isFullyCharged"))
            if "isFullyCharged" in data.get("chargeState", {})
            else None
        ),
    ),
    EnodeVehicleEntityDescription(
        key="is_plugged_in",
        name="Is Plugged In",
        icon="mdi:power-plug",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda data: (
            bool(data.get("chargeState", {}).get("isPluggedIn"))
            if "isPluggedIn" in data.get("chargeState", {})
            else None
        ),
    ),
    EnodeVehicleEntityDescription(
        key="power_delivery_state",
        name="Power Delivery State",
        icon="mdi:transmission-tower",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            data.get("chargeState", {}).get("powerDeliveryState")
            if isinstance(data.get("chargeState", {}).get("powerDeliveryState"), str)
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="smart_charging_enabled",
        name="Smart Charging Enabled",
        icon="mdi:car-electric",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda data: (
            data.get("chargeSettings", {}).get("isSmartChargingEnabled")
            if isinstance(
                data.get("chargeSettings", {}).get("isSmartChargingEnabled"), bool
            )
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="solar_charging_enabled",
        name="Solar Charging Enabled",
        icon="mdi:solar-power",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            data.get("chargeSettings", {}).get("isSolarChargingEnabled")
            if isinstance(
                data.get("chargeSettings", {}).get("isSolarChargingEnabled"), bool
            )
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="last_seen",
        name="Last Seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: _parse_iso_datetime(data.get("lastSeen")),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="calculated_deadline",
        name="Calculated Deadline",
        icon="mdi:calendar-clock",
        authenticated=True,
        device_class=SensorDeviceClass.TIMESTAMP,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: _parse_iso_datetime(
            data.get("chargeSettings", {}).get("calculatedDeadline")
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="deadline",
        name="Charging Deadline",
        icon="mdi:calendar-end",
        authenticated=True,
        device_class=SensorDeviceClass.TIMESTAMP,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: _parse_iso_datetime(
            data.get("chargeSettings", {}).get("deadline")
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="charge_settings_id",
        name="Charge Settings ID",
        icon="mdi:identifier",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: data.get("chargeSettings", {}).get("id"),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    EnodeVehicleEntityDescription(
        key="max_charge_limit",
        name="Max Charge Limit",
        icon="mdi:battery-high",
        authenticated=True,
        native_unit_of_measurement=PERCENTAGE,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            data.get("chargeSettings", {}).get("maxChargeLimit")
            if isinstance(
                data.get("chargeSettings", {}).get("maxChargeLimit"), (int, float)
            )
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="min_charge_limit",
        name="Min Charge Limit",
        icon="mdi:battery-low",
        authenticated=True,
        native_unit_of_measurement=PERCENTAGE,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            data.get("chargeSettings", {}).get("minChargeLimit")
            if isinstance(
                data.get("chargeSettings", {}).get("minChargeLimit"), (int, float)
            )
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="vehicle_vin",
        name="VIN",
        icon="mdi:card-account-details",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            data.get("information", {}).get("vin")
            if isinstance(data.get("information", {}).get("vin"), str)
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="interventions_count",
        name="Number of Interventions",
        icon="mdi:alert-circle-outline",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            len(data.get("interventions", []))
            if isinstance(data.get("interventions"), list)
            else 0
        ),
        attr_fn=lambda data: {"interventions": data.get("interventions", [])},
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="intervention_description",
        name="Intervention Description",
        icon="mdi:alert-circle-outline",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            data["interventions"][0].get("description")
            if isinstance(data.get("interventions"), list) and data["interventions"]
            else None
        ),
        attr_fn=lambda data: {
            "interventions": list(data.get("interventions", []))
            if isinstance(data.get("interventions"), list)
            else []
        },
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="intervention_title",
        name="Intervention Title",
        icon="mdi:alert-decagram",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            data["interventions"][0].get("title")
            if isinstance(data.get("interventions"), list) and data["interventions"]
            else None
        ),
        attr_fn=lambda data: {"interventions": data.get("interventions", [])},
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
] + [
    EnodeVehicleEntityDescription(
        key=f"charging_hour_{day}",
        name=f"Charging Hour {day.capitalize()}",
        translation_key=f"charging_hour_{day}",
        icon="mdi:clock-time-four-outline",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data, d=day: (
            _next_weekday_datetime(
                WEEKDAYS.index(d),
                data.get("chargeSettings", {}).get(f"hour{d.capitalize()}") // 60,
                data.get("chargeSettings", {}).get(f"hour{d.capitalize()}") % 60,
            )
            if isinstance(
                data.get("chargeSettings", {}).get(f"hour{d.capitalize()}"), int
            )
            else None
        ),
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        unique_id_fn=lambda vehicle, d=day: f"{vehicle.get('id')}_charging_hour_{d}",
    )
    for day in WEEKDAYS
]


def _safe_getattr(obj: object, attr: str) -> object | None:
    """Safely get an attribute from an object."""
    return getattr(obj, attr, None) if obj else None


def _calculate_market_percent_tax(price_data: object | None) -> float | None:
    """Calculate market percent tax safely with fallback to any non-zero hour."""
    if not price_data:
        return None

    # First, try to calculate using the current hour if price is non-zero
    current = getattr(price_data, "current_hour", None)
    if current and current.market_price != 0:
        return 100 * current.market_price_tax / current.market_price

    # Fallback to the first non-zero hour in the dataset
    all_prices = getattr(price_data, "all", [])
    for price in all_prices:
        if price.market_price != 0:
            return 100 * price.market_price_tax / price.market_price

    # If all prices are 0, and tax is also 0, it means tax is 0%
    if current and current.market_price == 0 and current.market_price_tax == 0:
        return 0.0

    return None


BATTERY_SESSION_SENSOR_DESCRIPTIONS: Final[
    tuple[FrankEnergieEntityDescription, ...]
] = (
    FrankEnergieEntityDescription(
        key="device_id",
        name="Device ID",
        icon="mdi:battery",
        native_unit_of_measurement=None,
        state_class=None,
        entity_category=EntityCategory.DIAGNOSTIC,
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: getattr(data, "device_id", None) if data else None,
        entity_registry_enabled_default=False,
    ),
    FrankEnergieEntityDescription(
        key="period_start_date",
        name="Period Start Date",
        icon="mdi:calendar-start",
        native_unit_of_measurement=None,
        entity_category=None,
        state_class=None,
        device_class=SensorDeviceClass.TIMESTAMP,
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            _format_battery_date(getattr(data, "period_start_date", None))
            if data
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="period_end_date",
        name="Period End Date",
        icon="mdi:calendar-end",
        native_unit_of_measurement=None,
        entity_category=None,
        state_class=None,
        device_class=SensorDeviceClass.TIMESTAMP,
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            _format_battery_date(data.period_end_date) if data else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="period_trade_index",
        name="Period Trade Index",
        icon="mdi:numeric",
        native_unit_of_measurement=None,
        entity_category=None,
        state_class="measurement",
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            getattr(data, "period_trade_index", None)
            if data and getattr(data, "period_trade_index", None) is not None
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="period_trading_result",
        name="Period Trading Result",
        icon="mdi:currency-eur",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        entity_category=None,
        state_class="measurement",
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            getattr(data, "period_trading_result", None)
            if data and getattr(data, "period_trading_result", None) is not None
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="period_total_result",
        name="Period Total Result",
        icon="mdi:currency-eur",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            getattr(data, "period_total_result", None)
            if data and getattr(data, "period_total_result", None) is not None
            else None
        ),
        attr_fn=lambda data: (
            {
                "device_id": getattr(data, "device_id", None),
                "period_start_date": getattr(data, "period_start_date", None),
                "period_end_date": getattr(data, "period_end_date", None),
                "period_trade_index": getattr(data, "period_trade_index", None),
                "period_trading_result": getattr(data, "period_trading_result", None),
                "period_total_result": getattr(data, "period_total_result", None),
                "period_imbalance_result": getattr(
                    data, "period_imbalance_result", None
                ),
                "period_epex_result": getattr(data, "period_epex_result", None),
                "period_frank_slim": getattr(data, "period_frank_slim", None),
                "sessions": [
                    {
                        "date": s.date,
                        "result": s.result,
                        "cumulative_result": s.cumulative_result,
                    }
                    for s in getattr(data, "sessions", []) or []
                ],
            }
            if data
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="period_imbalance_result",
        name="Period Imbalance Result",
        icon="mdi:currency-eur",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            getattr(data, "period_imbalance_result", None)
            if data and getattr(data, "period_imbalance_result", None) is not None
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="period_epex_result",
        name="Period EPEX Result",
        icon="mdi:currency-eur",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            getattr(data, "period_epex_result", None)
            if data and getattr(data, "period_epex_result", None) is not None
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="period_frank_slim_bonus",
        name="Period Frank Slim Bonus",
        icon="mdi:currency-eur",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        state_class="measurement",
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            getattr(data, "period_frank_slim", None)
            if data and getattr(data, "period_frank_slim", None) is not None
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="all_periods_trading_result",
        name="All Periods Trading Result",
        icon="mdi:currency-eur",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        state_class="measurement",
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            sum(
                getattr(session, "result", 0)
                for session in (getattr(data, "sessions", None) or [])
            )
            if data
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="total_trading_result",
        name="Total Trading Result",
        icon="mdi:currency-eur",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        state_class="measurement",
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: (
            getattr(data, "sessions")[-1].cumulative_result
            if data and getattr(data, "sessions", None)
            else None
        ),
    ),
)

SENSOR_TYPES: tuple[FrankEnergieEntityDescription, ...] = (
    FrankEnergieEntityDescription(
        key="contract_price_resolution_state",
        name="Contract Price Resolution State",
        translation_key="contract_price_resolution_state",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=None,
        icon="mdi:clock-digital",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            int(
                data[DATA_CONTRACT_PRICE_RESOLUTION_STATE]
                .activeOption.replace("PT", "")
                .replace("M", "")
            )
            if data.get(DATA_CONTRACT_PRICE_RESOLUTION_STATE)
            and data[DATA_CONTRACT_PRICE_RESOLUTION_STATE].activeOption
            else None
        ),
        attr_fn=lambda data: (
            {
                key: value
                for key, value in {
                    "available_options": state.availableOptions,
                    "change_request_effective_date": state.changeRequestEffectiveDate,
                    "is_change_request_possible": state.isChangeRequestPossible,
                    "upcoming_change": state.upcomingChange,
                    "upcoming_change_effective_date": state.upcomingChangeEffectiveDate,
                }.items()
                if value is not None
            }
            if (state := data.get(DATA_CONTRACT_PRICE_RESOLUTION_STATE))
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_markup",
        name="Current electricity price (All-in)",
        translation_key="current_electricity_price",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current.total
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current
            else None
        ),
        attr_fn=lambda data: {
            "prices": data[DATA_ELECTRICITY].asdict(
                "total", timezone=TIMEZONE_AMSTERDAM
            )
        },
    ),
    FrankEnergieEntityDescription(
        key="elec_market",
        name="Current electricity market price",
        translation_key="current_electricity_marketprice",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.market_price
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour
            else None
        ),
        attr_fn=lambda data: {
            "prices": data[DATA_ELECTRICITY].asdict(
                "market_price", timezone=TIMEZONE_AMSTERDAM
            )
        },
    ),
    FrankEnergieEntityDescription(
        key="elec_tax",
        name="Current electricity price including tax",
        translation_key="current_electricity_price_incl_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.market_price_with_tax
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour
            else None
        ),
        attr_fn=lambda data: {
            "prices": data[DATA_ELECTRICITY].asdict(
                "market_price_with_tax", timezone=TIMEZONE_AMSTERDAM
            )
        },
    ),
    FrankEnergieEntityDescription(
        key="elec_tax_vat",
        name="Current electricity VAT price",
        translation_key="current_electricity_tax_price",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.market_price_tax
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour
            else None
        ),
        attr_fn=lambda data: {
            "prices": data[DATA_ELECTRICITY].asdict(
                "market_price_tax", timezone=TIMEZONE_AMSTERDAM
            )
        },
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_sourcing",
        name="Current electricity sourcing markup",
        translation_key="current_electricity_sourcing_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.sourcing_markup_price
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_tax_only",
        name="Current electricity tax only",
        translation_key="elec_tax_only",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=5,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.energy_tax_price
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_fixed_kwh",
        name="Fixed electricity cost kWh",
        translation_key="elec_fixed_kwh",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=6,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            (
                data[DATA_ELECTRICITY].current_hour.sourcing_markup_price
                + data[DATA_ELECTRICITY].current_hour.energy_tax_price  # noqa: W503
            )
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_var_kwh",
        name="Variable electricity cost kWh",
        translation_key="elec_var_kwh",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=6,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            (data[DATA_ELECTRICITY].current_hour.market_price_with_tax)
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_markup",
        name="Current gas price (All-in)",
        translation_key="gas_markup",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].current_hour.total
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
        attr_fn=lambda data: (
            {"prices": data[DATA_GAS].asdict("total")}
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_market",
        name="Current gas market price",
        translation_key="gas_market",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].current_hour.market_price
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
        attr_fn=lambda data: (
            {"prices": data[DATA_GAS].asdict("market_price")}
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_tax",
        name="Current gas price including tax",
        translation_key="gas_tax",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].current_hour.market_price_with_tax
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
        attr_fn=lambda data: (
            {
                "prices": data[DATA_GAS].asdict(
                    "market_price_with_tax", timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_tax_vat",
        name="Current gas VAT price",
        translation_key="gas_tax_vat",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].current_hour.energy_tax_price
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_sourcing",
        name="Current gas sourcing price",
        translation_key="gas_sourcing",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].current_hour.sourcing_markup_price
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_tax_only",
        name="Current gas tax only",
        translation_key="gas_tax_only",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].current_hour.market_price_tax
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_min",
        name="Lowest gas price today (All-in)",
        translation_key="gas_min",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].today_min.total
            if data[DATA_GAS] and data[DATA_GAS].today_min
            else None
        ),
        attr_fn=lambda data: (
            {ATTR_FROM_TIME: data[DATA_GAS].today_min.date_from}
            if data[DATA_GAS] and data[DATA_GAS].today_min
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_max",
        name="Highest gas price today (All-in)",
        translation_key="gas_max",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].today_max.total
            if data[DATA_GAS] and data[DATA_GAS].today_max
            else None
        ),
        attr_fn=lambda data: (
            {ATTR_FROM_TIME: data[DATA_GAS].today_max.date_from}
            if data[DATA_GAS] and data[DATA_GAS].today_max
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_min",
        name="Lowest electricity price today (All-in)",
        translation_key="elec_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].today_min.total
            if data[DATA_ELECTRICITY].today_min
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_ELECTRICITY].today_min.date_from,
                ATTR_TILL_TIME: data[DATA_ELECTRICITY].today_min.date_till,
            }
            if data[DATA_ELECTRICITY].today_min
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_max",
        name="Highest electricity price today (All-in)",
        translation_key="elec_max",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].today_max.total
            if data[DATA_ELECTRICITY].today_max
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_ELECTRICITY].today_max.date_from,
                ATTR_TILL_TIME: data[DATA_ELECTRICITY].today_max.date_till,
            }
            if data[DATA_ELECTRICITY].today_max
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_avg",
        name="Average electricity price today (All-in)",
        translation_key="average_electricity_price_today_all_in",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_avg,
        attr_fn=lambda data: {
            "prices": data[DATA_ELECTRICITY].asdict(
                "total", today_only=True, timezone=TIMEZONE_AMSTERDAM
            )
        },
    ),
    FrankEnergieEntityDescription(
        key="elec_previoushour",
        name="Previous hour electricity price (All-in)",
        translation_key="elec_previoushour",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].previous_hour.total
            if data[DATA_ELECTRICITY].previous_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_nexthour",
        name="Next hour electricity price (All-in)",
        translation_key="elec_nexthour",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].next_hour.total
            if data[DATA_ELECTRICITY].next_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_market_percent_tax",
        name="Electricity market percent tax",
        translation_key="elec_market_percent_tax",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:percent",
        value_fn=lambda data: _calculate_market_percent_tax(data.get(DATA_ELECTRICITY)),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_market_percent_tax",
        name="Gas market percent tax",
        translation_key="gas_market_percent_tax",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:percent",
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: _calculate_market_percent_tax(data.get(DATA_GAS)),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_all_min",
        name="Lowest electricity price all hours (All-in)",
        translation_key="elec_all_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: data[DATA_ELECTRICITY].all_min.total,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_ELECTRICITY].all_min.date_from,
                ATTR_TILL_TIME: data[DATA_ELECTRICITY].all_min.date_till,
            }
            if data[DATA_ELECTRICITY].all_min
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_all_max",
        name="Highest electricity price all hours (All-in)",
        translation_key="elec_all_max",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].all_max.total
            if data[DATA_ELECTRICITY].all_max
            else None
        ),
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_ELECTRICITY].all_max.date_from,
                ATTR_TILL_TIME: data[DATA_ELECTRICITY].all_max.date_till,
            }
            if data[DATA_ELECTRICITY].all_max
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_min",
        name="Lowest electricity price tomorrow (All-in)",
        translation_key="elec_tomorrow_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].tomorrow_min.total
            if data[DATA_ELECTRICITY].tomorrow_min
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_ELECTRICITY].tomorrow_min.date_from,
                ATTR_TILL_TIME: data[DATA_ELECTRICITY].tomorrow_min.date_till,
            }
            if data[DATA_ELECTRICITY].tomorrow_min
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_max",
        name="Highest electricity price tomorrow (All-in)",
        translation_key="elec_tomorrow_max",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].tomorrow_max.total
            if data[DATA_ELECTRICITY].tomorrow_max
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_ELECTRICITY].tomorrow_max.date_from,
                ATTR_TILL_TIME: data[DATA_ELECTRICITY].tomorrow_max.date_till,
            }
            if data[DATA_ELECTRICITY].tomorrow_max
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_upcoming_min",
        name="Lowest electricity price upcoming hours (All-in)",
        translation_key="elec_upcoming_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_min.total,
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_ELECTRICITY].upcoming_min.date_from,
                ATTR_TILL_TIME: data[DATA_ELECTRICITY].upcoming_min.date_till,
            }
            if data[DATA_ELECTRICITY].upcoming_min
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_upcoming_max",
        name="Highest electricity price upcoming hours (All-in)",
        translation_key="elec_upcoming_max",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_max.total,
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_ELECTRICITY].upcoming_max.date_from,
                ATTR_TILL_TIME: data[DATA_ELECTRICITY].upcoming_max.date_till,
            }
            if data[DATA_ELECTRICITY].upcoming_max
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_avg_tax",
        name="Average electricity price today including tax",
        translation_key="average_electricity_price_today_including_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_tax_avg,
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_avg_tax_markup",
        name="Average electricity price today including tax and markup",
        translation_key="average_electricity_price_today_including_tax_and_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_tax_markup_avg,
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_avg_market",
        name="Average electricity market price today",
        translation_key="average_electricity_market_price_today",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_market_avg,
        suggested_display_precision=3,
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg_tax_markup",
        name="Average electricity price tomorrow including tax and markup",
        translation_key="average_electricity_price_tomorrow_including_tax_and_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].tomorrow_avg.market_price_with_tax_and_markup
            if data[DATA_ELECTRICITY].tomorrow_avg
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg",
        name="Average electricity price tomorrow (All-in)",
        translation_key="average_electricity_price_tomorrow_all_in",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=3,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].tomorrow_average_price
            # value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_avg.total
            if data[DATA_ELECTRICITY].tomorrow_avg
            else None
        ),
        attr_fn=lambda data: {
            "tomorrow_prices": data[DATA_ELECTRICITY].asdict(
                "total", tomorrow_only=True, timezone=TIMEZONE_AMSTERDAM
            )
        },
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg_tax",
        name="Average electricity price tomorrow including tax",
        translation_key="average_electricity_price_tomorrow_including_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].tomorrow_average_price_including_tax
            # value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_avg.market_price_with_tax
            if data[DATA_ELECTRICITY].tomorrow_avg
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg_market",
        name="Average electricity market price tomorrow",
        translation_key="average_electricity_market_price_tomorrow",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].tomorrow_average_market_price
            # value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_avg.market_price
            if data[DATA_ELECTRICITY].tomorrow_avg
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_market_upcoming",
        name="Average electricity market price upcoming",
        translation_key="average_electricity_market_price_upcoming",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].upcoming_avg.market_price
            if data[DATA_ELECTRICITY].upcoming_avg
            else None
        ),
        attr_fn=lambda data: (
            {
                "upcoming_prices": data[DATA_ELECTRICITY].asdict(
                    "market_price", upcoming_only=True, timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data[DATA_ELECTRICITY].upcoming_avg
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_upcoming",
        name="Average electricity price upcoming (All-in)",
        translation_key="average_electricity_price_upcoming_market",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].upcoming_avg.total
            if data[DATA_ELECTRICITY].upcoming_avg
            else None
        ),
        attr_fn=lambda data: {
            "upcoming_prices": data[DATA_ELECTRICITY].asdict(
                "total", upcoming_only=True, timezone=TIMEZONE_AMSTERDAM
            )
        },
    ),
    FrankEnergieEntityDescription(
        key="elec_all",
        name="Average electricity price all hours (All-in)",
        translation_key="elec_all",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].all_avg.total
            if data[DATA_ELECTRICITY].all_avg
            else None
        ),
        attr_fn=lambda data: (
            {
                "all_prices": data[DATA_ELECTRICITY].asdict(
                    "total", timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data[DATA_ELECTRICITY].all_avg
            else {}
        ),
        # attr_fn=lambda data: data[DATA_ELECTRICITY].all_attr,
    ),
    FrankEnergieEntityDescription(
        key="elec_tax_markup",
        name="Current electricity price including tax and markup",
        translation_key="current_electricity_price_incl_tax_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].current_hour.market_price_including_tax_and_markup
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour
            else None
        ),
        attr_fn=lambda data: (
            {
                "prices": data[DATA_ELECTRICITY].asdict(
                    "market_price_including_tax_and_markup", timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour
            else {}
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg",
        name="Average gas price tomorrow (All-in)",
        translation_key="gas_tomorrow_avg_all_in",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].tomorrow_average_price if data[DATA_GAS] else None
        ),
        # value_fn=lambda data: data[DATA_GAS].tomorrow_avg.total,
        # if data[DATA_GAS].tomorrow_avg else None,
        attr_fn=lambda data: (
            {
                "tomorrow_prices": data[DATA_GAS].asdict(
                    "total", tomorrow_only=True, timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data[DATA_GAS]
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_tax_markup",
        name="Current gas price including tax and markup",
        translation_key="gas_tax_markup",
        suggested_display_precision=3,
        native_unit_of_measurement=UNIT_GAS,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].current_hour.market_price_including_tax_and_markup
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else None
        ),
        attr_fn=lambda data: (
            {
                "prices": data[DATA_GAS].asdict(
                    "market_price_including_tax_and_markup", timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data[DATA_GAS] and data[DATA_GAS].current_hour
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_hourcount",
        name="Number of hours with electricity prices loaded",
        translation_key="elec_hourcount",
        icon="mdi:numeric-0-box-multiple",
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data[DATA_ELECTRICITY].length,
        attr_fn=lambda data: (
            {"DST": True}
            if data[DATA_ELECTRICITY].length == 25
            or data[DATA_ELECTRICITY].length == 49
            else {}
        ),
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_hourcount",
        name="Number of hours with gas prices loaded",
        translation_key="gas_hourcount",
        icon="mdi:numeric-0-box-multiple",
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: data[DATA_GAS].length if data[DATA_GAS] else None,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_previoushour_market",
        name="Previous hour electricity market price",
        translation_key="elec_previoushour_market",
        suggested_display_precision=3,
        native_unit_of_measurement=UNIT_ELECTRICITY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].previous_hour.market_price
            if data[DATA_ELECTRICITY].previous_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="elec_nexthour_market",
        name="Next hour electricity market price",
        translation_key="elec_nexthour_market",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].next_hour.market_price
            if data[DATA_ELECTRICITY].next_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_previoushour_all_in",
        name="Previous hour gas price (All-in)",
        translation_key="gas_previoushour_all_in",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].previous_hour.total
            if data[DATA_GAS] and data[DATA_GAS].previous_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_nexthour_all_in",
        name="Next hour gas price (All-in)",
        translation_key="gas_nexthour_all_in",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].next_hour.total
            if data[DATA_GAS] and data[DATA_GAS].next_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_previoushour_market",
        name="Previous hour gas market price",
        translation_key="gas_previoushour_market",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].previous_hour.market_price
            if data[DATA_GAS] and data[DATA_GAS].previous_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_nexthour_market",
        name="Next hour gas market price",
        translation_key="gas_nexthour_market",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].next_hour.market_price
            if data[DATA_GAS] and data[DATA_GAS].next_hour
            else None
        ),
        entity_registry_enabled_default=True,
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg_market",
        name="Average gas market price tomorrow",
        translation_key="gas_tomorrow_avg_market",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].tomorrow_prices_market
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market
            else None
        ),
        attr_fn=lambda data: (
            {
                "tomorrow_prices": data[DATA_GAS].asdict(
                    "market_price", tomorrow_only=True, timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg_market_tax",
        name="Average gas market price incl tax tomorrow",
        translation_key="gas_tomorrow_avg_market_tax",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].tomorrow_prices_market_tax
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market_tax
            else None
        ),
        attr_fn=lambda data: (
            {
                "tomorrow_prices": data[DATA_GAS].asdict(
                    "market_price_tax", tomorrow_only=True, timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market_tax
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg_market_tax_markup",
        name="Average gas market price incl tax and markup tomorrow",
        translation_key="gas_tomorrow_avg_market_tax_markup",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].tomorrow_prices_market_tax_markup
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market_tax_markup
            else None
        ),
        attr_fn=lambda data: (
            {
                "tomorrow_prices": data[DATA_GAS].asdict(
                    "market_price_including_tax_and_markup",
                    tomorrow_only=True,
                    timezone=TIMEZONE_AMSTERDAM,
                )
            }
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market_tax_markup
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_today_avg_all_in",
        name="Average gas price today (All-in)",
        translation_key="gas_today_avg_all_in",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].today_prices_total
            if data[DATA_GAS] and data[DATA_GAS].today_prices_total
            else None
        ),
        attr_fn=lambda data: (
            {
                "today_prices": data[DATA_GAS].asdict(
                    "total", today_only=True, timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data[DATA_GAS] and data[DATA_GAS].today_prices_total
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_avg_all_in",
        name="Average gas price tomorrow (All-in)",
        translation_key="gas_tomorrow_avg_all_in",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].tomorrow_prices_total
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_total
            else None
        ),
        attr_fn=lambda data: (
            {
                "tomorrow_prices": data[DATA_GAS].asdict(
                    "total", tomorrow_only=True, timezone=TIMEZONE_AMSTERDAM
                )
            }
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_total
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_min",
        name="Lowest gas price tomorrow (All-in)",
        translation_key="gas_tomorrow_min",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].tomorrow_min.total
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_min
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_GAS].tomorrow_min.date_from,
                ATTR_TILL_TIME: data[DATA_GAS].tomorrow_min.date_till,
            }
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_min
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_max",
        name="Highest gas price tomorrow (All-in)",
        translation_key="gas_tomorrow_max",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].tomorrow_max.total
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_max
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_GAS].tomorrow_max.date_from,
                ATTR_TILL_TIME: data[DATA_GAS].tomorrow_max.date_till,
            }
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_max
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_market_upcoming",
        name="Average gas market price upcoming hours",
        translation_key="gas_market_upcoming",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].upcoming_avg.market_price
            if data[DATA_GAS]
            and data[DATA_GAS].upcoming_avg
            and data[DATA_GAS].upcoming_avg.market_price
            else None
        ),
        attr_fn=lambda data: (
            {
                "prices": data[DATA_GAS].asdict(
                    "market_price", upcoming_only=True, timezone=TIMEZONE_AMSTERDAM
                )
                if data[DATA_GAS]
                else {}
            }
            if data[DATA_GAS]
            and data[DATA_GAS].upcoming_avg
            and data[DATA_GAS].upcoming_avg.market_price
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_upcoming_min",
        name="Lowest gas price upcoming hours (All-in)",
        translation_key="gas_upcoming_min",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].upcoming_min.total
            if data[DATA_GAS] and data[DATA_GAS].upcoming_min
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_GAS].upcoming_min.date_from,
                ATTR_TILL_TIME: data[DATA_GAS].upcoming_min.date_till,
            }
            if data[DATA_GAS] and data[DATA_GAS].upcoming_min
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_upcoming_max",
        name="Highest gas price upcoming hours (All-in)",
        translation_key="gas_upcoming_max",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            data[DATA_GAS].upcoming_max.total
            if data[DATA_GAS]
            and data[DATA_GAS].upcoming_max
            and data[DATA_GAS].upcoming_max.total
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: data[DATA_GAS].upcoming_max.date_from,
                ATTR_TILL_TIME: data[DATA_GAS].upcoming_max.date_till,
            }
            if data[DATA_GAS] and data[DATA_GAS].upcoming_max
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="average_electricity_price_upcoming_all_in",
        name="Average electricity price upcoming (All-in)",
        translation_key="average_electricity_price_upcoming_all_in",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].upcoming_avg.total
            if data[DATA_ELECTRICITY]
            and data[DATA_ELECTRICITY].upcoming_avg
            and data[DATA_ELECTRICITY].upcoming_avg.total
            else None
        ),
        attr_fn=lambda data: (
            {
                "Number of hours": len(data[DATA_ELECTRICITY].upcoming_avg.values),
                "average_electricity_price_upcoming_all_in": data[
                    DATA_ELECTRICITY
                ].upcoming_avg.total,
                "average_electricity_market_price_including_tax_and_markup_upcoming": (
                    data[DATA_ELECTRICITY].upcoming_avg.market_price_with_tax_and_markup
                ),
                "average_electricity_market_markup_price": (
                    data[DATA_ELECTRICITY].upcoming_avg.market_markup_price
                ),
                "average_electricity_market_price_including_tax_upcoming": (
                    data[DATA_ELECTRICITY].upcoming_avg.market_price_with_tax
                ),
                "average_electricity_market_price_tax_upcoming": (
                    data[DATA_ELECTRICITY].upcoming_avg.market_price_tax
                ),
                "average_electricity_market_price_upcoming": (
                    data[DATA_ELECTRICITY].upcoming_avg.market_price
                ),
                "upcoming_prices": data[DATA_ELECTRICITY].asdict(
                    "total", upcoming_only=True, timezone=TIMEZONE_AMSTERDAM
                ),
            }
            if data[DATA_ELECTRICITY] and data[DATA_ELECTRICITY].upcoming_avg
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="average_electricity_price_upcoming_market",
        name="Average electricity price (upcoming, market)",
        translation_key="average_electricity_price_upcoming_market",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].upcoming_market_avg
            if data[DATA_ELECTRICITY]
            and data[DATA_ELECTRICITY].upcoming_avg
            and data[DATA_ELECTRICITY].upcoming_market_avg
            else None
        ),
        attr_fn=lambda data: (
            {
                "average_electricity_price_upcoming_market": data[
                    DATA_ELECTRICITY
                ].upcoming_market_avg,
                "upcoming_market_prices": data[DATA_ELECTRICITY].asdict(
                    "market_price", upcoming_only=True
                ),
            }
            if data[DATA_ELECTRICITY]
            and data[DATA_ELECTRICITY].upcoming_avg
            and data[DATA_ELECTRICITY].upcoming_market_avg
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="average_electricity_price_upcoming_market_tax",
        name="Average electricity price (upcoming, market and tax)",
        translation_key="average_electricity_price_upcoming_market_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].upcoming_market_tax_avg
            if data[DATA_ELECTRICITY]
            and data[DATA_ELECTRICITY].upcoming_avg
            and data[DATA_ELECTRICITY].upcoming_market_tax_avg
            else None
        ),
        attr_fn=lambda data: (
            {
                "average_electricity_price_upcoming_market_tax": data[
                    DATA_ELECTRICITY
                ].upcoming_market_tax_avg,
                "upcoming_market_tax_prices": data[DATA_ELECTRICITY].asdict(
                    "market_price_with_tax", upcoming_only=True
                ),
            }
            if data[DATA_ELECTRICITY]
            and data[DATA_ELECTRICITY].upcoming_avg
            and data[DATA_ELECTRICITY].upcoming_market_tax_avg
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="average_electricity_price_upcoming_market_tax_markup",
        name="Average electricity price (upcoming, market, tax and markup)",
        translation_key="average_electricity_price_upcoming_market_tax_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: (
            data[DATA_ELECTRICITY].upcoming_market_tax_markup_avg
            if data[DATA_ELECTRICITY]
            and data[DATA_ELECTRICITY].upcoming_avg
            and data[DATA_ELECTRICITY].upcoming_market_tax_markup_avg
            else None
        ),
        attr_fn=lambda data: (
            {
                "average_electricity_price_upcoming_market_tax_markup": data[
                    DATA_ELECTRICITY
                ].upcoming_market_tax_markup_avg
            }
            if data[DATA_ELECTRICITY]
            and data[DATA_ELECTRICITY].upcoming_market_tax_markup_avg
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_markup_before6am",
        name="Gas price before 6AM (All-in)",
        translation_key="gas_markup_before6am",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            sum(data[DATA_GAS].today_gas_before6am)
            / len(data[DATA_GAS].today_gas_before6am)
            if data[DATA_GAS] and data[DATA_GAS].today_gas_before6am
            else None
        ),
        attr_fn=lambda data: (
            {"Number of hours": len(data[DATA_GAS].today_gas_before6am)}
            if data[DATA_GAS] and data[DATA_GAS].today_gas_before6am
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_markup_after6am",
        name="Gas price after 6AM (All-in)",
        translation_key="gas_markup_after6am",
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            sum(data[DATA_GAS].today_gas_after6am)
            / len(data[DATA_GAS].today_gas_after6am)
            if data[DATA_GAS] and data[DATA_GAS].today_gas_after6am
            else None
        ),
        attr_fn=lambda data: (
            {"Number of hours": len(data[DATA_GAS].today_gas_after6am)}
            if data[DATA_GAS] and data[DATA_GAS].today_gas_after6am
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_before6am",
        name="Gas price tomorrow before 6AM (All-in)",
        translation_key="gas_price_tomorrow_before6am_allin",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            (
                sum(data[DATA_GAS].tomorrow_gas_before6am)
                / len(data[DATA_GAS].tomorrow_gas_before6am)
            )
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_gas_before6am
            else None
        ),
        attr_fn=lambda data: (
            {"Number of hours": len(data[DATA_GAS].tomorrow_gas_before6am)}
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_gas_before6am
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gas_tomorrow_after6am",
        name="Gas price tomorrow after 6AM (All-in)",
        translation_key="gas_price_tomorrow_after6am_allin",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UNIT_GAS,
        suggested_display_precision=3,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            (
                sum(data[DATA_GAS].tomorrow_gas_after6am)
                / len(data[DATA_GAS].tomorrow_gas_after6am)
            )
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_gas_after6am
            else None
        ),
        attr_fn=lambda data: (
            {"Number of hours": len(data[DATA_GAS].tomorrow_gas_after6am)}
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_gas_after6am
            else {}
        ),
    ),
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
        value_fn=lambda data: (
            data[DATA_MONTH_SUMMARY].actualCostsUntilLastMeterReadingDate
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_MONTH_SUMMARY].actualCostsUntilLastMeterReadingDate
            else None
        ),
        attr_fn=lambda data: (
            {ATTR_LAST_UPDATE: data[DATA_MONTH_SUMMARY].lastMeterReadingDate}
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_MONTH_SUMMARY].lastMeterReadingDate
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_MONTH_SUMMARY].expectedCostsUntilLastMeterReadingDate
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_MONTH_SUMMARY].expectedCostsUntilLastMeterReadingDate
            else None
        ),
        attr_fn=lambda data: (
            {ATTR_LAST_UPDATE: data[DATA_MONTH_SUMMARY].lastMeterReadingDate}
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_MONTH_SUMMARY].lastMeterReadingDate
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_MONTH_SUMMARY].differenceUntilLastMeterReadingDate
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_MONTH_SUMMARY].differenceUntilLastMeterReadingDate
            else None
        ),
        attr_fn=lambda data: (
            {ATTR_LAST_UPDATE: data[DATA_MONTH_SUMMARY].lastMeterReadingDate}
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_MONTH_SUMMARY].lastMeterReadingDate
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_MONTH_SUMMARY].differenceUntilLastMeterReadingDateAvg
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_MONTH_SUMMARY].differenceUntilLastMeterReadingDateAvg
            else None
        ),
        attr_fn=lambda data: (
            {ATTR_LAST_UPDATE: data[DATA_MONTH_SUMMARY].lastMeterReadingDate}
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_MONTH_SUMMARY].lastMeterReadingDate
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_MONTH_SUMMARY].expectedCosts
            if data[DATA_MONTH_SUMMARY] and data[DATA_MONTH_SUMMARY].expectedCosts
            else None
        ),
        attr_fn=lambda data: (
            {
                "Description": data[
                    DATA_INVOICES
                ].current_period_invoice.PeriodDescription,
            }
            if data[DATA_INVOICES]
            and data[DATA_INVOICES].current_period_invoice
            and data[DATA_INVOICES].current_period_invoice.PeriodDescription
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_MONTH_SUMMARY].expectedCostsPerDay
            if data[DATA_MONTH_SUMMARY] and data[DATA_MONTH_SUMMARY].expectedCostsPerDay
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_LAST_UPDATE: data[DATA_MONTH_SUMMARY].lastMeterReadingDate,
                "Description": data[
                    DATA_INVOICES
                ].current_period_invoice.PeriodDescription,
            }
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_INVOICES]
            and data[DATA_INVOICES].current_period_invoice
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_MONTH_SUMMARY].costs_per_day_till_now
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_MONTH_SUMMARY].costs_per_day_till_now
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_LAST_UPDATE: data[DATA_MONTH_SUMMARY].lastMeterReadingDate,
                "Description": data[
                    DATA_INVOICES
                ].current_period_invoice.PeriodDescription,
            }
            if data[DATA_MONTH_SUMMARY]
            and data[DATA_INVOICES]
            and data[DATA_INVOICES].current_period_invoice
            and data[DATA_MONTH_SUMMARY].lastMeterReadingDate
            and data[DATA_INVOICES].current_period_invoice.PeriodDescription
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].previous_period_invoice.TotalAmount
            if data[DATA_INVOICES]
            and data[DATA_INVOICES].previous_period_invoice
            and data[DATA_INVOICES].previous_period_invoice.TotalAmount
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_START_DATE: data[DATA_INVOICES].previous_period_invoice.StartDate,
                "Description": data[
                    DATA_INVOICES
                ].previous_period_invoice.PeriodDescription,
            }
            if data[DATA_INVOICES]
            and data[DATA_INVOICES].previous_period_invoice
            and data[DATA_INVOICES].previous_period_invoice.StartDate
            and data[DATA_INVOICES].previous_period_invoice.StartDate
            and data[DATA_INVOICES].previous_period_invoice.PeriodDescription
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].current_period_invoice.TotalAmount
            if data[DATA_INVOICES]
            and data[DATA_INVOICES].current_period_invoice
            and data[DATA_INVOICES].current_period_invoice.TotalAmount
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_START_DATE: data[DATA_INVOICES].current_period_invoice.StartDate,
                "Description": data[
                    DATA_INVOICES
                ].current_period_invoice.PeriodDescription,
            }
            if data[DATA_INVOICES]
            and data[DATA_INVOICES].current_period_invoice
            and data[DATA_INVOICES].current_period_invoice.StartDate
            and data[DATA_INVOICES].current_period_invoice.PeriodDescription
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].upcoming_period_invoice.TotalAmount
            if data[DATA_INVOICES]
            and data[DATA_INVOICES].upcoming_period_invoice
            and data[DATA_INVOICES].upcoming_period_invoice.TotalAmount
            else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_START_DATE: data[DATA_INVOICES].upcoming_period_invoice.StartDate,
                "Description": data[
                    DATA_INVOICES
                ].upcoming_period_invoice.PeriodDescription,
            }
            if data[DATA_INVOICES]
            and data[DATA_INVOICES].upcoming_period_invoice
            and data[DATA_INVOICES].upcoming_period_invoice.TotalAmount
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].total_costs_this_year
            if data[DATA_INVOICES] and data[DATA_INVOICES].total_costs_this_year
            else None
        ),
        attr_fn=lambda data: (
            {"Invoices": data[DATA_INVOICES].all_invoices_dict_this_year}
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_invoices_dict_this_year
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="total_costs",
        name="Total costs",
        translation_key="total_costs",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_COSTS,
        value_fn=lambda data: (
            sum(
                invoice.TotalAmount
                for invoice in data[DATA_INVOICES].all_periods_invoices
            )
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_periods_invoices
            else None
        ),
        attr_fn=lambda data: (
            {
                "Invoices": data[DATA_INVOICES].all_invoices_dict,
                **{
                    label: parsed_date.strftime(FORMAT_DATE)
                    for label, field in {
                        "First meter reading": "firstMeterReadingDate",
                        "Last meter reading": "lastMeterReadingDate",
                    }.items()
                    if (value := getattr(data[DATA_USER], field, None))
                    and (parsed_date := dt_util.parse_date(value)) is not None
                },
            }
            if data[DATA_INVOICES]
            and data[DATA_INVOICES].all_periods_invoices
            and data[DATA_USER]
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].calculate_average_costs_per_month()
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_periods_invoices
            else None
        ),
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
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_periods_invoices
            else None
        ),
        attr_fn=lambda data: (
            {
                "Total amount": sum(
                    invoice.TotalAmount
                    for invoice in data[DATA_INVOICES].all_periods_invoices
                ),
                "Number of years": len(
                    data[DATA_INVOICES].get_all_invoices_dict_per_year()
                ),
                "Invoices": data[DATA_INVOICES].get_all_invoices_dict_per_year(),
            }
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_periods_invoices
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].calculate_average_costs_per_month() * 12
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_periods_invoices
            else None
        ),
        attr_fn=lambda data: (
            {
                "Month average": data[
                    DATA_INVOICES
                ].calculate_average_costs_per_month(),
                "Invoices": data[DATA_INVOICES].get_all_invoices_dict_per_year(),
            }
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_periods_invoices
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].calculate_average_costs_per_month(
                dt_util.now().year - 1
            )
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_periods_invoices
            else None
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].calculate_average_costs_per_month(dt_util.now().year)
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_periods_invoices
            else None
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].calculate_expected_costs_this_year()
            if data[DATA_INVOICES] and data[DATA_INVOICES].all_periods_invoices
            else None
        ),
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
        value_fn=lambda data: (
            data[DATA_INVOICES].total_costs_previous_year
            if data[DATA_INVOICES] and data[DATA_INVOICES].total_costs_previous_year
            else None
        ),
        attr_fn=lambda data: (
            {"Invoices": data[DATA_INVOICES].all_invoices_dict_previous_year}
            if data[DATA_INVOICES]
            and data[DATA_INVOICES].all_invoices_dict_previous_year
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="costs_electricity_yesterday",
        name="Costs electricity yesterday",
        translation_key="costs_electricity_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        value_fn=lambda data: (
            data[DATA_USAGE].electricity.costs_total
            if data[DATA_USAGE]
            and data[DATA_USAGE].electricity
            and data[DATA_USAGE].electricity.costs_total
            else None
        ),
        attr_fn=lambda data: (
            {"Electricity costs yesterday": data[DATA_USAGE].electricity}
            if data[DATA_USAGE] and data[DATA_USAGE].electricity
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="costs_electricity_this_month",
        name="Costs electricity this month",
        translation_key="costs_electricity_this_month",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        value_fn=lambda data: (
            data[DATA_USAGE].electricity.costs_total
            if data[DATA_USAGE]
            and data[DATA_USAGE].electricity
            and data[DATA_USAGE].electricity.costs_total
            else None
        ),
        attr_fn=lambda data: (
            {"Electricity costs total": data[DATA_USAGE].electricity.costs_total}
            if data[DATA_USAGE]
            and data[DATA_USAGE].electricity
            and hasattr(data[DATA_USAGE].electricity, "costs_total")
            else {}
        ),
        entity_registry_enabled_default=False,
    ),
    FrankEnergieEntityDescription(
        key="usage_electricity_yesterday",
        name="Usage electricity yesterday",
        translation_key="usage_electricity_yesterday",
        icon="mdi:transmission-tower-export",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        value_fn=lambda data: (
            data[DATA_USAGE].electricity.usage_total
            if data[DATA_USAGE] and data[DATA_USAGE].electricity
            else None
        ),
        attr_fn=lambda data: (
            {"Electricity usage yesterday": data[DATA_USAGE].electricity}
            if data[DATA_USAGE] and data[DATA_USAGE].electricity
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="costs_gas_yesterday",
        name="Costs gas yesterday",
        translation_key="costs_gas_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        is_gas=True,
        value_fn=lambda data: (
            data[DATA_USAGE].gas.costs_total
            if data[DATA_USAGE] and data[DATA_USAGE].gas
            else None
        ),
        attr_fn=lambda data: (
            {"Gas costs gas": data[DATA_USAGE].gas}
            if data[DATA_USAGE] and data[DATA_USAGE].gas
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="usage_gas_yesterday",
        name="Usage gas yesterday",
        translation_key="usage_gas_yesterday",
        icon="mdi:meter-gas",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        is_gas=True,
        value_fn=lambda data: (
            data[DATA_USAGE].gas.usage_total
            if data[DATA_USAGE] and data[DATA_USAGE].gas
            else None
        ),
        attr_fn=lambda data: (
            {"Gas usage yesterday": data[DATA_USAGE].gas}
            if data[DATA_USAGE] and data[DATA_USAGE].gas
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="gains_feed_in_yesterday",
        name="Gains feed-in yesterday",
        translation_key="gains_feed_in_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        is_feed_in=True,
        value_fn=lambda data: (
            data[DATA_USAGE].feed_in.costs_total
            if data[DATA_USAGE] and data[DATA_USAGE].feed_in
            else None
        ),
        attr_fn=lambda data: (
            {"feed-in gains yesterday": data[DATA_USAGE].feed_in}
            if data[DATA_USAGE] and data[DATA_USAGE].feed_in
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="delivered_feed_in_yesterday",
        name="Delivered feed-in yesterday",
        translation_key="delivered_feed_in_yesterday",
        icon="mdi:transmission-tower-import",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        is_feed_in=True,
        value_fn=lambda data: (
            data[DATA_USAGE].feed_in.usage_total
            if data[DATA_USAGE] and data[DATA_USAGE].feed_in
            else None
        ),
        attr_fn=lambda data: (
            {"Amount feed-in yesterday": data[DATA_USAGE].feed_in}
            if data[DATA_USAGE] and data[DATA_USAGE].feed_in
            else {}
        ),
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
        value_fn=lambda data: (
            data[DATA_USER].advancedPaymentAmount
            if data[DATA_USER] and data[DATA_USER].advancedPaymentAmount
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="reference",
        name="Reference",
        translation_key="reference",
        icon="mdi:numeric",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].reference
            if data[DATA_USER] and data[DATA_USER].reference
            else None
        ),
        # attr_fn=lambda data: data[DATA_USER_SITES].delivery_sites
    ),
    FrankEnergieEntityDescription(
        key="status",
        name="Status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=["active", "delivery_ended", "inactive", "in_delivery"],
        icon="mdi:connection",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER_SITES].status.lower()
            if data[DATA_USER_SITES] and data[DATA_USER_SITES].status
            else None
        ),
        attr_fn=lambda data: (
            {
                "Connections status": next(
                    (
                        connection["status"]
                        for connection in data[DATA_USER].connections
                        if connection.get("status")
                    ),
                    None,
                )
            }
            if data[DATA_USER] and data[DATA_USER].connections
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="propositionType",
        name="Proposition type",
        translation_key="proposition_type",
        device_class=SensorDeviceClass.ENUM,
        options=["dynamic"],
        icon="mdi:file-document-check",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER_SITES].propositionType.lower()
            if data[DATA_USER_SITES] and data[DATA_USER_SITES].propositionType
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="contractProductName",
        name="Contract Product Name",
        translation_key="contract_product_name",
        icon="mdi:file-document-check",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            next(
                (
                    (connection.get("externalDetails", {}).get("contract") or {}).get(
                        "productName"
                    )
                    for connection in data[DATA_USER].connections
                    if (
                        connection.get("externalDetails")
                        and connection["segment"] == "ELECTRICITY"
                        and connection.get("externalDetails", {}).get("contract")
                    )
                ),
                None,
            )
            if data[DATA_USER] and data[DATA_USER].connections
            else None
        ),
        attr_fn=lambda data: (
            (
                lambda product_name: (
                    # parser geeft dict terug → attributen
                    {"parsed": _parse_contract_product_name(product_name)}
                    if product_name
                    else {}
                )
            )(
                next(
                    (
                        (
                            connection.get("externalDetails", {}).get("contract") or {}
                        ).get("productName")
                        for connection in data[DATA_USER].connections
                        if (
                            connection.get("externalDetails")
                            and connection.get("segment") == "ELECTRICITY"
                            and connection.get("externalDetails", {}).get("contract")
                        )
                    ),
                    None,
                )
            )
            if data[DATA_USER] and data[DATA_USER].connections
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="countryCode",
        name="Country code",
        translation_key="country_code",
        icon="mdi:flag",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].countryCode
            if data[DATA_USER] and data[DATA_USER].countryCode
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="bankAccountNumber",
        name="Bankaccount Number",
        translation_key="bank_account_number",
        icon="mdi:bank",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].externalDetails.debtor.bankAccountNumber
            if data[DATA_USER]
            and data[DATA_USER].externalDetails
            and data[DATA_USER].externalDetails.debtor
            else None
        ),
        attr_fn=lambda data: (
            {
                "Ondertekend op": getattr(
                    data[DATA_USER].activePaymentAuthorization, "signedAt", "-"
                ),
                "Status": getattr(
                    data[DATA_USER].activePaymentAuthorization, "status", "-"
                ),
            }
            if data[DATA_USER] and data[DATA_USER].activePaymentAuthorization
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="preferredAutomaticCollectionDay",
        name="Preferred Automatic Collection Day",
        translation_key="preferred_automatic_collection_day",
        icon="mdi:bank",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].externalDetails.debtor.preferredAutomaticCollectionDay
            if data[DATA_USER]
            and data[DATA_USER].externalDetails
            and data[DATA_USER].externalDetails.debtor
            else None
        ),
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
            if data[DATA_USER]
            and data[DATA_USER].externalDetails
            and data[DATA_USER].externalDetails.person
            else None
        ),
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
            if data[DATA_USER]
            and data[DATA_USER].externalDetails
            and data[DATA_USER].externalDetails.contact
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="segments",
        name="Segments",
        translation_key="segments",
        icon="mdi:segment",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            ", ".join(data[DATA_USER_SITES].segments)
            if data[DATA_USER_SITES] and data[DATA_USER_SITES].segments
            else None
        ),
        attr_fn=lambda data: ({"available_segments": ["electricity", "gas"]}),
    ),
    FrankEnergieEntityDescription(
        key="gridOperator",
        name="Gridoperator",
        translation_key="grid_operator",
        icon="mdi:transmission-tower",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            next(
                (
                    connection["externalDetails"]["gridOperator"]
                    for connection in data[DATA_USER].connections
                    if connection.get("externalDetails")
                    and connection["externalDetails"].get("gridOperator")
                ),
                None,
            )
            if data[DATA_USER] and data[DATA_USER].connections
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="EAN",
        name="EAN (Energy Account Number)",
        translation_key="ean",
        icon="mdi:meter-electric",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            next(
                (
                    connection["EAN"]
                    for connection in data[DATA_USER].connections
                    if connection.get("EAN")
                ),
                None,
            )
            if data[DATA_USER] and data[DATA_USER].connections
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="meterType",
        name="Meter Type",
        translation_key="meter_type",
        device_class=SensorDeviceClass.ENUM,
        options=["slm"],
        icon="mdi:meter-electric",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            next(
                (
                    connection["meterType"].lower()
                    for connection in data[DATA_USER].connections
                    if connection.get("meterType")
                ),
                None,
            )
            if data[DATA_USER] and data[DATA_USER].connections
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="contractStartDate",
        name="Contract Start Date",
        translation_key="contract_start_date",
        icon="mdi:file-document-outline",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            next(
                (
                    _parse_contract_date(
                        connection.get("externalDetails", {})
                        .get("contract", {})
                        .get("startDate")
                    )
                    for connection in getattr(data.get(DATA_USER), "connections", [])
                    if connection.get("externalDetails", {})
                    .get("contract", {})
                    .get("startDate")
                ),
                None,
            )
            if data[DATA_USER] and data[DATA_USER].connections
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="EleccontractStatus",
        name="Electricity Contract Status",
        translation_key="elec_contract_status",
        device_class=SensorDeviceClass.ENUM,
        options=["loss", "switched"],
        icon="mdi:file-document-outline",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            next(
                (
                    conn.contractStatus.lower()
                    for conn in (
                        getattr(data.get(DATA_USER), "connections", None)
                        if data.get(DATA_USER) is not None
                        else []
                    )
                    if getattr(conn, "segment", None) == "ELECTRICITY"
                    and getattr(conn, "contractStatus", None)
                ),
                None,
            )
            if data[DATA_USER] and data[DATA_USER].connections
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="GascontractStatus",
        name="Gas Contract Status",
        translation_key="gas_contract_status",
        device_class=SensorDeviceClass.ENUM,
        options=["loss", "switched"],
        icon="mdi:file-document-outline",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            next(
                (
                    conn.contractStatus.lower()
                    for conn in (
                        getattr(data.get(DATA_USER), "connections", None)
                        if data.get(DATA_USER) is not None
                        else []
                    )
                    if getattr(conn, "segment", None) == "GAS"
                    and getattr(conn, "contractStatus", None)
                ),
                None,
            )
            if data[DATA_USER] and data[DATA_USER].connections
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="deliveryStartDate",
        name="Delivery start date",
        translation_key="delivery_start_date",
        icon="mdi:calendar-clock",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            _parse_site_date(data[DATA_USER_SITES].deliveryStartDate)
            if data[DATA_USER_SITES]
            else None
        ),
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
            _parse_site_date(data[DATA_USER_SITES].deliveryEndDate)
            if data[DATA_USER_SITES]
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="firstMeterReadingDate",
        name="First meter reading date",
        translation_key="first_meter_reading_date",
        icon="mdi:calendar-clock",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            _parse_site_date(data[DATA_USER_SITES].firstMeterReadingDate)
            if data[DATA_USER_SITES]
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="lastMeterReadingDate",
        name="Last meter reading date",
        translation_key="last_meter_reading_date",
        icon="mdi:calendar-clock",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            _parse_site_date(data[DATA_USER_SITES].lastMeterReadingDate)
            if data[DATA_USER_SITES]
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="treesCount",
        name="Trees count",
        translation_key="trees_count",
        icon="mdi:tree-outline",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].treesCount
            if data[DATA_USER] and data[DATA_USER].treesCount is not None
            else 0
        ),
    ),
    FrankEnergieEntityDescription(
        key="friendsCount",
        name="Friends count",
        translation_key="friends_count",
        icon="mdi:account-group",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].friendsCount
            if data[DATA_USER] and data[DATA_USER].friendsCount is not None
            else 0
        ),
    ),
    FrankEnergieEntityDescription(
        key="deliverySite",
        name="Delivery Site",
        translation_key="delivery_site",
        icon="mdi:home",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER_SITES].format_delivery_site_as_dict[0]
            if data[DATA_USER_SITES]
            and data[DATA_USER_SITES].format_delivery_site_as_dict
            else None
        ),
        # attr_fn=lambda data: next(
        #     iter(data[DATA_USER_SITES].delivery_site_as_dict.values()))
    ),
    FrankEnergieEntityDescription(
        key="rewardPayoutPreference",
        name="Reward payout preference",
        translation_key="reward_payout_preference",
        device_class=SensorDeviceClass.ENUM,
        options=["discount", "trees"],
        icon="mdi:trophy",
        authenticated=True,
        entity_registry_enabled_default=False,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].UserSettings.get("rewardPayoutPreference").lower()
            if data.get(DATA_USER)
            and data[DATA_USER].UserSettings
            and data[DATA_USER].UserSettings.get("rewardPayoutPreference")
            else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="site_reference",
        name="Site Reference",
        translation_key="site_reference",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:identifier",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER_SITES].reference if data.get(DATA_USER_SITES) else None
        ),
    ),
    FrankEnergieEntityDescription(
        key="token_expires_at",
        name="Token expires at",
        translation_key="token_expires_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:key-chain-variant",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data.get(DATA_TOKEN_EXPIRES_AT),
    ),
    FrankEnergieEntityDescription(
        key="refresh_token_expires_at",
        name="Refresh token expires at",
        translation_key="refresh_token_expires_at",
        icon="mdi:key-chain-variant",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data.get(DATA_REFRESH_TOKEN_EXPIRES_AT),
    ),
    FrankEnergieEntityDescription(
        key="elec_lowest_4p",
        name="Lowest average electricity price (4 periods)",
        translation_key="elec_lowest_4p",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        is_electricity=True,
        value_fn=lambda data: result[0] if (result := lowest_window(data, 4)) else None,
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: result[1].date_from,
                ATTR_TILL_TIME: result[2].date_till,
                "average_price": result[0],
            }
            if (result := lowest_window(data, 4))
            else {}
        ),
    ),
    FrankEnergieEntityDescription(
        key="elec_lowest_16p",
        name="Lowest average electricity price (16 periods)",
        translation_key="elec_lowest_16p",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        is_electricity=True,
        value_fn=lambda data: (
            result[0] if (result := lowest_window(data, 16)) else None
        ),
        attr_fn=lambda data: (
            {
                ATTR_FROM_TIME: result[1].date_from,
                ATTR_TILL_TIME: result[2].date_till,
                "average_price": result[0],
            }
            if (result := lowest_window(data, 16))
            else {}
        ),
    ),
)


class EnodeChargerSensor(CoordinatorEntity, SensorEntity):
    """Representation of an Enode charger sensor, grouped under its own device."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: ChargerSensorDescription,
        charger: EnodeCharger,
    ) -> None:
        """Initialize the Enode charger sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._charger_id = charger.id

        info = charger.information or {}
        brand = info.get("brand", "Frank Energie")
        model = info.get("model", "Charger")
        charger_name = f"{brand} {model}".strip() or f"Charger {charger.id}"

        self._attr_unique_id = f"{DOMAIN}_{self._charger_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._charger_id)},
            manufacturer=brand,
            model=model,
            name=charger_name,
        )
        if coordinator.config_entry:
            self._attr_device_info["via_device"] = (
                DOMAIN,
                f"{coordinator.config_entry.entry_id}_{SERVICE_NAME_ENODE_CHARGERS}",
            )

    def _get_charger(self) -> Any | None:
        """Look up the current charger object from coordinator data by ID."""
        enode = (
            self.coordinator.data.get(DATA_ENODE_CHARGERS)
            if self.coordinator.data
            else None
        )
        if not enode:
            return None
        return next((c for c in enode.chargers if c.id == self._charger_id), None)

    @property
    def native_value(self) -> StateType:
        """Return the current value from the latest coordinator data."""
        charger = self._get_charger()
        if charger is None:
            return None
        try:
            return self.entity_description.value_fn(charger)
        except (TypeError, AttributeError, KeyError):
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra attributes."""
        charger = self._get_charger()
        if (
            charger is None
            or not hasattr(self.entity_description, "attr_fn")
            or self.entity_description.attr_fn is None
        ):
            return {}
        try:
            return self.entity_description.attr_fn(charger)
        except (TypeError, AttributeError, KeyError):
            return {}


class FrankEnergieSensor(
    CoordinatorEntity[DataUpdateCoordinator[_DataT]],
    SensorEntity,
    Generic[_DataT],
):
    """Representation of a Frank Energie sensor."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_icon = ICON
    _unsub_update: Callable[[], None] | None = None
    # _attr_suggested_display_precision = DEFAULT_ROUND
    # _attr_device_class = SensorDeviceClass.MONETARY
    # _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except (TypeError, IndexError, ValueError, AttributeError):
            return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        if self._attr_unit_of_measurement == UNIT_GAS:
            if (
                self.coordinator.data
                and (gas_data := self.coordinator.data.get(DATA_GAS))
                and (per_unit := getattr(gas_data, "per_unit", None))
            ):
                return PER_UNIT_TO_UNIT.get(
                    per_unit.upper(), self._attr_unit_of_measurement
                )
        return self._attr_unit_of_measurement

    _no_record_keys: ClassVar[frozenset[str]] = frozenset(
        {
            "elec_all",
            "gas_all",
            "elec_tax",
            "elec_tax_markup",
            "elec_market",
            "elec_avg",
            "elec_tomorrow_avg",
            "elec_market_upcoming",
            "elec_upcoming",
            "average_electricity_price_upcoming_all_in",
            "average_electricity_price_upcoming_market",
            "average_electricity_price_upcoming_market_tax",
            "average_electricity_price_upcoming_market_tax_markup",
            "average_electricity_price_all_hours_all_in",
            "prices",
            "prices_today",
            "prices_tomorrow",
            "prices_upcoming",
            "all_prices",
            "hourly_prices",
            "quarter_hour_prices",
            "market_prices",
            "market_prices_upcoming",
            "today_prices",
            "tomorrow_prices",
            "upcoming_prices",
            "upcoming_market_prices",
            "upcoming_market_tax_prices",
            "upcoming_market_tax_markup_prices",
            # metadata / nested
            "connections",
            "contracts",
            "user",
            "site",
            "settings",
            "sessions",
            "summaries",
            # tax & cost breakdowns
            "gas_tax",
            "gas_tax_markup",
            "fixed_delivery_costs",
            "variable_delivery_costs",
        }
    )

    _unrecorded_attributes: ClassVar[frozenset[str]] = _no_record_keys

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: FrankEnergieEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        self.entity_description: FrankEnergieEntityDescription = description
        if hasattr(self.entity_description, "state_class"):
            self._attr_state_class = getattr(
                self.entity_description, "state_class", None
            )
        else:
            self._attr_state_class = None
        self._attr_device_class = self.entity_description.device_class
        self._attr_options = self.entity_description.options

        if hasattr(self.entity_description, "native_unit_of_measurement"):
            self._attr_unit_of_measurement = getattr(
                self.entity_description, "native_unit_of_measurement", None
            )
        else:
            self._attr_unit_of_measurement = None

        self._attr_unique_id = f"{entry.unique_id}.{description.key}"

        # Do not set extra identifier for default service, backwards compatibility
        device_info_identifiers: set[tuple[str, str]] = (
            {(DOMAIN, f"{entry.entry_id}")}
            if description.service_name == SERVICE_NAME_PRICES
            else {(DOMAIN, f"{entry.entry_id}_{description.service_name}")}
        )

        user_data = (
            coordinator.data.get(DATA_USER) if coordinator.data is not None else None
        )

        self._attr_device_info = DeviceInfo(
            identifiers=device_info_identifiers,
            name=f"{COMPONENT_TITLE} - {description.service_name}",
            translation_key=device_translation_key(description.service_name),
            manufacturer=COMPONENT_TITLE,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=(getattr(user_data, "websiteUrl", None) or API_CONF_URL),
            model=description.service_name,
            sw_version=VERSION,
        )

        # Set defaults or exceptions for non default sensors.
        self._attr_icon = description.icon or self._attr_icon

        self._update_job = HassJob(self._handle_scheduled_update)
        self._unsub_update = None

        # Zet enabled_default op False bij feed-in sensor zonder waarde
        if hasattr(description, "is_feed_in") and description.is_feed_in:
            user_data = coordinator.data.get(DATA_USER)
            connection = user_data.connections[0]
            estimated_feed_in = int(connection.get("estimatedFeedIn"))
            _LOGGER.debug("estimated_feed_in = %s", estimated_feed_in)
            _LOGGER.debug("has connections = %s", hasattr(user_data, "connections"))
            _LOGGER.debug("user connections = %s", user_data.connections)

            if user_data and connection:
                estimated_feed_in = int(connection.get("estimatedFeedIn"))

            _LOGGER.debug(
                "_attr_entity_registry_enabled_default = %s", estimated_feed_in > 0
            )

            self._attr_entity_registry_enabled_default = estimated_feed_in > 0

        super().__init__(coordinator)

    async def async_update(self) -> None:
        """Get the latest data and updates the states."""
        data = self.coordinator.data
        try:
            self._attr_native_value = self.entity_description.value_fn(data)
        except (IndexError, ValueError):
            self._attr_native_value = None
        except (AttributeError, TypeError, KeyError):
            _LOGGER.debug(
                "Sensor %s: upstream data unavailable, retaining last value",
                self.entity_description.key,
            )
        except ZeroDivisionError:
            _LOGGER.exception("Division by zero error in FrankEnergieSensor")
            self._attr_native_value = None

        # Cancel the currently scheduled event if there is any
        if self._unsub_update:
            self._unsub_update()
            self._unsub_update = None

        # Schedule the next update at exactly the next whole hour sharp or every quarter hour
        # TODO: Use hour updates when prices are available hourly only
        now = dt_util.now(timezone.utc)
        minute = now.minute
        if minute >= 45:
            # Next whole hour
            next_update_time = now.replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(hours=1)
        else:
            # Round up to next quarter: 0 → 15, 1–14 → 15, 15–29 → 30, 30–44 → 45
            next_quarter = ((minute // 15) + 1) * 15
            next_update_time = now.replace(
                minute=0, second=0, microsecond=0
            ) + timedelta(minutes=next_quarter)

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
    def extra_state_attributes(self) -> dict[str, object]:
        if not self.coordinator.data:
            return {}

        try:
            return self.entity_description.attr_fn(self.coordinator.data) or {}
        except Exception:
            return {}

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None


class FrankEnergieSmartBatterySensor(FrankEnergieSensor):
    """Representation of a Frank Energie Smart Battery sensor."""

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: FrankEnergieEntityDescription,
        entry: ConfigEntry,
        battery_id: str,
        battery_name: str,
        battery_brand: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description, entry)
        self._battery_id = battery_id
        self._battery_name = battery_name
        self._battery_brand = battery_brand
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.unique_id}.{battery_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._battery_id)},
            name=self._battery_name,
            manufacturer=self._battery_brand,
            model="SmartBattery",
            via_device=(DOMAIN, f"{self._entry_id}_{SERVICE_NAME_BATTERIES}"),
        )


class FrankEnergieBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for Frank Energie integration."""

    entity_description: FrankEnergieBinaryEntityDescription

    def __init__(
        self,
        coordinator,
        description: FrankEnergieBinaryEntityDescription,
        config_entry,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_name = description.name
        self._attr_icon = description.icon
        self._attr_unique_id = description.key
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        if self.entity_description.value_fn:
            return self.entity_description.value_fn(
                self.coordinator.data, self.coordinator
            )
        return None

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        if self.entity_description.attr_fn:
            return self.entity_description.attr_fn(
                self.coordinator.data, self.coordinator
            )
        return {}


class SmartBatteriesData:
    """Class to hold and manage Smart Batteries data."""

    def __init__(self, batteries: list[Any]):
        """
        Initialize SmartBatteriesData.

        :param batteries: List of battery dictionaries or _SmartBattery instances.
        """
        self.batteries = batteries

    class _SmartBattery:
        """Internal representation of a Smart Battery."""

        def __init__(
            self,
            brand: str,
            capacity: float,
            external_reference: str,
            id: str,
            max_charge_power: float,
            max_discharge_power: float,
            provider: str,
            created_at: Any,
            updated_at: Any,
            settings: Optional[dict[str, Any]] = None,
            summary: Optional[dict[str, Any]] = None,
        ) -> None:
            """Initialize a Smart Battery instance."""
            self.brand = brand
            self.capacity = capacity
            self.external_reference = external_reference
            self.id = id
            self.max_charge_power = max_charge_power
            self.max_discharge_power = max_discharge_power
            self.provider = provider
            self.created_at = self._validate_datetime(created_at, "created_at")
            self.updated_at = self._validate_datetime(updated_at, "updated_at")
            self.settings = settings
            self.summary = summary

        @staticmethod
        def _validate_datetime(value: Any, field_name: str) -> datetime:
            """
            Validate that a value is a timezone-aware datetime object.

            :param value: The value to validate.
            :param field_name: Name of the field for error reporting.
            :return: A valid datetime object.
            :raises ValueError: If value is not a valid datetime.
            """
            if not isinstance(value, datetime):
                raise ValueError(
                    "Field '%s' must be a datetime object, got %s"
                    % (field_name, type(value).__name__)
                )
            if value.tzinfo is None:
                raise ValueError("Field '%s' must be timezone-aware" % field_name)
            return value

        def __repr__(self) -> str:
            return f"SmartBattery(brand={self.brand}, capacity={self.capacity}, id={self.id})"

    def get_smart_batteries(self) -> list[_SmartBattery]:
        """Return the list of parsed SmartBattery objects."""
        return [
            self._SmartBattery(**b) if isinstance(b, dict) else b
            for b in self.batteries
        ]

    def get_battery_count(self) -> int:
        """Return the number of smart batteries."""
        return len(self.batteries)


def _build_single_smart_battery_descriptions(
    battery: Any,
    i: int,
) -> list[FrankEnergieEntityDescription]:
    """Build dynamic entity descriptions for a single smart battery."""

    descriptions: list[FrankEnergieEntityDescription] = []

    if not hasattr(battery, "id"):
        _LOGGER.warning("Battery at index %s has no ID. Skipping.", i)
        return descriptions

    base_key = f"smart_battery_{i}"
    # name_prefix is removed since the user requested to drop the "Battery 1" prefix

    # capture values (avoid lambda late binding)
    brand = battery.brand
    capacity = battery.capacity
    reference = battery.external_reference
    battery_id = battery.id
    max_charge_power = battery.max_charge_power
    max_discharge_power = battery.max_discharge_power
    provider = battery.provider
    created_at = battery.created_at
    updated_at = battery.updated_at

    settings = battery.settings
    summary = battery.summary

    _LOGGER.debug(
        "Battery %s parsed | settings=%s summary=%s",
        battery_id,
        settings,
        summary,
    )

    descriptions.extend(
        [
            FrankEnergieEntityDescription(
                key=f"{base_key}_brand",
                name="Brand",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:battery",
                value_fn=lambda _, val=brand: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_id",
                name="ID",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:fingerprint",
                value_fn=lambda _, val=battery_id: val,
                entity_registry_enabled_default=False,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_external_reference",
                name="External Reference",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:identifier",
                value_fn=lambda _, val=reference: val,
                entity_registry_enabled_default=False,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_max_charge_power",
                name="Max Charge Power",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:flash",
                device_class=SensorDeviceClass.POWER,
                native_unit_of_measurement=UnitOfPower.KILO_WATT,
                suggested_display_precision=1,
                value_fn=lambda _, val=max_charge_power: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_max_discharge_power",
                name="Max Discharge Power",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:flash-outline",
                device_class=SensorDeviceClass.POWER,
                native_unit_of_measurement=UnitOfPower.KILO_WATT,
                suggested_display_precision=1,
                value_fn=lambda _, val=max_discharge_power: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_capacity",
                name="Capacity",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:battery-charging",
                device_class=SensorDeviceClass.ENERGY,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                suggested_display_precision=0,
                value_fn=lambda _, val=capacity: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_provider",
                name="Provider",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:factory",
                value_fn=lambda _, val=provider: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_created_at",
                name="Created At",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:calendar-clock",
                device_class=SensorDeviceClass.TIMESTAMP,
                value_fn=lambda _, val=created_at: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_updated_at",
                name="Updated At",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:calendar-clock",
                device_class=SensorDeviceClass.TIMESTAMP,
                value_fn=lambda _, val=updated_at: val,
            ),
        ]
    )

    if settings:
        descriptions.extend(
            [
                FrankEnergieEntityDescription(
                    key=f"{base_key}_battery_mode",
                    translation_key="battery_mode",
                    name="Mode",
                    authenticated=True,
                    service_name=SERVICE_NAME_BATTERIES,
                    icon="mdi:battery",
                    device_class=SensorDeviceClass.ENUM,
                    options=["mode_smart", "mode_manual", "self_consumption_mix"],
                    value_fn=lambda data, idx=i: (
                        b.settings.battery_mode.lower()
                        if (bats := data.get(DATA_BATTERIES))
                        and bats.batteries
                        and idx < len(bats.batteries)
                        and (b := bats.batteries[idx])
                        and b.settings
                        else None
                    ),
                    attr_fn=lambda data, idx=i: (
                        asdict(b.settings)
                        if (bats := data.get(DATA_BATTERIES))
                        and bats.batteries
                        and idx < len(bats.batteries)
                        and (b := bats.batteries[idx])
                        and b.settings
                        else {}
                    ),
                ),
                FrankEnergieEntityDescription(
                    key=f"{base_key}_imbalance_trading_strategy",
                    translation_key="battery_imbalance_trading_strategy",
                    name="Imbalance Strategy",
                    authenticated=True,
                    service_name=SERVICE_NAME_BATTERIES,
                    icon="mdi:chart-line",
                    device_class=SensorDeviceClass.ENUM,
                    options=[
                        "strategy_balanced",
                        "strategy_max_return",
                        "strategy_min_degradation",
                        "aggressive",
                    ],
                    value_fn=lambda data, idx=i: (
                        b.settings.imbalance_trading_strategy.lower()
                        if (bats := data.get(DATA_BATTERIES))
                        and bats.batteries
                        and idx < len(bats.batteries)
                        and (b := bats.batteries[idx])
                        and b.settings
                        else None
                    ),
                    attr_fn=lambda data, idx=i: (
                        asdict(b.settings)
                        if (bats := data.get(DATA_BATTERIES))
                        and bats.batteries
                        and idx < len(bats.batteries)
                        and (b := bats.batteries[idx])
                        and b.settings
                        else {}
                    ),
                ),
            ]
        )

    if summary:
        descriptions.extend(
            [
                FrankEnergieEntityDescription(
                    key=f"{base_key}_state_of_charge",
                    name="State of Charge",
                    authenticated=True,
                    service_name=SERVICE_NAME_BATTERIES,
                    icon="mdi:battery-high",
                    device_class=SensorDeviceClass.BATTERY,
                    native_unit_of_measurement="%",
                    value_fn=lambda data, idx=i: (
                        b.summary.last_known_state_of_charge
                        if (bats := data.get(DATA_BATTERIES))
                        and bats.batteries
                        and idx < len(bats.batteries)
                        and (b := bats.batteries[idx])
                        and b.summary
                        and b.summary.last_known_status.lower()
                        != "status_unreliable_data"
                        else None
                    ),
                ),
                FrankEnergieEntityDescription(
                    key=f"{base_key}_status",
                    translation_key="battery_status",
                    name="Status",
                    authenticated=True,
                    service_name=SERVICE_NAME_BATTERIES,
                    icon="mdi:battery-clock",
                    device_class=SensorDeviceClass.ENUM,
                    options=[
                        "status_charging",
                        "status_discharging",
                        "status_idle",
                        "status_unreliable_data",
                        "status_offline",
                        "status_standby",
                        "separate_imbalances",
                    ],
                    value_fn=lambda data, idx=i: (
                        b.summary.last_known_status.lower()
                        if (bats := data.get(DATA_BATTERIES))
                        and bats.batteries
                        and idx < len(bats.batteries)
                        and (b := bats.batteries[idx])
                        and b.summary
                        else None
                    ),
                ),
                FrankEnergieEntityDescription(
                    key=f"{base_key}_last_update",
                    name="Last Update",
                    authenticated=True,
                    service_name=SERVICE_NAME_BATTERIES,
                    icon="mdi:clock-outline",
                    device_class=SensorDeviceClass.TIMESTAMP,
                    value_fn=lambda data, idx=i: (
                        b.summary.last_update
                        if (bats := data.get(DATA_BATTERIES))
                        and bats.batteries
                        and idx < len(bats.batteries)
                        and (b := bats.batteries[idx])
                        and b.summary
                        else None
                    ),
                ),
                FrankEnergieEntityDescription(
                    key=f"{base_key}_total_result",
                    name="Total Result",
                    authenticated=True,
                    service_name=SERVICE_NAME_BATTERIES,
                    device_class=SensorDeviceClass.MONETARY,
                    native_unit_of_measurement=CURRENCY_EURO,
                    suggested_display_precision=2,
                    icon="mdi:currency-eur",
                    value_fn=lambda data, idx=i: (
                        b.summary.total_result
                        if (bats := data.get(DATA_BATTERIES))
                        and bats.batteries
                        and idx < len(bats.batteries)
                        and (b := bats.batteries[idx])
                        and b.summary
                        else None
                    ),
                ),
            ]
        )

    return descriptions


def _build_aggregated_smart_batteries_descriptions() -> list[
    FrankEnergieEntityDescription
]:
    """Build aggregated entity descriptions for smart batteries."""
    descriptions: list[FrankEnergieEntityDescription] = []

    descriptions.extend(
        [
            FrankEnergieEntityDescription(
                key="total_capacity",
                name="Total Battery Capacity",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:battery-charging",
                device_class=SensorDeviceClass.ENERGY,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                suggested_display_precision=2,
                value_fn=lambda data: (
                    sum(
                        (b.capacity or 0)
                        for b in (
                            data.get(DATA_BATTERIES)
                            or type("_", (), {"batteries": []})()
                        ).batteries
                    )
                    or None
                ),
            ),
            FrankEnergieEntityDescription(
                key="total_max_charge_power",
                name="Total Max Charge Power",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:flash",
                device_class=SensorDeviceClass.POWER,
                native_unit_of_measurement=UnitOfPower.KILO_WATT,
                suggested_display_precision=1,
                value_fn=lambda data: (
                    sum(
                        (b.max_charge_power or 0)
                        for b in (
                            data.get(DATA_BATTERIES)
                            or type("_", (), {"batteries": []})()
                        ).batteries
                    )
                    or None
                ),
            ),
            FrankEnergieEntityDescription(
                key="total_max_discharge_power",
                name="Total Max Discharge Power",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:flash",
                device_class=SensorDeviceClass.POWER,
                native_unit_of_measurement=UnitOfPower.KILO_WATT,
                suggested_display_precision=1,
                value_fn=lambda data: (
                    sum(
                        (b.max_discharge_power or 0)
                        for b in (
                            data.get(DATA_BATTERIES)
                            or type("_", (), {"batteries": []})()
                        ).batteries
                    )
                    or None
                ),
            ),
            FrankEnergieEntityDescription(
                key="total_result",
                name="Total Result",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                device_class=SensorDeviceClass.MONETARY,
                native_unit_of_measurement=CURRENCY_EURO,
                suggested_display_precision=2,
                icon="mdi:currency-eur",
                value_fn=lambda data: (
                    sum(
                        (b.summary.total_result or 0)
                        for b in (
                            data.get(DATA_BATTERIES)
                            or type("_", (), {"batteries": []})()
                        ).batteries
                        if b.summary
                    )
                    or None
                ),
            ),
            FrankEnergieEntityDescription(
                key="average_state_of_charge",
                name="Average State of Charge",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                device_class=SensorDeviceClass.BATTERY,
                suggested_display_precision=0,
                icon="mdi:battery-high",
                native_unit_of_measurement="%",
                value_fn=lambda data: (
                    lambda bats: (
                        (
                            sum(
                                (b.summary.last_known_state_of_charge or 0)
                                for b in bats
                                if b.summary
                                and b.summary.last_known_status.lower()
                                != "status_unreliable_data"
                            )
                            / len(
                                [
                                    b
                                    for b in bats
                                    if b.summary
                                    and b.summary.last_known_status.lower()
                                    != "status_unreliable_data"
                                ]
                            )
                        )
                        if [
                            b
                            for b in bats
                            if b.summary
                            and b.summary.last_known_status.lower()
                            != "status_unreliable_data"
                        ]
                        else None
                    )
                )(
                    (
                        data.get(DATA_BATTERIES) or type("_", (), {"batteries": []})()
                    ).batteries
                ),
            ),
        ]
    )

    return descriptions


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie sensor entries."""
    _LOGGER.debug(
        "Setting up Frank Energie sensors for entry: %s", config_entry.entry_id
    )

    coordinator: FrankEnergieCoordinator = config_entry.runtime_data.coordinator
    batteries = coordinator.data.get(DATA_BATTERIES, [])

    session_coordinators: dict[str, FrankEnergieBatterySessionCoordinator] = {}
    entities: list = []

    if batteries and batteries.batteries:
        api = coordinator.api  # type: ignore[attr-defined]

        # Set up session coordinators per battery
        for battery in batteries.batteries:
            device_id = battery.id
            session_coordinator = FrankEnergieBatterySessionCoordinator(
                hass, config_entry, api, device_id
            )

            try:
                await session_coordinator.async_config_entry_first_refresh()
            except Exception as err:
                _LOGGER.exception(
                    "Failed to refresh battery session coordinator for device %s: %s",
                    device_id,
                    err,
                )
                continue

            session_coordinators[device_id] = session_coordinator

        config_entry.runtime_data.battery_session_coordinators = session_coordinators

    # Add an entity for each sensor type, when authenticated is True,
    # only add the entity if the user is authenticated
    # entities: list[SensorEntity] = []
    # entities: list[FrankEnergieSensor] = []
    # entities: list[FrankEnergieBatterySessionSensor] = []

    # Safely access user segments from DATA_USER_SITES
    user_segments = getattr(coordinator.data.get(DATA_USER_SITES), "segments", [])

    # Safely access user data, default to an empty dictionary if None

    user_data = None
    if coordinator.api.is_authenticated:
        if DATA_USER in coordinator.data:
            user_data = coordinator.data.get(DATA_USER, {})
            _LOGGER.debug("user_data: %s", user_data)
        if isinstance(user_data, object) and hasattr(user_data, "connections"):
            connections = user_data.connections
            first_connection = connections[0]
            estimated_feed_in = first_connection.get("estimatedFeedIn")
            _LOGGER.debug("estimated_feed_in1: %s", estimated_feed_in)

    # Safely access user data (defaulting to an empty dictionary if None)
    connections: list[dict] = []
    first_connection: dict | None = None
    estimated_feed_in: float | None = None

    if isinstance(user_data, object) and hasattr(user_data, "connections"):
        if isinstance(user_data.connections, list):
            connections = user_data.connections
        else:
            _LOGGER.warning(
                "Expected user_data.connections to be a list, got %s",
                type(user_data.connections).__name__,
            )
    else:
        _LOGGER.debug(
            "user_data does not have attribute 'connections' or is not valid: %s",
            type(user_data).__name__,
        )

    if connections:
        first_connection = connections[0]
        if isinstance(first_connection, dict):
            estimated_feed_in = first_connection.get("estimatedFeedIn")
        else:
            # Connection dataclass — use attribute access (DictLikeMixin also supports .get())
            estimated_feed_in = getattr(first_connection, "estimatedFeedIn", None)
    else:
        _LOGGER.debug("No connections found in user_data")

    _LOGGER.debug("estimated_feed_in: %s", estimated_feed_in)

    entities: list = [
        FrankEnergieSensor(
            coordinator,
            description,
            config_entry,
        )
        for description in SENSOR_TYPES
        if (
            (not description.authenticated or coordinator.api.is_authenticated)
            and (
                not description.is_gas
                or not coordinator.api.is_authenticated
                or "GAS" in user_segments
            )
            and (
                not description.is_feed_in
                or (estimated_feed_in is not None and estimated_feed_in > 0)
            )
            and not (
                description.service_name == SERVICE_NAME_GAS_PRICES
                and coordinator.api.is_authenticated
                and "GAS" not in user_segments
            )
        )
    ]

    if coordinator.api.is_authenticated and "GAS" not in user_segments:
        await _disable_gas_price_sensors(hass, config_entry)

    if (enode := coordinator.data.get(DATA_ENODE_CHARGERS)) and enode.chargers:
        _LOGGER.debug(
            "Setting up Enode charger sensors for %d chargers", len(enode.chargers)
        )
        for description in STATIC_ENODE_SENSOR_TYPES:
            if not description.authenticated or coordinator.api.is_authenticated:
                entities.append(
                    FrankEnergieSensor(coordinator, description, config_entry)
                )

        for charger in enode.chargers:
            for description in ENODE_CHARGER_SENSOR_TYPES:
                if not description.authenticated or coordinator.api.is_authenticated:
                    entities.append(
                        EnodeChargerSensor(coordinator, description, charger)
                    )

    # coordinator.data.get(DATA_BATTERIES)) = <class 'python_frank_energie.models.SmartBatteries'>
    if (batteries := coordinator.data.get(DATA_BATTERIES)) and batteries.batteries:
        _LOGGER.debug("Setting up smart battery sensors: %s", batteries)
        # SmartBatteries(smart_batteries=[SmartBatteries.SmartBattery(brand='Sessy', capacity=5.2, external_reference='AJM6UPPP', id='cm3sunryl0000tc3nhygweghn', max_charge_power=2.2, max_discharge_power=1.7, provider='SESSY', created_at=datetime.datetime(2024, 11, 22, 14, 41, 47, 853000, tzinfo=datetime.timezone.utc), updated_at=datetime.datetime(2025, 2, 7, 22, 3, 21, 898000, tzinfo=datetime.timezone.utc))])
        # <class 'python_frank_energie.models.SmartBatteries'>
        _LOGGER.debug("Setting up smart battery type: %s", type(batteries))
        _LOGGER.debug("Number of smart battery sensors: %d", len(batteries.batteries))
        _LOGGER.debug(
            "Setting up smart battery type: %s", type(batteries.batteries)
        )  # <class 'list'>
        aggregated_battery_descriptions = (
            _build_aggregated_smart_batteries_descriptions()
        )
        for description in (
            list(STATIC_BATTERY_SENSOR_TYPES) + aggregated_battery_descriptions
        ):
            if not description.authenticated or coordinator.api.is_authenticated:
                entities.append(
                    FrankEnergieSensor(coordinator, description, config_entry)
                )
                _LOGGER.debug("Added aggregate battery sensor for %s", description.key)

        for i, battery in enumerate(batteries.batteries):
            _LOGGER.debug("Setting up smart battery: %s", battery)
            _LOGGER.debug("Setting up smart battery type: %s", type(battery))
            _LOGGER.debug("Setting up smart battery brand: %s", battery.brand)
            _LOGGER.debug("Setting up smart battery id: %s", battery.id)
            _LOGGER.debug(
                "Setting up smart battery external_reference: %s",
                battery.external_reference,
            )
            _LOGGER.debug(
                "Setting up smart battery max_charge_power: %s",
                battery.max_charge_power,
            )
            _LOGGER.debug(
                "Setting up smart battery max_discharge_power: %s",
                battery.max_discharge_power,
            )
            _LOGGER.debug("Setting up smart battery provider: %s", battery.provider)
            _LOGGER.debug("Setting up smart battery created_at: %s", battery.created_at)
            _LOGGER.debug("Setting up smart battery updated_at: %s", battery.updated_at)
            _LOGGER.debug("Setting up smart battery capacity: %s", battery.capacity)

            single_battery_descriptions = _build_single_smart_battery_descriptions(
                battery, i
            )
            for description in single_battery_descriptions:
                if not description.authenticated or coordinator.api.is_authenticated:
                    entities.append(
                        FrankEnergieSmartBatterySensor(
                            coordinator,
                            description,
                            config_entry,
                            battery.id,
                            f"Smart Battery {battery.id}",
                            battery.brand,
                        )
                    )
                    _LOGGER.debug(
                        "Added individual smart battery sensor for %s", description.key
                    )

            # Create sensors for each battery session coordinator
            for battery_id, session_coordinator in session_coordinators.items():
                sessions_data = session_coordinator.data
                if sessions_data and sessions_data.sessions:
                    _LOGGER.debug(
                        "Creating battery session sensors for battery: %s",
                        battery_id,
                    )
                    for description in BATTERY_SESSION_SENSOR_DESCRIPTIONS:
                        if (
                            not description.authenticated
                            or coordinator.api.is_authenticated
                        ):
                            sensor = FrankEnergieBatterySessionSensor(
                                coordinator=session_coordinator,
                                description=description,
                                battery_id=battery_id,
                                is_total=False,
                            )
                        entities.append(sensor)
                else:
                    _LOGGER.debug(
                        "No session data found in session coordinator for battery %s (entry %s)",
                        battery_id,
                        config_entry.entry_id,
                    )

    enode_vehicles = coordinator.data.get(DATA_ENODE_VEHICLES)
    num_vehicles = len(enode_vehicles.vehicles) if enode_vehicles else 0
    _LOGGER.debug("Aantal voertuigen gevonden: %d", num_vehicles)

    if enode_vehicles and enode_vehicles.vehicles:
        enode_vehicle_sensors = []

        for veh_idx, vehicle in enumerate(enode_vehicles.vehicles):
            for description in ENODE_VEHICLE_SENSOR_TYPES:
                enode_vehicle_sensors.append(
                    EnodeVehicleSensor(hass, coordinator, description, vehicle, veh_idx)
                )

        for entity in enode_vehicle_sensors:
            _LOGGER.debug("Toegevoegde voertuig sensor: %s", entity.name)

        entities.extend(enode_vehicle_sensors)

    pv_systems = coordinator.data.get(DATA_PV_SYSTEMS)
    if pv_systems and pv_systems.systems:
        _LOGGER.debug(
            "Setting up smart PV sensors for %d systems", len(pv_systems.systems)
        )
        for system in pv_systems.systems:
            if system:
                entities.extend(
                    [
                        FrankEnergiePvSensor(coordinator, system.id, "total_bonus"),
                        FrankEnergiePvSensor(
                            coordinator, system.id, "operational_status"
                        ),
                        FrankEnergiePvSensor(coordinator, system.id, "steering_status"),
                    ]
                )

    try:
        async_add_entities(entities, update_before_add=True)
    except Exception:
        _LOGGER.exception("Failed to add entities for entry %s", config_entry.entry_id)
        raise

    _LOGGER.debug("All sensors added for entry: %s", config_entry.entry_id)


def _get_nested(data: object, *keys: str) -> object | None:
    """Safely get nested value from a dict."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _parse_iso_datetime(dt_str: str | None) -> datetime | None:
    """Parse ISO 8601 datetime string to aware datetime or return None."""
    if not dt_str:
        return None
    try:
        # Zorg dat 'Z' vervangen wordt door '+00:00' voor UTC
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        return datetime.fromisoformat(dt_str)
    except ValueError:
        return None


def _next_weekday_datetime(weekday: int, hour: int, minute: int) -> datetime:
    """
    Return the next datetime (UTC) for the given weekday and time.

    Args:
        weekday: Target weekday (0=Monday, 6=Sunday)
        hour: Hour of day (0–23)
        minute: Minute of hour (0–59)

    Returns:
        datetime: Timezone-aware datetime in UTC
    """
    now = dt_util.now()
    days_ahead = (weekday - now.weekday()) % 7
    # If it's today but the time has passed, jump to next week
    if days_ahead == 0 and (
        hour < now.hour or (hour == now.hour and minute <= now.minute)
    ):
        days_ahead = 7
    target_date = now + timedelta(days=days_ahead)
    return target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)


async def _disable_gas_price_sensors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Disable gas price sensors if user is authenticated but has no gas contract."""
    entity_registry = er.async_get(hass)

    for entity_id, entity_entry in entity_registry.entities.items():
        if (
            entity_entry.platform == DOMAIN
            and entity_entry.config_entry_id == entry.entry_id
            and entity_entry.domain == "sensor"
            and SERVICE_NAME_GAS_PRICES in entity_entry.unique_id.lower()
            and not entity_entry.disabled
        ):
            _LOGGER.info("Disabling gas price sensor '%s' (no gas contract)", entity_id)
            entity_registry.async_update_entity(
                entity_id=entity_id,
                disabled_by=er.RegistryEntryDisabler.INTEGRATION,
            )


def _parse_contract_product_name(code: str) -> dict[str, str]:
    """Parse een Frank Energie productcode naar betekenis per onderdeel."""

    parts = code.split("-")
    mapping: dict[str, str] = {
        # Algemeen
        "b2c": "Particulier klantcontract (Business-to-Consumer)",
        "b2b": "Zakelijk klantcontract (Business-to-Business)",
        # Energie type
        "e": "Elektriciteit",
        "g": "Gas",
        # Prijsmodel
        "vg": "Variabel gascomponent",
        "mp": "Marktprijs-basis contract (Market Price)",
        "dyn": "Dynamisch energiecontract",
        # Resolutie
        "qh": "Kwartier-gebaseerde prijsvorming (Quarter-Hourly)",
        "h": "Uurprijzen",
        # Add-ons
        "solar": "Solar-optimalisatie / zonnestroomvriendelijk",
        # Varianten
        "dt": "Dynamisch tarief",
        "var": "Variabele prijssamenstelling",
        "normaal": "Normaal telwerk",
        "dubbel": "Dubbel telwerk",
        "hoog": "Hoog tarief",
        "laag": "Laag tarief",
    }

    months = {
        "jan": "januari",
        "feb": "februari",
        "maa": "maart",
        "apr": "april",
        "mei": "mei",
        "jun": "juni",
        "jul": "juli",
        "aug": "augustus",
        "sep": "september",
        "okt": "oktober",
        "nov": "november",
        "dec": "december",
    }

    result: dict[str, str] = {}

    for part in parts:
        if part in mapping:
            result[part] = mapping[part]
            continue

        if part.isdigit() and len(part) == 4:
            result[part] = f"Contractjaar {part}"
            continue

        if part.lower() in months:
            result[part] = f"Startmaand: {months[part.lower()]}"
            continue

        result[part] = "Onbekend onderdeel"

    return result
