"""The Frank Energie component."""
# __init__.py
# version 2026.06.15

import asyncio
import logging
from datetime import datetime, time
from typing import Final
from zoneinfo import ZoneInfo

from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_utc_time_change
from homeassistant.util import dt as dt_util
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import AuthException
from python_frank_energie.models import UserSites

from .const import (
    DOMAIN,
    SERVICE_NAME_BATTERY_SESSIONS,
    TIMEZONE_AMSTERDAM,
    TOMORROW_PUBLICATION_HOUR_LOCAL,
)
from .coordinator import (
    FrankEnergieCoordinator,
    FrankEnergieSettingsCoordinator,
    FrankEnergiePriceCoordinator,
    FrankEnergieBatteryCoordinator,
    FrankEnergieChargerCoordinator,
    FrankEnergiePVCoordinator,
    FrankEnergieVehicleCoordinator,
    FrankEnergieStatisticsCoordinator,
)
from .exceptions import NoSuitableSitesFoundError
from .helpers import log_api_exception, price_cache_store
from .runtime_data import FrankEnergieConfigEntry, FrankEnergieEntryData

_LOGGER = logging.getLogger(__name__)
PRICE_RELEASE_HOUR_UTC: Final[int] = 11
# HA Core versions before the config-entry-scoped device registry API don't
# have this method.
_HAS_DEVICE_BY_IDENTIFIER: Final = hasattr(
    dr.DeviceRegistry, "async_get_device_by_identifier"
)


# Sensor must be listed separately — see _async_forward_entry_setups below.
_DEPENDENT_PLATFORMS: list[str] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SELECT,
]
PLATFORMS: list[str] = [Platform.SENSOR] + _DEPENDENT_PLATFORMS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FrankEnergieConfigEntry,
) -> bool:
    """Set up the Frank Energie component from a config entry."""
    _LOGGER.debug("Setting up Frank Energie component for entry: %s", entry.entry_id)
    component = FrankEnergieComponent(hass, entry)
    entry.runtime_data = await component.async_prepare()
    try:
        await component.async_setup()
    except BaseException:
        # HA runs registered unload callbacks after a failed setup, but only
        # deletes runtime_data after a successful unload of a loaded entry.
        del entry.runtime_data
        raise
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: FrankEnergieConfigEntry,
) -> bool:
    """Handle removal of an entry."""
    _LOGGER.debug("Unloading entry: %s", entry.entry_id)

    return await hass.config_entries.async_unload_platforms(
        entry,
        PLATFORMS,
    )


async def async_remove_entry(
    hass: HomeAssistant,
    entry: FrankEnergieConfigEntry,
) -> None:
    """Clean up persistent storage when a config entry is removed."""
    _LOGGER.debug("Removing entry: %s", entry.entry_id)
    await price_cache_store(hass, entry.entry_id).async_remove()


