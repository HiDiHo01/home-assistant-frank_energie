"""Datetime platform for Frank Energie integration."""

# datetime.py
# version 2026.05.31
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DOMAIN,
)
from .coordinator import FrankEnergieCoordinator
from .helpers import build_charge_settings_input

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie datetime entities."""
    runtime_data = config_entry.runtime_data
    realtime_coordinator = runtime_data.realtime_coordinator
    vehicle_coordinator = runtime_data.vehicle_coordinator
    entities: list[DateTimeEntity] = []

    # EV vehicle charging deadlines
    if vehicle_coordinator.api.is_authenticated:
        enode_vehicles = vehicle_coordinator.data.get(DATA_ENODE_VEHICLES)
        if enode_vehicles and enode_vehicles.vehicles:
            for vehicle in enode_vehicles.vehicles:
                entities.append(
                    FrankEnergieVehicleDeadlineEntity(
                        vehicle_coordinator, config_entry, vehicle.id
                    )
                )

    # Wall charger charging deadlines
    if realtime_coordinator.api.is_authenticated:
        enode_chargers = realtime_coordinator.data.get(DATA_ENODE_CHARGERS)
        if enode_chargers and enode_chargers.chargers:
            for charger in enode_chargers.chargers:
                entities.append(
                    FrankEnergieChargerDeadlineEntity(
                        realtime_coordinator, config_entry, charger.id
                    )
                )

    if entities:
        async_add_entities(entities)


class FrankEnergieEnodeDeadlineEntity(
    CoordinatorEntity[FrankEnergieCoordinator], DateTimeEntity
):
    """Base class for Enode charging deadline entities."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-end"
    _attr_translation_key = "charging_deadline"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        device_id: str,
        unique_id_suffix: str,
    ) -> None:
        """Initialize the datetime entity."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_{device_id}_{unique_id_suffix}"

    @property
    def _device_data_key(self) -> str:
        """Return the key in coordinator data for this device type."""
        raise NotImplementedError

    def _get_device_list(self) -> list:
        """Return the list of devices from coordinator data."""
        raise NotImplementedError

    def _get_device(self) -> Any:
        """Find and return the device matching self._device_id."""
        return next(
            (d for d in self._get_device_list() if d.id == self._device_id), None
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the next calculated charging deadline from Frank.

        Uses ``calculated_deadline`` (Frank's computed next target based on the
        per-weekday hour schedule) rather than the nullable ``deadline`` field
        which is a one-time override and can be a stale past date.
        """
        device = self._get_device()
        if not device or not device.charge_settings:
            return None
        return device.charge_settings.calculated_deadline

    async def _update_charge_settings(self, input_data: dict) -> bool:
        """Call the appropriate API update settings mutation."""
        raise NotImplementedError

    async def async_set_value(self, value: datetime) -> None:
        """Set the charging deadline via API mutation."""
        device = self._get_device()
        if not device or not device.charge_settings:
            _LOGGER.error(
                "Cannot set deadline: device %s not found or has no charge settings",
                self._device_id,
            )
            return

        input_data = build_charge_settings_input(device.charge_settings)
        input_data["deadline"] = value.isoformat()

        _LOGGER.debug(
            "Setting charging deadline for device %s to %s", self._device_id, value
        )
        success = await self._update_charge_settings(input_data)
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Failed to set charging deadline for device %s", self._device_id
            )


class FrankEnergieVehicleDeadlineEntity(FrankEnergieEnodeDeadlineEntity):
    """Editable charging deadline / departure time for an Enode EV vehicle."""

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        vehicle_id: str,
    ) -> None:
        """Initialize the datetime entity."""
        super().__init__(coordinator, config_entry, vehicle_id, "charging_deadline")

        # Find vehicle to get info for device registration
        vehicle = self._get_device()

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
    def _device_data_key(self) -> str:
        return DATA_ENODE_VEHICLES

    def _get_device_list(self) -> list:
        enode_vehicles = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        return enode_vehicles.vehicles if enode_vehicles else []

    async def _update_charge_settings(self, input_data: dict) -> bool:
        return await self.coordinator.api.enode_update_vehicle_charge_settings(
            input_data
        )


class FrankEnergieChargerDeadlineEntity(FrankEnergieEnodeDeadlineEntity):
    """Editable charging deadline / departure time for an Enode wall charger."""

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        charger_id: str,
    ) -> None:
        """Initialize the datetime entity."""
        super().__init__(coordinator, config_entry, charger_id, "charging_deadline")

        # Find charger for device info
        charger = self._get_device()

        # Charger information is a plain dict (not a dataclass like vehicle)
        info = charger.information if charger else {}
        brand = (
            info.get("brand", "Frank Energie")
            if isinstance(info, dict)
            else "Frank Energie"
        )
        model = info.get("model", "Charger") if isinstance(info, dict) else "Charger"
        name = (
            f"{brand} {model}".strip() if (brand or model) else f"Charger {charger_id}"
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, charger_id)},
            manufacturer=brand,
            model=model,
            name=name,
        )

    @property
    def _device_data_key(self) -> str:
        return DATA_ENODE_CHARGERS

    def _get_device_list(self) -> list:
        enode_chargers = self.coordinator.data.get(DATA_ENODE_CHARGERS)
        return enode_chargers.chargers if enode_chargers else []

    async def _update_charge_settings(self, input_data: dict) -> bool:
        return await self.coordinator.api.enode_update_charger_charge_settings(
            input_data
        )
