import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up refresh buttons for Frank Energie coordinators."""
    entry_data = hass.data[DOMAIN][entry.entry_id]

    entities: list[ButtonEntity] = []

    # Main prices coordinator
    if "coordinator" in entry_data:
        entities.append(
            FrankEnergieRefreshButton(
                entry_id=entry.entry_id,
                coordinator=entry_data["coordinator"],
                name="Refresh Frank Energie Prices"
            )
        )

    # Battery sessions
    if "battery_session_coordinator" in entry_data:
        entities.append(
            FrankEnergieRefreshButton(
                entry_id=entry.entry_id,
                coordinator=entry_data["battery_session_coordinator"],
                name="Refresh Battery Sessions"
            )
        )

    # Chargers
    if "charger_coordinator" in entry_data:
        entities.append(
            FrankEnergieRefreshButton(
                entry_id=entry.entry_id,
                coordinator=entry_data["charger_coordinator"],
                name="Refresh Chargers"
            )
        )

    if entities:
        async_add_entities(entities)


class FrankEnergieRefreshButton(ButtonEntity):
    """Button to manually refresh a Frank Energie coordinator."""

    _attr_icon = "mdi:refresh"

    def __init__(
        self,
        entry_id: str,
        coordinator: object,  # at runtime any coordinator type
        name: str
    ) -> None:
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_{name.lower().replace(' ', '_')}"
        self._coordinator = coordinator

    async def async_press(self) -> None:
        """Handle button press to trigger manual refresh."""
        _LOGGER.debug("Manual refresh requested: %s", self._attr_name)
        await self._coordinator.async_request_refresh()