class FrankEnergieComponent:  # pylint: disable=too-few-public-methods
    """Core setup handler for the Frank Energie component."""

    def __init__(self, hass: HomeAssistant, entry: FrankEnergieConfigEntry) -> None:
        """Initialize the Frank Energie component."""
        self.hass = hass
        self.entry = entry

    async def _schedule_aligned_updates(
        self,
        price_coordinator: FrankEnergiePriceCoordinator,
    ) -> None:
        """Schedule coordinator updates on aligned boundaries and handle midnight local rollover."""

        async def _async_aligned_refresh(
            _: datetime,
        ) -> None:
            # Check for midnight local TIMEZONE_AMSTERDAM rollover
            now_local = dt_util.now(ZoneInfo(TIMEZONE_AMSTERDAM))
            if now_local.hour == 0 and now_local.minute == 0:
                _LOGGER.info(
                    "Midnight local time: promoting tomorrow's prices to today"
                )
                price_coordinator.promote_tomorrow_prices()

            # Every aligned tick across the price release window explicitly
            # triggers a refresh, not just the exact 13:00 one. A single
            # exact-tick trigger is a single point of failure: if this
            # listener's registration (the last step of a slow startup) loses
            # the race against the exact 13:00:00 boundary, that tick is
            # gone for good and nothing else was scheduled to try again until
            # the next day. FrankEnergiePriceCoordinator's _async_update_data
            # already short-circuits both today's and tomorrow's fetch to a
            # no-op once genuinely up to date, so the extra calls on ticks
            # where nothing changed cost nothing.
            if (
                time(TOMORROW_PUBLICATION_HOUR_LOCAL, 0)
                <= now_local.time()
                < time(15, 0)
            ):
                # Debug, not info: this now fires up to 8x/day, and
                # _refresh_tomorrow_cache already logs its own outcome
                # (fetched vs. already cached, skip) at the appropriate level.
                _LOGGER.debug(
                    "Price release window local time: explicitly triggering price fetch"
                )
                await price_coordinator.async_request_refresh()
            else:
                # Trigger sensor state updates using cached data
                price_coordinator.async_update_listeners()

            # Skip non-hour ticks dynamically when resolution is hourly
            if price_coordinator.resolution != "PT15M" and now_local.minute != 0:
                return

        unsub = async_track_utc_time_change(
            self.hass,
            _async_aligned_refresh,
            minute=[0, 15, 30, 45],
            second=0,
        )

        self.entry.async_on_unload(unsub)

    async def async_prepare(self) -> FrankEnergieEntryData:
        """Create site-specific coordinators before publishing the entry runtime."""
        _LOGGER.debug("Setting up Frank Energie component")

        # For backwards compatibility, update the unique ID
        self._update_unique_id()

        # Clean up the obsolete "Frank Energie - Battery Sessions" umbrella device
        # from the registry; session sensors are grouped under their individual
        # battery devices now. The "Batteries" and "Chargers" service devices are
        # NOT obsolete - they carry the aggregate sensors and are the parent the
        # per-battery / per-charger child devices link to via `via_device_id`.
        device_registry = dr.async_get(self.hass)
        identifier = (DOMAIN, f"{self.entry.entry_id}_{SERVICE_NAME_BATTERY_SESSIONS}")
        if _HAS_DEVICE_BY_IDENTIFIER:
            device = device_registry.async_get_device_by_identifier(
                identifier, config_entry_id=self.entry.entry_id
            )
        else:
            # Older HA Core: fall back to the deprecated lookup so setup
            # doesn't crash.
            device = device_registry.async_get_device(identifiers={identifier})

        if device:
            device_registry.async_remove_device(device.id)
            _LOGGER.debug("Removed obsolete Battery Sessions umbrella device")

        # Create API and Coordinators
        _LOGGER.debug("Creating Frank Energie API instance")
        clientsession = async_get_clientsession(self.hass)
        api = FrankEnergie(
            clientsession=clientsession,
            auth_token=self.entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=self.entry.data.get(CONF_TOKEN),
        )

        settings_coordinator = FrankEnergieSettingsCoordinator(
            self.hass, self.entry, api
        )
        price_coordinator = FrankEnergiePriceCoordinator(
            self.hass, self.entry, api, settings_coordinator
        )
        battery_coordinator = FrankEnergieBatteryCoordinator(
            self.hass, self.entry, api, settings_coordinator
        )
        charger_coordinator = FrankEnergieChargerCoordinator(
            self.hass, self.entry, api, settings_coordinator
        )
        pv_coordinator = FrankEnergiePVCoordinator(
            self.hass, self.entry, api, settings_coordinator
        )
        vehicle_coordinator = FrankEnergieVehicleCoordinator(
            self.hass, self.entry, api, settings_coordinator
        )
        statistics_coordinator = FrankEnergieStatisticsCoordinator(
            self.hass, self.entry, api, settings_coordinator
        )

        # Awaiting the site reference selection using settings coordinator
        await self._select_site_reference(settings_coordinator)

        return FrankEnergieEntryData(
            coordinator=price_coordinator,
            settings_coordinator=settings_coordinator,
            price_coordinator=price_coordinator,
            battery_coordinator=battery_coordinator,
            charger_coordinator=charger_coordinator,
            pv_coordinator=pv_coordinator,
            vehicle_coordinator=vehicle_coordinator,
            statistics_coordinator=statistics_coordinator,
        )

    async def async_setup(self) -> None:
        """Refresh all resources, register platforms and schedule aligned updates."""
        _LOGGER.debug("Performing initial refresh for sub-coordinators")
        await self._async_refresh_coordinators()
        await self._async_forward_entry_setups()
        await self._schedule_aligned_updates(self.entry.runtime_data.price_coordinator)

    async def _async_refresh_coordinators(self) -> None:
        """Refresh settings first and finish or cancel every dependent refresh."""
        runtime = self.entry.runtime_data
        await runtime.settings_coordinator.async_config_entry_first_refresh()
        coordinators = (
            runtime.price_coordinator,
            runtime.statistics_coordinator,
            runtime.battery_coordinator,
            runtime.charger_coordinator,
            runtime.pv_coordinator,
            runtime.vehicle_coordinator,
        )
        tasks = [
            self.hass.async_create_task(
                coordinator.async_config_entry_first_refresh(),
                f"{DOMAIN} initial refresh {coordinator.name}",
            )
            for coordinator in coordinators
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            # gather propagates the first error without cancelling siblings.
            # Drain them before HA tears down coordinators or retries setup.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

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
                    "Failed to generate a delivery site title for entry %s",
                    self.entry.entry_id,
                )
                return

            _LOGGER.debug("Selected delivery site for entry %s", self.entry.entry_id)
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
                "Multiple delivery sites found; defaulting to the first one. "
                "Create an issue on github if you need support for multiple sites."
            )
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

        _LOGGER.debug("Generated delivery site title for entry %s", self.entry.entry_id)
        return reference, title

    async def _async_forward_entry_setups(self) -> None:
        """Forward entry setups to appropriate platforms.

        The sensor platform is set up first and awaited before the dependent
        platforms (binary_sensor, button, switch, datetime) run. This ensures
        all parent service devices (Frank Energie - Batteries, Chargers, etc.)
        are registered in the device registry before child devices attempt to
        link to them via via_device_id.
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
        except BaseException:
            try:
                if not await self.hass.config_entries.async_unload_platforms(
                    self.entry, PLATFORMS
                ):
                    _LOGGER.error("Could not unload all platforms after setup failed")
            except Exception as err:
                log_api_exception(_LOGGER, "Failed to roll back platform setup", err)
            raise
