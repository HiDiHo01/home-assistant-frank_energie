"""Binary sensors for the Frank Energie integration."""

# binary_sensor.py
# version 2026.05.31
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Callable, Generic, TypeVar

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_CONF_URL,
    COMPONENT_TITLE,
    DATA_PV_SYSTEMS,
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
    DOMAIN,
    SERVICE_NAME_BATTERIES,
    SERVICE_NAME_USER,
)
from .coordinator import FrankEnergieCoordinator
from .sensor import SmartBatteriesData

_LOGGER = logging.getLogger(__name__)
_DataT = TypeVar("_DataT")

VERSION = "2026.5.31"


@dataclass(slots=True, frozen=True)
class FrankEnergieBinarySensorEntityDescription(
    BinarySensorEntityDescription,
    Generic[_DataT],
):
    """Entity description for FrankEnergie binary sensors."""

    authenticated: bool = True
    service_name: str | None = None

    value_fn: Callable[[SmartBatteriesData], bool] | None = None
    attr_fn: Callable[[SmartBatteriesData], dict[str, object]] | None = None
    exists_fn: Callable[[SmartBatteriesData], bool] | None = None

    entity_registry_enabled_default: bool = True

    # If set, creates a per-item device (e.g. individual battery) instead of the
    # shared service device.  No parent link (via_device) — omitted as it proved
    # unreliable across HA versions.
    child_device_id: str | None = None
    child_device_name: str | None = None
    child_device_manufacturer: str | None = None


