"""Select entity controlling resolution via coordinator state."""

# select.py
# version 2026.05.31
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from dataclasses import dataclass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from python_frank_energie.domain import SmartBatteryMode
from python_frank_energie.models import EnodeCharger


from .const import (
    API_CONF_URL,
    BATTERY_MODE_OPTIONS,
    BATTERY_STRATEGY_OPTIONS,
    ENODE_CHARGING_MODE_OPTIONS,
    COMPONENT_TITLE,
    DATA_BATTERY_DETAILS,
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DOMAIN,
    MANUFACTURER_FRANK_ENERGIE,
    SERVICE_NAME_SETTINGS,
)
from .coordinator import FrankEnergieCoordinator
from .helpers import device_translation_key

_LOGGER = logging.getLogger(__name__)


DISPLAY_TO_VALUE: dict[str, str] = {
    "pt15m": "PT15M",
    "pt60m": "PT60M",
}

VALUE_TO_DISPLAY: dict[str, str] = {v: k for k, v in DISPLAY_TO_VALUE.items()}

DEFAULT_DISPLAY = "pt15m"
DEFAULT_VALUE = DISPLAY_TO_VALUE[DEFAULT_DISPLAY]


@dataclass(kw_only=True)
class FrankEnergieSelectEntityDescription(SelectEntityDescription):
    """Class describing Frank Energie select entities."""


SELECT_DESCRIPTIONS: tuple[FrankEnergieSelectEntityDescription, ...] = (
    FrankEnergieSelectEntityDescription(
        key="battery_mode",
        translation_key="battery_mode",
        icon="mdi:battery-sync",
        options=BATTERY_MODE_OPTIONS,
    ),
    FrankEnergieSelectEntityDescription(
        key="battery_strategy",
        translation_key="battery_strategy",
        icon="mdi:shield-sync",
        options=BATTERY_STRATEGY_OPTIONS,
    ),
    FrankEnergieSelectEntityDescription(
        key="enode_charging_mode",
        translation_key="enode_charging_mode",
        icon="mdi:ev-station",
        options=ENODE_CHARGING_MODE_OPTIONS,
    ),
)


def _setup_battery_entities(
    coordinator: FrankEnergieCoordinator, entry: ConfigEntry
) -> list[SelectEntity]:
    """Create and return smart battery select entities."""
    entities: list[SelectEntity] = []
    if coordinator.api.is_authenticated:
        battery_details = coordinator.data.get(DATA_BATTERY_DETAILS)
        if battery_details:
            for battery in battery_details:
                for description in SELECT_DESCRIPTIONS:
                    if description.key == "battery_mode":
                        entities.append(
                            FrankEnergieBatteryModeSelect(
                                coordinator,
                                entry,
                                battery.smart_battery.id,
                                description,
                            )
                        )
                    elif description.key == "battery_strategy":
                        entities.append(
                            FrankEnergieBatteryStrategySelect(
                                coordinator,
                                entry,
                                battery.smart_battery.id,
                                description,
                            )
                        )
    return entities


