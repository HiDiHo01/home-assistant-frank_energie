"""Binary sensors for the Frank Energie integration."""

# binary_sensor.py
# version 2026.05.31

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from math import isfinite
from typing import Callable

from python_frank_energie.models import EnodeCharger, EnodeVehicle
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_CONF_URL,
    COMPONENT_TITLE,
    DATA_BATTERY_DETAILS,
    DATA_DELIVERY_SITE,
    DATA_ELECTRICITY,
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DATA_PV_SYSTEMS,
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
    DATA_USER_SMART_FEED_IN_FETCHED,
    DOMAIN,
    ATTRIBUTION,
    ICON,
    SERVICE_NAME_BATTERIES,
    SERVICE_NAME_ENODE_CHARGERS,
    SERVICE_NAME_PRICES,
    SERVICE_NAME_USER,
    TIMEZONE_AMSTERDAM,
    VERSION,
)
from .coordinator import FrankEnergieCoordinator, FrankEnergieData
from .helpers import (
    async_sync_contract_entities,
    device_translation_key,
    known_site_contracts,
    service_device_info,
    register_service_device,
)
from .smart_hvac import smart_hvac_activation, smart_hvac_site_attributes
from .device_inventory import inventory_device_identifiers

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class FrankEnergieBinarySensorDescription(
    BinarySensorEntityDescription,
):
    """Frank Energie binary sensor description."""

    service_name: str | None = None
    authenticated: bool = False
    entity_registry_enabled_default: bool = True

    value_fn: (
        Callable[
            [FrankEnergieData],
            bool | None,
        ]
        | None
    ) = None

    attr_fn: (
        Callable[
            [FrankEnergieData],
            dict[str, object],
        ]
        | None
    ) = None

    available_fn: (
        Callable[
            [FrankEnergieData],
            bool,
        ]
        | None
    ) = None

    child_device_id: str | None = None
    child_device_name: str | None = None
    child_device_manufacturer: str | None = None

    @property
    def is_authenticated(self) -> bool:
        """Return whether this entity requires authentication."""
        return self.authenticated


def _extract_activation_state(
    value: object,
) -> bool | None:
    """Extract activation state from API object."""

    if value is None:
        return None

    if isinstance(value, dict):
        return bool(value.get("isActivated"))

    return bool(
        getattr(
            value,
            "isActivated",
            getattr(
                value,
                "is_activated",
                False,
            ),
        )
    )


def _smart_feed_in_state(data: FrankEnergieData) -> bool | None:
    """Distinguish a confirmed missing contract from an unsuccessful API fetch."""
    status = data.get(DATA_USER_SMART_FEED_IN)
    if status is None:
        return False if data.get(DATA_USER_SMART_FEED_IN_FETCHED) is True else None
    return _extract_activation_state(status)


def _user_feature_state(
    data: FrankEnergieData,
    attribute_name: str,
) -> bool | None:
    """Return smart feature activation state."""

    user = data.get(DATA_USER)

    if user is None:
        return None

    value = getattr(
        user,
        attribute_name,
        None,
    )

    return _extract_activation_state(value)


def _user_feature_attributes(
    feature_name: str,
) -> Callable[[FrankEnergieData], dict[str, object]]:
    """Create user feature attribute callback."""

    def attr_fn(
        data: FrankEnergieData,
    ) -> dict[str, object]:
        """Return feature attributes."""

        user = data.get(DATA_USER)

        if user is None:
            return {}

        feature = getattr(
            user,
            feature_name,
            None,
        )

        if feature is None:
            return {}

        if isinstance(feature, dict):
            return {
                key: value
                for key, value in {
                    "provider": feature.get("provider"),
                    "available_in_country": feature.get("isAvailableInCountry"),
                    "user_created_at": feature.get("userCreatedAt"),
                    "needs_subscription": feature.get("needsSubscription"),
                    "user_id": feature.get("userId"),
                }.items()
                if value is not None
            }

        return {
            key: value
            for key, value in {
                "provider": getattr(
                    feature,
                    "provider",
                    None,
                ),
                "available_in_country": getattr(
                    feature,
                    "isAvailableInCountry",
                    None,
                ),
                "user_created_at": getattr(
                    feature,
                    "userCreatedAt",
                    None,
                ),
                "needs_subscription": getattr(
                    feature,
                    "needsSubscription",
                    None,
                ),
                "user_id": getattr(
                    feature,
                    "userId",
                    None,
                ),
            }.items()
            if value is not None
        }

    return attr_fn