class FrankEnergieBinarySensor(
    CoordinatorEntity[FrankEnergieCoordinator],
    BinarySensorEntity,
):
    """Representation of a Frank Energie binary sensor."""

    entity_description: FrankEnergieBinarySensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: FrankEnergieBinarySensorEntityDescription,
        config_entry: ConfigEntry,
        battery_id: str | None = None,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)

        self.entity_description = description
        self._battery_id = (
            battery_id or description.key.split("_")[2]
            if "smart_battery_" in description.key
            else None
        )
        self._attr_unique_id = f"{config_entry.entry_id}_{description.key}"
        self._attr_name = (
            description.name if isinstance(description.name, str) else None
        )
        self._attr_icon = description.icon
        self._attr_device_class = description.device_class
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
        )

        device_info_identifiers: set[tuple[str, str]] = (
            {(DOMAIN, f"{config_entry.entry_id}")}
            if description.service_name == "" or description.service_name is None
            else {(DOMAIN, f"{config_entry.entry_id}_{description.service_name}")}
        )

        if description.child_device_id:
            # Per-item device (e.g. individual battery). Named from brand/model.
            # No entry_type — batteries are physical hardware, not services.
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, description.child_device_id)},
                name=description.child_device_name or description.child_device_id,
                manufacturer=description.child_device_manufacturer or COMPONENT_TITLE,
                model=description.service_name,
                configuration_url=API_CONF_URL,
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers=device_info_identifiers,
                name=f"{COMPONENT_TITLE} - {description.service_name}",
                translation_key=f"{COMPONENT_TITLE} - {description.service_name}",
                manufacturer=COMPONENT_TITLE,
                model=description.service_name,
                configuration_url=API_CONF_URL,
                entry_type=DeviceEntryType.SERVICE,
            )

    @property
    def is_on(self) -> bool | None:
        """Return the state of the binary sensor."""
        value_fn = self.entity_description.value_fn
        if value_fn is None:
            return None

        data = self.coordinator.data
        if data is None:
            return None

        return value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Return extra attributes."""
        description = self.entity_description

        attr_fn = description.attr_fn
        if attr_fn is None:
            return None

        data = self.coordinator.data
        if data is None:
            return None

        return attr_fn(data)


# ---------------------------------------------------------------------------
# User-level smart feature binary sensors
# ---------------------------------------------------------------------------


class FrankEnergieSmartFeatureBinarySensor(
    CoordinatorEntity[FrankEnergieCoordinator],
    BinarySensorEntity,
):
    """Read-only binary sensor for a Frank Energie smart feature activation state.

    Used for Smart Charging, Smart Trading, Smart Feed-In, and Smart HVAC.
    These features can only be enabled via the Frank app (subscription flow),
    but can be disabled via HA buttons.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        key: str,
        name: str,
        icon: str,
        translation_key: str,
    ) -> None:
        """Initialize the smart feature binary sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{config_entry.entry_id}_{key}"
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{SERVICE_NAME_USER}")},
            name=f"{COMPONENT_TITLE} - {SERVICE_NAME_USER}",
            manufacturer=COMPONENT_TITLE,
            model=SERVICE_NAME_USER,
            configuration_url=API_CONF_URL,
            entry_type=DeviceEntryType.SERVICE,
        )

    def _get_is_on(self) -> bool | None:
        """Subclasses implement this to return the activation state."""
        return None

    @property
    def is_on(self) -> bool | None:
        """Return the activation state of the smart feature."""
        return self._get_is_on()


class FrankEnergieSmartChargingBinarySensor(FrankEnergieSmartFeatureBinarySensor):
    """Binary sensor for Smart Charging activation state."""

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            key="smart_charging",
            name="Smart Charging",
            icon="mdi:car-electric",
            translation_key="smart_charging",
        )
        self._attr_device_class = BinarySensorDeviceClass.RUNNING

    def _get_is_on(self) -> bool | None:
        user_data = self.coordinator.data.get(DATA_USER)
        if not user_data:
            return None
        smart_charging = user_data.smartCharging
        if isinstance(smart_charging, dict):
            return smart_charging.get("isActivated", False)
        return getattr(smart_charging, "isActivated", False)


class FrankEnergieSmartTradingBinarySensor(FrankEnergieSmartFeatureBinarySensor):
    """Binary sensor for Smart Trading activation state."""

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            key="smart_trading",
            name="Smart Trading",
            icon="mdi:battery-sync",
            translation_key="smart_trading",
        )
        self._attr_device_class = BinarySensorDeviceClass.RUNNING

    def _get_is_on(self) -> bool | None:
        user_data = self.coordinator.data.get(DATA_USER)
        if not user_data:
            return None
        smart_trading = user_data.smartTrading
        if isinstance(smart_trading, dict):
            return smart_trading.get("isActivated", False)
        return getattr(smart_trading, "isActivated", False)


class FrankEnergieSmartFeedInBinarySensor(FrankEnergieSmartFeatureBinarySensor):
    """Binary sensor for Smart Feed-In activation state."""

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            key="smart_feed_in",
            name="Smart Feed-In",
            icon="mdi:solar-power-variant",
            translation_key="smart_feed_in",
        )
        self._attr_device_class = BinarySensorDeviceClass.RUNNING

    def _get_is_on(self) -> bool | None:
        feed_in_status = self.coordinator.data.get(DATA_USER_SMART_FEED_IN, None)
        if feed_in_status is None:
            self._attr_available = False
            return None
        if isinstance(feed_in_status, dict):
            return feed_in_status.get("isActivated", False)
        return getattr(feed_in_status, "is_activated", False)


class FrankEnergieSmartHvacBinarySensor(FrankEnergieSmartFeatureBinarySensor):
    """Binary sensor for Smart HVAC activation state."""

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            key="smart_hvac",
            name="Smart HVAC",
            icon="mdi:heat-pump",
            translation_key="smart_hvac",
        )
        self._attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def available(self) -> bool:
        """Return True if smart PV systems are present."""
        user_data = self.coordinator.data.get(DATA_USER)
        if not user_data:
            return False
        smart_hvac = getattr(user_data, "smartHvac", None)
        if smart_hvac is None:
            self._attr_available = False
            return False
        return True

    def _get_is_on(self) -> bool | None:
        user_data = self.coordinator.data.get(DATA_USER, None)
        if user_data is None:
            return None
        smart_hvac = getattr(user_data, "smartHvac", None)
        if smart_hvac is None:
            self._attr_available = False
            return None
        if isinstance(smart_hvac, dict):
            return smart_hvac.get("isActivated", False)
        return getattr(smart_hvac, "isActivated", False)


class FrankEnergieSmartPvSystemsSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor indicating whether smart PV systems are present."""

    _attr_name = "Frank Energie Smart PV Systems"
    _attr_icon = "mdi:solar-panel"
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry
        )
        self._attr_device_class = BinarySensorDeviceClass.RUNNING
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_smart_pv_systems"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{SERVICE_NAME_USER}")},
            name=f"{COMPONENT_TITLE}",
            manufacturer=COMPONENT_TITLE,
            configuration_url=API_CONF_URL,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Return True if smart PV systems are present."""
        pv = self.coordinator.data.get(DATA_PV_SYSTEMS, None)
        if pv is None:
            self._attr_available = False
            return False
        return bool(pv)

    @property
    def is_on(self) -> bool:
        """Return True if smart PV systems are present."""
        pv = self.coordinator.data.get(DATA_PV_SYSTEMS)
        return bool(pv)

    @property
    def extra_state_attributes(self) -> dict:
        pv = self.coordinator.data.get(DATA_PV_SYSTEMS)
        if not pv:
            return {"system_count": 0}
        return {
            "system_count": len(pv.systems),
            "systems": [
                {
                    "id": s.id,
                    "display_name": s.display_name,
                    "brand": s.brand,
                    "model": s.model,
                    "status": s.onboardingStatus,
                }
                for s in pv.systems
            ],
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.config_entry.entry_id)}}

# ---------------------------------------------------------------------------
# Battery self-consumption binary sensor (existing, kept from old approach)
# ---------------------------------------------------------------------------


def _build_dynamic_smart_batteries_descriptions(
    batteries: list["SmartBatteriesData._SmartBattery"],
) -> list[FrankEnergieBinarySensorEntityDescription] | None:
    """Build dynamic entity descriptions for smart batteries."""

    binary_descriptions: list[FrankEnergieBinarySensorEntityDescription] = []

    for i, battery in enumerate(batteries):
        if not hasattr(battery, "id"):
            _LOGGER.warning("Battery at index %s has no ID. Skipping.", i)
            continue

        base_key = f"smart_battery_{i}"
        name_prefix = f"Battery {i + 1}"

        settings = battery.settings
        _LOGGER.debug("Processing battery %d: %s", i, battery)
        _LOGGER.debug("Battery %d settings: %s", i, settings)

        binary_descriptions.append(
            FrankEnergieBinarySensorEntityDescription(
                key=f"{base_key}_self_consumption_trading_allowed",
                name=f"{name_prefix} Self Consumption Trading Allowed",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:home-battery",
                # Each battery gets its own child device under Frank Energie - Batteries
                child_device_id=battery.id,
                child_device_name=f"{battery.brand} Battery"
                if battery.brand
                else f"Battery {i + 1}",
                child_device_manufacturer=battery.brand or COMPONENT_TITLE,
                value_fn=lambda _, val=settings: (
                    bool(val.self_consumption_trading_allowed) if val else False
                ),
                attr_fn=lambda _, val=battery.settings: asdict(val) if val else {},
            ),
        )

    return binary_descriptions


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie binary sensors."""

    coordinator_wrapper: FrankEnergieCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]
    coordinator: FrankEnergieCoordinator = coordinator_wrapper["coordinator"]
    _LOGGER.debug("Setting up binary sensors for entry %s", config_entry.entry_id)

    entities: list[BinarySensorEntity] = []

    if coordinator.api.is_authenticated:
        # --- User-level smart feature state sensors ---
        entities.append(
            FrankEnergieSmartChargingBinarySensor(coordinator, config_entry)
        )
        entities.append(FrankEnergieSmartTradingBinarySensor(coordinator, config_entry))
        entities.append(FrankEnergieSmartFeedInBinarySensor(coordinator, config_entry))
        entities.append(FrankEnergieSmartHvacBinarySensor(coordinator, config_entry))
        entities.append(FrankEnergieSmartPvSystemsSensor(coordinator, config_entry))

    # --- Battery self-consumption trading (per battery, from SmartBatteryDetails) ---
    batteries_data: SmartBatteriesData | None = coordinator.data.get("smart_batteries")
    if batteries_data:
        batteries_list = getattr(batteries_data, "batteries", [])
        _LOGGER.debug("Number of Batteries found: %s", len(batteries_list))
        binary_descriptions = _build_dynamic_smart_batteries_descriptions(
            batteries_list
        )
        entities.extend(
            FrankEnergieBinarySensor(coordinator, description, config_entry)
            for description in binary_descriptions
        )

    if entities:
        async_add_entities(entities)
