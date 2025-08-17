"""Frank Energie current electricity and gas price information service.
Sensor platform for Frank Energie integration."""
# sensor.py
# -*- coding: utf-8 -*-
# VERSION = "2025.8.6"

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Final, Optional, Union
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
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
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HassJob, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import event
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    API_CONF_URL,
    ATTR_TIME,
    ATTRIBUTION,
    COMPONENT_TITLE,
    CONF_COORDINATOR,
    DATA_BATTERIES,
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
    DOMAIN,
    ICON,
    SERVICE_NAME_BATTERIES,
    SERVICE_NAME_BATTERY_SESSIONS,
    SERVICE_NAME_COSTS,
    SERVICE_NAME_ENODE_CHARGERS,
    SERVICE_NAME_ENODE_VEHICLES,
    SERVICE_NAME_GAS_PRICES,
    SERVICE_NAME_PRICES,
    SERVICE_NAME_USAGE,
    SERVICE_NAME_USER,
    UNIT_ELECTRICITY,
    UNIT_GAS,
    VERSION,
)
from .coordinator import FrankEnergieBatterySessionCoordinator, FrankEnergieCoordinator

_LOGGER = logging.getLogger(__name__)

FORMAT_DATE = "%d-%m-%Y"


@dataclass
class FrankEnergieEntityDescription(SensorEntityDescription):
    """Describes Frank Energie sensor entity."""

    authenticated: bool = False
    service_name: str | None = SERVICE_NAME_PRICES
    value_fn: Callable[[dict], StateType] = field(default=lambda _: STATE_UNKNOWN)
    attr_fn: Callable[[dict], dict[str, Union[StateType, list, None]]] | None = None
    is_gas: bool = False
    is_electricity: bool = False
    is_feed_in: bool = False  # used to filter based on estimatedFeedIn
    is_battery_session: bool = False

    def __init__(
        self,
        key: str,
        name: str,
        device_class: Union[str, SensorDeviceClass] | None = None,
        state_class: str | None = None,
        native_unit_of_measurement: str | None = None,
        suggested_display_precision: int | None = None,
        authenticated: bool | None = None,
        service_name: Union[str, None] = None,
        value_fn: Callable[[dict], StateType] | None = None,
        attr_fn: Callable[[dict], dict[str, Union[StateType, list, None]]] | None = None,
        entity_registry_enabled_default: bool = True,
        entity_registry_visible_default: bool = True,
        entity_category: Union[str, EntityCategory] | None = None,
        translation_key: str | None = None,
        icon: str | None = None,
        is_gas: bool = False,  # used externally for gas filtering
        is_electricity: bool = False,  # used externally for electricity filtering
        is_feed_in: bool = False,  # used to filter based on estimatedFeedIn
        is_battery_session: bool = False,  # used to indicate battery session sensors
    ) -> None:
        super().__init__(
            key=key,
            name=name,
            device_class=(
                device_class if isinstance(device_class, SensorDeviceClass)
                else SensorDeviceClass(device_class) if device_class is not None and isinstance(device_class, str)
                else None
            ),
            state_class=state_class,
            native_unit_of_measurement=native_unit_of_measurement,
            suggested_display_precision=suggested_display_precision,
            translation_key=translation_key,
            entity_category=EntityCategory(entity_category) if isinstance(entity_category, str) else entity_category
        )
        object.__setattr__(self, 'authenticated', authenticated or False)
        object.__setattr__(self, 'service_name', service_name or SERVICE_NAME_PRICES)
        object.__setattr__(self, 'value_fn', value_fn or (lambda _: STATE_UNKNOWN))
        object.__setattr__(self, 'attr_fn', attr_fn if attr_fn is not None else lambda data: {})
        self.entity_registry_enabled_default = entity_registry_enabled_default
        self.entity_registry_visible_default = entity_registry_visible_default
        self.icon = icon
        self.is_gas = is_gas
        self.is_electricity = is_electricity
        self.is_feed_in = is_feed_in
        self.is_battery_session = is_battery_session

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


@dataclass(frozen=False, kw_only=True)
class EnodeVehicleEntityDescription(SensorEntityDescription):
    """Describes a sensor for an Enode vehicle."""

    value_fn: Callable[[dict], object]
    attr_fn: Callable[[dict], dict] = field(default_factory=lambda: (lambda _: {}))
    authenticated: bool = False
    service_name: str = SERVICE_NAME_ENODE_VEHICLES
    translation_key: str | None = None

    def __init__(
        self,
        key: str,
        name: str,
        device_class: Union[str, SensorDeviceClass] | None = None,
        state_class: str | None = None,
        native_unit_of_measurement: str | None = None,
        suggested_display_precision: int | None = None,
        authenticated: bool | None = None,
        service_name: Union[str, None] = None,
        value_fn: Callable[[dict], StateType] | None = None,
        attr_fn: Callable[[dict], dict[str, Union[StateType, list, None]]] | None = None,
        entity_registry_enabled_default: bool = True,
        entity_registry_visible_default: bool = True,
        entity_category: Union[str, EntityCategory] | None = None,
        translation_key: str | None = None,
        icon: str | None = None,
        is_gas: bool = False,  # used externally for gas filtering
        is_electricity: bool = False,  # used externally for electricity filtering
        is_feed_in: bool = False,  # used to filter based on estimatedFeedIn
        is_battery_session: bool = False,  # used to indicate battery session sensors
    ) -> None:
        super().__init__(
            key=key,
            name=name,
            # device_class=SensorDeviceClass(device_class) if device_class else None,
            device_class=device_class,
            state_class=state_class,
            native_unit_of_measurement=native_unit_of_measurement,
            suggested_display_precision=suggested_display_precision,
            translation_key=translation_key,
            entity_category=EntityCategory(entity_category) if isinstance(entity_category, str) else entity_category
        )
        object.__setattr__(self, 'value_fn', value_fn or (lambda _: STATE_UNKNOWN))
        self.icon = icon

    def get_state(self, data: dict) -> StateType:
        """Get the state value."""
        return self.value_fn(data)


@dataclass
class ChargerSensorDescription:
    """Describes a charger sensor entity."""
    key: str
    name: str
    device_class: Optional[str] = None
    state_class: Optional[str] = None
    native_unit_of_measurement: Optional[str] = None
    authenticated: bool = False
    service_name: str = SERVICE_NAME_ENODE_CHARGERS
    icon: Optional[str] = None
    value_fn: Callable[[dict], StateType] = field(default_factory=lambda: lambda _: STATE_UNKNOWN)
    attr_fn: Callable[[dict], dict[str, Union[StateType, list, None]]] = field(default_factory=lambda: lambda _: {})
    entity_registry_enabled_default: bool = True
    entity_registry_visible_default: bool = True
    entity_category: Optional[Union[str, EntityCategory]] = None

    def get_state(self, data: dict) -> StateType:
        """Get the state value."""
        try:
            return self.value_fn(data)
        except Exception as e:
            _LOGGER.error("Failed to evaluate state for '%s': %s", self.key, e)
            return STATE_UNAVAILABLE

    def get_attributes(self, data: dict) -> dict[str, Union[StateType, list]]:
        """Get the additional attributes."""
        try:
            return self.attr_fn(data)
        except Exception as e:
            _LOGGER.error("Failed to evaluate attributes for '%s': %s", self.key, e)
            return {}

    @property
    def is_authenticated(self) -> bool:
        """Check if the entity is authenticated."""
        return self.authenticated


