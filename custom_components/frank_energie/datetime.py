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
    DATA_ENODE_CHARGERS,
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
    coordinator: FrankEnergieCoordinator = hass.data[DOMAIN][config_entry.entry_id][
        "coordinator"
    ]
    entities: list[DateTimeEntity] = []

    if coordinator.api.is_authenticated:
        # EV vehicle charging deadlines
        enode_vehicles = coordinator.data.get(DATA_ENODE_VEHICLES)
        if enode_vehicles and enode_vehicles.vehicles:
            for vehicle in enode_vehicles.vehicles:
                entities.append(
                    FrankEnergieVehicleDeadlineEntity(
                        coordinator, config_entry, vehicle.id
                    )
                )

        # Wall charger charging deadlines
        enode_chargers = coordinator.data.get(DATA_ENODE_CHARGERS)
        if enode_chargers and enode_chargers.chargers:
            for charger in enode_chargers.chargers:
                entities.append(
                    FrankEnergieChargerDeadlineEntity(
                        coordinator, config_entry, charger.id
                    )
                )

    if entities:
        async_add_entities(entities)


def _build_charge_settings_input(settings) -> dict:
    """Build the full charge settings dict required by the mutation.

    All 13 fields must be present — the API does not support partial updates.
    We read the current values from the coordinator and only override what
    the user explicitly changed (deadline in this case).
    """
    return {
        "id": settings.id,
        "isSmartChargingEnabled": settings.is_smart_charging_enabled,
        "isSolarChargingEnabled": settings.is_solar_charging_enabled,
        "minChargeLimit": settings.min_charge_limit,
        "maxChargeLimit": settings.max_charge_limit,
        "hourMonday": settings.hour_monday,
        "hourTuesday": settings.hour_tuesday,
        "hourWednesday": settings.hour_wednesday,
        "hourThursday": settings.hour_thursday,
        "hourFriday": settings.hour_friday,
        "hourSaturday": settings.hour_saturday,
        "hourSunday": settings.hour_sunday,
    }


class FrankEnergieVehicleDeadlineEntity(
    CoordinatorEntity[FrankEnergieCoordinator], DateTimeEntity
):
    """Editable charging deadline / departure time for an Enode EV vehicle."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-end"
    _attr_translation_key = "charging_deadline"

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
        self._attr_unique_id = f"{DOMAIN}_{vehicle_id}_charging_deadline"

        # Find vehicle to get info for device registration
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
    def native_value(self) -> datetime | None:
        """Return the next calculated charging deadline from Frank.

        Uses ``calculated_deadline`` (Frank's computed next target based on the
        per-weekday hour schedule) rather than the nullable ``deadline`` field
        which is a one-time override and can be a stale past date.
        """
        enode_vehicles = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not enode_vehicles or not enode_vehicles.vehicles:
            return None
        vehicle = next(
            (v for v in enode_vehicles.vehicles if v.id == self._vehicle_id), None
        )
        if not vehicle or not vehicle.charge_settings:
            return None
        return vehicle.charge_settings.calculated_deadline

    async def async_set_value(self, value: datetime) -> None:
        """Set the EV charging deadline via EnodeUpdateVehicleChargeSettings."""
        enode_vehicles = self.coordinator.data.get(DATA_ENODE_VEHICLES)
        if not enode_vehicles or not enode_vehicles.vehicles:
            _LOGGER.error("Cannot set deadline: no vehicle data available")
            return

        vehicle = next(
            (v for v in enode_vehicles.vehicles if v.id == self._vehicle_id), None
        )
        if not vehicle or not vehicle.charge_settings:
            _LOGGER.error(
                "Cannot set deadline: vehicle %s not found or has no charge settings",
                self._vehicle_id,
            )
            return

        input_data = _build_charge_settings_input(vehicle.charge_settings)
        input_data["deadline"] = value.isoformat()

        _LOGGER.debug(
            "Setting charging deadline for vehicle %s to %s", self._vehicle_id, value
        )
        success = await self.coordinator.api.enode_update_vehicle_charge_settings(
            input_data
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Failed to set charging deadline for vehicle %s", self._vehicle_id
            )


class FrankEnergieChargerDeadlineEntity(
    CoordinatorEntity[FrankEnergieCoordinator], DateTimeEntity
):
    """Editable charging deadline / departure time for an Enode wall charger."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-end"
    _attr_translation_key = "charging_deadline"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        charger_id: str,
    ) -> None:
        """Initialize the datetime entity."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._charger_id = charger_id
        self._attr_unique_id = f"{DOMAIN}_{charger_id}_charging_deadline"

        # Find charger for device info
        enode_chargers = coordinator.data.get(DATA_ENODE_CHARGERS)
        charger = None
        if enode_chargers and enode_chargers.chargers:
            charger = next(
                (c for c in enode_chargers.chargers if c.id == charger_id), None
            )

        # Charger information is a plain dict (not a dataclass like vehicle)
        info = charger.information if charger else {}
        brand = info.get("brand", "Frank Energie") if isinstance(info, dict) else "Frank Energie"
        model = info.get("model", "Charger") if isinstance(info, dict) else "Charger"
        name = f"{brand} {model}".strip() if (brand or model) else f"Charger {charger_id}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, charger_id)},
            manufacturer=brand,
            model=model,
            name=name,
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the next calculated charging deadline from Frank.

        Uses ``calculated_deadline`` (Frank's computed next target based on the
        per-weekday hour schedule) rather than the nullable ``deadline`` field
        which is a one-time override and can be a stale past date.
        """
        enode_chargers = self.coordinator.data.get(DATA_ENODE_CHARGERS)
        if not enode_chargers or not enode_chargers.chargers:
            return None
        charger = next(
            (c for c in enode_chargers.chargers if c.id == self._charger_id), None
        )
        if not charger or not charger.charge_settings:
            return None
        return charger.charge_settings.calculated_deadline

    async def async_set_value(self, value: datetime) -> None:
        """Set the charger charging deadline via EnodeUpdateChargerChargeSettings."""
        enode_chargers = self.coordinator.data.get(DATA_ENODE_CHARGERS)
        if not enode_chargers or not enode_chargers.chargers:
            _LOGGER.error("Cannot set deadline: no charger data available")
            return

        charger = next(
            (c for c in enode_chargers.chargers if c.id == self._charger_id), None
        )
        if not charger or not charger.charge_settings:
            _LOGGER.error(
                "Cannot set deadline: charger %s not found or has no charge settings",
                self._charger_id,
            )
            return

        input_data = _build_charge_settings_input(charger.charge_settings)
        input_data["deadline"] = value.isoformat()

        _LOGGER.debug(
            "Setting charging deadline for charger %s to %s", self._charger_id, value
        )
        success = await self.coordinator.api.enode_update_charger_charge_settings(
            input_data
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Failed to set charging deadline for charger %s", self._charger_id
            )
