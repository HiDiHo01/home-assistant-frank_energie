"""Binary sensors for the Frank Energie integration."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Callable, Generic, TypeVar

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import API_CONF_URL, COMPONENT_TITLE, DOMAIN, SERVICE_NAME_BATTERIES
from .coordinator import FrankEnergieCoordinator
from .sensor import SmartBatteriesData

_LOGGER = logging.getLogger(__name__)
_DataT = TypeVar("_DataT")


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
        self._battery_id = battery_id or description.key.split("_")[2] if "smart_battery_" in description.key else None
        self._attr_unique_id = f"{config_entry.entry_id}_{description.key}"  # f"{battery_id}_{description.key}"
        self._attr_name = (
            description.name
            if isinstance(description.name, str)
            else None
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
        self._attr_device_info = DeviceInfo(
            identifiers=device_info_identifiers,
            name=f"{COMPONENT_TITLE} - {description.service_name}" or None,
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


def _build_dynamic_smart_batteries_descriptions(
    batteries: list["SmartBatteriesData._SmartBattery"]
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
        if settings:
            mode = settings.battery_mode
            strategy = settings.imbalance_trading_strategy
            self_consumption_trading_allowed = settings.self_consumption_trading_allowed

        binary_descriptions.append(
            FrankEnergieBinarySensorEntityDescription(
                key=f"{base_key}_self_consumption_trading_allowed",
                name=f"{name_prefix} Self Consumption Trading Allowed",
                authenticated=True,
                service_name=SERVICE_NAME_BATTERIES,
                icon="mdi:power-plug",
                value_fn=lambda _, val=settings: bool(val.self_consumption_trading_allowed) if val else False,
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
    _LOGGER.debug("Coordinator data: %s", coordinator.data)  # Log the entire coordinator data for debugging
    # 'smart_batteries': None, 'smart_battery_details': [], 'smart_battery_sessions': []
    _LOGGER.debug("Smart Batteries data: %s", coordinator.data.get(
        "smart_batteries"))  # Log the smart batteries data for debugging
    _LOGGER.debug("Smart Battery Details: %s", coordinator.data.get(
        "smart_battery_details"))  # Log the smart battery details for debugging
    _LOGGER.debug("Smart Battery Sessions: %s", coordinator.data.get(
        "smart_battery_sessions"))  # Log the smart battery sessions for debugging

    batteries_data: SmartBatteriesData | None = coordinator.data.get("smart_batteries")
    if batteries_data:
        _LOGGER.debug("Found1 Batteries data: %s", batteries_data)  # Log the entire data object for debugging

    # batteries_data: SmartBatteriesData | None = coordinator.data.get("batteries")
    if not batteries_data:
        _LOGGER.debug("No batteries data found in coordinator. Cannot set up binary sensors.")
        return

    # _LOGGER.debug("Found Batteries data: %s", batteries_data)  # Log the entire data object for debugging

    batteries_list = getattr(batteries_data, "batteries")  # get the list of batteries from the data object

    # Log the number of batteries found for debugging
    _LOGGER.debug("Number of Batteries found: %s", len(batteries_list))
    _LOGGER.debug("Batteries list: %s", batteries_list)  # Log the list of batteries for debugging

    binary_descriptions = _build_dynamic_smart_batteries_descriptions(
        batteries_list
    )

    entities: list[FrankEnergieBinarySensor] = [
        FrankEnergieBinarySensor(coordinator, description, config_entry)
        for description in binary_descriptions
    ]

    async_add_entities(entities)