class FrankEnergieBatterySessionSensor(
    CoordinatorEntity,  # type: ignore
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
        self._attr_unique_id = description.key if is_total else f"{battery_id}_{description.key}"
        self._attr_name = description.name
        self._attr_has_entity_name = True
        self._is_total = is_total

    @property
    def native_value(self) -> StateType:
        """Return the native value of the sensor."""
        try:
            value = self.entity_description.value_fn(self.coordinator.data)
            return round(value, self.entity_description.suggested_display_precision) if isinstance(value, (int, float)) else value
        except Exception as err:
            self._logger().error(
                "Failed to get native value for %s: %s", self.entity_description.key, err
            )
            return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        if not self.coordinator.data:
            return {}
        try:
            return self.entity_description.attr_fn(self.coordinator.data) or {}
        except Exception as err:
            _LOGGER.error("Failed to get attributes for %s: %s", self.entity_description.key, err)
            return {}

    @property
    def device_info(self) -> dict | None:
        """Return device info."""
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


# class EnodeVehicleSensor(SensorEntity):
class EnodeVehicleSensor(CoordinatorEntity, SensorEntity):
    """Representation of an Enode vehicle sensor."""
    _attr_should_poll = False
    _attr_has_entity_name = True  # Allow entity name to be set in the UI
    _attr_entity_registry_enabled_default = True  # Default to enabled in entity registry

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
        vehicle_name = f"{info.get('brand', '')} {info.get('model', '')}".strip() or None
        self._attr_unique_id = f"{DOMAIN}_{self._vehicle_id}_{description.key}"
        self._attr_name = description.name
        self._attr_translation_key = description.translation_key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._vehicle_id)},
            manufacturer=vehicle_data.get("information", {}).get("brand"),
            model=vehicle_data.get("information", {}).get("model"),
            serial_number=vehicle_data.get("information", {}).get("vin", None),
            name=vehicle_name,
            hw_version=str(vehicle_data.get("information", {}).get("year")),
        )

    @property
    def native_value(self) -> StateType:
        """Return the current value from the latest coordinator data."""
        vehicles_obj = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not vehicles_obj:
            return None

        latest_vehicle_data = next(
            (v for v in vehicles_obj.vehicles if v["id"] == self._vehicle_id),
            None
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
            (v for v in vehicles_obj.vehicles if v["id"] == self._vehicle_id),
            None
        )
        if not latest_vehicle_data:
            return {}

        try:
            return self.entity_description.attr_fn(latest_vehicle_data) or {}
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.error("Could not get attributes for %s: %s", self.entity_id, err)
            return {}

    @property
    def available(self) -> bool:
        """Return True if the native_value is valid."""
        try:
            value = self.native_value
            return value not in (STATE_UNAVAILABLE, STATE_UNKNOWN, None)
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.debug("Error checking availability for %s: %s", self.entity_id, err)
            return False

    async def async_update_data(self, data: dict) -> None:
        """Update stored data for the sensor."""
        self._vehicle_data = data
        self.async_write_ha_state()


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
    except KeyError as e:
        _LOGGER.error("Missing data key: %s", e)
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
            else None
        ),
        attr_fn=lambda data: {
            "chargers": [asdict(charger) for charger in data[DATA_ENODE_CHARGERS].chargers]
            if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers
            else []
        }
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
            len(data[DATA_BATTERIES].smart_batteries)
            if DATA_BATTERIES in data and data[DATA_BATTERIES].smart_batteries
            else None
        ),
        attr_fn=lambda data: {
            "batteries": [asdict(battery) for battery in data[DATA_BATTERIES].smart_batteries]
            if DATA_BATTERIES in data and data[DATA_BATTERIES].smart_batteries
            else []
        }
    ),
)

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

ENODE_VEHICLE_SENSOR_TYPES: list[EnodeVehicleEntityDescription] = [
    EnodeVehicleEntityDescription(
        key="vehicle_name",
        name="Vehicle Name",
        translation_key="vehicle_name",
        icon="mdi:car",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: ((data.get("information") or {}).get("brand") or "") +
        " " + ((data.get("information") or {}).get("model") or ""),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="can_smart_charge",
        name="Can Smart Charge",
        icon="mdi:car-electric",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: bool(data.get("canSmartCharge")) if "canSmartCharge" in data else None,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="is_reachable",
        name="Vehicle Reachable",
        icon="mdi:car-connected",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: bool(data.get("isReachable")) if "isReachable" in data else None,
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
            if isinstance(data, dict) else None
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
        value_fn=lambda data: _parse_iso_datetime(data.get("chargeState", {}).get("lastUpdated")),
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
            state if isinstance(state := data.get("chargeState", {}).get("powerDeliveryState"), str) else None
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
            value if isinstance(value := data.get("chargeSettings", {}).get("isSmartChargingEnabled"), bool) else None
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
            value if isinstance(value := data.get("chargeSettings", {}).get("isSolarChargingEnabled"), bool) else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="last_seen",
        name="Last Seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value := data.get("lastSeen"), str) and value
            else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="calculated_deadline",
        name="Calculated Deadline",
        icon="mdi:calendar-clock",
        authenticated=True,
        device_class=SensorDeviceClass.TIMESTAMP,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value := data.get("chargeSettings", {}).get("calculatedDeadline"), str) and value
            else None
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
        value_fn=lambda data: _parse_iso_datetime(data.get("chargeSettings", {}).get("deadline")),
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
            val if (val := data.get("chargeSettings", {}).get("maxChargeLimit")
                    ) is None or isinstance(val, (int, float)) else None
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
            val if (val := data.get("chargeSettings", {}).get("minChargeLimit")
                    ) is None or isinstance(val, (int, float)) else None
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
            vin if (vin := data.get("information", {}).get("vin")) is None or isinstance(vin, str) else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="intervention_description",
        name="Intervention Description",
        icon="mdi:alert-circle-outline",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            desc if (desc := (data.get("interventions") or {}).get(
                "description")) is None or isinstance(desc, str) else None
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    EnodeVehicleEntityDescription(
        key="intervention_title",
        name="Intervention Title",
        icon="mdi:alert-decagram",
        authenticated=True,
        service_name=SERVICE_NAME_ENODE_VEHICLES,
        value_fn=lambda data: (
            desc if (desc := (data.get("interventions") or {}).get("title")) is None or isinstance(desc, str) else None
        ),
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
                minutes // 60,
                minutes % 60
            )
            if isinstance(minutes := data.get("chargeSettings", {}).get(f"hour{d.capitalize()}"), int)
            else None
        ),
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    )
    for day in WEEKDAYS
]

