"""Number platform for Frank Energie integration."""

# number.py
# date 2026.6.24

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Final, override

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from python_frank_energie.models import SmartBatteryDetails

from . import FrankEnergieEntryData
from .const import (
    API_CONF_URL,
    COMPONENT_TITLE,
    CONF_ENERGY_TAX_ODE,
    CONF_ENERGY_TAX_REDUCTION,
    CONF_MONTHLY_SUBSCRIPTION_FEE,
    CONF_NETWORK_CHARGES,
    CONF_NETWORK_CHARGES,
    DATA_BATTERY_DETAILS,
    DATA_ENODE_CHARGERS,
    DATA_ENODE_VEHICLES,
    DEFAULT_ENERGY_TAX_ODE,
    DEFAULT_ENERGY_TAX_REDUCTION,
    DEFAULT_MONTHLY_SUBSCRIPTION_FEE,
    DEFAULT_NETWORK_CHARGES,
    DOMAIN,
    SERVICE_NAME_COSTS,
    UNIT_ELECTRICITY,
)
from .coordinator import FrankEnergieCoordinator
from .helpers import device_translation_key

_LOGGER = logging.getLogger(__name__)

BATTERY_MODE_SELF_CONSUMPTION_MIX: Final = "SELF_CONSUMPTION_MIX"
MANUFACTURER_FRANK_ENERGIE = "Frank Energie"


@dataclass(frozen=True, slots=True, kw_only=True)
class FrankEnergieNumberEntityDescription(NumberEntityDescription):
    """Describes a Frank Energie number entity."""

    # key: str
    # translation_key: str = ""
    service_name: str = ""

    # native_min_value: float = 0
    # native_max_value: float = 10
    # native_step: float = 0.50

    # native_unit_of_measurement: str | None = None
    # icon: str | None = None
    # mode: NumberMode = NumberMode.BOX
    suggested_display_precision: int | None = None

    value_fn: Callable[
        [ConfigEntry[FrankEnergieEntryData]],
        float,
    ] | None = None


MONTHLY_SUBSCRIPTION_FEE_DESCRIPTION = (
    FrankEnergieNumberEntityDescription(
        key="monthly_subscription_fee",
        translation_key="monthly_subscription_fee",
        service_name=SERVICE_NAME_COSTS,
        native_min_value=0.0,
        native_max_value=10.0,
        native_step=0.01,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        icon="mdi:cash",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda entry: float(
            entry.options.get(
                CONF_MONTHLY_SUBSCRIPTION_FEE,
                DEFAULT_MONTHLY_SUBSCRIPTION_FEE,
            )
        ),
    )
)
ENERGY_TAX_ODE = (
    FrankEnergieNumberEntityDescription(
        key="energy_tax_ode",
        translation_key="energy_tax_ode",
        service_name=SERVICE_NAME_COSTS,
        native_min_value=0.0,
        native_max_value=50.0,
        native_step=0.01,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        icon="mdi:cash",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda entry: float(
            entry.options.get(
                CONF_ENERGY_TAX_ODE,
                DEFAULT_ENERGY_TAX_ODE,
            )
        ),
    )
)
ENERGY_TAX_REDUCTION = (
    FrankEnergieNumberEntityDescription(
        key="energy_tax_reduction",
        translation_key="energy_tax_reduction",
        service_name=SERVICE_NAME_COSTS,
        native_min_value=-100.00,
        native_max_value=0.00,
        native_step=0.01,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        icon="mdi:cash",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda entry: float(
            entry.options.get(
                CONF_ENERGY_TAX_REDUCTION,
                DEFAULT_ENERGY_TAX_REDUCTION,
            )
        ),
    )
)
NETWORK_CHARGES = (
    FrankEnergieNumberEntityDescription(
        key="network_charges",
        translation_key="network_charges",
        service_name=SERVICE_NAME_COSTS,
        native_min_value=0.00,
        native_max_value=50.00,
        native_step=0.01,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        icon="mdi:cash",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda entry: float(
            entry.options.get(
                CONF_NETWORK_CHARGES,
                DEFAULT_NETWORK_CHARGES,
            )
        ),
    )
)

