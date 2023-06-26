"""Config flow for Frank Energie integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_AUTHENTICATION,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.data_entry_flow import FlowResult
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import AuthException

from .const import DOMAIN
from .api import FrankEnergieAPI

_LOGGER = logging.getLogger(__name__)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Frank Energie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        #self._reauth_entry = None
        self._reauth_entry: Optional[config_entries.ConfigEntry] = None

    async def async_step_login(
        self, user_input: Optional[dict[str, Any]] = None, errors=None
    ) -> FlowResult:
        """Handle login with credentials by user."""
        if not user_input:
            username = (
                self._reauth_entry.data[CONF_USERNAME]
                if self._reauth_entry
                else None
            )

            data_schema = vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=username): vol.Coerce(str),
                    vol.Required(CONF_PASSWORD): vol.Coerce(str),
                }
            )

            return self.async_show_form(
                step_id="login",
                data_schema=data_schema,
                errors=errors,
            )

        errors = self._validate_login_input(user_input)

        if errors:
            return self._show_login_form(errors=errors)

        # Create an instance of the FrankEnergieAPI class
        api = FrankEnergieAPI()

        try:
            # Authenticate with Frank Energie API
            await api.authenticate(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
        except AuthException:
            _LOGGER.exception("Error during login")
            return await self.async_step_login(errors={"base": "invalid_auth"})

        # Check if a refresh token is available
        if self._reauth_entry and CONF_TOKEN in self._reauth_entry.data:
            # Use the refresh token to get a new auth token
            async with FrankEnergie() as api:
                try:
                    auth = await api.refresh_token(
                        self._reauth_entry.data[CONF_TOKEN]
                    )
                except AuthException as ex:
                    _LOGGER.exception("Error during login", exc_info=ex)
                    return await self.async_step_login(errors={"base": "invalid_auth"})
        else:
            # Perform the initial login
            async with FrankEnergie() as api:
                try:
                    auth = await api.login(
                        user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                    )
                except AuthException as ex:
                    _LOGGER.exception("Error during login", exc_info=ex)
                    return await self.async_step_login(errors={"base": "invalid_auth"})

        data = {
            CONF_USERNAME: user_input[CONF_USERNAME],
            CONF_ACCESS_TOKEN: auth.authToken,
            CONF_TOKEN: auth.refreshToken,
        }

        if self._reauth_entry:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                data=data,
            )

            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            )

            return self.async_abort(reason="reauth_successful")

        await self.async_set_unique_id(user_input[CONF_USERNAME])
        self._abort_if_unique_id_configured()

        return await self._async_create_entry(data)

    async def async_step_user(
        self, user_input=None, errors=None
    ) -> FlowResult:
        """Handle a flow initiated by the user."""
        #if not user_input:
        if user_input is None:
            data_schema = vol.Schema(
                {
                    vol.Required(CONF_AUTHENTICATION, default=True): bool,
                }
            )

            return self.async_show_form(
                step_id="user",
                data_schema=data_schema,
                errors=errors,
            )

        if user_input[CONF_AUTHENTICATION]:
            return await self.async_step_login()

        data = {}

        return await self._async_create_entry(data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """Handle configuration by re-auth."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_login()

    async def _async_create_entry(self, data):
        await self.async_set_unique_id(data.get(CONF_USERNAME, "frank_energie"))
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=data.get(CONF_USERNAME, "Frank Energie"), data=data
        )

    def _show_user_form(self, errors: Optional[dict[str, str]] = None) -> FlowResult:
        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_AUTHENTICATION, default=True): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

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

    @staticmethod
    def _validate_login_input(user_input: dict[str, Any]) -> dict[str, str]:
        errors = {}
        if user_input[CONF_USERNAME].strip() == "":
            errors[CONF_USERNAME] = "Username is required."
        if user_input[CONF_PASSWORD].strip() == "":
            errors[CONF_PASSWORD] = "Password is required."
        return errors