def _smart_hvac_available(data: FrankEnergieData) -> bool:
    """Return whether a known Smart HVAC activation value is available."""
    return smart_hvac_activation(data) is not None


def _electricity_price_lowest_today(data: FrankEnergieData) -> bool | None:
    """Compare the active all-in price with today's Amsterdam-local minimum."""
    electricity = data.get(DATA_ELECTRICITY)
    if electricity is None:
        return None

    now_utc = datetime.now(UTC)
    today_prices = electricity.today
    local_timezone = ZoneInfo(TIMEZONE_AMSTERDAM)
    local_date = now_utc.astimezone(local_timezone).date()
    local_start = datetime.combine(local_date, time.min, local_timezone)
    local_end = datetime.combine(
        local_date + timedelta(days=1), time.min, local_timezone
    )
    start_utc = local_start.astimezone(UTC)
    end_utc = local_end.astimezone(UTC)

    # The merged series can include a different contract resolution for tomorrow.
    for price in sorted(today_prices, key=lambda item: item.date_from.astimezone(UTC)):
        price_start = price.date_from.astimezone(UTC)
        price_end = price.date_till.astimezone(UTC)
        if price_start != start_utc or price_end <= price_start or price_end > end_utc:
            return None
        start_utc = price_end
    if start_utc != end_utc:
        return None

    current_price = next(
        (price for price in today_prices if price.contains_time(now_utc)), None
    )
    if current_price is None:
        return None

    totals = [price.total for price in today_prices]
    if not all(isfinite(total) for total in totals):
        return None

    return current_price.total == min(totals)


def _tomorrow_prices_available(data: FrankEnergieData | None) -> bool | None:
    """Return whether electricity prices exist for tomorrow in Amsterdam."""
    if data is None:
        return None

    electricity = data.get(DATA_ELECTRICITY)
    if electricity is None:
        return None

    return bool(electricity.tomorrow)


def _disabled_haptic_feedback(
    data: FrankEnergieData,
) -> bool | None:
    """Return whether haptic feedback is disabled."""

    user = data.get(DATA_USER)

    if user is None:
        return None

    settings = getattr(
        user,
        "UserSettings",
        None,
    )

    if settings is None:
        return None

    return bool(
        getattr(
            settings,
            "disabledHapticFeedback",
            False,
        )
    )


def _pv_systems_attributes(
    data: FrankEnergieData,
) -> dict[str, object]:
    """Return PV system attributes."""

    pv = data.get(DATA_PV_SYSTEMS)

    if not pv:
        return {
            "system_count": 0,
        }

    return {
        "system_count": len(pv.systems),
        "systems": [
            {
                "id": system.id,
                "display_name": system.display_name,
                "brand": system.brand,
                "model": system.model,
                "status": system.onboarding_status,
            }
            for system in pv.systems
        ],
    }


def _battery_self_consumption_allowed(
    battery_id: str,
) -> Callable[[FrankEnergieData], bool | None]:
    """Create battery self-consumption callback."""

    def value_fn(
        data: FrankEnergieData,
    ) -> bool | None:
        battery_details = data.get(DATA_BATTERY_DETAILS)

        if not battery_details:
            return None

        battery = next(
            (item for item in battery_details if item.smart_battery.id == battery_id),
            None,
        )

        if battery is None:
            return None

        settings = battery.smart_battery.settings

        if settings is None:
            return None

        return bool(settings.self_consumption_trading_allowed)

    return value_fn


