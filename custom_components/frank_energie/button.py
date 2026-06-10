import logging
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    API_CONF_URL,
    COMPONENT_TITLE,
    CONF_COORDINATOR,
    DOMAIN,
    SERVICE_NAME_BATTERY_SESSIONS,
    SERVICE_NAME_ENODE_CHARGERS,
    SERVICE_NAME_PRICES,
    VERSION,
)

# if TYPE_CHECKING:
#     pass

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrankEnergieButtonEntityDescription(ButtonEntityDescription):
    """Describes a Frank Energie button entity."""
    service_name: str = SERVICE_NAME_PRICES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up refresh buttons for Frank Energie coordinators."""
    entry_data = hass.data[DOMAIN][entry.entry_id]

    entities: list[ButtonEntity] = []

    # Main prices coordinator
    if CONF_COORDINATOR in entry_data:
        entities.append(
            FrankEnergieRefreshButton(
                coordinator=entry_data[CONF_COORDINATOR],
                description=FrankEnergieButtonEntityDescription(
                    key="refresh_prices",
                    name="Refresh Frank Energie Prices",
                    service_name=SERVICE_NAME_PRICES,
                ),
                entry=entry,
            )
        )

    # Battery sessions
    if "battery_session_coordinator" in entry_data:
        entities.append(
            FrankEnergieRefreshButton(
                coordinator=entry_data["battery_session_coordinator"],
                description=FrankEnergieButtonEntityDescription(
                    key="refresh_battery_sessions",
                    name="Refresh Battery Sessions",
                    service_name=SERVICE_NAME_BATTERY_SESSIONS,
                ),
                entry=entry,
            )
        )

    # Chargers
    if "charger_coordinator" in entry_data:
        entities.append(
            FrankEnergieRefreshButton(
                coordinator=entry_data["charger_coordinator"],
                description=FrankEnergieButtonEntityDescription(
                    key="refresh_chargers",
                    name="Refresh Chargers",
                    service_name=SERVICE_NAME_ENODE_CHARGERS,
                    translation_key="refresh_chargers",
                ),
                entry=entry,
            )
        )

    if entities:
        async_add_entities(entities)


class FrankEnergieRefreshButton(ButtonEntity):
    """Button to manually refresh a Frank Energie coordinator."""

    _attr_icon = "mdi:refresh"
    _attr_has_entity_name = True
    entity_description: FrankEnergieButtonEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,  # at runtime any coordinator type
        description: FrankEnergieButtonEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        self.entity_description = description  # type: ignore[override]
        self._attr_translation_key = description.key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._coordinator = coordinator

        device_info_identifiers: set[tuple[str, str]] = (
            {(DOMAIN, f"{entry.entry_id}")}
            if description.service_name == SERVICE_NAME_PRICES
            else {(DOMAIN, f"{entry.entry_id}_{description.service_name}")}
        )
        self._attr_device_info = DeviceInfo(
            identifiers=device_info_identifiers,
            name=f"{COMPONENT_TITLE} - {description.service_name}",
            translation_key=f"{COMPONENT_TITLE} - {description.service_name}",
            manufacturer=COMPONENT_TITLE,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=API_CONF_URL,
            sw_version=VERSION,
        )

    async def async_press(self) -> None:
        """Handle button press to trigger manual refresh."""
        _LOGGER.debug("Manual refresh requested: %s", self.entity_description.name)
        await self._coordinator.async_request_refresh()
