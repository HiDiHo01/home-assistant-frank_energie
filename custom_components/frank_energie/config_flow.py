"""Config flow for Frank Energie integration."""
# config_flow.py
import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_AUTHENTICATION,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from python_frank_energie import Authentication, FrankEnergie
from python_frank_energie.exceptions import AuthException, ConnectionException

from .const import CONF_SITE, DOMAIN

_LOGGER = logging.getLogger(__name__)
VERSION = "2025.7.19"


async def async_handle_auth_failure(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle an authentication failure by triggering reauthentication.

    This function attempts to start a reauthentication flow for the given config entry.
    If the entry is not found or reauth initiation fails, it logs the error.
    """
    try:
        current_entry = next(
            (e for e in hass.config_entries.async_entries(entry.domain) if e.entry_id == entry.entry_id),
            None
        )
        if not current_entry:
            _LOGGER.warning("Config entry %s not found for reauthentication", entry.entry_id)
            return

        _LOGGER.info("Authentication failure detected, triggering reauth for %s", entry.title)
        await hass.config_entries.async_start_reauth(entry.entry_id)
    except Exception as err:
        _LOGGER.error("Failed to initiate reauthentication for %s: %s", entry.entry_id, err)


@config_entries.HANDLERS.register(DOMAIN)
class ConfigFlow(config_entries.ConfigFlow):
    """Handle the config flow for Frank Energie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._errors: dict[str, str] = {}
        # self._reauth_entry: Optional[config_entries.ConfigEntry] = None
        self._reauth_entry: Optional[ConfigEntry] = None
        self.sign_in_data: dict[str, Any] = {}

    async def async_step_login(
        self,
        user_input: Optional[dict[str, Any]] = None,
        errors: Optional[dict[str, str]] = None
    ) -> FlowResult:
        """Handle login with credentials by user."""
        if not user_input:
            return self._show_login_form()

        errors = self._validate_login_input(user_input)

        if errors:
            return self._show_login_form(errors=errors)

        auth = await self._authenticate(user_input)
        if auth:
            return await self._handle_authentication_success(user_input, auth)
        return await self._handle_authentication_failure()

    async def async_step_site(
        self,
        user_input: Optional[dict[str, Any]] = None,
        errors: Optional[dict[str, str]] = None
    ) -> FlowResult:
        """Handle possible multi site accounts."""
        if user_input and user_input.get(CONF_SITE) is not None:
            self.sign_in_data[CONF_SITE] = user_input[CONF_SITE]
            return await self._async_create_entry(self.sign_in_data)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional("timeout", default=5): int
            }
        )

        try:
            async with FrankEnergie(
                auth_token=self.sign_in_data.get(CONF_ACCESS_TOKEN, None),
                refresh_token=self.sign_in_data.get(CONF_TOKEN, None),
            ) as api:
                user_sites = await api.UserSites()
                _LOGGER.debug("All user_sites: %s", user_sites)

                # Check if the user has any delivery sites
                if not user_sites or not user_sites.deliverySites:
                    _LOGGER.warning("No delivery sites found for this account")
                    raise NoDeliverySitesError("No delivery sites found for this account")

                # Log all available sites with their statuses for debugging
                _LOGGER.debug("Available delivery sites count: %d", len(user_sites.deliverySites))
                for i, site in enumerate(user_sites.deliverySites):
                    status = getattr(site, "status", "NO_STATUS")
                    _LOGGER.debug("Site %d: status=%s, site=%s", i, status, site)

                # Get all sites that have a status attribute
                all_delivery_sites = [
                    site for site in user_sites.deliverySites if hasattr(site, "status")
                ]

                # First try to filter for sites with status "IN_DELIVERY"
                in_delivery_sites = [site for site in all_delivery_sites if site.status == "IN_DELIVERY"]
                _LOGGER.debug("Sites with status IN_DELIVERY: %d", len(in_delivery_sites))

                # If no "IN_DELIVERY" sites found, try other possible active statuses
                suitable_sites = in_delivery_sites
                if not suitable_sites:
                    # Try other potentially valid statuses
                    other_valid_statuses = ["ACTIVE", "CONNECTED", "ENABLED", "OPERATIONAL"]
                    for status in other_valid_statuses:
                        sites_with_status = [site for site in all_delivery_sites if site.status == status]
                        if sites_with_status:
                            _LOGGER.info("No IN_DELIVERY sites found, using sites with status %s: %d", status, len(sites_with_status))
                            suitable_sites = sites_with_status
                            break

                # If still no suitable sites found, use any sites that have an address (likely to be valid)
                if not suitable_sites:
                    sites_with_address = [site for site in all_delivery_sites if hasattr(site, "address") and site.address]
                    if sites_with_address:
                        _LOGGER.info("Found %d site(s) ready for setup (IN_DELIVERY or similar status)", len(sites_with_address))
                        suitable_sites = sites_with_address

                # Last resort: use all available sites if they have the required attributes for creating a title
                if not suitable_sites:
                    sites_with_required_attrs = []
                    for site in user_sites.deliverySites:
                        try:
                            # Test if we can create a title (this will fail if required attributes are missing)
                            self.create_title(site)
                            sites_with_required_attrs.append(site)
                        except Exception as e:
                            _LOGGER.debug("Site cannot be used (missing required attributes): %s", e)
                            continue

                    if sites_with_required_attrs:
                        _LOGGER.warning("Using all available sites with required attributes: %d", len(sites_with_required_attrs))
                        suitable_sites = sites_with_required_attrs

                if not suitable_sites:
                    # Provide detailed error message
                    available_statuses = [getattr(site, "status", "NO_STATUS") for site in user_sites.deliverySites]
                    error_msg = f"No suitable sites found. Available sites: {len(user_sites.deliverySites)}, Statuses: {set(available_statuses)}"
                    _LOGGER.error(error_msg)
                    raise NoSitesFoundError(error_msg)

                number_of_sites = len(suitable_sites)
                _LOGGER.info("Found %d suitable sites for selection", number_of_sites)

                first_site = suitable_sites[0]  # We know suitable_sites is not empty at this point

                if number_of_sites == 1:
                    # for backward compatibility (do nothing)
                    # Check if entry with CONF_USERNAME exists, then abort
                    # if CONF_USERNAME in user_input:
                    if user_input and CONF_USERNAME in user_input:
                        await self.async_set_unique_id(user_input[CONF_USERNAME])
                        self._abort_if_unique_id_configured()

                    # Create entry with unique_id as user_sites.deliverySites[0].reference
                    self.sign_in_data[CONF_SITE] = first_site.reference
                    # self.sign_in_data[CONF_USERNAME] = self.create_title(first_site)
                    self.sign_in_data["site_title"] = self.create_title(first_site)
                    # return await self._async_create_entry(self.sign_in_data)

                # Prepare site options for selection
                site_options = [
                    {
                        "value": site.reference,
                        "label": self.create_title(site)
                    }
                    for site in suitable_sites
                ]

                default_site = first_site.reference

                options = {
                    vol.Required(CONF_SITE, default=default_site): SelectSelector(
                        SelectSelectorConfig(
                            options=site_options,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }

                return self.async_show_form(
                    step_id="site", data_schema=vol.Schema(options), errors=errors
                )

        except AuthException:
            _LOGGER.error("Authentication failed during site fetch")
            return self.async_show_form(
                step_id="login",
                data_schema=data_schema,
                errors={"base": "invalid_auth"},
            )
        except ConnectionException:
            _LOGGER.error("Connection error during site fetch")
            return self.async_show_form(
                step_id="login",
                data_schema=data_schema,
                errors={"base": "connection_error"},
            )
        except NoDeliverySitesError as err:
            _LOGGER.error("No delivery sites found: %s", err)
            errors = {"base": "no_delivery_sites"}
            return self.async_show_form(
                step_id="site",
                data_schema=self._site_error_schema(),
                errors=errors,
            )
        except NoSitesFoundError as err:
            _LOGGER.error("No suitable sites found: %s", err)
            errors = {"base": "no_suitable_sites"}
            return self.async_show_form(
                step_id="site",
                data_schema=self._site_error_schema(),
                errors=errors,
            )
        except Exception as err:
            _LOGGER.exception("Failed to retrieve delivery sites: %s", err)
            errors = {"base": "site_retrieval_failed"}
            return self.async_show_form(
                step_id="site",
                data_schema=self._site_error_schema(),
                errors=errors,
            )

    def _site_error_schema(self) -> vol.Schema:
        """Return schema used when site retrieval fails."""
        return vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional("timeout", default=5): int,
            }
        )

    async def async_step_user(
        self,
        user_input: Optional[dict[str, Any]] = None
    ) -> FlowResult:
        """Handle a flow initiated by the user."""
        if not user_input:
            data_schema = vol.Schema(
                {
                    vol.Required(CONF_AUTHENTICATION): bool,
                }
            )
            return self.async_show_form(step_id="user", data_schema=data_schema)

        if user_input[CONF_AUTHENTICATION]:
            return await self.async_step_login()

        return await self._async_create_entry({})

    def _validate_user_input(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Validate user input for reconfiguration or login."""
        errors = {}

        username = user_input.get(CONF_USERNAME, "").strip()
        password = user_input.get(CONF_PASSWORD, "").strip()

        if not username:
            errors[CONF_USERNAME] = "Username is required."
        if not password:
            errors[CONF_PASSWORD] = "Password is required."

        return errors

    async def async_step_reconfigure(
        self,
        user_input: Optional[dict[str, Any]] = None,
        errors: Optional[dict[str, str]] = None
    ) -> FlowResult:
        """Handle the reconfiguration step."""
        if not user_input:
            return self._show_login_form()

        errors = self._validate_login_input(user_input)

        if errors:
            return self._show_login_form(errors=errors)

        auth = await self._authenticate(user_input)
        if auth:
            return await self._handle_authentication_success(user_input, auth)
        return await self._handle_authentication_failure()

    async def _get_available_sites(self, username: str) -> list[str]:
        """Retrieve available delivery sites for the user."""
        try:
            # Initialize the FrankEnergie API with the access and refresh tokens
            async with FrankEnergie(
                auth_token=self.sign_in_data.get(CONF_ACCESS_TOKEN, None),
                refresh_token=self.sign_in_data.get(CONF_TOKEN, None),
            ) as api:
                # Fetch user sites with retry using the FrankEnergie API
                for _ in range(2):
                    try:
                        user_sites = await api.UserSites()
                        break
                    except ConnectionException:
                        await asyncio.sleep(1)
                else:
                    raise ConnectionException("Failed to connect to Frank Energie API after retries")

                _LOGGER.debug("All user_sites: %s", user_sites)

                # Check if the user has any delivery sites
                if not user_sites or not user_sites.deliverySites:
                    raise NoDeliverySitesError("No delivery sites found for this account")

                # Filter sites that have the status attribute
                all_delivery_sites = [
                    site for site in user_sites.deliverySites if hasattr(site, "status")
                ]

                # First try sites with status "IN_DELIVERY"
                in_delivery_sites = [site for site in all_delivery_sites if site.status == "IN_DELIVERY"]
                _LOGGER.debug("Sites with status IN_DELIVERY: %d", len(in_delivery_sites))

                # If no "IN_DELIVERY" sites, try other potentially valid statuses
                suitable_sites = in_delivery_sites
                if not suitable_sites:
                    other_valid_statuses = ["ACTIVE", "CONNECTED", "ENABLED", "OPERATIONAL"]
                    for status in other_valid_statuses:
                        sites_with_status = [site for site in all_delivery_sites if site.status == status]
                        if sites_with_status:
                            suitable_sites = sites_with_status
                            break

                # If still no suitable sites, use any sites with required attributes
                if not suitable_sites:
                    sites_with_address = [site for site in all_delivery_sites if hasattr(site, "address") and site.address]
                    if sites_with_address:
                        suitable_sites = sites_with_address

                # Raise an exception if no suitable sites are found
                if not suitable_sites:
                    available_statuses = [getattr(site, "status", "NO_STATUS") for site in user_sites.deliverySites]
                    error_msg = f"No suitable sites found. Available sites: {len(user_sites.deliverySites)}, Statuses: {set(available_statuses)}"
                    raise NoSitesFoundError(error_msg)

                # Return a list of site names or identifiers from the available sites
                return [site.name for site in suitable_sites]

        except NoDeliverySitesError as e:
            _LOGGER.error("No delivery sites found for user %s: %s", username, str(e))
            return []  # Return an empty list if no delivery sites are found
        except NoSitesFoundError as e:
            _LOGGER.error("No sites found error for user %s: %s", username, str(e))
            return []  # Return an empty list if no suitable sites are found
        except Exception as e:
            _LOGGER.error("Error fetching sites for user %s: %s", username, str(e))
            return []  # Return an empty list in case of error

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Handle configuration by re-auth."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None:
            _LOGGER.warning("Reauthentication entry with ID %s not found, aborting reauth flow.",
                            self.context["entry_id"])
            raise ValueError("Reauthentication entry not found. Cannot continue reauthentication flow.")
        return await self.async_step_login()

    async def _async_create_entry(self, data: dict[str, Any]) -> FlowResult:
        """Create a configuration entry."""
        unique_id = data.get(CONF_USERNAME, "frank_energie")
        if data.get(CONF_SITE, None):
            _LOGGER.debug("CONF_SITE: %s", CONF_SITE)
            _LOGGER.debug("data CONF_SITE: %s", data[CONF_SITE])
            _LOGGER.debug("CONF_USERNAME: %s", CONF_USERNAME)
            _LOGGER.debug("data CONF_USERNAME: %s", data[CONF_USERNAME])
            # unique_id = data[CONF_SITE] + data[CONF_USERNAME]
            # unique_id = f"{data[CONF_SITE]}_{data[CONF_USERNAME]}"
            unique_id = f"{data[CONF_SITE] or data.get(CONF_USERNAME, "frank_energie")}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=data.get(CONF_USERNAME, "Frank Energie"),
            data=data
        )

    @staticmethod
    def create_title(site: Any) -> str:
        """Create a formatted title from the site's address."""
        try:
            street = site.address.street
            number = site.address.houseNumber
            addition = site.address.houseNumberAddition

            if not street or not number:
                raise ValueError("Missing required address fields")

            title = f"{street} {number}"
            if addition:
                title += f" {addition}"

            return title
        except AttributeError as err:
            _LOGGER.error("Invalid site structure: %s", err)
            return "Onbekend adres"
        except Exception as err:
            _LOGGER.exception("Unexpected error creating title: %s", err)
            return "Onbekend adres"

    def _login_schema(self, user_input: Optional[dict[str, Any]] = None) -> vol.Schema:
        return vol.Schema({
            vol.Required(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "") if user_input else ""): str,
            vol.Required(CONF_PASSWORD): str,
        })

    def _show_login_form(self, errors: Optional[dict[str, str]] = None) -> FlowResult:
        username = (
            self._reauth_entry.data[CONF_USERNAME]
            if self._reauth_entry
            else None
        )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=username): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional("debug_mode", default=False): bool
            }
        )

        return self.async_show_form(
            step_id="login",
            data_schema=data_schema,
            errors=errors,
        )

    async def _authenticate(self, user_input: dict[str, Any]) -> Authentication:
        """Authenticate with Frank Energie API.

        Raises:
            AuthException: If authentication fails due to invalid credentials.
            ConnectionException: If a network-related error occurs.
        """
        async with FrankEnergie() as api:
            try:
                return await api.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except AuthException as ex:
                _LOGGER.exception("Authentication failed for user %s", user_input[CONF_USERNAME], exc_info=ex)
                raise
            except ConnectionException as ex:
                _LOGGER.error("Connection error during authentication for user %s",
                              user_input[CONF_USERNAME], exc_info=ex)
                raise

    async def _handle_authentication_success(
        self,
        user_input: dict[str, Any],
        auth: Authentication
    ) -> FlowResult:
        """Handle successful authentication."""
        self.sign_in_data = {
            CONF_USERNAME: user_input[CONF_USERNAME],
            CONF_ACCESS_TOKEN: auth.authToken,
            CONF_TOKEN: auth.refreshToken
        }
        if self._reauth_entry:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                data=self.sign_in_data
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            )
            return self.async_abort(reason="reauth_successful")
        return await self.async_step_site(self.sign_in_data)

    async def _handle_authentication_failure(self) -> FlowResult:
        """Handle authentication failure."""
        return await self.async_step_login(errors={"base": "invalid_auth"})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> Optional[config_entries.OptionsFlow]:
        """Get options flow handler only if a site is selected."""
        _LOGGER.debug("config_entry for %s", config_entry)
        entry_data = config_entry.data
        if CONF_SITE in entry_data:
            _LOGGER.debug("Site %s is selected, providing options flow: %s", entry_data[CONF_SITE], entry_data)
            return FrankEnergieOptionsFlowHandler(entry_data)

        _LOGGER.debug("No site selected, no options flow available.")
        return NoOptionsAvailableFlowHandler()

    @staticmethod
    def _validate_login_input(user_input: dict[str, Any]) -> dict[str, str]:
        """Validate user input for login."""
        errors: dict[str, str] = {}

        # Check if username is provided
        username = user_input.get(CONF_USERNAME, "").strip()
        if not username:
            errors[CONF_USERNAME] = "Username is required and cannot be empty."

        # Check if password is provided
        password = user_input.get(CONF_PASSWORD, "").strip()
        if not password:
            errors[CONF_PASSWORD] = "Password is required and cannot be empty."

        return errors