def _extract_activation_attributes(
    value: object,
) -> dict[str, object]:
    """Extract smart feed-in attributes."""

    if value is None:
        return {}

    if isinstance(value, dict):
        return {
            key: attr_value
            for key, attr_value in {
                "has_accepted_terms": value.get("has_accepted_terms"),
                "is_app_onboarding_available": value.get(
                    "is_app_onboarding_available",
                ),
                "is_available_in_country": value.get(
                    "is_available_in_country",
                ),
                "user_created_at": value.get("user_created_at"),
                "user_id": value.get("user_id"),
            }.items()
            if attr_value is not None
        }

    return {
        key: attr_value
        for key, attr_value in {
            "has_accepted_terms": getattr(
                value,
                "has_accepted_terms",
                None,
            ),
            "is_app_onboarding_available": getattr(
                value,
                "is_app_onboarding_available",
                None,
            ),
            "is_available_in_country": getattr(
                value,
                "is_available_in_country",
                None,
            ),
            "user_created_at": getattr(
                value,
                "user_created_at",
                None,
            ),
            "user_id": getattr(
                value,
                "user_id",
                None,
            ),
        }.items()
        if attr_value is not None
    }


def _battery_attributes(
    battery_id: str,
) -> Callable[[FrankEnergieData], dict[str, object]]:
    """Create battery attribute callback."""

    def attr_fn(
        data: FrankEnergieData,
    ) -> dict[str, object]:
        battery_details = data.get(DATA_BATTERY_DETAILS)

        if not battery_details:
            return {}

        battery = next(
            (item for item in battery_details if item.smart_battery.id == battery_id),
            None,
        )

        if battery is None:
            return {}

        sb = battery.smart_battery
        settings = sb.settings

        return {
            "battery_id": sb.id,
            "brand": sb.brand,
            "capacity_kwh": sb.capacity,
            "max_charge_power_kw": sb.max_charge_power,
            "max_discharge_power_kw": sb.max_discharge_power,
            "battery_mode": settings.battery_mode if settings else None,
            "imbalance_trading_strategy": (
                settings.imbalance_trading_strategy if settings else None
            ),
            "self_consumption_trading_threshold_price": getattr(
                settings,
                "self_consumption_trading_threshold_price",
                None,
            ),
        }

    return attr_fn


class FrankEnergieBinarySensor(
    CoordinatorEntity[FrankEnergieCoordinator],
    BinarySensorEntity,
):
    """Frank Energie binary sensor."""

    entity_description: FrankEnergieBinarySensorDescription

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: FrankEnergieBinarySensorDescription,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator)

        self.entity_description = description

        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
        )

        self._attr_unique_id = f"{config_entry.unique_id}_{description.key}"

        if description.child_device_id:
            self._attr_device_info = DeviceInfo(
                identifiers={
                    (
                        DOMAIN,
                        description.child_device_id,
                    )
                },
                name=description.child_device_name,
                manufacturer=description.child_device_manufacturer,
                model=SERVICE_NAME_BATTERIES,
                configuration_url=API_CONF_URL,
            )
        elif description.service_name == SERVICE_NAME_PRICES:
            self._attr_device_info = service_device_info(
                config_entry.entry_id, SERVICE_NAME_PRICES
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={
                    (
                        DOMAIN,
                        f"{config_entry.entry_id}_{description.service_name}",
                    )
                },
                name=f"{COMPONENT_TITLE} - {description.service_name}",
                translation_key=device_translation_key(description.service_name),
                manufacturer=COMPONENT_TITLE,
                entry_type=DeviceEntryType.SERVICE,
                model=description.service_name,
                configuration_url=API_CONF_URL,
                sw_version=VERSION,
            )

    @property
    def available(self) -> bool:
        """Return availability."""

        available_fn = self.entity_description.available_fn

        if available_fn is None:
            return super().available

        return super().available and available_fn(
            self.coordinator.data,
        )

    @property
    def is_on(self) -> bool | None:
        """Return state."""

        value_fn = self.entity_description.value_fn

        if value_fn is None:
            return None

        return value_fn(
            self.coordinator.data,
        )

    @property
    def extra_state_attributes(
        self,
    ) -> dict[str, object] | None:
        """Return extra attributes."""

        attr_fn = self.entity_description.attr_fn

        if attr_fn is None:
            return None

        return attr_fn(
            self.coordinator.data,
        )