def _setup_enode_entities(
    hass: HomeAssistant,
    vehicle_coordinator: FrankEnergieCoordinator,
    charger_coordinator: FrankEnergieCoordinator,
    entry: ConfigEntry,
) -> list[SelectEntity]:
    """Create and return Enode vehicle and charger select entities."""
    entities: list[SelectEntity] = []
    enode_vehicles = vehicle_coordinator.data.get(DATA_ENODE_VEHICLES)
    if enode_vehicles and enode_vehicles.vehicles:
        ent_reg = er.async_get(hass)
        for vehicle in enode_vehicles.vehicles:
            if vehicle.can_smart_charge:
                # Clean up the deprecated switch entity
                old_unique_id = f"{DOMAIN}_{vehicle.id}_enode_smart_charging"
                if entity_id := ent_reg.async_get_entity_id(
                    "switch", DOMAIN, old_unique_id
                ):
                    ent_reg.async_remove(entity_id)

                for description in SELECT_DESCRIPTIONS:
                    if description.key == "enode_charging_mode":
                        entities.append(
                            FrankEnergieEnodeChargingModeSelect(
                                vehicle_coordinator, entry, vehicle.id, description
                            )
                        )

    enode_chargers = charger_coordinator.data.get(DATA_ENODE_CHARGERS)
    if enode_chargers and getattr(enode_chargers, "chargers", None):
        for charger in enode_chargers.chargers:
            if getattr(charger, "can_smart_charge", False):
                for description in SELECT_DESCRIPTIONS:
                    if description.key == "enode_charging_mode":
                        entities.append(
                            FrankEnergieEnodeChargerChargingModeSelect(
                                charger_coordinator, entry, charger.id, description
                            )
                        )

    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie select entities."""
    price_coordinator = entry.runtime_data.price_coordinator
    battery_coordinator = entry.runtime_data.battery_coordinator
    vehicle_coordinator = entry.runtime_data.vehicle_coordinator
    charger_coordinator = entry.runtime_data.charger_coordinator

    entities: list[SelectEntity] = [FrankEnergieResolutionSelect(price_coordinator)]
    entities.extend(_setup_battery_entities(battery_coordinator, entry))
    entities.extend(
        _setup_enode_entities(hass, vehicle_coordinator, charger_coordinator, entry)
    )

    async_add_entities(entities)


class FrankEnergieResolutionSelect(CoordinatorEntity, SelectEntity):
    """Select entity controlling resolution via coordinator state."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-time-four-outline"
    _attr_options = list(DISPLAY_TO_VALUE.keys())
    _attr_translation_key = "resolution"

    service_name = SERVICE_NAME_SETTINGS

    def __init__(self, coordinator: FrankEnergieCoordinator) -> None:
        """Initialize select entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_resolution"

    @property
    def current_option(self) -> str:
        """Return the current resolution."""
        value = self.coordinator.resolution
        return VALUE_TO_DISPLAY.get(value, DEFAULT_DISPLAY)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for the resolution select entity."""
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{self.coordinator.config_entry.entry_id}_{self.service_name}",
                )
            },
            name=f"{COMPONENT_TITLE} - {self.service_name}",
            translation_key=device_translation_key(self.service_name),
            manufacturer=COMPONENT_TITLE,
            model=self.service_name,
            configuration_url=API_CONF_URL,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Return False when a resolution change is not currently possible."""
        if not super().available:
            return False
        if not self.coordinator.api.is_authenticated:
            return True
        if getattr(self.coordinator, "_resolution_change_pending", False):
            return False
        state = getattr(self.coordinator, "_api_resolution_state", None)
        if state is None:
            return False
        return state.is_change_request_possible

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes for the resolution select entity."""
        api = getattr(self.coordinator, "api_resolution", None)
        state = getattr(self.coordinator, "_api_resolution_state", None)

        if not self.coordinator.api.is_authenticated:
            return {
                "is_authenticated": False,
                "resolution": VALUE_TO_DISPLAY.get(self.coordinator.resolution),
            }

        return {
            "api_resolution": VALUE_TO_DISPLAY.get(api) if api else None,
            "active_option": VALUE_TO_DISPLAY.get(state.activeOption)
            if state and state.activeOption
            else None,
            "available_options": [
                VALUE_TO_DISPLAY.get(v) for v in state.availableOptions
            ]
            if state
            else None,
            "is_change_request_possible": state.isChangeRequestPossible
            if state
            else False,
            "change_request_effective_date": str(state.changeRequestEffectiveDate)
            if state and state.changeRequestEffectiveDate
            else None,
            "upcoming_change": str(state.upcomingChange)
            if state and state.upcomingChange
            else None,
            "upcoming_change_effective_date": str(state.upcomingChangeEffectiveDate)
            if state and state.upcomingChangeEffectiveDate
            else None,
        }

    async def async_select_option(self, option: str) -> None:
        """Update resolution via coordinator."""
        if option not in DISPLAY_TO_VALUE:
            _LOGGER.warning("Invalid resolution selected: %s", option)
            return

        value = DISPLAY_TO_VALUE[option]

        try:
            await self.coordinator.async_set_resolution(value)
        except Exception as err:
            _LOGGER.error("Failed to set resolution to %s: %s", value, err)
            return

        _LOGGER.debug("Resolution updated: %s -> %s", option, value)


