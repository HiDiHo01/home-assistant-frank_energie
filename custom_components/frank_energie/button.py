"""Refresh buttons for the Frank Energie integration."""

import logging
from dataclasses import dataclass, replace

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    API_CONF_URL,
    COMPONENT_TITLE,
    DATA_DELIVERY_SITE,
    DOMAIN,
    SERVICE_NAME_BATTERIES,
    SERVICE_NAME_GAS_PRICES,
    SERVICE_NAME_PRICES,
    VERSION,
)

from .helpers import (
    async_sync_contract_entities,
    device_translation_key,
    known_site_contracts,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrankEnergieButtonEntityDescription(ButtonEntityDescription):
    """Describes a Frank Energie button entity."""

    service_name: str = SERVICE_NAME_PRICES


_PRICE_REFRESH = FrankEnergieButtonEntityDescription(
    key="refresh_prices",
    translation_key="refresh_prices",
)
_GAS_PRICE_REFRESH = replace(
    _PRICE_REFRESH, key="refresh_gas_prices", service_name=SERVICE_NAME_GAS_PRICES
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up refresh buttons for Frank Energie coordinators."""
    runtime_data = entry.runtime_data
    settings = runtime_data.settings_coordinator
    authenticated = settings.api.is_authenticated
    contracts = (
        known_site_contracts(settings.data.get(DATA_DELIVERY_SITE))
        if authenticated
        else None
    )
    has_dual_contract = contracts == {"ELECTRICITY", "GAS"}
    if contracts is not None or not authenticated:
        gas_refresh_unique_id = f"{entry.entry_id}_{_GAS_PRICE_REFRESH.key}"
        async_sync_contract_entities(
            hass,
            entry,
            "button",
            {gas_refresh_unique_id: has_dual_contract},
            enabled_by_default=(
                None if entry.pref_disable_new_entities else {gas_refresh_unique_id}
            ),
        )

    price_description = (
        replace(_PRICE_REFRESH, service_name=SERVICE_NAME_GAS_PRICES)
        if contracts == {"GAS"}
        else _PRICE_REFRESH
    )
    price_descriptions = [price_description]
    if has_dual_contract:
        price_descriptions.append(_GAS_PRICE_REFRESH)

    entities: list[ButtonEntity] = [
        FrankEnergieRefreshButton(
            coordinator=runtime_data.coordinator,
            description=description,
            entry=entry,
        )
        for description in price_descriptions
    ]

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

    async def async_press(self) -> None:
        """Handle button press to trigger manual refresh."""
        _LOGGER.debug("Manual refresh requested: %s", self.entity_description.key)
        await self._coordinator.async_request_refresh()
