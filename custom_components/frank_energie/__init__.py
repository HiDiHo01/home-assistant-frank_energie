"""The Frank Energie component."""
# __init__.py

import logging
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry  # type: ignore
from homeassistant.const import (CONF_ACCESS_TOKEN, CONF_TOKEN,  # type: ignore
                                 Platform)
from homeassistant.core import HomeAssistant  # type: ignore
from homeassistant.helpers.aiohttp_client import \
    async_get_clientsession  # type: ignore
from homeassistant.helpers.entity import Entity  # type: ignore
from python_frank_energie import FrankEnergie

from .const import CONF_COORDINATOR, DOMAIN
from .coordinator import FrankEnergieCoordinator
from .exceptions import NoSuitableSitesFoundError

_LOGGER = logging.getLogger(__name__)

# PLATFORMS = [Platform.SENSOR, "frank_energie_diagnostic_sensor"]
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Frank Energie component from a config entry."""
    _LOGGER.debug(
        "Setting up Frank Energie component for entry: %s", entry.entry_id)
    _LOGGER.debug("Setting up Frank Energie entry: %s", entry)
    _LOGGER.debug("Setting up Frank Energie entry data: %s", entry.data)
    _LOGGER.debug("Setting up Frank Energie entry domain: %s", entry.domain)
    _LOGGER.debug("Setting up Frank Energie entry unique_id: %s",
                  entry.unique_id)
    _LOGGER.debug("Setting up Frank Energie entry options: %s", entry.options)
    component = FrankEnergieComponent(hass, entry)
    return await component.setup()


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict[str, Any],
    async_add_entities,
    discovery_info=None
) -> bool:
    """Set up the Frank Energie sensor platform.
    Deprecated for new development because Home Assistant encourages the use of
    config entries and UI-driven setup.
    """
    _LOGGER.debug("Setting up Frank Energie sensor platform")
    timezone = hass.config.time_zone  # Get the configured time zone
    _LOGGER.debug("Configured Time Zone: %s", timezone)
    # Pass the timezone to a platform
    hass.data[DOMAIN] = {
        "timezone": timezone,
    }
    coordinator = hass.data[DOMAIN][CONF_COORDINATOR]
    api = coordinator.api
    sensor = FrankEnergieDiagnosticSensor(api)
    async_add_entities([sensor])
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    _LOGGER.debug("Unloading entry: %s", entry.entry_id)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


class FrankEnergieComponent:  # pylint: disable=too-few-public-methods
    """Class representing the Frank Energie component."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the Frank Energie component."""
        self.hass = hass
        self.entry = entry

    async def setup(self) -> bool:
        """Set up the Frank Energie component from a config entry."""
        _LOGGER.debug("Setting up Frank Energie component")

        # For backwards compatibility, update the unique ID
        self._update_unique_id()

        # Create API and Coordinator
        api = self._create_frank_energie_api()
        coordinator = self._create_frank_energie_coordinator(api)

        # Awaiting the coroutine method call
        await self._select_site_reference(coordinator)

        # Perform the initial refresh for the coordinator
        _LOGGER.debug("Performing initial refresh for coordinator")
        await coordinator.async_config_entry_first_refresh()

        # Save the coordinator to Home Assistant data
        await self._save_coordinator_to_hass_data(coordinator)

        # Forward entry setups to appropriate platforms
        _LOGGER.debug("Forwarding entry setups to platforms")
        await self._async_forward_entry_setups()
        _LOGGER.debug("Finished forwarding entry setups to platforms")
        return True

    def _update_unique_id(self) -> None:
        """Update the unique ID of the config entry."""
        if self.entry.unique_id is None or self.entry.unique_id == "frank_energie_component":
            self.hass.config_entries.async_update_entry(
                self.entry, unique_id="frank_energie")

    async def _select_site_reference(self, coordinator: FrankEnergieCoordinator) -> None:
        """Select the site reference for the coordinator."""
        _LOGGER.debug("Selecting site reference for coordinator")
        if self.entry.data.get("site_reference") is None and self.entry.data.get(CONF_ACCESS_TOKEN):
            site_reference, title = await self._get_site_reference_and_title(coordinator)
            if not site_reference:
                raise NoSuitableSitesFoundError(
                    "No suitable sites found for this account")

            # Controleer of de titel correct is gegenereerd
            if not isinstance(title, str):
                _LOGGER.warning(
                    "Failed to generate title for the site reference: %s", site_reference)
                return

            _LOGGER.debug("Site reference: %s, Title: %s",
                          site_reference, title)
            # Update entry data and title using async_update_entry method
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, "site_reference": site_reference}, title=title
            )

    async def _get_site_reference_and_title(self,
                                            coordinator: FrankEnergieCoordinator
                                            ) -> tuple[str, str]:
        _LOGGER.debug("Getting site reference and title for coordinator")

        # Haal de 'Me' gegevens op van de coordinator API
        # me_data = await coordinator.api.me()
        user_sites_data = await coordinator.api.UserSites()

        # Haal de bezorgsites op uit de 'Me' gegevens
        # delivery_sites = me_data.deliverySites # remove this line
        user_sites = user_sites_data.deliverySites

        # Controleer of er bezorgsites zijn gevonden
        # if not delivery_sites:
        #     raise NoSuitableSitesFoundError(
        #         "No suitable delivery sites found for this account")
        if not user_sites:
            raise NoSuitableSitesFoundError(
                "No suitable delivery sites found for this account")

        # Selecteer de eerste bezorgsite voor nu, je kunt logica toevoegen
        # om de juiste site te selecteren op basis van voorkeuren
        # selected_site = delivery_sites[0]
        selected_site = user_sites[0]

        # Maak een titel op basis van de adresgegevens van de bezorgsite
        title = f"{selected_site.address.street} {selected_site.address.houseNumber}"
        if selected_site.address.houseNumberAddition:
            title += f" {selected_site.address.houseNumberAddition}"

        # Retourneer de referentie van de site en de titel
        return selected_site.reference, title

    def _create_frank_energie_api(self) -> FrankEnergie:
        """Create the Frank Energie API instance."""
        _LOGGER.debug("Creating Frank Energie API instance")
        return FrankEnergie(
            clientsession=async_get_clientsession(self.hass),
            auth_token=self.entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=self.entry.data.get(CONF_TOKEN),
        )

    def _create_frank_energie_coordinator(self, api: FrankEnergie
                                          ) -> FrankEnergieCoordinator:
        """Create the Frank Energie Coordinator instance."""
        _LOGGER.debug("Creating Frank Energie Coordinator instance")
        return FrankEnergieCoordinator(self.hass, self.entry, api)

    async def old_async_forward_entry_setups(self) -> None:
        """Forward entry setups to appropriate platforms."""
        _LOGGER.debug("Forwarding entry setups to platforms")
        await self.hass.config_entries.async_forward_entry_setups(self.entry,
                                                                  PLATFORMS)
        _LOGGER.debug("Finished forwarding entry setups to platforms 2")

    async def _async_forward_entry_setups(self) -> None:
        """Forward entry setups to appropriate platforms."""
        _LOGGER.debug("Starting to forward entry setups to platforms")
        try:
            await self.hass.config_entries.async_forward_entry_setups(self.entry, PLATFORMS)
            _LOGGER.debug("Successfully forwarded entry setups to platforms")
        except Exception as e:
            _LOGGER.error("Error forwarding entry setups to platforms: %s", str(e))
            raise
        _LOGGER.debug("Finished forwarding entry setups to platforms")

    async def _save_coordinator_to_hass_data(self,
                                             coordinator: FrankEnergieCoordinator
                                             ) -> None:
        """Save the coordinator to the Home Assistant data."""
        _LOGGER.debug("Saving coordinator to Home Assistant data")
        hass_data = self.hass.data.setdefault(DOMAIN, {})
        hass_data[self.entry.entry_id] = {CONF_COORDINATOR: coordinator}

    def _remove_entry_from_hass_data(self) -> None:
        """Remove the entry from the Home Assistant data."""
        _LOGGER.debug("Removing entry from Home Assistant data")
        self.hass.data[DOMAIN].pop(self.entry.entry_id)


class FrankEnergieDiagnosticSensor(Entity):
    """Class representing the Frank Energie diagnostic sensor."""

    def __init__(self, frank_energie: FrankEnergie) -> None:
        """Initialize the sensor."""
        self._frank_energie = frank_energie
        self._state: Optional[str] = None

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "frank_energie_diagnostic_sensor"

    @property
    def state(self) -> Optional[str]:
        """Return the sensor state."""
        return self._state

    @property
    def device_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        return {
            # Add additional attributes if needed
        }

    async def async_update(self) -> None:
        """Update the sensor state."""
        _LOGGER.debug("Updating FrankEnergieDiagnosticSensor")
        # Implement the logic to update the sensor state
        # You can use the FrankEnergie API client instance (self._frank_energie)
        # to fetch diagnostic data and update the sensor state accordingly
        try:
            self._state = await self._frank_energie.get_diagnostic_data()
        except Exception as e:
            # Handle specific exceptions and raise more descriptive ones if necessary
            raise ValueError(
                f"Failed to update FrankEnergieDiagnosticSensor: {str(e)}") from e
