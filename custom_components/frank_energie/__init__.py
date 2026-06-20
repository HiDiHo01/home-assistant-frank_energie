"""The Frank Energie component."""
# __init__.py
# version 2026.06.15

import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.util import dt as dt_util
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import AuthException
from python_frank_energie.models import UserSites

from .const import CONF_COORDINATOR, DATA_ELECTRICITY, DATA_GAS, DOMAIN
from .coordinator import (
    FrankEnergieCoordinator,
)
from .exceptions import NoSuitableSitesFoundError

_LOGGER = logging.getLogger(__name__)
PRICE_RELEASE_HOUR_UTC: Final[int] = 11


@dataclass
class FrankEnergieEntryData:
    """Runtime data stored on a ConfigEntry."""

    coordinator: FrankEnergieCoordinator
    battery_session_coordinators: dict[str, object] = field(default_factory=dict)


# Sensor must be listed separately — see _async_forward_entry_setups below.
_DEPENDENT_PLATFORMS: list[str] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]
PLATFORMS: list[str] = [Platform.SENSOR] + _DEPENDENT_PLATFORMS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[FrankEnergieEntryData],
) -> bool:
    """Set up the Frank Energie component from a config entry."""
    _LOGGER.debug("Setting up Frank Energie component for entry: %s", entry.entry_id)
    _LOGGER.debug("Setting up Frank Energie entry: %s", entry)
    _LOGGER.debug("Setting up Frank Energie entry data: %s", entry.data)
    _LOGGER.debug("Setting up Frank Energie entry domain: %s", entry.domain)
    _LOGGER.debug("Setting up Frank Energie entry unique_id: %s", entry.unique_id)
    _LOGGER.debug("Setting up Frank Energie entry options: %s", entry.options)
    component = FrankEnergieComponent(hass, entry)
    return await component.setup()


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict[str, Any],
    async_add_entities,
) -> bool:
    """Set up the Frank Energie sensor platform.
    Deprecated for new development because Home Assistant encourages the use of
    config entries and UI-driven setup.
    """
    warnings.warn(
        "async_setup_platform is deprecated; use config entries instead.",
        DeprecationWarning,
    )
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


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[FrankEnergieEntryData],
) -> bool:
    """Handle removal of an entry."""
    _LOGGER.debug("Unloading entry: %s", entry.entry_id)

    if unload_ok := await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    ):
        hass.data.setdefault(DOMAIN, {}).pop(
            entry.entry_id,
            None,
        )

    return unload_ok