BATTERY_SESSION_SENSOR_DESCRIPTIONS: Final[tuple[FrankEnergieEntityDescription, ...]] = (
    FrankEnergieEntityDescription(
        key="device_id",
        name="Device ID",
        icon="mdi:battery",
        native_unit_of_measurement=None,
        entity_category=None,
        state_class=None,
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: data.device_id,
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
            (
                datetime.strptime(data.period_start_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
                if isinstance(data.period_start_date, str)
                else data.period_start_date.replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
            )
            if data.period_start_date
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
            (
                datetime.strptime(data.period_end_date, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
                if isinstance(data.period_end_date, str)
                else data.period_end_date.replace(tzinfo=ZoneInfo("Europe/Amsterdam"))
            )
            if data.period_end_date
            else None
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
        value_fn=lambda data: data.period_trade_index,
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
        value_fn=lambda data: data.period_trading_result,
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
        value_fn=lambda data: data.period_total_result,
        attr_fn=lambda data: {
            "device_id": data.device_id,
            "period_start_date": data.period_start_date,
            "period_end_date": data.period_end_date,
            "period_trade_index": data.period_trade_index,
            "period_trading_result": data.period_trading_result,
            "period_total_result": data.period_total_result,
            "period_imbalance_result": data.period_imbalance_result,
            "period_epex_result": data.period_epex_result,
            "period_frank_slim": data.period_frank_slim,
            "sessions": [
                {
                    "date": s.date,
                    "trading_result": s.trading_result,
                    "cumulative_trading_result": s.cumulative_trading_result,
                }
                for s in data.sessions
            ],
            "total_trading_result": data.total_trading_result,
        }
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
        value_fn=lambda data: data.period_imbalance_result,
        # attr_fn=lambda data: {"imbalance": data.get("periodImbalanceResult", 0.0)},
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
        value_fn=lambda data: data.period_epex_result,
        # attr_fn=lambda data: {"epex": data.get("periodEpexResult", 0.0)},
    ),
    FrankEnergieEntityDescription(
        key="period_frank_slim_bonus",
        name="Period Frank Slim Bonus",
        icon="mdi:currency-eur",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        state_class="measurement",
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: data.period_frank_slim,
    ),
    FrankEnergieEntityDescription(
        key="total_trading_result",
        name="Total Trading Result",
        icon="mdi:currency-eur",
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        state_class="measurement",
        service_name=SERVICE_NAME_BATTERY_SESSIONS,
        value_fn=lambda data: data.total_trading_result,
    ),
)

SENSOR_TYPES: tuple[FrankEnergieEntityDescription, ...] = (
    FrankEnergieEntityDescription(
        key="elec_markup",
        name="Current electricity price (All-in)",
        translation_key="current_electricity_price",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].current_hour.total
        if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else None,
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
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].current_hour.market_price
        if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else None,
        attr_fn=lambda data: {
            "prices": data[DATA_ELECTRICITY].asdict("market_price", timezone="Europe/Amsterdam")
        }
    ),
    FrankEnergieEntityDescription(
        key="elec_tax",
        name="Current electricity price including tax",
        translation_key="current_electricity_price_incl_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].current_hour.market_price_with_tax
        if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else None,
        attr_fn=lambda data: {
            "prices": data[DATA_ELECTRICITY].asdict("market_price_with_tax", timezone="Europe/Amsterdam")
        }
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
            if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else None
        ),
        attr_fn=lambda data: {
            'prices': data[DATA_ELECTRICITY].asdict('market_price_tax', timezone="Europe/Amsterdam")
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
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data:
            data[DATA_ELECTRICITY].current_hour.sourcing_markup_price
        if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_tax_only",
        name="Current electricity tax only",
        translation_key="elec_tax_only",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=5,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data:
            data[DATA_ELECTRICITY].current_hour.energy_tax_price
        if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else None,
        entity_registry_enabled_default=True
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
        state_class=SensorStateClass.TOTAL,
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
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: data[DATA_GAS].current_hour.total
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
        attr_fn=lambda data: {"prices": data[DATA_GAS].asdict("total")}
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
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
        value_fn=lambda data: data[DATA_GAS].current_hour.market_price
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
        attr_fn=lambda data: {"prices": data[DATA_GAS].asdict("market_price")}
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
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
        value_fn=lambda data: data[DATA_GAS].current_hour.market_price_with_tax
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
        attr_fn=lambda data: {
            "prices": data[DATA_GAS].asdict("market_price_with_tax", timezone="Europe/Amsterdam")}
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
        entity_registry_enabled_default=True
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
        value_fn=lambda data: data[DATA_GAS].current_hour.market_price_tax
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
        entity_registry_enabled_default=True
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
        value_fn=lambda data: data[DATA_GAS].current_hour.sourcing_markup_price
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
        entity_registry_enabled_default=True
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
        value_fn=lambda data: data[DATA_GAS].current_hour.energy_tax_price
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
        entity_registry_enabled_default=True
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
        value_fn=lambda data: data[DATA_GAS].today_min.total
        if data[DATA_GAS] and data[DATA_GAS].today_min else None,
        attr_fn=lambda data: {ATTR_TIME: data[DATA_GAS].today_min.date_from}
        if data[DATA_GAS] and data[DATA_GAS].today_min else None
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
        value_fn=lambda data: data[DATA_GAS].today_max.total
        if data[DATA_GAS] and data[DATA_GAS].today_max else None,
        attr_fn=lambda data: {ATTR_TIME: data[DATA_GAS].today_max.date_from}
        if data[DATA_GAS] and data[DATA_GAS].today_max else None
    ),
    FrankEnergieEntityDescription(
        key="elec_min",
        name="Lowest electricity price today (All-in)",
        translation_key="elec_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
        state_class=SensorStateClass.TOTAL,
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
        state_class=SensorStateClass.TOTAL,
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
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
            ) else None
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
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: (
            100 / (
                data[DATA_GAS].current_hour.market_price /
                data[DATA_GAS].current_hour.market_price_tax
            )
            if (
                data[DATA_GAS] and
                data[DATA_GAS].current_hour and
                data[DATA_GAS].current_hour.market_price != 0 and
                data[DATA_GAS].current_hour.market_price_tax != 0
            ) else None
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
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].all_max.date_from}
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_min",
        name="Lowest electricity price tomorrow (All-in)",
        translation_key="elec_tomorrow_min",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_min.total
        if data[DATA_ELECTRICITY].tomorrow_min else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].tomorrow_min.date_from}
        if data[DATA_ELECTRICITY].tomorrow_min else {}
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_max",
        name="Highest electricity price tomorrow (All-in)",
        translation_key="elec_tomorrow_max",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=4,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_max.total
        if data[DATA_ELECTRICITY].tomorrow_max else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].tomorrow_max.date_from}
        if data[DATA_ELECTRICITY].tomorrow_max else {}
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
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].upcoming_min.date_from}
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
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_ELECTRICITY].upcoming_max.date_from}
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
        entity_registry_enabled_default=True
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
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_avg_market",
        name="Average electricity market price today",
        translation_key="average_electricity_market_price_today",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].today_market_avg,
        suggested_display_precision=3
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg_tax_markup",
        name="Average electricity price tomorrow including tax and markup",
        translation_key="average_electricity_price_tomorrow_including_tax_and_markup",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_avg.market_price_with_tax_and_markup
        if data[DATA_ELECTRICITY].tomorrow_avg else None,
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg",
        name="Average electricity price tomorrow (All-in)",
        translation_key="average_electricity_price_tomorrow_all_in",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_average_price_including_tax
        # value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_avg.market_price_with_tax
        if data[DATA_ELECTRICITY].tomorrow_avg else None,
    ),
    FrankEnergieEntityDescription(
        key="elec_tomorrow_avg_market",
        name="Average electricity market price tomorrow",
        translation_key="average_electricity_market_price_tomorrow",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_average_market_price
        # value_fn=lambda data: data[DATA_ELECTRICITY].tomorrow_avg.market_price
        if data[DATA_ELECTRICITY].tomorrow_avg else None,
    ),
    FrankEnergieEntityDescription(
        key="elec_market_upcoming",
        name="Average electricity market price upcoming",
        translation_key="average_electricity_market_price_upcoming",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_avg.market_price
        if data[DATA_ELECTRICITY].upcoming_avg else None,
        attr_fn=lambda data: {'upcoming_prices': data[DATA_ELECTRICITY].asdict(
            'market_price', upcoming_only=True, timezone="Europe/Amsterdam")
        }
        if data[DATA_ELECTRICITY].upcoming_avg else {}
    ),
    FrankEnergieEntityDescription(
        key="elec_upcoming",
        name="Average electricity price upcoming (All-in)",
        translation_key="average_electricity_price_upcoming_market",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_avg.total
        if data[DATA_ELECTRICITY].upcoming_avg else None,
        attr_fn=lambda data: {'upcoming_prices': data[DATA_ELECTRICITY].asdict(
            'total', upcoming_only=True, timezone="Europe/Amsterdam")},
    ),
    FrankEnergieEntityDescription(
        key="elec_all",
        name="Average electricity price all hours (All-in)",
        translation_key="elec_all",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].current_hour.market_price_including_tax_and_markup
        if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else None,
        attr_fn=lambda data: {'prices': data[DATA_ELECTRICITY].asdict(
            'market_price_including_tax_and_markup', timezone="Europe/Amsterdam")}
        if data.get(DATA_ELECTRICITY) and data[DATA_ELECTRICITY].current_hour else {},
        entity_registry_enabled_default=True
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
        value_fn=lambda data: data[DATA_GAS].tomorrow_average_price
        if data[DATA_GAS] else None,
        # value_fn=lambda data: data[DATA_GAS].tomorrow_avg.total,
        # if data[DATA_GAS].tomorrow_avg else None,
        attr_fn=lambda data: {'tomorrow_prices': data[DATA_GAS].asdict(
            'total', tomorrow_only=True, timezone="Europe/Amsterdam")}
        if data[DATA_GAS] else {},
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
        value_fn=lambda data: data[DATA_GAS].current_hour.market_price_including_tax_and_markup
        if data[DATA_GAS] and data[DATA_GAS].current_hour else None,
        attr_fn=lambda data: {'prices': data[DATA_GAS].asdict(
            'market_price_including_tax_and_markup', timezone="Europe/Amsterdam")}
        if data[DATA_GAS] and data[DATA_GAS].current_hour else {},
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
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: data[DATA_GAS].length
        if data[DATA_GAS] else None,
        entity_registry_enabled_default=True,
        entity_registry_visible_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_previoushour_market",
        name="Previous hour electricity market price",
        translation_key="elec_previoushour_market",
        suggested_display_precision=3,
        native_unit_of_measurement=UNIT_ELECTRICITY,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].previous_hour.market_price
        if data[DATA_ELECTRICITY].previous_hour else None,
        entity_registry_enabled_default=True
    ),
    FrankEnergieEntityDescription(
        key="elec_nexthour_market",
        name="Next hour electricity market price",
        translation_key="elec_nexthour_market",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
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
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: data[DATA_GAS].previous_hour.total
        if data[DATA_GAS] and data[DATA_GAS].previous_hour else None,
        entity_registry_enabled_default=True
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
        value_fn=lambda data: data[DATA_GAS].next_hour.total
        if data[DATA_GAS] and data[DATA_GAS].next_hour else None,
        entity_registry_enabled_default=True
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
        value_fn=lambda data: data[DATA_GAS].previous_hour.market_price
        if data[DATA_GAS] and data[DATA_GAS].previous_hour else None,
        entity_registry_enabled_default=True
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
        value_fn=lambda data: data[DATA_GAS].next_hour.market_price
        if data[DATA_GAS] and data[DATA_GAS].next_hour else None,
        entity_registry_enabled_default=True
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
        value_fn=lambda data: data[DATA_GAS].tomorrow_prices_market
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market else None,
        attr_fn=lambda data: {'tomorrow_prices': data[DATA_GAS].asdict(
            'market_price', tomorrow_only=True, timezone="Europe/Amsterdam")}
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market else {},
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
        value_fn=lambda data: data[DATA_GAS].tomorrow_prices_market_tax
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market_tax else None,
        attr_fn=lambda data: {'tomorrow_prices': data[DATA_GAS].asdict(
            'market_price_tax', tomorrow_only=True, timezone="Europe/Amsterdam")}
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market_tax else {},
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
        value_fn=lambda data: data[DATA_GAS].tomorrow_prices_market_tax_markup
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market_tax_markup else None,
        attr_fn=lambda data: {'tomorrow_prices': data[DATA_GAS].asdict(
            'market_price_including_tax_and_markup', tomorrow_only=True, timezone="Europe/Amsterdam")}
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_market_tax_markup else {},
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
        value_fn=lambda data: data[DATA_GAS].today_prices_total
        if data[DATA_GAS] and data[DATA_GAS].today_prices_total else None,
        attr_fn=lambda data: {'today_prices': data[DATA_GAS].asdict(
            'total', today_only=True, timezone="Europe/Amsterdam")}
        if data[DATA_GAS] and data[DATA_GAS].today_prices_total else {},
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
        value_fn=lambda data: data[DATA_GAS].tomorrow_prices_total
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_total else None,
        attr_fn=lambda data: {'tomorrow_prices': data[DATA_GAS].asdict(
            'total', tomorrow_only=True, timezone="Europe/Amsterdam")}
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_prices_total else {},
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
        value_fn=lambda data: data[DATA_GAS].tomorrow_min.total
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_min
        else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_GAS].tomorrow_min.date_from
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_min
            else {}
        }
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
        value_fn=lambda data: data[DATA_GAS].tomorrow_max.total
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_max
        else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_GAS].tomorrow_max.date_from
            if data[DATA_GAS] and data[DATA_GAS].tomorrow_max
            else {}
        }
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
        value_fn=lambda data: data[DATA_GAS].upcoming_avg.market_price
        if data[DATA_GAS] and data[DATA_GAS].upcoming_avg.market_price else None,
        attr_fn=lambda data: {
            'prices': data[DATA_GAS].asdict('market_price', upcoming_only=True, timezone="Europe/Amsterdam")
            if data[DATA_GAS] else {}
        }
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
        value_fn=lambda data: data[DATA_GAS].upcoming_min.total
        if data[DATA_GAS] else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_GAS].upcoming_min.date_from
        }
        if data[DATA_GAS] else {},
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
        value_fn=lambda data: data[DATA_GAS].upcoming_max.total
        if data[DATA_GAS] else None,
        attr_fn=lambda data: {
            ATTR_TIME: data[DATA_GAS].upcoming_max.date_from
        }
        if data[DATA_GAS] else {},
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
                    data[DATA_ELECTRICITY].upcoming_avg.market_price_tax
                ),
                'average_electricity_market_price_upcoming': (
                    data[DATA_ELECTRICITY].upcoming_avg.market_price
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
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_market_avg
        if data[DATA_ELECTRICITY].upcoming_avg else None,
        attr_fn=lambda data: {
            'average_electricity_price_upcoming_market': data[DATA_ELECTRICITY].upcoming_market_avg,
            'upcoming_market_prices': data[DATA_ELECTRICITY].asdict('market_price', upcoming_only=True)
        }
    ),
    FrankEnergieEntityDescription(
        key="average_electricity_price_upcoming_market_tax",
        name="Average electricity price (upcoming, market and tax)",
        translation_key="average_electricity_price_upcoming_market_tax",
        native_unit_of_measurement=UNIT_ELECTRICITY,
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_market_tax_avg
        if data[DATA_ELECTRICITY].upcoming_avg else None,
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
        suggested_display_precision=3,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data[DATA_ELECTRICITY].upcoming_market_tax_markup_avg
        if data[DATA_ELECTRICITY] else None,
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
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        service_name=SERVICE_NAME_GAS_PRICES,
        value_fn=lambda data: sum(
            data[DATA_GAS].today_gas_before6am) / len(data[DATA_GAS].today_gas_before6am)
        if data[DATA_GAS] else None,
        attr_fn=lambda data: {"Number of hours": len(
            data[DATA_GAS].today_gas_before6am)}
        if data[DATA_GAS] else None,
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
        value_fn=lambda data: sum(
            data[DATA_GAS].today_gas_after6am) / len(data[DATA_GAS].today_gas_after6am)
        if data[DATA_GAS] else None,
        attr_fn=lambda data: {"Number of hours": len(
            data[DATA_GAS].today_gas_after6am)}
        if data[DATA_GAS] else None,
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
        value_fn=lambda data: (sum(data[DATA_GAS].tomorrow_gas_before6am) / len(
            data[DATA_GAS].tomorrow_gas_before6am))
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_gas_before6am else None,
        attr_fn=lambda data: {"Number of hours": len(
            data[DATA_GAS].tomorrow_gas_before6am)}
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_gas_before6am else None,
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
        value_fn=lambda data: (sum(data[DATA_GAS].tomorrow_gas_after6am) / len(
            data[DATA_GAS].tomorrow_gas_after6am))
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_gas_after6am else None,
        attr_fn=lambda data: {"Number of hours": len(
            data[DATA_GAS].tomorrow_gas_after6am)}
        if data[DATA_GAS] and data[DATA_GAS].tomorrow_gas_after6am else None,
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
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].actualCostsUntilLastMeterReadingDate
        if data[DATA_MONTH_SUMMARY] else None,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate
        } if data[DATA_MONTH_SUMMARY] else {}
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
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].expectedCostsUntilLastMeterReadingDate
        if data[DATA_MONTH_SUMMARY] else None,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate
        } if data[DATA_MONTH_SUMMARY] else {}
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
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].differenceUntilLastMeterReadingDate
        if data[DATA_MONTH_SUMMARY] else None,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate
        } if data[DATA_MONTH_SUMMARY] else {}
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
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].differenceUntilLastMeterReadingDateAvg
        if data[DATA_MONTH_SUMMARY] else None,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate
        } if data[DATA_MONTH_SUMMARY] else {}
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
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].expectedCosts
        if data[DATA_MONTH_SUMMARY] else None,
        attr_fn=lambda data: {
            "Description": data[DATA_INVOICES].currentPeriodInvoice.PeriodDescription,
        } if data[DATA_INVOICES] and data[DATA_INVOICES].currentPeriodInvoice else {}
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
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].expectedCostsPerDay
        if data[DATA_MONTH_SUMMARY] else None,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate,
            "Description": data[DATA_INVOICES].currentPeriodInvoice.PeriodDescription,
        } if data[DATA_MONTH_SUMMARY] and data[DATA_INVOICES] and data[DATA_INVOICES].currentPeriodInvoice else {}
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
        value_fn=lambda data: data[DATA_MONTH_SUMMARY].CostsPerDayTillNow
        if data[DATA_MONTH_SUMMARY] else None,
        attr_fn=lambda data: {
            "Last update": data[DATA_MONTH_SUMMARY].lastMeterReadingDate,
            "Description": data[DATA_INVOICES].currentPeriodInvoice.PeriodDescription,
        } if data[DATA_MONTH_SUMMARY] and data[DATA_INVOICES] and data[DATA_INVOICES].currentPeriodInvoice else {}
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
        state_class=SensorStateClass.TOTAL,
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
                and (parsed_date := dt_util.parse_date(value)) is not None
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
            dt_util.now().year - 1)
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
            dt_util.now().year)
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
        key="costs_electricity_yesterday",
        name="Costs electricity yesterday",
        translation_key="costs_electricity_yesterday",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        value_fn=lambda data: data[DATA_USAGE].electricity.costs_total
        if data[DATA_USAGE] and data[DATA_USAGE].electricity
        else None,
        attr_fn=lambda data: {
            "Electricity costs yesterday": data[DATA_USAGE].electricity
        } if data[DATA_USAGE] and data[DATA_USAGE].electricity else {}
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
        value_fn=lambda data: data[DATA_USAGE].electricity.costs_total
        if data[DATA_USAGE] and data[DATA_USAGE].electricity
        else None,
        attr_fn=lambda data: {
            "Electricity costs total": data[DATA_USAGE].electricity.costs_total
        } if data[DATA_USAGE] and data[DATA_USAGE].electricity and hasattr(data[DATA_USAGE].electricity, 'costs_total') else {},
        entity_registry_enabled_default=False
    ),
    FrankEnergieEntityDescription(
        key="usage_electricity_yesterday",
        name="Usage electricity yesterday",
        translation_key="usage_electricity_yesterday",
        icon="mdi:transmission-tower-import",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        value_fn=lambda data: data[DATA_USAGE].electricity.usage_total
        if data[DATA_USAGE] and data[DATA_USAGE].electricity
        else None,
        attr_fn=lambda data: {
            "Electricity usage yesterday": data[DATA_USAGE].electricity
        } if data[DATA_USAGE] and data[DATA_USAGE].electricity else {}
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
        value_fn=lambda data: data[DATA_USAGE].gas.costs_total
        if data[DATA_USAGE] and data[DATA_USAGE].gas
        else None,
        attr_fn=lambda data: {
            "Gas costs gas": data[DATA_USAGE].gas
        } if data[DATA_USAGE] and data[DATA_USAGE].gas else {}
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
        value_fn=lambda data: data[DATA_USAGE].gas.usage_total
        if data[DATA_USAGE] and data[DATA_USAGE].gas
        else None,
        attr_fn=lambda data: {
            "Gas usage yesterday": data[DATA_USAGE].gas
        } if data[DATA_USAGE] and data[DATA_USAGE].gas else {}
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
        value_fn=lambda data: data[DATA_USAGE].feed_in.costs_total
        if data[DATA_USAGE].feed_in
        else None,
        attr_fn=lambda data: {
            "feed-in gains yesterday": data[DATA_USAGE].feed_in
        } if data[DATA_USAGE].feed_in else {}
    ),
    FrankEnergieEntityDescription(
        key="delivered_feed_in_yesterday",
        name="Delivered feed-in yesterday",
        translation_key="delivered_feed_in_yesterday",
        icon="mdi:transmission-tower-export",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        authenticated=True,
        service_name=SERVICE_NAME_USAGE,
        is_feed_in=True,
        value_fn=lambda data: data[DATA_USAGE].feed_in.usage_total
        if data[DATA_USAGE].feed_in
        else None,
        attr_fn=lambda data: {
            "Amount feed-in yesterday": data[DATA_USAGE].feed_in
        } if data[DATA_USAGE].feed_in else {}
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
        if data[DATA_USER].externalDetails and data[DATA_USER].externalDetails.debtor else None,
        attr_fn=lambda data: (
            {
                "Ondertekend op": getattr(data[DATA_USER].activePaymentAuthorization, "signedAt", "-"),
                "Status": getattr(data[DATA_USER].activePaymentAuthorization, "status", "-"),
            }
            if data[DATA_USER].activePaymentAuthorization
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
        key="contractStartDate",
        name="Contract Start Date",
        translation_key="contract_start_date",
        icon="mdi:file-document-outline",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: next(
            (
                dt_util.as_local(
                    datetime.fromisoformat(
                        connection["externalDetails"]["contract"]["startDate"].replace("Z", "+00:00")
                    )
                ).strftime(FORMAT_DATE)
                for connection in getattr(data.get(DATA_USER), "connections", [])
                if connection.get("externalDetails", {}).get("contract", {}).get("startDate")
            ),
            None,
        ),
    ),
    FrankEnergieEntityDescription(
        key="contractStatus",
        name="Contract Status",
        translation_key="contract_status",
        icon="mdi:file-document-outline",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: next(
            (
                connection['contractStatus']
                for connection in getattr(data.get(DATA_USER), "connections", [])
                if connection.get('contractStatus')
            ),
            None,
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
            parsed_date.strftime(FORMAT_DATE)
            if (parsed_date := dt_util.parse_date(data[DATA_USER_SITES].deliveryStartDate)) is not None
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
            if (parsed_date := dt_util.parse_date(data[DATA_USER_SITES].deliveryEndDate)) is not None
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
            if (parsed_date := dt_util.parse_date(data[DATA_USER_SITES].firstMeterReadingDate)) is not None
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
            if (parsed_date := dt_util.parse_date(data[DATA_USER_SITES].lastMeterReadingDate)) is not None
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
        key="smartPushNotifications",
        name="Smart Push notification price alerts",
        translation_key="smart_push_notification_price_alerts",
        icon="mdi:bell-alert",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].UserSettings.get(
            "smartPushNotifications")
        if data[DATA_USER].UserSettings
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
            'Available In Country': data[DATA_USER].smartCharging.get('isAvailableInCountry'),
            'User Created At': data[DATA_USER].smartCharging.get('userCreatedAt')
            if data[DATA_USER].smartCharging
            else []
        }
    ),
    FrankEnergieEntityDescription(
        key="smartTradingisActivated",
        name="Smart Trading Activated",
        translation_key="smarttrading_isactivated",
        icon="mdi:ev-station",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: data[DATA_USER].smartTrading.get('isActivated')
        if data[DATA_USER].smartTrading
        else None,
        attr_fn=lambda data: {
            'Available In Country': data[DATA_USER].smartTrading.get('isAvailableInCountry'),
            'User Created At': data[DATA_USER].smartTrading.get('userCreatedAt')
            if data[DATA_USER].smartTrading
            else []
        }
    )
)


class EnodeChargerSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    _attr_icon = ICON
    _unsub_update: Callable[[], None] | None = None

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: ChargerSensorDescription,
        entry: ConfigEntry,
    ) -> None:
        self.entity_description: FrankEnergieEntityDescription = description
        self._attr_unique_id = f"{entry.unique_id}.{description.key}"
        # self._charger = charger
        self.entity_description = description
        # self._attr_name = f"{charger.information['brand']} {description.name}"
        # self._attr_unique_id = f"{charger.id}_{description.key}"
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_native_value = description.value_fn(coordinator.data)
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        # self._attr_suggested_display_precision = description.suggested_display_precision
        self._attr_icon = description.icon

        device_info_identifiers: set[tuple[str, str]] = (
            {(DOMAIN, f"{entry.entry_id}")}
            if description.service_name == SERVICE_NAME_PRICES
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

        super().__init__(coordinator)

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self._charger)


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
        # self._attr_unique_id = f"{entry.unique_id}.{description.key}.{description.service_name}.{description.sensor_type}"
        # self._attr_unique_id = f"{entry.unique_id}.{description.key}.{entry.entry_id}.{description.service_name}.{description.sensor_type}"
        # Do not set extra identifier for default service, backwards compatibility
        device_info_identifiers: set[tuple[str, str]] = (
            {(DOMAIN, f"{entry.entry_id}")}
            if description.service_name == SERVICE_NAME_PRICES
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

        # Zet enabled_default op False bij feed-in sensor zonder waarde
        if description.is_feed_in:
            user_data = coordinator.data.get(DATA_USER)
            connection = user_data.connections[0]
            estimated_feed_in = int(connection.get("estimatedFeedIn"))
            _LOGGER.debug("estimated_feed_in = %s", estimated_feed_in)
            _LOGGER.debug("has connections = %s", hasattr(user_data, "connections"))
            _LOGGER.debug("user connections = %s", user_data.connections)
            _LOGGER.debug("estimated_feed_in > 0 = %s", estimated_feed_in > 0)

            if user_data and connection:
                estimated_feed_in = int(connection.get("estimatedFeedIn"))

            _LOGGER.debug("_attr_entity_registry_enabled_default = %s", estimated_feed_in > 0)
            self._attr_entity_registry_enabled_default = estimated_feed_in > 0

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
        # next_update_time = datetime.now(timezone.utc).replace(minute=0, second=0) + timedelta(hours=1)
        next_update_time = dt_util.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
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


class EnodeChargersData:
    """Class to hold Enode charger data."""

    def __init__(self, chargers: list[object]) -> None:
        self.chargers = chargers


def _build_dynamic_enode_sensor_descriptions(
    enode_data: EnodeChargersData,
    index: int
) -> list[FrankEnergieEntityDescription]:
    """Build dynamic Enode charger sensor descriptions."""

    descriptions: list[FrankEnergieEntityDescription] = []
    chargers = enode_data.chargers
    if not isinstance(chargers, list) or not chargers:
        return descriptions

    total_charge_capacity = sum(
        charger.charge_settings.capacity
        for charger in chargers
        if charger.charge_settings and charger.charge_settings.capacity is not None
    )

    total_charge_rate = sum(
        charger.charge_state.charge_rate
        for charger in chargers
        if charger.charge_state and charger.charge_state.charge_rate is not None
    )

    for i, charger in enumerate(chargers):
        descriptions.extend([
            FrankEnergieEntityDescription(
                key=f"enode_charger_id_{i+1}",
                name=f"Charger {i+1} ID",
                native_unit_of_measurement=None,
                state_class=None,
                device_class=None,
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:ev-station",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].id
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers
                    else None
                ),
                attr_fn=lambda data, i=i: {
                    "charger": data[DATA_ENODE_CHARGERS].chargers[i]
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
                entity_registry_enabled_default=False,
            ),
            FrankEnergieEntityDescription(
                key=f"enode_charger_brand_{i+1}",
                name=f"Charger {i+1} Brand",
                translation_key=f"enode_charger_brand_{i+1}",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:ev-station",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].information.get("brand")
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "information": data[DATA_ENODE_CHARGERS].chargers[i].information
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"enode_charger_model_{i+1}",
                name=f"Charger {i+1} Model",
                translation_key=f"enode_charger_model_{i+1}",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:ev-station",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].information.get("model")
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "information": data[DATA_ENODE_CHARGERS].chargers[i].information
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                }
            ),
            FrankEnergieEntityDescription(
                key=f"can_smart_charge_{i+1}",
                name=f"Charger {i+1} Can Smart Charge",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:flash",
                value_fn=lambda data, i=i: data[DATA_ENODE_CHARGERS].chargers[i].can_smart_charge
                if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None,
                attr_fn=lambda data, i=i: {
                    "chargers": data[DATA_ENODE_CHARGERS].chargers[i]
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                }
            ),
            FrankEnergieEntityDescription(
                key=f"charge_capacity_{i+1}",
                name=f"Charger {i+1} Charge Capacity",
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:flash",
                device_class=SensorDeviceClass.ENERGY,
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_settings.capacity
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "settings": data[DATA_ENODE_CHARGERS].chargers[i].charge_settings
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"is_plugged_in_{i+1}",
                name=f"Charger {i+1} Is Plugged In",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:flash",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_state.is_plugged_in
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "charge_state": data[DATA_ENODE_CHARGERS].chargers[i].charge_state
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"power_delivery_state_{i+1}",
                name=f"Charger {i+1} Power Delivery State",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:flash",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_state.power_delivery_state
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "charge_state": data[DATA_ENODE_CHARGERS].chargers[i].charge_state
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"enode_is_reachable_{i+1}",
                name=f"Charger {i+1} Is Reachable",
                native_unit_of_measurement=None,
                state_class=None,
                device_class=None,
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:ev-station",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].is_reachable
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "charger": asdict(data[DATA_ENODE_CHARGERS].chargers[i])
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"is_charging_{i+1}",
                name=f"Charger {i+1} Is Charging",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:ev-station",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_state.is_charging
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "charge_state": data[DATA_ENODE_CHARGERS].chargers[i].charge_state
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                }
            ),
            FrankEnergieEntityDescription(
                key=f"enode_charger_name_{i+1}",
                name=f"Charger {i+1} Name",
                translation_key=f"enode_charger_name_{i+1}",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:ev-station",
                value_fn=lambda data, i=i: (
                    None
                    if DATA_ENODE_CHARGERS not in data or not data[DATA_ENODE_CHARGERS].chargers[i]
                    else " ".join(
                        filter(None, (
                            data[DATA_ENODE_CHARGERS].chargers[i].information.get("brand"),
                            data[DATA_ENODE_CHARGERS].chargers[i].information.get("model"),
                            data[DATA_ENODE_CHARGERS].chargers[i].information.get("year")
                        ))
                    )
                ),
                attr_fn=lambda data, i=i: {
                    "information": data[DATA_ENODE_CHARGERS].chargers[i].information
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"charge_rate_{i+1}",
                name=f"Charger {i+1} Charge Rate",
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfPower.KILO_WATT,
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:flash",
                device_class=SensorDeviceClass.POWER,
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_state.charge_rate
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "charge_state": data[DATA_ENODE_CHARGERS].chargers[i].charge_state
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                }
            ),
            FrankEnergieEntityDescription(
                key=f"is_smart_charging_enabled_{i+1}",
                name=f"Charger {i+1} Is Smart Charging Enabled",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:flash",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_settings.is_smart_charging_enabled
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "settings": data[DATA_ENODE_CHARGERS].chargers[i].charge_settings
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"is_solar_charging_enabled_{i+1}",
                name=f"Charger {i+1} Is Solar Charging Enabled",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:flash",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_settings.is_solar_charging_enabled
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "settings": data[DATA_ENODE_CHARGERS].chargers[i].charge_settings
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"calculated_deadline_{i+1}",
                name=f"Charger {i+1} Calculated Deadline",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:clock",
                device_class=SensorDeviceClass.TIMESTAMP,
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_settings.calculated_deadline
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "settings": data[DATA_ENODE_CHARGERS].chargers[i].charge_settings
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"initial_charge_timestamp_{i+1}",
                name=f"Charger {i+1} Initial Charge Timestamp",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:clock",
                device_class=SensorDeviceClass.TIMESTAMP,
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_settings.initial_charge_timestamp
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "settings": data[DATA_ENODE_CHARGERS].chargers[i].charge_settings
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"last_updated_{i+1}",
                name=f"Charger {i+1} Last Updated",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:clock",
                device_class=SensorDeviceClass.TIMESTAMP,
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_state.last_updated
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "charge_state": data[DATA_ENODE_CHARGERS].chargers[i].charge_state
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            ),
            FrankEnergieEntityDescription(
                key=f"battery_level_{i+1}",
                name=f"Charger {i+1} Battery Level",
                authenticated=True,
                service_name=SERVICE_NAME_ENODE_CHARGERS,
                icon="mdi:battery",
                value_fn=lambda data, i=i: (
                    data[DATA_ENODE_CHARGERS].chargers[i].charge_state.battery_level
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else None
                ),
                attr_fn=lambda data, i=i: {
                    "charge_state": data[DATA_ENODE_CHARGERS].chargers[i].charge_state
                    if DATA_ENODE_CHARGERS in data and data[DATA_ENODE_CHARGERS].chargers[i] else {}
                },
            )
        ])

    descriptions.extend([
        FrankEnergieEntityDescription(
            key="total_charge_capacity",
            name="Total Charge Capacity",
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            authenticated=True,
            service_name=SERVICE_NAME_ENODE_CHARGERS,
            icon="mdi:flash",
            device_class=SensorDeviceClass.ENERGY,
            value_fn=lambda _, : total_charge_capacity,
            attr_fn=lambda data: {
                "chargers capacity": {
                    charger.id: charger.charge_settings.capacity
                    for charger in getattr(data.get(DATA_ENODE_CHARGERS), "chargers", [])
                }
                if DATA_ENODE_CHARGERS in data and getattr(data[DATA_ENODE_CHARGERS], "chargers", None) else {}
            }
        ),
        FrankEnergieEntityDescription(
            key="total_charge_rate",
            name="Total Charge Rate",
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            authenticated=True,
            service_name=SERVICE_NAME_ENODE_CHARGERS,
            icon="mdi:flash",
            device_class=SensorDeviceClass.POWER,
            value_fn=lambda _, : total_charge_rate,
            attr_fn=lambda data: {
                "chargers charge rate": {
                    charger.id: charger.charge_state.charge_rate
                    for charger in getattr(data.get(DATA_ENODE_CHARGERS), "chargers", [])
                }
                if DATA_ENODE_CHARGERS in data and getattr(data[DATA_ENODE_CHARGERS], "chargers", None) else {}
            }
        )
    ])

    return descriptions


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

        def __init__(self, brand: str, capacity: float, external_reference: str, id: str, max_charge_power: float, max_discharge_power: float, provider: str, created_at: Any, updated_at: Any):
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
                raise ValueError("Field '%s' must be a datetime object, got %s" % (field_name, type(value).__name__))
            if value.tzinfo is None:
                raise ValueError("Field '%s' must be timezone-aware" % field_name)
            return value

        def __repr__(self) -> str:
            return f"SmartBattery(brand={self.brand}, capacity={self.capacity}, id={self.id})"

    def get_smart_batteries(self) -> list[_SmartBattery]:
        """Return the list of parsed SmartBattery objects."""
        return [self._SmartBattery(**b) if isinstance(b, dict) else b for b in self.batteries]

    def get_battery_count(self) -> int:
        """Return the number of smart batteries."""
        return len(self.batteries)


