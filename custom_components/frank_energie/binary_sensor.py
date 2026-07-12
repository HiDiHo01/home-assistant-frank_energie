"""Binary sensors for the Frank Energie integration."""

# binary_sensor.py
# version 2026.05.31

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_CONF_URL,
    COMPONENT_TITLE,
    DATA_BATTERY_DETAILS,
    DATA_PV_SYSTEMS,
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
    DOMAIN,
    SERVICE_NAME_BATTERIES,
    SERVICE_NAME_USER,
    VERSION,
)
from .coordinator import FrankEnergieCoordinator, FrankEnergieData
from .helpers import device_translation_key

_LOGGER = logging.getLogger(__name__)


class UserFeatureKey(str, Enum):
    SMART_HVAC = "smartHvac"


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


def _smart_hvac_state(data: FrankEnergieData) -> bool | None:
    """Return smart HVAC active state."""
    return _user_feature_state(data, UserFeatureKey.SMART_HVAC)


def _smart_hvac_available(data: FrankEnergieData) -> bool:
    """Return smart HVAC availability."""
    user = data.get(DATA_USER)
    if user is None:
        return False

    feature = getattr(user, "smartHvac", None)
    if feature is None:
        return False

    if isinstance(feature, dict):
        return feature.get("isActivated") is not None

    return getattr(feature, "isActivated", None) is not None


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

        return available_fn(
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
        value_fn=lambda data: _extract_activation_state(
            data.get(DATA_USER_SMART_FEED_IN),
        ),
        attr_fn=lambda data: _extract_activation_attributes(
            data.get(DATA_USER_SMART_FEED_IN),
        ),
    ),
    FrankEnergieBinarySensorDescription(
        key="smart_hvac",
        translation_key="smart_hvac",
        icon="mdi:heat-pump",
        authenticated=True,
        device_class=BinarySensorDeviceClass.RUNNING,
        service_name=SERVICE_NAME_USER,
        value_fn=_smart_hvac_state,
        available_fn=_smart_hvac_available,
        attr_fn=_user_feature_attributes(
            "smartHvac",
        ),
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


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie binary sensors."""

    runtime_data = config_entry.runtime_data
    settings_coordinator = runtime_data.settings_coordinator
    pv_coordinator = runtime_data.pv_coordinator
    battery_coordinator = runtime_data.battery_coordinator

    entities: list[FrankEnergieBinarySensor] = []

    for description in BINARY_SENSOR_DESCRIPTIONS:
        if description.key in ("smart_feed_in", "smart_pv_systems"):
            coord = pv_coordinator
        else:
            coord = settings_coordinator

        if coord.api.is_authenticated:
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

    _LOGGER.debug(
        "Created %s binary sensors",
        len(entities),
    )

    async_add_entities(entities)
