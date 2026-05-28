"""Switch platform for Frank Energie integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    DATA_USER,
    DATA_BATTERIES,
    DATA_BATTERY_DETAILS,
    DATA_USER_SMART_FEED_IN,
    DATA_PV_SYSTEMS,
    DATA_PV_SUMMARY,
)
from .coordinator import FrankEnergieCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie switches."""
    coordinator: FrankEnergieCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]
    entities: list[SwitchEntity] = []

    # If the user is authenticated, set up user-level smart charging & trading switches
    if coordinator.api.is_authenticated:
        entities.append(FrankEnergieSmartChargingSwitch(coordinator, config_entry))
        entities.append(FrankEnergieSmartTradingSwitch(coordinator, config_entry))
        entities.append(FrankEnergieSmartFeedInSwitch(coordinator, config_entry))

        # Check for batteries
        batteries = coordinator.data.get(DATA_BATTERIES)
        if batteries and batteries.batteries:
            for battery in batteries.batteries:
                entities.append(
                    FrankEnergieBatteryTradingSwitch(
                        coordinator, config_entry, battery.id
                    )
                )

        # Check for PV systems
        pv_systems = coordinator.data.get(DATA_PV_SYSTEMS)
        if pv_systems and pv_systems.systems:
            for system in pv_systems.systems:
                entities.append(
                    FrankEnergiePvSteeringSwitch(coordinator, config_entry, system.id)
                )

    if entities:
        async_add_entities(entities)


class FrankEnergieSmartChargingSwitch(
    CoordinatorEntity[FrankEnergieCoordinator], SwitchEntity
):
    """Switch to toggle smart charging on/off."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:car-electric"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_name = "Smart charging"
        self._attr_translation_key = "smart_charging"
        self._attr_unique_id = f"{config_entry.entry_id}_smart_charging"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_User")},
            name="Frank Energie - User",
            manufacturer="Frank Energie",
            model="User",
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        user_data = self.coordinator.data.get(DATA_USER)
        if not user_data:
            return None
        smart_charging = user_data.smartCharging
        if isinstance(smart_charging, dict):
            return smart_charging.get("isActivated", False)
        return getattr(smart_charging, "isActivated", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        _LOGGER.warning(
            "Turning on Smart Charging is currently unverified. Captured GraphQL mutations are required."
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.warning(
            "Turning off Smart Charging is currently unverified. Captured GraphQL mutations are required."
        )


class FrankEnergieSmartTradingSwitch(
    CoordinatorEntity[FrankEnergieCoordinator], SwitchEntity
):
    """Switch to toggle smart trading on/off."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-sync"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_name = "Smart trading"
        self._attr_translation_key = "smart_trading"
        self._attr_unique_id = f"{config_entry.entry_id}_smart_trading"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_User")},
            name="Frank Energie - User",
            manufacturer="Frank Energie",
            model="User",
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        user_data = self.coordinator.data.get(DATA_USER)
        if not user_data:
            return None
        smart_trading = user_data.smartTrading
        if isinstance(smart_trading, dict):
            return smart_trading.get("isActivated", False)
        return getattr(smart_trading, "isActivated", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        _LOGGER.warning(
            "Turning on Smart Trading is currently unverified. Captured GraphQL mutations are required."
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.warning(
            "Turning off Smart Trading is currently unverified. Captured GraphQL mutations are required."
        )


class FrankEnergieSmartFeedInSwitch(
    CoordinatorEntity[FrankEnergieCoordinator], SwitchEntity
):
    """Switch to toggle smart feed-in on/off."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:solar-power-variant"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_name = "Smart feed-in"
        self._attr_translation_key = "smart_feed_in"
        self._attr_unique_id = f"{config_entry.entry_id}_smart_feed_in"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_User")},
            name="Frank Energie - User",
            manufacturer="Frank Energie",
            model="User",
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        feed_in_status = self.coordinator.data.get(DATA_USER_SMART_FEED_IN)
        if not feed_in_status:
            return None
        if isinstance(feed_in_status, dict):
            return feed_in_status.get("isActivated", False)
        return getattr(feed_in_status, "is_activated", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        _LOGGER.warning(
            "Turning on Smart Feed-in is currently unverified. Captured GraphQL mutations are required."
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.warning(
            "Turning off Smart Feed-in is currently unverified. Captured GraphQL mutations are required."
        )


class FrankEnergieBatteryTradingSwitch(
    CoordinatorEntity[FrankEnergieCoordinator], SwitchEntity
):
    """Switch to toggle self consumption battery trading on/off."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:home-battery"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        battery_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._battery_id = battery_id
        self._attr_name = "Self consumption trading allowed"
        self._attr_translation_key = "self_consumption_trading_allowed"
        self._attr_unique_id = f"{DOMAIN}_{battery_id}_self_consumption_trading_allowed"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
            name=f"Smart Battery {battery_id}",
            manufacturer="Frank Energie",
            model="SmartBattery",
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        details_list = self.coordinator.data.get(DATA_BATTERY_DETAILS)
        if not details_list:
            return None
        for detail in details_list:
            if (
                detail
                and getattr(detail, "smart_battery", None)
                and getattr(detail.smart_battery, "id", None) == self._battery_id
            ):
                settings = getattr(detail.smart_battery, "settings", None)
                if settings:
                    return getattr(settings, "self_consumption_trading_allowed", None)
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        _LOGGER.warning(
            "Turning on Battery Self Consumption Trading is currently unverified. Captured GraphQL mutations are required."
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.warning(
            "Turning off Battery Self Consumption Trading is currently unverified. Captured GraphQL mutations are required."
        )


class FrankEnergiePvSteeringSwitch(
    CoordinatorEntity[FrankEnergieCoordinator], SwitchEntity
):
    """Switch to toggle PV export steering on/off."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:solar-power"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        system_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._system_id = system_id
        self._attr_name = "Steering enabled"
        self._attr_translation_key = "pv_steering_enabled"
        self._attr_unique_id = f"{DOMAIN}_{system_id}_steering_enabled"

        # Get PV system metadata (brand, model, name, serial_number)
        metadata = coordinator.get_pv_system_metadata(system_id)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, system_id)},
            manufacturer=metadata["brand"],
            model=metadata["model"],
            name=metadata["display_name"],
            serial_number=metadata["serial_number"],
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        summary_dict = self.coordinator.data.get(DATA_PV_SUMMARY)
        status = None
        if summary_dict:
            summary = summary_dict.get(self._system_id)
            if summary:
                status = getattr(summary, "steering_status", None)

        if status is None:
            systems_obj = self.coordinator.data.get(DATA_PV_SYSTEMS)
            if systems_obj and systems_obj.systems:
                pv_system = next(
                    (s for s in systems_obj.systems if s.id == self._system_id), None
                )
                if pv_system:
                    status = getattr(pv_system, "steering_status", None)

        if status is None:
            return None

        # Return True if active/steering
        return str(status).upper() in {"ACTIVE", "STEERING"}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        _LOGGER.warning(
            "Turning on PV Steering is currently unverified. Captured GraphQL mutations are required."
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        _LOGGER.warning(
            "Turning off PV Steering is currently unverified. Captured GraphQL mutations are required."
        )