def _build_dynamic_smart_batteries_descriptions(batteries: SmartBatteriesData) -> list[FrankEnergieEntityDescription]:
    """Build dynamic entity descriptions for all smart batteries.

    Args:
        batteries: List of SmartBattery instances from API.

    Returns:
        List of FrankEnergieEntityDescription objects.
    """
    descriptions: list[FrankEnergieEntityDescription] = []

    _LOGGER.debug("Building dynamic smart batteries descriptions...")
    # _LOGGER.debug("Raw batteries data: %s", batteries)
    # Check if batteries is empty
    if not batteries:
        _LOGGER.debug("No batteries found.")
        return descriptions
    _LOGGER.debug("Found %s batteries.", len(batteries))
    # Check if batteries is a list
    if not isinstance(batteries, list):
        _LOGGER.error("Batteries data is not a list.")
        return descriptions
    # first_type = type(batteries[0])  # <class 'python_frank_energie.models.SmartBattery'>
    # _LOGGER.debug("First battery type: %s", first_type)
    # Check if batteries contain SmartBattery instances
    # if not all(isinstance(b, SmartBatteries.SmartBattery) for b in batteries):
    #    _LOGGER.error("Not all items in batteries are SmartBattery instances.")
    #    return

    total_battery_capacity = 0
    total_max_charge_power = 0

    for i, battery in enumerate(batteries):
        if not hasattr(battery, "id"):
            _LOGGER.warning("Battery at index %d has no 'id' attribute; skipping.", i)
            continue

        base_key = f"smart_battery_{i}"
        name_prefix = f"Battery {i + 1}"

        # Capture values immediately to avoid lambda late binding
        battery_brand = battery.brand
        battery_capacity = battery.capacity
        battery_reference = battery.external_reference
        battery_id = battery.id
        battery_max_charge_power = battery.max_charge_power
        battery_provider = battery.provider
        battery_created_at = battery.created_at
        battery_updated_at = battery.updated_at

        total_battery_capacity += battery_capacity
        total_max_charge_power += battery_max_charge_power

        descriptions.extend([
            FrankEnergieEntityDescription(
                key=f"{base_key}_brand",
                name=f"{name_prefix} Brand",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:battery",
                value_fn=lambda _, val=battery_brand: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_capacity",
                name=f"{name_prefix} Capacity",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:battery-charging",
                device_class=SensorDeviceClass.ENERGY,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                value_fn=lambda _, val=battery_capacity: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_external_reference",
                name=f"{name_prefix} External Reference",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:identifier",
                value_fn=lambda _, val=battery_reference: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_id",
                name=f"{name_prefix} ID",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:fingerprint",
                value_fn=lambda _, val=battery_id: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_max_charge_power",
                name=f"{name_prefix} Max Charge Power",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:flash",
                device_class=SensorDeviceClass.POWER,
                native_unit_of_measurement=UnitOfPower.KILO_WATT,
                value_fn=lambda _, val=battery_max_charge_power: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_provider",
                name=f"{name_prefix} Provider",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:factory",
                value_fn=lambda _, val=battery_provider: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_created_at",
                name=f"{name_prefix} Created At",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:calendar-clock",
                device_class=SensorDeviceClass.TIMESTAMP,
                value_fn=lambda _, val=battery_created_at: val,
            ),
            FrankEnergieEntityDescription(
                key=f"{base_key}_updated_at",
                name=f"{name_prefix} Updated At",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:calendar-clock",
                device_class=SensorDeviceClass.TIMESTAMP,
                value_fn=lambda _, val=battery_updated_at: val,
            ),
        ])

    descriptions.extend([
        FrankEnergieEntityDescription(
            key="total_capacity",
            name="Total Capacity",
            authenticated=True,
            service_name=SERVICE_NAME_BATTERIES,
            icon="mdi:battery-charging",
            device_class=SensorDeviceClass.ENERGY,
            native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            value_fn=lambda _, val=total_battery_capacity: val,
        ),
        FrankEnergieEntityDescription(
            key="total_max_charge_power",
            name="Total Max Charge Power",
            authenticated=True,
            service_name=SERVICE_NAME_BATTERIES,
            icon="mdi:flash",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.KILO_WATT,
            value_fn=lambda _, val=total_max_charge_power: val,
        ),
    ])

    return descriptions