class FrankEnergieBatteryBaseSelect(
    CoordinatorEntity[FrankEnergieCoordinator], SelectEntity
):
    """Base select entity for controlling smart battery."""

    entity_description: FrankEnergieSelectEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        entry: ConfigEntry,
        battery_id: str,
        description: FrankEnergieSelectEntityDescription,
    ) -> None:
        """Initialize the base select entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._battery_id = battery_id

        battery = self._get_battery()
        sb = battery.smart_battery if battery else None

        brand = sb.brand if sb else MANUFACTURER_FRANK_ENERGIE
        model = "Smart Battery"
        name = f"{brand} {model}".strip()

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
            manufacturer=brand,
            model=model,
            name=name,
        )

    def _get_battery(self) -> Any | None:
        """Fetch the smart battery instance for this entity from the coordinator data."""
        battery_details = self.coordinator.data.get(DATA_BATTERY_DETAILS) or []
        return next(
            (b for b in battery_details if b.smart_battery.id == self._battery_id),
            None,
        )


class FrankEnergieBatteryModeSelect(FrankEnergieBatteryBaseSelect):
    """Select entity for controlling smart battery mode."""

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        entry: ConfigEntry,
        battery_id: str,
        description: FrankEnergieSelectEntityDescription,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, entry, battery_id, description)
        self._attr_unique_id = f"{DOMAIN}_{battery_id}_battery_mode"

    @property
    def current_option(self) -> str | None:
        """Return the current battery mode."""
        battery = self._get_battery()
        if not battery or not battery.smart_battery.settings:
            return None
        mode = battery.smart_battery.settings.battery_mode
        if mode:
            mode_lower = mode.lower()
            return mode_lower
        return None

    async def async_select_option(self, option: str) -> None:
        """Update battery mode via mutation."""
        _LOGGER.debug("Setting smart battery %s mode to %s", self._battery_id, option)
        success = await self.coordinator.api.smart_battery_update_settings(
            self._battery_id, {"batteryMode": option.upper()}
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Failed to set battery %s mode to %s", self._battery_id, option
            )


class FrankEnergieBatteryStrategySelect(FrankEnergieBatteryBaseSelect):
    """Select entity for controlling smart battery trading strategy."""

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        entry: ConfigEntry,
        battery_id: str,
        description: FrankEnergieSelectEntityDescription,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, entry, battery_id, description)
        self._attr_unique_id = f"{DOMAIN}_{battery_id}_battery_strategy"

    @property
    def available(self) -> bool:
        """Return True if strategy selection is available."""
        if not super().available:
            return False
        battery = self._get_battery()
        if not battery or not battery.smart_battery.settings:
            return False
        return battery.smart_battery.settings.battery_mode == SmartBatteryMode.TRADING

    @property
    def current_option(self) -> str | None:
        """Return the current battery strategy."""
        battery = self._get_battery()
        if not battery or not battery.smart_battery.settings:
            return None
        strategy = battery.smart_battery.settings.imbalance_trading_strategy
        if strategy:
            strategy_lower = strategy.lower()
            return strategy_lower
        return None

    async def async_select_option(self, option: str) -> None:
        """Update battery trading strategy via mutation."""
        _LOGGER.debug(
            "Setting smart battery %s strategy to %s", self._battery_id, option
        )
        success = await self.coordinator.api.smart_battery_update_settings(
            self._battery_id, {"imbalanceTradingStrategy": option.upper()}
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Failed to set battery %s strategy to %s", self._battery_id, option
            )


class FrankEnergieEnodeChargingModeSelect(
    CoordinatorEntity[FrankEnergieCoordinator], SelectEntity
):
    """Select entity for controlling EV charging mode."""

    entity_description: FrankEnergieSelectEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        entry: ConfigEntry,
        vehicle_id: str,
        description: FrankEnergieSelectEntityDescription,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{DOMAIN}_{vehicle_id}_enode_charging_mode"

        enode_vehicles = coordinator.data.get(DATA_ENODE_VEHICLES)
        vehicle = (
            next((v for v in enode_vehicles.vehicles if v.id == vehicle_id), None)
            if enode_vehicles and enode_vehicles.vehicles
            else None
        )

        brand = (
            vehicle.information.brand
            if vehicle and vehicle.information
            else MANUFACTURER_FRANK_ENERGIE
        )
        model = (
            vehicle.information.model if vehicle and vehicle.information else "Vehicle"
        )
        name = (
            f"{brand} {model}".strip() if (brand or model) else f"Vehicle {vehicle_id}"
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vehicle_id)},
            manufacturer=brand,
            model=model,
            name=name,
        )

    def _get_vehicle(self) -> Any | None:
        """Fetch the Enode vehicle instance from the coordinator data."""
        enode_vehicles = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not enode_vehicles or not enode_vehicles.vehicles:
            return None
        return next(
            (v for v in enode_vehicles.vehicles if v.id == self._vehicle_id), None
        )

    @property
    def current_option(self) -> str | None:
        """Return the current charging mode."""
        vehicle = self._get_vehicle()
        if not vehicle or not vehicle.charge_settings:
            return None
        return (
            "smart_charging"
            if vehicle.charge_settings.is_smart_charging_enabled
            else "boost_charging"
        )

    async def async_select_option(self, option: str) -> None:
        """Update charging mode via mutation."""
        if option not in self.options:
            _LOGGER.error("Invalid charging mode selected: %s", option)
            return

        if option == self.current_option:
            return

        vehicle = self._get_vehicle()
        if not vehicle or not vehicle.charge_settings:
            _LOGGER.error(
                "Cannot change charging mode: vehicle %s not found or has no charge settings",
                self._vehicle_id,
            )
            return

        is_smart = option == "smart_charging"

        success = await self.coordinator.async_update_enode_charge_settings(
            self._vehicle_id,
            True,
            {"isSmartChargingEnabled": is_smart},
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Failed to update charge settings for vehicle %s", self._vehicle_id
            )


class FrankEnergieEnodeChargerChargingModeSelect(
    CoordinatorEntity[FrankEnergieCoordinator], SelectEntity
):
    """Select entity for controlling EV charger charging mode."""

    entity_description: FrankEnergieSelectEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        entry: ConfigEntry,
        charger_id: str,
        description: FrankEnergieSelectEntityDescription,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._charger_id = charger_id
        self._attr_unique_id = f"{DOMAIN}_{charger_id}_enode_charging_mode"

        # Device Info registration
        enode_chargers = coordinator.data.get(DATA_ENODE_CHARGERS)
        charger = (
            next((c for c in enode_chargers.chargers if c.id == charger_id), None)
            if enode_chargers and getattr(enode_chargers, "chargers", None)
            else None
        )

        info = (
            charger.information
            if charger
            and getattr(charger, "information", None)
            and isinstance(charger.information, dict)
            else {}
        )
        brand = info.get("brand") or MANUFACTURER_FRANK_ENERGIE
        model = info.get("model") or "Charger"
        name = (
            f"{brand} {model}".strip() if (brand or model) else f"Charger {charger_id}"
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, charger_id)},
            manufacturer=brand,
            model=model,
            name=name,
        )

    def _get_charger(self) -> EnodeCharger | None:
        """Return the Enode charger object if it exists."""
        enode_chargers = self.coordinator.data.get(DATA_ENODE_CHARGERS)
        if not enode_chargers or not getattr(enode_chargers, "chargers", None):
            return None
        return next(
            (c for c in enode_chargers.chargers if c.id == self._charger_id), None
        )

    @property
    def current_option(self) -> str | None:
        """Return the current charging mode."""
        charger = self._get_charger()
        if not charger or not charger.charge_settings:
            return None
        return (
            "smart_charging"
            if charger.charge_settings.is_smart_charging_enabled
            else "boost_charging"
        )

    async def async_select_option(self, option: str) -> None:
        """Update charging mode via mutation."""
        if option not in self.options:
            _LOGGER.error("Invalid charging mode selected: %s", option)
            return

        if option == self.current_option:
            return

        charger = self._get_charger()
        if not charger or not charger.charge_settings:
            _LOGGER.error(
                "Cannot change charging mode: charger %s not found or has no charge settings",
                self._charger_id,
            )
            return

        is_smart = option == "smart_charging"

        _LOGGER.debug(
            "Setting EV charging mode for charger %s to %s", self._charger_id, option
        )
        success = await self.coordinator.async_update_enode_charge_settings(
            self._charger_id, False, {"isSmartChargingEnabled": is_smart}
        )

        if success:
            if self.hass:
                self.async_write_ha_state()
        else:
            _LOGGER.error(
                "Failed to set EV charging mode for charger %s to %s",
                self._charger_id,
                option,
            )