class FrankEnergieComponent:  # pylint: disable=too-few-public-methods
    """Core setup handler for the Frank Energie component."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry[FrankEnergieEntryData]
    ) -> None:
        """Initialize the Frank Energie component."""
        self.hass = hass
        self.entry = entry

    async def _maybe_refresh_tomorrow(
        self,
        coordinator: FrankEnergieCoordinator,
    ) -> None:
        """Refresh tomorrow prices when they are expected to be available."""

        now_utc = dt_util.utcnow()

        if now_utc.hour == 0 and now_utc.minute == 0:
            coordinator.promote_tomorrow_prices()
            return

        if now_utc.hour == 0:
            return

        # Convert to Europe/Amsterdam time to handle publication window in local time
        tz_amsterdam = ZoneInfo("Europe/Amsterdam")
        now_local = now_utc.astimezone(tz_amsterdam)

        if now_local.hour < 13:
            return

        if coordinator.cached_prices_tomorrow is not None and (
            coordinator.cached_prices_tomorrow.electricity is not None
            or coordinator.cached_prices_tomorrow.gas is not None
        ):
            _LOGGER.debug("Tomorrow prices already available, skipping refresh")
            return

        _LOGGER.debug(
            "Tomorrow prices not available, requesting refresh at %s local time",
            now_local.strftime("%H:%M"),
        )

        await coordinator.async_request_refresh()

    async def old_maybe_refresh_tomorrow(
        self, coordinator: FrankEnergieCoordinator, minute: int
    ) -> None:
        """Trigger a refresh during the price release window (11:00-12:59 UTC)."""
        # now_utc = datetime.now(timezone.utc)
        now_utc = dt_util.utcnow()

        if (
            now_utc.hour == 0
            and now_utc.minute == 0
            and coordinator.cached_prices_tomorrow is not None
        ):
            cached_prices = coordinator.cached_prices or coordinator.data

            if cached_prices is None:
                _LOGGER.warning(
                    "Cannot promote tomorrow prices: no cached data available"
                )
                return

            cached_prices[DATA_ELECTRICITY] = (
                coordinator.cached_prices_tomorrow.electricity
            )
            cached_prices[DATA_GAS] = coordinator.cached_prices_tomorrow.gas

            coordinator.cached_prices_tomorrow = None
            coordinator.cached_prices = cached_prices
            coordinator.data = cached_prices

            coordinator.async_update_listeners()

            _LOGGER.debug("Promoted tomorrow prices to today prices at midnight")

        if now_utc.hour == 1 and coordinator.cached_prices_tomorrow is not None:
            coordinator.cached_prices_tomorrow = None
            _LOGGER.debug("Cleared cached tomorrow's prices at midnight UTC")

        if (
            now_utc.hour < PRICE_RELEASE_HOUR_UTC
            and coordinator.cached_prices_tomorrow is not None
        ):
            _LOGGER.debug("Tomorrow's prices already cached, skipping extra refresh")
            return

        if now_utc.hour in {11, 13} and coordinator.cached_prices_tomorrow is None:
            _LOGGER.debug(
                "Price release window: triggering refresh at %02d:%02d UTC",
                now_utc.hour,
                minute,
            )
            await coordinator.async_request_refresh()

        if now_utc.hour > 13 and coordinator.cached_prices_tomorrow is None:
            _LOGGER.debug(
                "After price release window and no cached prices: triggering refresh"
            )
            await coordinator.async_request_refresh()

    async def _schedule_aligned_updates(
        self,
        coordinator: FrankEnergieCoordinator,
    ) -> None:
        """Schedule coordinator refreshes."""

        async def _async_refresh(
            _: datetime,
        ) -> None:
            await coordinator.async_request_refresh()

        if coordinator.resolution == "PT15M":
            unsub = async_track_utc_time_change(
                self.hass,
                _async_refresh,
                minute=[0, 15, 30, 45],
                second=0,
            )
        else:
            unsub = async_track_utc_time_change(
                self.hass,
                _async_refresh,
                minute=0,
                second=0,
            )

        self.entry.async_on_unload(unsub)

        async def _async_tomorrow_refresh(
            now: datetime,
        ) -> None:
            await self._maybe_refresh_tomorrow(
                coordinator,
            )

        self.entry.async_on_unload(
            async_track_utc_time_change(
                self.hass,
                _async_tomorrow_refresh,
                minute=list(range(0, 60, 5)),
                second=0,
            )
        )

    async def setup(self) -> bool:
        """Set up the Frank Energie component from a config entry."""
        _LOGGER.debug("Setting up Frank Energie component")

        # For backwards compatibility, update the unique ID
        self._update_unique_id()

        # Create API and Coordinator
        _LOGGER.debug("Creating Frank Energie API instance")
        clientsession = async_get_clientsession(self.hass)
        api = FrankEnergie(
            clientsession=clientsession,
            auth_token=self.entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=self.entry.data.get(CONF_TOKEN),
        )
        coordinator = self._create_frank_energie_coordinator(api)

        # Awaiting the coroutine method call
        await self._select_site_reference(coordinator)

        # Load cached data before the first refresh to ensure we have data to work with immediately
        # await coordinator.async_load_cached_data() # not in use yet, but could be implemented in the future if needed

        # Perform the initial refresh for the coordinator
        _LOGGER.debug("Performing initial refresh for coordinator")
        await coordinator.async_config_entry_first_refresh()

        # Schedule updates aliged to price slot boundaries
        _LOGGER.debug("Scheduling aligned updates for coordinator")
        await self._schedule_aligned_updates(coordinator)

        # Save the coordinator to Home Assistant data
        await self._save_coordinator_to_hass_data(coordinator)

        # Forward entry setups to appropriate platforms
        _LOGGER.debug("Forwarding entry setups to platforms")
        await self._async_forward_entry_setups()
        _LOGGER.debug("Finished forwarding entry setups to platforms")
        return True

    def _update_unique_id(self) -> None:
        """Update the unique ID of the config entry."""
        if (
            self.entry.unique_id is None
            or self.entry.unique_id == "frank_energie_component"
        ):
            self.hass.config_entries.async_update_entry(
                self.entry, unique_id="frank_energie"
            )

    async def _select_site_reference(
        self, coordinator: FrankEnergieCoordinator
    ) -> None:
        """Get access token from entry data or options and select site reference of not already set"""
        """Ensure a site reference is selected and stored in entry data."""
        """Select the site reference for the coordinator."""
        """In Home Assistant worden deze attributen als volgt gebruikt:
        entry.data: bevat de gegevens die tijdens de initiële configuratie zijn opgeslagen (via config_flow).
        entry.options: bevat de gegevens die via een options flow zijn aangepast/nageleverd."""
        _LOGGER.debug("Selecting site reference for coordinator")

        access_token = self.entry.options.get(CONF_ACCESS_TOKEN) or self.entry.data.get(
            CONF_ACCESS_TOKEN
        )

        site_reference = self.entry.data.get("site_reference")

        if site_reference is not None:
            if "@" in self.entry.title or self.entry.title == "Frank Energie":
                _LOGGER.info(
                    "Config entry title is email or default; restoring site address title"
                )
                try:
                    _, title = await self._get_site_reference_and_title(coordinator)
                    if title and isinstance(title, str) and title != "Onbekend adres":
                        self.hass.config_entries.async_update_entry(
                            self.entry, title=title
                        )
                except Exception as ex:
                    _LOGGER.warning("Could not restore site title: %s", ex)
            return

        if self.entry.data.get("site_reference") is None and access_token:
            site_reference, title = await self._get_site_reference_and_title(
                coordinator
            )
            if not site_reference:
                raise NoSuitableSitesFoundError(
                    "No suitable sites found for this account"
                )

            # Controleer of de titel correct is gegenereerd
            if not isinstance(title, str):
                _LOGGER.warning(
                    "Failed to generate title for the site reference: %s",
                    site_reference,
                )
                return

            _LOGGER.debug("Site reference: %s, Title: %s", site_reference, title)
            # Update entry data and title using async_update_entry method
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, "site_reference": site_reference},
                title=title,
            )

    async def _get_site_reference_and_title(
        self, coordinator: FrankEnergieCoordinator
    ) -> tuple[str, str]:
        """Fetch site reference and human-readable title."""
        _LOGGER.debug("Getting site reference and title for coordinator")

        try:
            # Haal de 'UserSites' gegevens op van de coordinator API
            user_sites_data: UserSites = await coordinator.api.UserSites()
        except AuthException:
            _LOGGER.debug(
                "Authentication failed during site fetch, attempting token renewal"
            )
            try:
                await coordinator._try_renew_token()
                user_sites_data = await coordinator.api.UserSites()
            except Exception as renew_ex:
                _LOGGER.exception("Token renewal failed during setup: %s", renew_ex)
                raise ConfigEntryAuthFailed from renew_ex

        # Haal de bezorgsites op uit de 'UserSites' gegevens
        user_sites = user_sites_data.deliverySites

        # Controleer of er bezorgsites zijn gevonden
        if not user_sites:
            raise NoSuitableSitesFoundError(
                "No suitable delivery sites found for this account"
            )

        # Selecteer de bezorgsite
        if len(user_sites) > 1:
            _LOGGER.warning(
                "Multiple delivery sites found; defaulting to the first one. Create an issue on github if you need support for multiple sites."
            )
            # TODO: Iimplementeer logica voor site-selectie
            selected_site = user_sites[0]
        else:
            # Selecteer de eerste bezorgsite
            selected_site = user_sites[0]

        # Genereer een titel op basis van de adresgegevens van de bezorgsite
        address = getattr(selected_site, "address", None)
        street = getattr(address, "street", "")
        number = getattr(address, "houseNumber", "")
        addition = getattr(address, "houseNumberAddition", "") or ""

        title = " ".join(p for p in [street, f"{number}{addition}"] if p)

        reference = str(getattr(selected_site, "reference", user_sites_data.reference))

        _LOGGER.debug("Generated title: %s for site reference: %s", title, reference)
        return reference, title

    def _create_frank_energie_coordinator(
        self, api: FrankEnergie
    ) -> FrankEnergieCoordinator:
        """Create the Frank Energie Coordinator instance."""
        _LOGGER.debug("Creating Frank Energie Coordinator instance")
        return FrankEnergieCoordinator(self.hass, self.entry, api)

    async def _async_forward_entry_setups(self) -> None:
        """Forward entry setups to appropriate platforms.

        The sensor platform is set up first and awaited before the dependent
        platforms (binary_sensor, button, switch, datetime) run. This ensures
        all parent service devices (Frank Energie - Batteries, Chargers, etc.)
        are registered in the device registry before child devices attempt to
        link to them via via_device.
        """
        _LOGGER.debug("Starting to forward entry setups to platforms")
        try:
            # 1. Register all sensor (parent) devices first.
            await self.hass.config_entries.async_forward_entry_setups(
                self.entry, [Platform.SENSOR]
            )
            # 2. Now set up all remaining platforms concurrently.
            await self.hass.config_entries.async_forward_entry_setups(
                self.entry, _DEPENDENT_PLATFORMS
            )
            _LOGGER.debug("Successfully forwarded entry setups to platforms")
        except Exception as e:
            _LOGGER.error("Error forwarding entry setups to platforms: %s", str(e))
            raise

    async def _save_coordinator_to_hass_data(
        self,
        coordinator: FrankEnergieCoordinator,
    ) -> None:
        """Save the coordinator to the Home Assistant data."""
        _LOGGER.debug("Saving coordinator to Home Assistant data (entry.runtime_data)")
        hass_data = self.hass.data.setdefault(DOMAIN, {})
        hass_data[self.entry.entry_id] = {
            CONF_COORDINATOR: coordinator,
        }

        self.entry.runtime_data = FrankEnergieEntryData(
            coordinator=coordinator,
        )

    def _remove_entry_from_hass_data(self) -> None:
        """Remove the entry from the Home Assistant data."""
        _LOGGER.debug("Removing entry from Home Assistant data")
        self.hass.data[DOMAIN].pop(
            self.entry.entry_id, None
        )  # Ensure no KeyError if entry_id does not exist


class FrankEnergieDiagnosticSensor(Entity):
    """Class representing the Frank Energie diagnostic sensor."""

    def __init__(self, frank_energie: FrankEnergie) -> None:
        """Initialize the sensor."""
        self._frank_energie = frank_energie
        self._state: str | None = None

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        # return "Frank Energie Diagnostic Sensor"
        return "frank_energie_diagnostic_sensor"

    @property
    def state(self) -> str | None:
        """Return the sensor state."""
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return optional state attributes."""
        return {}

    async def async_update(self) -> None:
        """Fetch latest state from the Frank Energie API."""
        _LOGGER.debug("Updating FrankEnergieDiagnosticSensor")
        # Implement the logic to update the sensor state
        # You can use the FrankEnergie API client instance (self._frank_energie)
        # to fetch diagnostic data and update the sensor state accordingly
        try:
            self._state = await self._frank_energie.get_diagnostic_data()
        except Exception as err:
            # Handle specific exceptions and raise more descriptive ones if necessary
            _LOGGER.exception("Failed to update diagnostic sensor: %s", str(err))
            self._state = "error"
            raise ValueError(
                f"Failed to update FrankEnergieDiagnosticSensor: {str(err)}"
            ) from err