_HAS_VIA_DEVICE_ID = (
    "via_device_id"
    in inspect.signature(dr.DeviceRegistry.async_get_or_create).parameters
)


def _strict_bool(value: object) -> bool | None:
    """Return only confirmed provider boolean values, never a truthy fallback."""
    return value if isinstance(value, bool) else None


def _vehicle_nested_bool(
    vehicle: EnodeVehicle, section: str, field: str
) -> bool | None:
    """Read a nested vehicle flag without treating malformed data as a state."""
    data = vehicle.get(section)
    return _strict_bool(data.get(field)) if hasattr(data, "get") else None


@dataclass(frozen=True, kw_only=True)
class EnodeVehicleBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a read-only Enode vehicle status."""

    value_fn: Callable[[EnodeVehicle], bool | None]
    authenticated: bool = True

    def __post_init__(self) -> None:
        """Use the original translation key when none is explicitly provided."""
        if self.translation_key is None:
            object.__setattr__(self, "translation_key", self.key)


@dataclass(frozen=True, kw_only=True)
class EnodeChargerBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a read-only Enode charger status."""

    value_fn: Callable[[EnodeCharger], bool | None]
    authenticated: bool = True

    def __post_init__(self) -> None:
        """Use the original translation key when none is explicitly provided."""
        if self.translation_key is None:
            object.__setattr__(self, "translation_key", self.key)


class EnodeVehicleBinarySensor(
    CoordinatorEntity[FrankEnergieCoordinator], BinarySensorEntity
):
    """A live boolean status on an Enode vehicle device."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: EnodeVehicleBinarySensorDescription

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: EnodeVehicleBinarySensorDescription,
        vehicle: EnodeVehicle,
    ) -> None:
        """Bind a vehicle status to the same device and unique ID as its old sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._vehicle_id = vehicle["id"]
        self._attr_unique_id = f"{DOMAIN}_{self._vehicle_id}_{description.key}"
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
        )
        info = vehicle.get("information") or {}
        self._attr_device_info = DeviceInfo(
            identifiers=inventory_device_identifiers(
                coordinator.config_entry.entry_id, "vehicle", self._vehicle_id
            ),
            manufacturer=info.get("brand"),
            model=info.get("model"),
            serial_number=info.get("vin"),
            name=f"{info.get('brand', '')} {info.get('model', '')}".strip() or None,
            hw_version=str(info.get("year")),
        )

    def _current_vehicle(self) -> EnodeVehicle | None:
        """Find this vehicle in the latest vehicle inventory by its stable ID."""
        if not self.coordinator.data:
            return None
        inventory = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        for vehicle in getattr(inventory, "vehicles", ()) or ():
            if vehicle.get("id") == self._vehicle_id:
                return vehicle
        return None

    @property
    def available(self) -> bool:
        """Report unavailable when the vehicle is absent from the latest inventory."""
        return super().available and self._current_vehicle() is not None

    @property
    def is_on(self) -> bool | None:
        """Expose only an actual boolean; null and malformed data remain unknown."""
        vehicle = self._current_vehicle()
        if vehicle is None:
            return None
        return _strict_bool(self.entity_description.value_fn(vehicle))


