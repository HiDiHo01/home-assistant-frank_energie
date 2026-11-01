import logging
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)

from .const import (
    API_CONF_URL,
    COMPONENT_TITLE,
    DOMAIN,
    SERVICE_NAME_BATTERIES,
    SERVICE_NAME_PRICES,
    VERSION,
)

from .helpers import device_translation_key
from .service_refresh import (
    RefreshService,
    service_refresh_attributes,
    service_refreshes,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrankEnergieButtonEntityDescription(ButtonEntityDescription):
    """Describes a Frank Energie button entity."""

    service_name: str = SERVICE_NAME_PRICES


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up refresh buttons for Frank Energie coordinators."""
    runtime_data = entry.runtime_data

    entities: list[ButtonEntity] = []

    # One button per existing logical service, reusing its source coordinator.
    # Prices retains its established unique key/entity ID across upgrades.
    trace = getattr(runtime_data.price_coordinator, "_diagnostic_trace", None)
    for service in service_refreshes(runtime_data):
        # Keep the established literal key and translation for existing users.
        # New service buttons use their own stable keys.
        description = (
            FrankEnergieButtonEntityDescription(
                key="refresh_prices",
                name="Refresh Frank Energie Prices",
                service_name=SERVICE_NAME_PRICES,
            )
            if service.key == "prices"
            else FrankEnergieButtonEntityDescription(
                key=f"refresh_{service.key}",
                name=f"Refresh {service.device_name}",
                service_name=service.device_name,
            )
        )
        entities.append(
            FrankEnergieRefreshButton(
                coordinator=service.coordinator,
                description=description,
                entry=entry,
                service=service,
                trace=trace,
            )
        )

    # Battery sessions — one button covering all battery session coordinators
    if runtime_data.battery_session_coordinators:
        first_session_coordinator = next(
            iter(runtime_data.battery_session_coordinators.values())
        )
        entities.append(
            FrankEnergieRefreshButton(
                coordinator=first_session_coordinator,
                description=FrankEnergieButtonEntityDescription(
                    key="refresh_battery_sessions",
                    name="Refresh Battery Sessions",
                    service_name=SERVICE_NAME_BATTERIES,
                ),
                entry=entry,
            )
        )

    if entities:
        async_add_entities(entities)


class FrankEnergieRefreshButton(CoordinatorEntity, ButtonEntity):
    """Button to manually refresh a Frank Energie coordinator."""

    _attr_icon = "mdi:refresh"
    _attr_has_entity_name = True
    entity_description: FrankEnergieButtonEntityDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,  # at runtime any coordinator type
        description: FrankEnergieButtonEntityDescription,
        entry: ConfigEntry,
        service: RefreshService | None = None,
        trace: object | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._service = service or RefreshService(
            description.key, description.service_name, coordinator, ()
        )
        self._trace = trace
        self.entity_description = description  # type: ignore[override]
        self._attr_translation_key = description.translation_key or description.key
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
            translation_key=device_translation_key(description.service_name),
            manufacturer=COMPONENT_TITLE,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=API_CONF_URL,
            sw_version=VERSION,
        )

    @property
    def available(self) -> bool:
        """Manual recovery stays possible even if the last poll failed."""
        return True

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose actual polling cadence and observed API calls on the button."""
        return service_refresh_attributes(self._service, self._trace)

    async def async_press(self) -> None:
        """Handle button press to trigger manual refresh."""
        _LOGGER.debug("Manual refresh requested: %s", self.entity_description.name)
        await self._coordinator.async_request_refresh()
