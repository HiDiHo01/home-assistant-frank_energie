"""Datetime platform for Frank Energie integration."""
from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.datetime import DateTimeEntity
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

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie datetime entities."""
    coordinator: FrankEnergieCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    entities: list[DateTimeEntity] = []

    if coordinator.api.is_authenticated:
        enode_vehicles = coordinator.data.get(DATA_ENODE_VEHICLES)
        if enode_vehicles and enode_vehicles.vehicles:
            for vehicle in enode_vehicles.vehicles:
                entities.append(
                    FrankEnergieVehicleDeadlineEntity(coordinator, config_entry, vehicle.id)
                )

    if entities:
        async_add_entities(entities)


class FrankEnergieVehicleDeadlineEntity(CoordinatorEntity[FrankEnergieCoordinator], DateTimeEntity):
    """Representation of an editable EV charging deadline/departure time."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-end"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        vehicle_id: str,
    ) -> None:
        """Initialize the datetime entity."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._vehicle_id = vehicle_id
        self._attr_name = "Charging deadline"
        self._attr_translation_key = "charging_deadline"
        self._attr_unique_id = f"{DOMAIN}_{vehicle_id}_charging_deadline"

        # Find vehicle to get info for device registration
        enode_vehicles = coordinator.data.get(DATA_ENODE_VEHICLES)
        vehicle = None
        if enode_vehicles and enode_vehicles.vehicles:
            vehicle = next((v for v in enode_vehicles.vehicles if v.id == vehicle_id), None)

        brand = vehicle.information.brand if (vehicle and vehicle.information) else "Frank Energie"
        model = vehicle.information.model if (vehicle and vehicle.information) else "Vehicle"
        name = f"{brand} {model}".strip() if (brand or model) else f"Vehicle {vehicle_id}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, vehicle_id)},
            manufacturer=brand,
            model=model,
            name=name,
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the current datetime setting."""
        enode_vehicles = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not enode_vehicles or not enode_vehicles.vehicles:
            return None
        vehicle = next((v for v in enode_vehicles.vehicles if v.id == self._vehicle_id), None)
        if not vehicle or not vehicle.charge_settings:
            return None
        return vehicle.charge_settings.deadline

    async def async_set_value(self, value: datetime) -> None:
        """Set the charging deadline datetime."""
        _LOGGER.warning(
            "Changing the EV charging deadline to %s is currently unverified. Captured GraphQL mutations are required.",
            value,
        )