CONFIG_NUMBER_DESCRIPTIONS: Final = (
    MONTHLY_SUBSCRIPTION_FEE_DESCRIPTION,
    ENERGY_TAX_ODE,
    ENERGY_TAX_REDUCTION,
    NETWORK_CHARGES,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry[FrankEnergieEntryData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie number entities."""
    coordinator: FrankEnergieCoordinator = config_entry.runtime_data.coordinator
    entities: list[NumberEntity] = []

    if coordinator.api.is_authenticated:
        battery_details = coordinator.data.get(DATA_BATTERY_DETAILS)
        if battery_details:
            for battery in battery_details:
                entities.append(
                    FrankEnergieBatteryThresholdNumber(
                        coordinator, config_entry, battery.smart_battery.id
                    )
                )

        for description in CONFIG_NUMBER_DESCRIPTIONS:
            entities.append(
                FrankEnergieFixedMonthlyCostsNumber(
                    coordinator,
                    config_entry,
                    description,
                )
            )

        enode_vehicles = coordinator.data.get(DATA_ENODE_VEHICLES)
        if enode_vehicles and enode_vehicles.vehicles:
            for vehicle in enode_vehicles.vehicles:
                entities.append(FrankEnergieEnodeChargeLimitNumber(coordinator, vehicle.id, "minChargeLimit", translation_key="min_charge_limit", is_vehicle=True))
                entities.append(FrankEnergieEnodeChargeLimitNumber(coordinator, vehicle.id, "maxChargeLimit", translation_key="max_charge_limit", is_vehicle=True))

        enode_chargers = coordinator.data.get(DATA_ENODE_CHARGERS)
        if enode_chargers and enode_chargers.chargers:
            for charger in enode_chargers.chargers:
                entities.append(FrankEnergieEnodeChargeLimitNumber(coordinator, charger.id, "minChargeLimit", translation_key="min_charge_limit", is_vehicle=False))
                entities.append(FrankEnergieEnodeChargeLimitNumber(coordinator, charger.id, "maxChargeLimit", translation_key="max_charge_limit", is_vehicle=False))
                entities.append(FrankEnergieEnodeChargeLimitNumber(coordinator, charger.id, "initialCharge", translation_key="initial_charge", is_vehicle=False))

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
                if item.smart_battery and item.smart_battery.id == battery_id
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


class FrankEnergieFixedMonthlyCostsNumber(
    CoordinatorEntity[FrankEnergieCoordinator],
    NumberEntity,
):
    """Monthly subscription fee."""

    entity_description: FrankEnergieNumberEntityDescription

    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        entry: ConfigEntry[FrankEnergieEntryData],
        description: FrankEnergieNumberEntityDescription,
    ) -> None:
        super().__init__(coordinator)

        self._entry = entry
        self.entity_description = description

        self._attr_unique_id = (
            f"{entry.entry_id}_{self.entity_description.key}"
        )

        self._service_name = self.entity_description.service_name

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return True  # always available
        # return super().available

    @property
    def native_value(self) -> float:
        """Return current configured value."""
        assert self.entity_description.value_fn is not None

        return self.entity_description.value_fn(
            self._entry,
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{self._entry.entry_id}_{self._service_name}",
                )
            },
            name=f"{COMPONENT_TITLE} - {self._service_name}",
            translation_key=device_translation_key(self._service_name),
            manufacturer=COMPONENT_TITLE,
            model=self._service_name,
            configuration_url=API_CONF_URL,
            entry_type=DeviceEntryType.SERVICE,
        )

    @override
    async def async_set_native_value(
        self,
        value: float,
    ) -> None:
        """Persist value."""

        self.hass.config_entries.async_update_entry(
            self._entry,
            options={
                **self._entry.options,
                CONF_MONTHLY_SUBSCRIPTION_FEE: value,
            },
        )

        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()


class old_FrankEnergieFixedMonthlyCostsNumber(
    CoordinatorEntity[FrankEnergieCoordinator],
    NumberEntity,
):
    """Fixed monthly subscription costs."""

    _attr_has_entity_name = True
    _attr_name = "Monthly Subscription Fee"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 50.0
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = CURRENCY_EURO
    _attr_suggested_display_precision = 2
    _attr_translation_key = "monthly_subscription_fee"
    _attr_icon = "mdi:cash"
    _attr_entity_category = EntityCategory.CONFIG
    service_name = SERVICE_NAME_COSTS

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        entry: ConfigEntry[FrankEnergieEntryData],
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator)

        self._entry = entry

        self._attr_unique_id = (
            f"{entry.entry_id}_monthly_subscription_fee"
        )

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return True  # always available
        # return super().available

    @property
    def native_value(self) -> float:
        """Return current configured value."""
        return float(
            self._entry.options.get(
                CONF_MONTHLY_SUBSCRIPTION_FEE,
                DEFAULT_MONTHLY_SUBSCRIPTION_FEE,
            )
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{self._entry.entry_id}_{self.service_name}",
                )
            },
            name=f"{COMPONENT_TITLE} - {self.service_name}",
            translation_key=device_translation_key(self.service_name),
            manufacturer=COMPONENT_TITLE,
            model=self.service_name,
            configuration_url=API_CONF_URL,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_set_native_value(
        self,
        value: float,
    ) -> None:
        """Persist value."""
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={
                **self._entry.options,
                CONF_MONTHLY_SUBSCRIPTION_FEE: value,
            },
        )

        await self.coordinator.async_request_refresh()
        self.async_write_ha_state()

class FrankEnergieEnodeChargeLimitNumber(CoordinatorEntity[FrankEnergieCoordinator], NumberEntity):
    """Number entity for setting charge limits for an Enode device."""

    _attr_has_entity_name = True
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        device_id: str,
        target_key: str,
        translation_key: str,
        is_vehicle: bool = False,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._target_key = target_key
        self._is_vehicle = is_vehicle
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{DOMAIN}_{device_id}_{translation_key}"

        if target_key == "maxChargeLimit":
            self._attr_native_min_value = 50
        else:
            self._attr_native_min_value = 0

        if is_vehicle:
            enode_data = coordinator.data.get(DATA_ENODE_VEHICLES)
            item = next((v for v in enode_data.vehicles if v.id == device_id), None) if enode_data else None
            brand = item.information.brand if item and item.information else MANUFACTURER_FRANK_ENERGIE
            model = item.information.model if item and item.information else "Vehicle"
        else:
            enode_data = coordinator.data.get(DATA_ENODE_CHARGERS)
            item = next((c for c in enode_data.chargers if c.id == device_id), None) if enode_data else None
            brand = item.information.get("brand") if item and isinstance(item.information, dict) else MANUFACTURER_FRANK_ENERGIE
            model = item.information.get("model") if item and isinstance(item.information, dict) else "Charger"

        name = f"{brand} {model}".strip() if (brand or model) else f"Device {device_id}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=brand,
            model=model,
            name=name,
        )

    def _get_item(self):
        """Return the device details."""
        if self._is_vehicle:
            enode_data = self.coordinator.data.get(DATA_ENODE_VEHICLES)
            return next((v for v in enode_data.vehicles if v.id == self._device_id), None) if enode_data else None
        else:
            enode_data = self.coordinator.data.get(DATA_ENODE_CHARGERS)
            return next((c for c in enode_data.chargers if c.id == self._device_id), None) if enode_data else None

    @property
    def icon(self) -> str | None:
        """Return a dynamic icon based on the current value."""
        value = self.native_value
        if value is None:
            return "mdi:battery-unknown"
            
        rounded = int(round(value / 10.0) * 10)
        
        if rounded == 0:
            return "mdi:battery-charging-outline"
        elif rounded == 100:
            return "mdi:battery-charging-100"
        
        return f"mdi:battery-charging-{rounded}"

    @property
    def native_value(self) -> float | None:
        """Return the target value."""
        item = self._get_item()
        if not item or not item.charge_settings:
            return None
        
        if self._target_key == "minChargeLimit":
            return item.charge_settings.min_charge_limit
        elif self._target_key == "maxChargeLimit":
            return item.charge_settings.max_charge_limit
        elif self._target_key == "initialCharge":
            return item.charge_settings.initial_charge
        return None

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Set the target value."""
        item = self._get_item()
        if not item or not item.charge_settings:
            raise ValueError("Item or charge settings not found")
            
        _LOGGER.debug("Setting %s to %s for %s", self._target_key, value, self._device_id)

        try:
            if self._is_vehicle:
                success = await self.coordinator.api.enode_update_vehicle_charge_settings({
                    "id": item.charge_settings.id,
                    self._target_key: int(value),
                })
            else:
                success = await self.coordinator.api.enode_update_charger_charge_settings({
                    "id": item.charge_settings.id,
                    self._target_key: int(value),
                })
        except Exception:
            _LOGGER.exception("Failed to update %s for %s", self._target_key, self._device_id)
            raise

        if not success:
            raise ValueError(f"Failed to update {self._target_key} for {self._device_id}")

        # Optimistically update the local state to prevent the UI slider from jumping back
        if self._target_key == "minChargeLimit":
            item.charge_settings.min_charge_limit = int(value)
        elif self._target_key == "maxChargeLimit":
            item.charge_settings.max_charge_limit = int(value)
        elif self._target_key == "initialCharge":
            item.charge_settings.initial_charge = float(value)

        if self.hass:
            self.async_write_ha_state()