def old_build_dynamic_battery_session_descriptions(battery_id: str) -> list[FrankEnergieEntityDescription]:
    """Return dynamic sensor descriptions for battery session metrics."""
    return [
        FrankEnergieEntityDescription(
            key=f"{battery_id}_trading_result",
            name=f"Trading Result {battery_id}",
            icon="mdi:chart-line",
            device_class=SensorDeviceClass.MONETARY,
            native_unit_of_measurement=CURRENCY_EURO,
            authenticated=True,
            service_name=SERVICE_NAME_BATTERY_SESSIONS,
            value_fn=lambda data, battery_id=battery_id: (
                getattr(data[DATA_BATTERY_SESSIONS].get(battery_id), "period_trading_result", None)
                if data.get(DATA_BATTERY_SESSIONS) else None
            ),
            attr_fn=lambda data, battery_id=battery_id: (
                vars(data[DATA_BATTERY_SESSIONS].get(battery_id))  # or .__dict__
                if data.get(DATA_BATTERY_SESSIONS) and battery_id in data[DATA_BATTERY_SESSIONS] else {}
            ),
            suggested_display_precision=2,
            is_battery_session=True,
        ),
        FrankEnergieEntityDescription(
            key=f"{battery_id}_trading_result_2",
            name=f"Trading Result {battery_id} 2",
            icon="mdi:chart-line",
            device_class=SensorDeviceClass.MONETARY,
            native_unit_of_measurement=CURRENCY_EURO,
            authenticated=True,
            service_name=SERVICE_NAME_BATTERY_SESSIONS,
            value_fn=lambda data, battery_id=battery_id: (
                data[DATA_BATTERY_SESSIONS][battery_id].period_trading_result
                if DATA_BATTERY_SESSIONS in data and battery_id in data[DATA_BATTERY_SESSIONS] else None
            ),
            attr_fn=lambda data, battery_id=battery_id: {
                "battery_session": data[DATA_BATTERY_SESSIONS].get(battery_id, {})
                if DATA_BATTERY_SESSIONS in data else {}
            },
            state_class=SensorStateClass.TOTAL_INCREASING,
            suggested_display_precision=2,
            is_battery_session=True,
        ),
    ]