class NoDeliverySitesError(Exception):
    """Raised when no delivery sites are found for the user."""


class NoSitesFoundError(Exception):
    """Raised when no suitable sites are found for the user."""


class FrankEnergieOptionsFlowHandler(config_entries.OptionsFlow):
    """Frank Energie config flow options handler."""

    def __init__(self, entry_data: dict) -> None:
        """Initialize Frank Energie options flow."""
        self.entry_data = entry_data
        self.options = dict(entry_data)  # Gebruik entry_data in plaats van config_entry.options

    async def async_step_init(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        """Manage the options."""
        return await self.async_step_user()

    async def async_step_user(
        self,
        user_input: Optional[dict[str, Any]] = None,
        errors: Optional[dict[str, str]] = None
    ) -> FlowResult:
        """Handle a flow initialized by the user."""
        if user_input is not None:
            self.options.update(user_input)
            return await self._update_options()

        username = self.entry_data.get("username", "")

        data_schema = vol.Schema({
            vol.Required(CONF_USERNAME, default=username): str,
            vol.Required(CONF_PASSWORD): str
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors
        )

    async def _update_options(self) -> FlowResult:
        """Update config entry options."""
        return self.async_create_entry(
            title=self.entry_data.get(CONF_USERNAME, "Frank Energie"),
            data=self.options
        )


class NoOptionsAvailableFlowHandler(config_entries.OptionsFlow):
    """Handler for displaying a message when no options are available."""

    async def async_step_init(self, user_input=None):
        """Display a message that no options are available."""
        if user_input is not None:
            # You can handle the user action here, such as closing the form or navigating back
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({}),
            errors={"base": "You do not have to login for this entry."},
        )
