"""Number platform for Frank Energie integration."""

from __future__ import annotations

import logging
from typing import Final, override

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from python_frank_energie.models import SmartBatteryDetails

from . import FrankEnergieEntryData
from .const import DATA_BATTERY_DETAILS, DOMAIN, UNIT_ELECTRICITY
from .coordinator import FrankEnergieCoordinator

_LOGGER = logging.getLogger(__name__)

BATTERY_MODE_SELF_CONSUMPTION_MIX: Final = (
    "SELF_CONSUMPTION_MIX"
)


async def async_setup_entry(
    _hass: HomeAssistant,
    config_entry: ConfigEntry[FrankEnergieEntryData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie number entities."""
    coordinator: FrankEnergieCoordinator = config_entry.runtime_data.coordinator
    entities: list[FrankEnergieBatteryThresholdNumber] = []

    if coordinator.api.is_authenticated:
        battery_details = coordinator.data.get(DATA_BATTERY_DETAILS)
        if battery_details:
            for battery in battery_details:
                entities.append(
                    FrankEnergieBatteryThresholdNumber(
                        coordinator, config_entry, battery.smart_battery.id
                    )
                )

    if entities:
        async_add_entities(entities)


class FrankEnergieBatteryThresholdNumber(
    CoordinatorEntity[FrankEnergieCoordinator], NumberEntity
):
    """Number entity for setting the self consumption trading threshold price."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:currency-eur"
    _attr_native_min_value = 0.20
    _attr_native_max_value = 0.40
    _attr_native_step = 0.05
    _attr_native_unit_of_measurement = UNIT_ELECTRICITY
    _attr_translation_key = "consumption_threshold_price"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry[FrankEnergieEntryData],
        battery_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._battery_id = battery_id
        self._attr_unique_id = f"{DOMAIN}_{battery_id}_consumption_threshold_price"

        # Find battery for device info
        battery_details = coordinator.data.get(DATA_BATTERY_DETAILS) or []
        battery = next(
            (
                item
                for item in battery_details
                if item.smart_battery
                and item.smart_battery.id == battery_id
            ),
            None,
        )
        sb = battery.smart_battery if battery else None

        brand = sb.brand if sb else "Frank Energie"
        model = "Smart Battery"
        name = f"{brand} {model}".strip()

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, battery_id)},
            manufacturer=brand,
            model=model,
            name=name,
        )

    def _get_battery(self) -> SmartBatteryDetails | None:
        """Return the battery details for this entity."""
        battery_details = self.coordinator.data.get(DATA_BATTERY_DETAILS) or []

        return next(
            (
                battery
                for battery in battery_details
                if battery.smart_battery
                and battery.smart_battery.id == self._battery_id
            ),
            None,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not super().available:
            return False
        battery = self._get_battery()
        settings = (
            battery.smart_battery.settings
            if battery and battery.smart_battery
            else None
        )

        if settings is None:
            return False

        return settings.battery_mode == BATTERY_MODE_SELF_CONSUMPTION_MIX

    @property
    def native_value(self) -> float | None:
        """Return the threshold price value."""
        battery = self._get_battery()
        settings = (
            battery.smart_battery.settings
            if battery and battery.smart_battery
            else None
        )

        if settings is None:
            return None

        return settings.self_consumption_trading_threshold_price

    @override
    async def old_async_set_native_value(self, value: float) -> None:
        """Set the threshold price."""
        _LOGGER.debug(
            "Setting threshold price for smart battery %s to %s",
            self._battery_id,
            value,
        )
        success = await self.coordinator.api.smart_battery_update_settings(
            self._battery_id, {"selfConsumptionTradingThresholdPrice": value}
        )
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error(
                "Failed to set threshold price for smart battery %s to %s",
                self._battery_id,
                value,
            )

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set the threshold price."""

        min_value = self.native_min_value
        max_value = self.native_max_value

        # native_min_value/native_max_value may be properties or callables depending
        # on Home Assistant version; ensure we compare floats.
        if callable(min_value):
            min_value = min_value()
        if callable(max_value):
            max_value = max_value()

        if min_value is not None and value < min_value:
            raise ValueError(
                "Threshold value %.2f is below the minimum allowed value %.2f"
                % (value, min_value)
            )

        if max_value is not None and value > max_value:
            raise ValueError(
                "Threshold value %.2f exceeds the maximum allowed value %.2f"
                % (value, max_value)
            )

        _LOGGER.debug(
            "Setting threshold price for smart battery %s to %.2f",
            self._battery_id,
            value,
        )

        try:
            success = await self.coordinator.api.smart_battery_update_settings(
                self._battery_id,
                {
                    "selfConsumptionTradingThresholdPrice": value,
                },
            )
        except Exception:
            _LOGGER.exception(
                "Failed to update threshold price for smart battery %s",
                self._battery_id,
            )
            raise

        if not success:
            message = (
                "smart_battery_update_settings returned unsuccessful result "
                "for smart battery %s"
            )

            _LOGGER.error(
                message,
                self._battery_id,
            )

            raise ValueError(message % self._battery_id)

        await self.coordinator.async_request_refresh()
