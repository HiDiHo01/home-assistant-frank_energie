"""Select entity controlling resolution via coordinator state. """

# select.py
# version 2026.05.31
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_CONF_URL,
    COMPONENT_TITLE,
    CONF_COORDINATOR,
    DOMAIN,
    SERVICE_NAME_SETTINGS,
)
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
    coordinator: FrankEnergieCoordinator = hass.data[DOMAIN][entry.entry_id][CONF_COORDINATOR]

    # no need to add select if not authenticated, as it won't be available until after authentication
    # this disables the select, remove these two lines if you want the select to always be present
    # if not coordinator.api.is_authenticated:
    #     return

    async_add_entities(
        [
            FrankEnergieResolutionSelect(coordinator)
        ]
    )


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
        value = self.coordinator.resolution
        return VALUE_TO_DISPLAY.get(value, DEFAULT_DISPLAY)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.config_entry.entry_id}_{self.service_name}")},
            name=f"{COMPONENT_TITLE} - {self.service_name}",
            translation_key=f"{COMPONENT_TITLE} - {self.service_name}",
            manufacturer=COMPONENT_TITLE,
            model=self.service_name,
            configuration_url=API_CONF_URL,
            entry_type=DeviceEntryType.SERVICE,
        )
        # {"identifiers": {(DOMAIN, self.coordinator.config_entry.entry_id)}}

    @property
    def available(self) -> bool:
        """Return False when a resolution change is not currently possible."""
        if not self.coordinator.api.is_authenticated:
            return True
        if self.coordinator._resolution_change_pending:
            return False
        state = self.coordinator._api_resolution_state
        if state is None:
            return True  # unknown, allow optimistically
        return state.isChangeRequestPossible

    @property
    def extra_state_attributes(self) -> dict:
        api = self.coordinator.api_resolution
        state = self.coordinator._api_resolution_state

        if not self.coordinator.api.is_authenticated:
            return {
                "is_authenticated": False,
                "resolution": VALUE_TO_DISPLAY.get(self.coordinator.resolution),
            }

        return {
            "api_resolution": VALUE_TO_DISPLAY.get(api) if api else None,
            "active_option": VALUE_TO_DISPLAY.get(state.activeOption) if state and state.activeOption else None,
            "available_options": [VALUE_TO_DISPLAY.get(v) for v in state.availableOptions] if state else None,
            "change_possible": state.isChangeRequestPossible if state else None,
            "effective_date": str(state.changeRequestEffectiveDate) if state and state.changeRequestEffectiveDate else None,
            "upcoming_change": str(state.upcomingChange) if state and state.upcomingChange else None,
            "upcoming_change_effective_date": str(state.upcomingChangeEffectiveDate) if state and state.upcomingChangeEffectiveDate else None,
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