def old2_build_dynamic_battery_session_descriptions(
    battery_ids: list[str],
    include_total: bool = True
) -> list[FrankEnergieEntityDescription]:
    """Generate sensor descriptions for all smart battery sessions, including total if desired."""
    descriptions: list[FrankEnergieEntityDescription] = []

    for battery_id in battery_ids:
        descriptions.extend([
            FrankEnergieEntityDescription(
                key=f"{battery_id}_trading_result",
                name=f"Trading Result {battery_id}",
                icon="mdi:chart-line",
                device_class=SensorDeviceClass.MONETARY,
                native_unit_of_measurement=CURRENCY_EURO,
                authenticated=True,
                service_name=SERVICE_NAME_BATTERY_SESSIONS,
                value_fn=lambda data: getattr(data, "period_trading_result", None),
                attr_fn=lambda data: vars(data) if data else {},
                state_class=SensorStateClass.TOTAL,
                suggested_display_precision=2,
            ),
            FrankEnergieEntityDescription(
                key=f"{battery_id}_total_result",
                name=f"Total Result {battery_id}",
                icon="mdi:chart-box-outline",
                device_class=SensorDeviceClass.MONETARY,
                native_unit_of_measurement=CURRENCY_EURO,
                authenticated=True,
                service_name=SERVICE_NAME_BATTERY_SESSIONS,
                value_fn=lambda data: getattr(data, "period_total_result", None),
                state_class=SensorStateClass.TOTAL,
                suggested_display_precision=2,
            ),
            FrankEnergieEntityDescription(
                key=f"{battery_id}_epex_result",
                name=f"EPEX Result {battery_id}",
                icon="mdi:flash",
                device_class=SensorDeviceClass.MONETARY,
                native_unit_of_measurement=CURRENCY_EURO,
                authenticated=True,
                service_name=SERVICE_NAME_BATTERY_SESSIONS,
                value_fn=lambda data: getattr(data, "period_epex_result", None),
                state_class=SensorStateClass.TOTAL,
                suggested_display_precision=2,
            ),
            FrankEnergieEntityDescription(
                key=f"{battery_id}_imbalance_result",
                name=f"Imbalance Result {battery_id}",
                icon="mdi:scale-balance",
                device_class=SensorDeviceClass.MONETARY,
                native_unit_of_measurement=CURRENCY_EURO,
                authenticated=True,
                service_name=SERVICE_NAME_BATTERY_SESSIONS,
                value_fn=lambda data: getattr(data, "period_imbalance_result", None),
                state_class=SensorStateClass.TOTAL,
                suggested_display_precision=2,
            ),
            FrankEnergieEntityDescription(
                key=f"{battery_id}_frank_slim",
                name=f"Frank Slim Result {battery_id}",
                icon="mdi:robot",
                device_class=SensorDeviceClass.MONETARY,
                native_unit_of_measurement=CURRENCY_EURO,
                authenticated=True,
                service_name=SERVICE_NAME_BATTERY_SESSIONS,
                value_fn=lambda data: getattr(data, "period_frank_slim", None),
                state_class=SensorStateClass.TOTAL,
                suggested_display_precision=2,
            ),
        ])

    if include_total:
        # Voeg één gecombineerde totaalsensor toe
        descriptions.append(
            FrankEnergieEntityDescription(
                key="all_batteries_total_result",
                name="Total Result (All Batteries)",
                icon="mdi:chart-donut",
                device_class=SensorDeviceClass.MONETARY,
                native_unit_of_measurement=CURRENCY_EURO,
                authenticated=True,
                service_name=SERVICE_NAME_BATTERY_SESSIONS,
                value_fn=lambda data: sum(
                    getattr(session, "period_total_result", 0)
                    for session in data.values()
                    if session and hasattr(session, "period_total_result")
                ) if isinstance(data, dict) else None,
                state_class=SensorStateClass.TOTAL,
                suggested_display_precision=2,
            )
        )

    return descriptions


