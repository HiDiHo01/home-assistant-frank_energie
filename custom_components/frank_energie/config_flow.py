"""Config flow for Frank Energie integration."""
# config_flow.py
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

from .const import COMPONENT_TITLE, CONF_SITE, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_handle_auth_failure(hass: HomeAssistant, entry: ConfigEntry):
    """Handle an authentication failure by triggering reauthentication."""
    hass.config_entries.async_start_reauth(entry.entry_id)


@config_entries.HANDLERS.register(DOMAIN)
class ConfigFlow(config_entries.ConfigFlow):
    """Handle the config flow for Frank Energie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._errors: dict[str, str] = {}
        self._reauth_entry: Optional[config_entries.ConfigEntry] = None
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
            api = FrankEnergie(
                auth_token=self.sign_in_data.get(CONF_ACCESS_TOKEN, None),
                refresh_token=self.sign_in_data.get(CONF_TOKEN, None),
            )
            # me = await api.me()
            user_sites = await api.UserSites()
            all_delivery_sites = [
                site for site in user_sites.deliverySites if hasattr(site, "status")
            ]

            # filter out all sites that are not in delivery
            in_delivery_sites = [site for site in all_delivery_sites if site.status == "IN_DELIVERY"]

            if not in_delivery_sites:
                raise Exception("No suitable sites found for this account")

            number_of_sites = len(in_delivery_sites)

            if number_of_sites > 0:
                first_site = in_delivery_sites[0]

            if number_of_sites == 1:
                # for backward compatibility (do nothing)
                # Check if entry with CONF_USERNAME exists, then abort
                if CONF_USERNAME in user_input:
                    await self.async_set_unique_id(user_input[CONF_USERNAME])
                    self._abort_if_unique_id_configured()

                # Create entry with unique_id as me.deliverySites[0].reference
                self.sign_in_data[CONF_SITE] = first_site.reference
                self.sign_in_data[CONF_USERNAME] = self.create_title(first_site)
                return await self._async_create_entry(self.sign_in_data)

            # Prepare site options for selection
            site_options = [{"value": site.reference, "label": self.create_title(site)} for site in me.deliverySites]
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
            return self.async_show_form(
                step_id="login",
                data_schema=data_schema,
                errors={"base": "invalid_auth"},
            )
        except ConnectionException:
            return self.async_show_form(
                step_id="login",
                data_schema=data_schema,
                errors={"base": "connection_error"},
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
        if not user_input.get(CONF_USERNAME, "").strip():
            errors[CONF_USERNAME] = "Username is required."
        if not user_input.get(CONF_PASSWORD, "").strip():
            errors[CONF_PASSWORD] = "Password is required."
        return errors

    async def async_step_reconfigure(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        """Handle the reconfiguration step."""
        errors = {}

        if user_input:
            entry_id = self.context.get("entry_id")
            if entry_id:
                entry = self.hass.config_entries.async_get_entry(entry_id)
                username = entry.data.get(CONF_USERNAME, "")

                # Validate the provided credentials
                errors = self._validate_user_input(user_input)
                if not errors:
                    # Update the existing entry with new credentials
                    self.hass.config_entries.async_update_entry(
                        entry, data=user_input
                    )
                    _LOGGER.info("Reconfiguration successful for user: %s", username)
                    await self.async_set_unique_id(username)
                    return self.async_create_entry(
                        title=COMPONENT_TITLE,
                        data=user_input
                    )
                errors["base"] = "invalid_auth"

        # If user_input is None or validation failed
        entry_id = self.context.get("entry_id")
        if entry_id:
            entry = self.hass.config_entries.async_get_entry(entry_id)
            username = entry.data.get(CONF_USERNAME, "")
        data_schema = vol.Schema({
            vol.Required(CONF_USERNAME, default=username): str,
            vol.Required(CONF_PASSWORD): str
        })

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Handle configuration by re-auth."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_login()

    async def _async_create_entry(self, data: dict[str, Any]) -> FlowResult:
        """Create a configuration entry."""
        unique_id = data.get(CONF_USERNAME, "frank_energie")
        if data.get(CONF_SITE, None):
            _LOGGER.debug("CONF_SITE %s", CONF_SITE)
            _LOGGER.debug("data CONF_SITE %s", data[CONF_SITE])
            # unique_id = data[CONF_SITE] + data[CONF_USERNAME]
            unique_id = f"{data[CONF_SITE]}_{data[CONF_USERNAME]}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=data.get(CONF_USERNAME, "Frank Energie"), data=data
        )

    @staticmethod
    def create_title(site) -> str:
        """Create a formatted title from the site's address."""
        title = f"{site.address.street} {site.address.houseNumber}"
        if site.address.houseNumberAddition is not None:
            title += f" {site.address.houseNumberAddition}"
        return title

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
            }
        )

        return self.async_show_form(
            step_id="login",
            data_schema=data_schema,
            errors=errors,
        )

    async def _authenticate(self, user_input: dict[str, Any]) -> Optional[Authentication]:
        """Authenticate with Frank Energie API."""
        async with FrankEnergie() as api:
            try:
                return await api.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except AuthException as ex:
                _LOGGER.exception("Error during login", exc_info=ex)
                return None

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
        else:
            return await self.async_step_site(self.sign_in_data)

    async def _handle_authentication_failure(self) -> FlowResult:
        """Handle authentication failure."""
        return await self.async_step_login(errors={"base": "invalid_auth"})

    @staticmethod
    @callback
    def _async_get_options_flow(config_entry: ConfigEntry) -> Optional[config_entries.OptionsFlow]:
        """Get options flow handler."""
        _LOGGER.debug("config_entry for %s", config_entry)
        return FrankEnergieOptionsFlowHandler(config_entry)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Get options flow handler only if a site is selected."""
        if CONF_SITE in config_entry.data:
            _LOGGER.debug("A site is selected, providing options flow.")
            return FrankEnergieOptionsFlowHandler(config_entry)

        _LOGGER.debug("No site selected, no options flow available.")
        return NoOptionsAvailableFlowHandler()
        # raise HomeAssistantError("No login needed for public prices, use ADD ITEM")

    @staticmethod
    def _validate_login_input(user_input: dict[str, Any]) -> dict[str, str]:
        """Validate user input for login."""
        errors = {}
        if user_input[CONF_USERNAME].strip() == "":
            errors[CONF_USERNAME] = "Username is required."
        if user_input[CONF_PASSWORD].strip() == "":
            errors[CONF_PASSWORD] = "Password is required."
        return errors


class FrankEnergieOptionsFlowHandler(config_entries.OptionsFlow):
    """Frank Energie config flow options handler."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize Frank Energie options flow."""
        self.config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(self, user_input: Optional[dict[str, Any]] = None) -> FlowResult:
        # async def async_step_init(self, user_input=None):
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

        # username = (
        #     self.config_entry.data.get(CONF_USERNAME)
        #     if self.config_entry
        #     else None
        # )
        # username = self.config_entry.data.get(CONF_USERNAME, "")
        username = self.config_entry.unique_id

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
            title=self.config_entry.data.get(CONF_USERNAME, "Frank Energie"),
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