class EnodeChargerBinarySensor(
    CoordinatorEntity[FrankEnergieCoordinator], BinarySensorEntity
):
    """A live boolean status on an Enode charger device."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_attribution = ATTRIBUTION
    entity_description: EnodeChargerBinarySensorDescription

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: EnodeChargerBinarySensorDescription,
        charger: EnodeCharger,
        via_device_id: str | None = None,
    ) -> None:
        """Associate the status with its charger and existing service parent."""
        super().__init__(coordinator)
        self.entity_description = description
        self._charger_id = charger.id
        self._attr_unique_id = f"{DOMAIN}_{self._charger_id}_{description.key}"
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
        )
        info = charger.information or {}
        brand = info.get("brand", "Frank Energie")
        model = info.get("model", "Charger")
        self._attr_device_info = DeviceInfo(
            identifiers=inventory_device_identifiers(
                coordinator.config_entry.entry_id, "charger", self._charger_id
            ),
            manufacturer=brand,
            model=model,
            name=f"{brand} {model}".strip() or f"Charger {self._charger_id}",
        )
        if via_device_id:
            if _HAS_VIA_DEVICE_ID:
                self._attr_device_info["via_device_id"] = via_device_id
            else:
                self._attr_device_info["via_device"] = (
                    DOMAIN,
                    f"{coordinator.config_entry.entry_id}_{SERVICE_NAME_ENODE_CHARGERS}",
                )

    def _current_charger(self) -> EnodeCharger | None:
        """Find this charger in the latest charger inventory by its stable ID."""
        if not self.coordinator.data:
            return None
        inventory = self.coordinator.data.get(DATA_ENODE_CHARGERS)
        for charger in getattr(inventory, "chargers", ()) or ():
            if charger.id == self._charger_id:
                return charger
        return None

    @property
    def available(self) -> bool:
        """Report unavailable when the charger is absent from the latest inventory."""
        return super().available and self._current_charger() is not None

    @property
    def is_on(self) -> bool | None:
        """Expose the latest confirmed boolean state of the charger."""
        charger = self._current_charger()
        if charger is None:
            return None
        return _strict_bool(self.entity_description.value_fn(charger))


def _migrate_legacy_enode_sensor(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    unique_id: str,
    description: BinarySensorEntityDescription,
) -> None:
    """Move one registered sensor to the binary platform, preserving user choices.

    The entity domain changes; a custom entity_id suffix is retained where possible.
    Only registry entries belonging to this config entry may be removed.
    """
    registry = er.async_get(hass)
    old_entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    if old_entity_id is None:
        return
    old_entry = registry.async_get(old_entity_id)
    if old_entry is None or old_entry.config_entry_id != config_entry.entry_id:
        return

    new_entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, unique_id)
    if new_entity_id is not None:
        new_entry = registry.async_get(new_entity_id)
        if new_entry is None or new_entry.config_entry_id != config_entry.entry_id:
            _LOGGER.warning(
                "Skipped cross-entry Enode entity migration for %s", description.key
            )
            return
    else:
        new_entry = registry.async_get_or_create(
            "binary_sensor",
            DOMAIN,
            unique_id,
            config_entry=config_entry,
            device_id=old_entry.device_id,
            has_entity_name=True,
            original_icon=description.icon,
            translation_key=description.translation_key,
            entity_category=description.entity_category,
            disabled_by=old_entry.disabled_by,
            suggested_object_id=old_entity_id.partition(".")[2],
        )
        if old_entry.name is not None or old_entry.icon is not None:
            registry.async_update_entity(
                new_entry.entity_id, name=old_entry.name, icon=old_entry.icon
            )
    registry.async_remove(old_entity_id)


BINARY_SENSOR_DESCRIPTIONS: tuple[
    FrankEnergieBinarySensorDescription,
    ...,
] = (
    FrankEnergieBinarySensorDescription(
        key="smartChargingisActivated",
        translation_key="smartcharging_isactivated",
        icon="mdi:car-electric",
        authenticated=True,
        device_class=BinarySensorDeviceClass.RUNNING,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: _user_feature_state(
            data,
            "smartCharging",
        ),
        attr_fn=_user_feature_attributes(
            "smartCharging",
        ),
    ),
    FrankEnergieBinarySensorDescription(
        key="smartTradingisActivated",
        translation_key="smarttrading_isactivated",
        icon="mdi:battery-sync",
        authenticated=True,
        device_class=BinarySensorDeviceClass.RUNNING,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: _user_feature_state(
            data,
            "smartTrading",
        ),
        attr_fn=_user_feature_attributes(
            "smartTrading",
        ),
    ),
    FrankEnergieBinarySensorDescription(
        key="smart_feed_in",
        translation_key="smart_feed_in",
        icon="mdi:solar-power-variant",
        authenticated=True,
        device_class=BinarySensorDeviceClass.RUNNING,
        service_name=SERVICE_NAME_USER,
        value_fn=_smart_feed_in_state,
        attr_fn=lambda data: _extract_activation_attributes(
            data.get(DATA_USER_SMART_FEED_IN),
        ),
    ),
    FrankEnergieBinarySensorDescription(
        key="smart_hvac",
        translation_key="smart_hvac",
        authenticated=True,
        device_class=BinarySensorDeviceClass.RUNNING,
        service_name=SERVICE_NAME_USER,
        value_fn=smart_hvac_activation,
        available_fn=_smart_hvac_available,
        attr_fn=smart_hvac_site_attributes,
    ),
    FrankEnergieBinarySensorDescription(
        key="disabled_haptic_feedback",
        translation_key="disabled_haptic_feedback",
        icon="mdi:vibrate-off",
        authenticated=True,
        entity_registry_enabled_default=False,
        device_class=BinarySensorDeviceClass.RUNNING,
        service_name=SERVICE_NAME_USER,
        value_fn=_disabled_haptic_feedback,
    ),
    FrankEnergieBinarySensorDescription(
        key="smart_pv_systems",
        translation_key="smart_pv_systems",
        icon="mdi:solar-panel",
        authenticated=True,
        device_class=BinarySensorDeviceClass.RUNNING,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: bool(
            data.get(DATA_PV_SYSTEMS),
        ),
        attr_fn=_pv_systems_attributes,
    ),
    FrankEnergieBinarySensorDescription(
        key="smartPushNotifications",
        translation_key="smart_push_notification_price_alerts",
        icon="mdi:bell-alert",
        authenticated=True,
        entity_registry_enabled_default=False,
        device_class=BinarySensorDeviceClass.RUNNING,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].UserSettings.get("smartPushNotifications")
            if data[DATA_USER] and data[DATA_USER].UserSettings
            else None
        ),
    ),
    FrankEnergieBinarySensorDescription(
        key="has_CO2_compensation",
        translation_key="co2_compensation",
        icon="mdi:molecule-co2",
        authenticated=True,
        service_name=SERVICE_NAME_USER,
        value_fn=lambda data: (
            data[DATA_USER].hasCO2Compensation
            if data[DATA_USER] and data[DATA_USER].hasCO2Compensation
            else False
        ),
    ),
    FrankEnergieBinarySensorDescription(
        key="electricity_price_lowest_today",
        translation_key="electricity_price_lowest_today",
        icon="mdi:arrow-down-bold",
        service_name=SERVICE_NAME_PRICES,
        value_fn=_electricity_price_lowest_today,
    ),
    FrankEnergieBinarySensorDescription(
        key="tomorrow_prices_available",
        translation_key="tomorrow_prices_available",
        icon=ICON,
        service_name=SERVICE_NAME_PRICES,
        value_fn=_tomorrow_prices_available,
    ),
)


ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS: tuple[
    EnodeVehicleBinarySensorDescription, ...
] = (
    EnodeVehicleBinarySensorDescription(
        key="can_smart_charge",
        icon="mdi:car-electric",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda vehicle: _strict_bool(vehicle.get("canSmartCharge")),
    ),
    EnodeVehicleBinarySensorDescription(
        key="is_reachable",
        translation_key="is_reachable",
        icon="mdi:car-connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda vehicle: _strict_bool(vehicle.get("isReachable")),
    ),
    EnodeVehicleBinarySensorDescription(
        key="is_charging",
        icon="mdi:ev-station",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda vehicle: _vehicle_nested_bool(
            vehicle, "chargeState", "isCharging"
        ),
    ),
    EnodeVehicleBinarySensorDescription(
        key="is_fully_charged",
        icon="mdi:battery-check",
        value_fn=lambda vehicle: _vehicle_nested_bool(
            vehicle, "chargeState", "isFullyCharged"
        ),
    ),
    EnodeVehicleBinarySensorDescription(
        key="is_plugged_in",
        icon="mdi:power-plug",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda vehicle: _vehicle_nested_bool(
            vehicle, "chargeState", "isPluggedIn"
        ),
    ),
    EnodeVehicleBinarySensorDescription(
        key="smart_charging_enabled",
        icon="mdi:car-electric",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda vehicle: _vehicle_nested_bool(
            vehicle, "chargeSettings", "isSmartChargingEnabled"
        ),
    ),
    EnodeVehicleBinarySensorDescription(
        key="solar_charging_enabled",
        icon="mdi:solar-power",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda vehicle: _vehicle_nested_bool(
            vehicle, "chargeSettings", "isSolarChargingEnabled"
        ),
    ),
)

ENODE_CHARGER_BINARY_SENSOR_DESCRIPTIONS: tuple[
    EnodeChargerBinarySensorDescription, ...
] = (
    EnodeChargerBinarySensorDescription(
        key="can_smart_charge",
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda charger: _strict_bool(charger.can_smart_charge),
    ),
    EnodeChargerBinarySensorDescription(
        key="is_plugged_in",
        icon="mdi:flash",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda charger: _strict_bool(
            getattr(charger.charge_state, "is_plugged_in", None)
        ),
    ),
    EnodeChargerBinarySensorDescription(
        key="is_reachable",
        translation_key="charger_is_reachable",
        icon="mdi:ev-station",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda charger: _strict_bool(charger.is_reachable),
    ),
    EnodeChargerBinarySensorDescription(
        key="is_charging",
        icon="mdi:ev-station",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda charger: _strict_bool(
            getattr(charger.charge_state, "is_charging", None)
        ),
    ),
    EnodeChargerBinarySensorDescription(
        key="is_smart_charging_enabled",
        icon="mdi:flash",
        value_fn=lambda charger: _strict_bool(
            getattr(charger.charge_settings, "is_smart_charging_enabled", None)
        ),
    ),
    EnodeChargerBinarySensorDescription(
        key="is_solar_charging_enabled",
        icon="mdi:flash",
        value_fn=lambda charger: _strict_bool(
            getattr(charger.charge_settings, "is_solar_charging_enabled", None)
        ),
    ),
)


def _build_battery_descriptions(
    data: FrankEnergieData,
) -> list[FrankEnergieBinarySensorDescription]:
    """Build battery binary sensor descriptions."""

    battery_details = data.get(
        DATA_BATTERY_DETAILS,
    )

    if not battery_details:
        return []

    descriptions: list[FrankEnergieBinarySensorDescription] = []

    for battery in battery_details:
        sb = battery.smart_battery
        descriptions.append(
            FrankEnergieBinarySensorDescription(
                key=(f"battery_{sb.id}_self_consumption_trading_allowed"),
                translation_key=("battery_self_consumption_trading_allowed"),
                icon="mdi:home-battery",
                device_class=BinarySensorDeviceClass.RUNNING,
                service_name=SERVICE_NAME_BATTERIES,
                child_device_id=sb.id,
                child_device_name=f"{sb.brand} Battery",
                child_device_manufacturer=sb.brand,
                value_fn=_battery_self_consumption_allowed(
                    sb.id,
                ),
                attr_fn=_battery_attributes(
                    sb.id,
                ),
            )
        )

    return descriptions


def _sync_price_contract_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    settings_coordinator: FrankEnergieCoordinator,
) -> bool:
    """Sync price-entity registry state and return electricity support."""
    contracts = (
        known_site_contracts(settings_coordinator.data.get(DATA_DELIVERY_SITE))
        if settings_coordinator.api.is_authenticated
        else None
    )
    electricity_supported = contracts is None or "ELECTRICITY" in contracts
    if contracts is not None or not settings_coordinator.api.is_authenticated:
        async_sync_contract_entities(
            hass,
            config_entry,
            "binary_sensor",
            {
                f"{config_entry.unique_id}_{description.key}": electricity_supported
                for description in BINARY_SENSOR_DESCRIPTIONS
                if description.service_name == SERVICE_NAME_PRICES
            },
            enabled_by_default={
                f"{config_entry.unique_id}_{description.key}"
                for description in BINARY_SENSOR_DESCRIPTIONS
                if description.service_name == SERVICE_NAME_PRICES
                and description.entity_registry_enabled_default
            },
        )
    return electricity_supported


def _build_enode_charger_binary_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    settings_coordinator: FrankEnergieCoordinator,
    charger_coordinator: FrankEnergieCoordinator,
) -> list[EnodeChargerBinarySensor]:
    """Build authenticated charger statuses and migrate matching legacy entries."""
    if not charger_coordinator.api.is_authenticated:
        return []
    chargers = charger_coordinator.data.get(DATA_ENODE_CHARGERS)
    if not chargers or not chargers.chargers:
        return []

    user_data = (
        settings_coordinator.data.get(DATA_USER)
        if settings_coordinator.api.is_authenticated
        else None
    )
    parent_id = register_service_device(
        hass,
        config_entry,
        SERVICE_NAME_ENODE_CHARGERS,
        configuration_url=getattr(user_data, "websiteUrl", None),
    )
    entities: list[EnodeChargerBinarySensor] = []
    for charger in chargers.chargers:
        if not isinstance(charger.id, str) or not charger.id:
            continue
        for description in ENODE_CHARGER_BINARY_SENSOR_DESCRIPTIONS:
            _migrate_legacy_enode_sensor(
                hass, config_entry, f"{DOMAIN}_{charger.id}_{description.key}", description
            )
            entities.append(
                EnodeChargerBinarySensor(
                    charger_coordinator, description, charger, parent_id
                )
            )
    return entities


def _build_enode_vehicle_binary_sensors(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    vehicle_coordinator: FrankEnergieCoordinator,
) -> list[EnodeVehicleBinarySensor]:
    """Build authenticated vehicle statuses and migrate matching legacy entries."""
    if not vehicle_coordinator.api.is_authenticated:
        return []
    vehicles = vehicle_coordinator.data.get(DATA_ENODE_VEHICLES)
    if not vehicles or not vehicles.vehicles:
        return []

    entities: list[EnodeVehicleBinarySensor] = []
    for vehicle in vehicles.vehicles:
        vehicle_id = vehicle.get("id")
        if not isinstance(vehicle_id, str) or not vehicle_id:
            continue
        for description in ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS:
            _migrate_legacy_enode_sensor(
                hass, config_entry, f"{DOMAIN}_{vehicle_id}_{description.key}", description
            )
            entities.append(
                EnodeVehicleBinarySensor(vehicle_coordinator, description, vehicle)
            )
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie binary sensors."""

    runtime_data = config_entry.runtime_data
    settings_coordinator = runtime_data.settings_coordinator
    price_coordinator = runtime_data.price_coordinator
    pv_coordinator = runtime_data.pv_coordinator
    battery_coordinator = runtime_data.battery_coordinator
    charger_coordinator = runtime_data.charger_coordinator
    vehicle_coordinator = runtime_data.vehicle_coordinator

    entities: list[BinarySensorEntity] = []
    electricity_supported = _sync_price_contract_entities(
        hass, config_entry, settings_coordinator
    )
    for description in BINARY_SENSOR_DESCRIPTIONS:
        if description.service_name == SERVICE_NAME_PRICES:
            if not electricity_supported:
                continue
            coord = price_coordinator
        elif description.key in ("smart_feed_in", "smart_pv_systems"):
            coord = pv_coordinator
        else:
            coord = settings_coordinator

        if not description.authenticated or coord.api.is_authenticated:
            entities.append(
                FrankEnergieBinarySensor(
                    coord,
                    description,
                    config_entry,
                )
            )

    if battery_coordinator.api.is_authenticated:
        entities.extend(
            FrankEnergieBinarySensor(
                battery_coordinator,
                description,
                config_entry,
            )
            for description in _build_battery_descriptions(
                battery_coordinator.data,
            )
        )

    entities.extend(
        _build_enode_charger_binary_sensors(
            hass, config_entry, settings_coordinator, charger_coordinator
        )
    )
    entities.extend(
        _build_enode_vehicle_binary_sensors(
            hass, config_entry, vehicle_coordinator
        )
    )

    _LOGGER.debug(
        "Created %s binary sensors",
        len(entities),
    )

    async_add_entities(entities)