def _build_dynamic_battery_session_descriptions(
    battery_ids: list[str],
    include_total: bool = True
) -> list[FrankEnergieEntityDescription]:
    """Genereer sensorbeschrijvingen voor alle smart battery sessions."""
    descriptions: list[FrankEnergieEntityDescription] = []

    for battery_id in battery_ids:
        for base_description in BATTERY_SESSION_SENSOR_DESCRIPTIONS:
            desc = FrankEnergieEntityDescription(
                key=f"battery_{battery_id}_{base_description.key}",
                name=f"{base_description.name} {battery_id}",
                icon=base_description.icon,
                device_class=base_description.device_class,
                state_class=base_description.state_class,
                native_unit_of_measurement=base_description.native_unit_of_measurement,
                suggested_display_precision=base_description.suggested_display_precision,
                entity_category=base_description.entity_category,
                authenticated=True,
                service_name=base_description.service_name,
                value_fn=base_description.value_fn,
                attr_fn=base_description.attr_fn,
            )
            descriptions.append(desc)

    if include_total:
        # Voeg één gecombineerde totaalsensor toe
        # Deze sensor verwacht dat alle sessies in FrankEnergieCoordinator.data[DATA_BATTERY_SESSIONS] zitten als dict[battery_id → SmartBatterySessions]
        descriptions.append(
            FrankEnergieEntityDescription(
                key="battery_all_total_result",
                name="Total Result (All Batteries)",
                icon="mdi:chart-donut",
                device_class=SensorDeviceClass.MONETARY,
                native_unit_of_measurement=CURRENCY_EURO,
                authenticated=True,
                service_name=SERVICE_NAME_BATTERY_SESSIONS,
                value_fn=lambda data: sum(
                    getattr(session, "period_total_result", 0.0)
                    for session in data.get(DATA_BATTERY_SESSIONS, {}).values()
                    if session and hasattr(session, "period_total_result")
                ) if isinstance(data.get(DATA_BATTERY_SESSIONS, None), dict) else None,
                attr_fn=lambda data: {
                    "battery_ids": list(data.get(DATA_BATTERY_SESSIONS, {}).keys()),
                    "values": [
                        getattr(session, "period_total_result", None)
                        for session in data.get(DATA_BATTERY_SESSIONS, {}).values()
                    ]
                },
                state_class=SensorStateClass.TOTAL,
                suggested_display_precision=2,
            )
        )

    return descriptions


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Frank Energie sensor entries."""
    _LOGGER.debug("Setting up Frank Energie sensors for entry: %s", config_entry.entry_id)

    coordinator: FrankEnergieCoordinator = hass.data[DOMAIN][config_entry.entry_id][CONF_COORDINATOR]
    batteries = coordinator.data.get(DATA_BATTERIES, [])
    battery_sessions = coordinator.data.get(DATA_BATTERY_SESSIONS, [])

    session_coordinators: dict[str, FrankEnergieBatterySessionCoordinator] = {}
    entities: list = []

    if batteries and batteries.smart_batteries:
        api = coordinator.api  # type: ignore[attr-defined]

        # Set up session coordinators per battery
        for battery in batteries.smart_batteries:
            device_id = battery.id
            session_coordinator = FrankEnergieBatterySessionCoordinator(
                hass,
                config_entry,
                api,
                device_id
            )

            try:
                await session_coordinator.async_config_entry_first_refresh()
            except Exception as err:
                _LOGGER.exception(
                    "Failed to refresh battery session coordinator for device %s: %s",
                    device_id,
                    err
                )
                continue

            session_coordinators[device_id] = session_coordinator

        hass.data[DOMAIN][config_entry.entry_id][DATA_BATTERY_SESSIONS] = session_coordinators

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
        user_data = coordinator.data.get(DATA_USER, {})
        connections = user_data.connections
        first_connection = connections[0]
        estimated_feed_in = first_connection.get("estimatedFeedIn")
        _LOGGER.debug("estimated_feed_in1: %s", estimated_feed_in)
    _LOGGER.debug("user_data: %s", user_data)
    batteries = coordinator.data.get(DATA_BATTERIES, {})
    chargers = coordinator.data.get(DATA_ENODE_CHARGERS, {})
    vehicles = coordinator.data.get(DATA_ENODE_VEHICLES, {})

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
                type(user_data.connections).__name__
            )
    else:
        _LOGGER.debug(
            "user_data does not have attribute 'connections' or is not valid: %s",
            type(user_data).__name__
        )

    if connections:
        first_connection = connections[0]
        if isinstance(first_connection, dict):
            estimated_feed_in = first_connection.get("estimatedFeedIn")
        else:
            _LOGGER.warning("Expected first connection to be a dict, got %s", type(first_connection).__name__)
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
            and (not description.is_gas or "GAS" in user_segments)
            and (not description.is_feed_in or (estimated_feed_in is not None and estimated_feed_in > 0))
            and not (
                description.service_name == SERVICE_NAME_GAS_PRICES
                and coordinator.api.is_authenticated
                and "GAS" not in user_segments

            )
            # and (not description.is_battery or batteries is not None)
            # and (not description.is_charger or chargers is not None)
            # and (not description.is_battery_session or session_coordinator is not None)
        )
    ]

    if coordinator.api.is_authenticated and "GAS" not in user_segments:
        await _disable_gas_price_sensors(hass, config_entry)

    # _LOGGER.debug("coordinator.enode_chargers: %d", coordinator.data.get('enode_chargers'))
    # _LOGGER.debug("coordinator.enode_chargers chargers: %d", coordinator.data['enode_chargers'].chargers)
    # _LOGGER.debug("coordinator.enode_chargers chargers: %d", coordinator.data.get('enode_chargers').get('chargers'))

    if (enode := coordinator.data.get(DATA_ENODE_CHARGERS)) and enode.chargers:
        _LOGGER.debug("Setting up Enode charger sensors for %d chargers", len(enode.chargers))
        static_sensor_descriptions = list(STATIC_ENODE_SENSOR_TYPES)

        for i, charger in enumerate(enode.chargers):
            sensor_descriptions = static_sensor_descriptions + _build_dynamic_enode_sensor_descriptions(enode, i)

            for description in sensor_descriptions:
                if not description.authenticated or coordinator.api.is_authenticated:
                    entities.append(FrankEnergieSensor(coordinator, description, config_entry))
    # Add Enode charger sensors if available
#    entities.extend(
#        FrankEnergieSensor(coordinator, description, config_entry)
#        for description in ENODE_SENSOR_TYPES
#        if not description.authenticated or coordinator.api.is_authenticated
#    )

    # if coordinator.data.get('enode_chargers') and coordinator.data.get('enode_chargers').get('chargers'):
    #     _LOGGER.debug("coordinator.enode_chargers: %d", coordinator.data['enode_chargers'])
    #     _LOGGER.debug("Setting up Enode charger sensors for %d chargers", len(coordinator.data['enode_chargers'].chargers))
    #     for charger in coordinator.data['enode_chargers'].chargers:
    #         for description in ENODE_SENSOR_TYPES:
    #             if not description.authenticated or coordinator.api.is_authenticated:
    #                 entities.append(EnodeChargerSensor(charger, description))

    # _LOGGER.debug("coordinator.smart_batteries: %d", coordinator.data.get('smart_batteries'))
    # _LOGGER.debug("coordinator.enode_chargers chargers: %d", coordinator.data['enode_chargers'].chargers)
    # _LOGGER.debug("coordinator.enode_chargers chargers: %d", coordinator.data.get('enode_chargers').get('chargers'))

    # coordinator.data.get(DATA_BATTERIES)) = <class 'python_frank_energie.models.SmartBatteries'>
    if (batteries := coordinator.data.get(DATA_BATTERIES)) and batteries.smart_batteries:
        _LOGGER.debug("Setting up smart battery sensors: %s", batteries)
        # SmartBatteries(smart_batteries=[SmartBatteries.SmartBattery(brand='Sessy', capacity=5.2, external_reference='AJM6UPPP', id='cm3sunryl0000tc3nhygweghn', max_charge_power=2.2, max_discharge_power=1.7, provider='SESSY', created_at=datetime.datetime(2024, 11, 22, 14, 41, 47, 853000, tzinfo=datetime.timezone.utc), updated_at=datetime.datetime(2025, 2, 7, 22, 3, 21, 898000, tzinfo=datetime.timezone.utc))])
        # <class 'python_frank_energie.models.SmartBatteries'>
        _LOGGER.debug("Setting up smart battery type: %s", type(batteries))
        _LOGGER.debug("Number of smart battery sensors: %d", len(batteries.smart_batteries))
        _LOGGER.debug("Setting up smart battery type: %s", type(batteries.smart_batteries))  # <class 'list'>
        dynamic_battery_descriptions = _build_dynamic_smart_batteries_descriptions(batteries.smart_batteries)
        sensor_descriptions = list(STATIC_BATTERY_SENSOR_TYPES) + \
            dynamic_battery_descriptions
        for i, battery in enumerate(batteries.smart_batteries):
            _LOGGER.debug("Setting up smart battery: %s", battery)
            _LOGGER.debug("Setting up smart battery type: %s", type(battery))
            _LOGGER.debug("Setting up smart battery brand: %s", battery.brand)
            _LOGGER.debug("Setting up smart battery id: %s", battery.id)
            _LOGGER.debug("Setting up smart battery external_reference: %s", battery.external_reference)
            _LOGGER.debug("Setting up smart battery max_charge_power: %s", battery.max_charge_power)
            _LOGGER.debug("Setting up smart battery max_discharge_power: %s", battery.max_discharge_power)
            _LOGGER.debug("Setting up smart battery provider: %s", battery.provider)
            _LOGGER.debug("Setting up smart battery created_at: %s", battery.created_at)
            _LOGGER.debug("Setting up smart battery updated_at: %s", battery.updated_at)
            _LOGGER.debug("Setting up smart battery capacity: %s", battery.capacity)

            for description in sensor_descriptions:
                if not description.authenticated or coordinator.api.is_authenticated:
                    entities.append(FrankEnergieSensor(coordinator, description, config_entry))
                    _LOGGER.debug("Added sensor for %s", description.key)

            # Create sensors for each battery session coordinator
            for battery_id, session_coordinator in session_coordinators.items():
                descriptions = _build_dynamic_battery_session_descriptions([battery_id], include_total=False)
                sessions_data = session_coordinator.data
                if sessions_data and sessions_data.sessions:
                    _LOGGER.debug("Creating dynamic battery session sensors for battery: %s", battery_id)
                    for description in descriptions:
                        if not description.authenticated or coordinator.api.is_authenticated:
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
                        config_entry.entry_id
                    )

            total_descriptions = _build_dynamic_battery_session_descriptions([], include_total=True)
            for desc in total_descriptions:
                sensor = FrankEnergieBatterySessionSensor(
                    coordinator=coordinator,  # hoofdcoördinator!
                    description=desc,
                    battery_id="all_batteries",
                    is_total=True,
                )
                # entities.append(sensor)

            enode_vehicles = coordinator.data.get(DATA_ENODE_VEHICLES)
            num_vehicles = len(enode_vehicles.vehicles) if enode_vehicles else 0
            _LOGGER.debug("Aantal voertuigen gevonden: %d", num_vehicles)

            if enode_vehicles and enode_vehicles.vehicles:
                enode_vehicle_sensors = []

                for i, vehicle in enumerate(enode_vehicles.vehicles):
                    for description in ENODE_VEHICLE_SENSOR_TYPES:
                        enode_vehicle_sensors.append(
                            EnodeVehicleSensor(hass, coordinator, description, vehicle, i)
                        )

                for entity in enode_vehicle_sensors:
                    _LOGGER.debug("Toegevoegde voertuig sensor: %s", entity.name)

                entities.extend(enode_vehicle_sensors)

    # Register the sensors to Home Assistant
    try:
        async_add_entities(entities, update_before_add=True)
    except Exception as e:
        _LOGGER.error("Failed to add entities for entry %s: %s", config_entry.entry_id, str(e))

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
    if days_ahead == 0 and (hour < now.hour or (hour == now.hour and minute <= now.minute)):
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
