"""Select entity controlling resolution via coordinator state. """
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FrankEnergieCoordinator

_LOGGER = logging.getLogger(__name__)


DISPLAY_TO_VALUE: dict[str, str] = {
    "15 minutes": "PT15M",
    "60 minutes": "PT60M",
}

VALUE_TO_DISPLAY: dict[str, str] = {v: k for k, v in DISPLAY_TO_VALUE.items()}

DEFAULT_DISPLAY = "15 minutes"
DEFAULT_VALUE = DISPLAY_TO_VALUE[DEFAULT_DISPLAY]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Frank Energie select entities."""
    # coordinator: FrankEnergieCoordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator: FrankEnergieCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    async_add_entities(
        [
            FrankEnergieResolutionSelect(coordinator)
        ]
    )


class FrankEnergieResolutionSelect(CoordinatorEntity, SelectEntity):
    """Select entity controlling resolution via coordinator state."""

    _attr_name = "Frank Energie Resolution"
    _attr_icon = "mdi:clock-time-four-outline"
    _attr_options = list(DISPLAY_TO_VALUE.keys())

    def __init__(self, coordinator: FrankEnergieCoordinator) -> None:
        """Initialize select entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_resolution"

    @property
    def current_option(self) -> str:
        """Return current resolution from coordinator state."""
        value = self.coordinator.resolution

        if value is None:
            return DEFAULT_DISPLAY

        return VALUE_TO_DISPLAY.get(value, DEFAULT_DISPLAY)

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.config_entry.entry_id)}}

    @property
    def extra_state_attributes(self) -> dict:
        api = self.coordinator.api_resolution
        return {"api_resolution": VALUE_TO_DISPLAY.get(api) if api else None}

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
