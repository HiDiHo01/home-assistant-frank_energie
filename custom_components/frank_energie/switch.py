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
    DATA_ENODE_VEHICLES,
)
from .coordinator import FrankEnergieCoordinator
from .helpers import build_charge_settings_input

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

    if coordinator.api.is_authenticated:
        # Enode smart charging: true bidirectional switch (both mutations confirmed)
        enode_vehicles = coordinator.data.get(DATA_ENODE_VEHICLES)
        if enode_vehicles and enode_vehicles.vehicles:
            for vehicle in enode_vehicles.vehicles:
                if vehicle.can_smart_charge:
                    entities.append(
                        FrankEnergieEnodeSmartChargingSwitch(
                            coordinator, config_entry, vehicle.id
                        )
                    )

    if entities:
        async_add_entities(entities)


class FrankEnergieEnodeSmartChargingSwitch(
    CoordinatorEntity[FrankEnergieCoordinator], SwitchEntity
):
    """Switch to enable/disable Enode smart charging for a vehicle.

    This is the only bidirectional switch in the integration — toggled
    via the vehicle-scoped EnodeUpdateVehicleChargeSettings mutation.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:car-electric-outline"
    _attr_translation_key = "enode_smart_charging"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        vehicle_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{DOMAIN}_{vehicle_id}_enode_smart_charging"

        # Build device info from vehicle data
        enode_vehicles = coordinator.data.get(DATA_ENODE_VEHICLES)
        vehicle = None
        if enode_vehicles and enode_vehicles.vehicles:
            vehicle = next(
                (v for v in enode_vehicles.vehicles if v.id == vehicle_id), None
            )

        brand = (
            vehicle.information.brand
            if (vehicle and vehicle.information)
            else "Frank Energie"
        )
        model = (
            vehicle.information.model
            if (vehicle and vehicle.information)
            else "Vehicle"
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

    @property
    def is_on(self) -> bool | None:
        """Return True if Enode smart charging is enabled for this vehicle."""
        enode_vehicles = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not enode_vehicles or not enode_vehicles.vehicles:
            return None
        vehicle = next(
            (v for v in enode_vehicles.vehicles if v.id == self._vehicle_id), None
        )
        if not vehicle or not vehicle.charge_settings:
            return None
        return vehicle.charge_settings.is_smart_charging_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Enode smart charging."""
        enode_vehicles = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not enode_vehicles or not enode_vehicles.vehicles:
            _LOGGER.error("Cannot enable smart charging: no vehicle data available")
            return

        vehicle = next(
            (v for v in enode_vehicles.vehicles if v.id == self._vehicle_id), None
        )
        if not vehicle or not vehicle.charge_settings:
            _LOGGER.error(
                "Cannot enable smart charging: vehicle %s not found or has no charge settings",
                self._vehicle_id,
            )
            return

        input_data = build_charge_settings_input(vehicle.charge_settings)
        input_data["isSmartChargingEnabled"] = True

        _LOGGER.debug("Enabling Enode smart charging for vehicle %s", self._vehicle_id)
        success = await self.coordinator.api.enode_update_vehicle_charge_settings(
            input_data
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Failed to enable Enode smart charging for vehicle %s",
                self._vehicle_id,
            )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Enode smart charging."""
        enode_vehicles = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not enode_vehicles or not enode_vehicles.vehicles:
            _LOGGER.error("Cannot disable smart charging: no vehicle data available")
            return

        vehicle = next(
            (v for v in enode_vehicles.vehicles if v.id == self._vehicle_id), None
        )
        if not vehicle or not vehicle.charge_settings:
            _LOGGER.error(
                "Cannot disable smart charging: vehicle %s not found or has no charge settings",
                self._vehicle_id,
            )
            return

        input_data = build_charge_settings_input(vehicle.charge_settings)
        input_data["isSmartChargingEnabled"] = False

        _LOGGER.debug("Disabling Enode smart charging for vehicle %s", self._vehicle_id)
        success = await self.coordinator.api.enode_update_vehicle_charge_settings(
            input_data
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Failed to disable Enode smart charging for vehicle %s",
                self._vehicle_id,
            )
