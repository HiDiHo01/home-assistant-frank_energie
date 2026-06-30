"""Config flow for Frank Energie integration."""

# config_flow.py
import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_AUTHENTICATION,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
from python_frank_energie import Authentication, FrankEnergie
from python_frank_energie.exceptions import AuthException, ConnectionException

from .const import (
    CONF_SITE,
    DOMAIN,
    CONF_INTERVAL_SETTINGS,
    CONF_INTERVAL_STATISTICS,
    CONF_INTERVAL_BATTERIES,
    CONF_INTERVAL_BATTERY_SESSIONS,
    CONF_INTERVAL_CHARGERS,
    CONF_INTERVAL_VEHICLES,
    CONF_INTERVAL_PV,
    DEFAULT_INTERVAL_SETTINGS,
    DEFAULT_INTERVAL_PRICES,
    DEFAULT_INTERVAL_STATISTICS,
    DEFAULT_INTERVAL_BATTERIES,
    DEFAULT_INTERVAL_BATTERY_SESSIONS,
    DEFAULT_INTERVAL_CHARGERS,
    DEFAULT_INTERVAL_VEHICLES,
    DEFAULT_INTERVAL_PV,
)
from .helpers import decrypt_password, encrypt_password

_LOGGER = logging.getLogger(__name__)
INT_VERSION = "2026.3.22"

VERSION = 2
MINOR_VERSION = 1


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry format."""

    if entry.version < 2:
        new_options = dict(entry.options)

        if "resolution" not in new_options:
            new_options["resolution"] = "PT15M"

        hass.config_entries.async_update_entry(
            entry,
            options=new_options,
            version=2,
        )

    return True


async def async_handle_auth_failure(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle an authentication failure by triggering reauthentication.

    This function attempts to start a reauthentication flow for the given config entry.
    If the entry is not found or reauth initiation fails, it logs the error.
    """
    try:
        current_entry = next(
            (
                e
                for e in hass.config_entries.async_entries(entry.domain)
                if e.entry_id == entry.entry_id
            ),
            None,
        )
        if not current_entry:
            _LOGGER.warning(
                "Config entry %s not found for reauthentication", entry.entry_id
            )
            return

        _LOGGER.info(
            "Authentication failure detected, triggering reauth for %s", entry.title
        )
        await hass.config_entries.async_start_reauth(entry.entry_id)
    except Exception as err:
        _LOGGER.error(
            "Failed to initiate reauthentication for %s: %s", entry.entry_id, err
        )


@config_entries.HANDLERS.register(DOMAIN)
class ConfigFlow(config_entries.ConfigFlow):
    """Handle the config flow for Frank Energie."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: Optional[ConfigEntry] = None
        self.sign_in_data: dict[str, Any] = {}

    async def async_step_login(
        self,
        user_input: Optional[dict[str, Any]] = None,
        errors: Optional[dict[str, str]] = None,
    ) -> ConfigFlowResult:
        """Handle the login step with credentials and show friendly errors."""
        if not user_input:
            # Show login form for first time
            return self._show_login_form(errors=errors)

        # Validate user input locally first
        input_errors = self._validate_login_input(user_input)
        if input_errors:
            return self._show_login_form(errors=input_errors)

        try:
            # Attempt authentication with the API
            auth = await self._authenticate(user_input)
            # Successful login
            return await self._handle_authentication_success(user_input, auth)

        except AuthException:
            # Wrong username/password
            return await self._handle_authentication_failure(reason="invalid_auth")
        except ConnectionException:
            # Network/API issues
            return await self._handle_authentication_failure(reason="connection_error")
        except Exception as ex:
            # Catch-all for unexpected errors
            _LOGGER.exception(
                "Unexpected error during login for user %s: %s",
                user_input.get(CONF_USERNAME, "UNKNOWN"),
                ex,
            )
            return await self._handle_authentication_failure(reason="unknown_error")

    async def async_step_site(
        self,
        user_input: Optional[dict[str, Any]] = None,
        errors: Optional[dict[str, str]] = None,
    ) -> ConfigFlowResult:
        """Handle possible multi site accounts."""
        if user_input and user_input.get(CONF_SITE) is not None:
            self.sign_in_data[CONF_SITE] = user_input[CONF_SITE]
            site_titles = self.sign_in_data.get("site_titles", {})
            self.sign_in_data["site_title"] = site_titles.get(user_input[CONF_SITE])
            return await self._async_create_entry(self.sign_in_data)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional("timeout", default=5): int,
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
                    raise NoDeliverySitesError(
                        "No delivery sites found for this account"
                    )

                # Log all available sites with their statuses for debugging
                _LOGGER.debug(
                    "Available delivery sites count: %d", len(user_sites.deliverySites)
                )
                for i, site in enumerate(user_sites.deliverySites):
                    status = getattr(site, "status", "NO_STATUS")
                    _LOGGER.debug("Site %d: status=%s, site=%s", i, status, site)

                # Get all sites that have a status attribute
                all_delivery_sites = [
                    site for site in user_sites.deliverySites if hasattr(site, "status")
                ]

                # First try to filter for sites with status "IN_DELIVERY"
                in_delivery_sites = [
                    site for site in all_delivery_sites if site.status == "IN_DELIVERY"
                ]
                _LOGGER.debug(
                    "Sites with status IN_DELIVERY: %d", len(in_delivery_sites)
                )

                # If no "IN_DELIVERY" sites found, try other possible active statuses
                suitable_sites = in_delivery_sites
                if not suitable_sites:
                    # Try other potentially valid statuses
                    other_valid_statuses = [
                        "ACTIVE",
                        "CONNECTED",
                        "ENABLED",
                        "OPERATIONAL",
                    ]
                    for status in other_valid_statuses:
                        sites_with_status = [
                            site for site in all_delivery_sites if site.status == status
                        ]
                        if sites_with_status:
                            _LOGGER.info(
                                "No IN_DELIVERY sites found, using sites with status %s: %d",
                                status,
                                len(sites_with_status),
                            )
                            suitable_sites = sites_with_status
                            break

                # If still no suitable sites found, use any sites that have an address (likely to be valid)
                if not suitable_sites:
                    sites_with_address = [
                        site
                        for site in all_delivery_sites
                        if hasattr(site, "address") and site.address
                    ]
                    if sites_with_address:
                        _LOGGER.info(
                            "Found %d site(s) ready for setup (IN_DELIVERY or similar status)",
                            len(sites_with_address),
                        )
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
                            _LOGGER.debug(
                                "Site cannot be used (missing required attributes): %s",
                                e,
                            )
                            continue

                    if sites_with_required_attrs:
                        _LOGGER.warning(
                            "Using all available sites with required attributes: %d",
                            len(sites_with_required_attrs),
                        )
                        suitable_sites = sites_with_required_attrs

                if not suitable_sites:
                    # Provide detailed error message
                    available_statuses = [
                        getattr(site, "status", "NO_STATUS")
                        for site in user_sites.deliverySites
                    ]
                    error_msg = f"No suitable sites found. Available sites: {len(user_sites.deliverySites)}, Statuses: {set(available_statuses)}"
                    _LOGGER.error(error_msg)
                    raise NoSitesFoundError(error_msg)

                number_of_sites = len(suitable_sites)
                _LOGGER.info("Found %d suitable sites for selection", number_of_sites)

                first_site = suitable_sites[
                    0
                ]  # We know suitable_sites is not empty at this point

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

                self.sign_in_data["site_titles"] = {
                    site.reference: self.create_title(site) for site in suitable_sites
                }

                # Prepare site options for selection
                site_options = [
                    {"value": site.reference, "label": self.create_title(site)}
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
        self, user_input: Optional[dict[str, Any]] = None
    ) -> ConfigFlowResult:
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

    async def async_step_reconfigure(
        self,
        user_input: Optional[dict[str, Any]] = None,
        errors: Optional[dict[str, str]] = None,
    ) -> ConfigFlowResult:
        """Handle the reconfiguration step."""
        if self._reauth_entry is None:
            self._reauth_entry = self.hass.config_entries.async_get_entry(
                self.context["entry_id"]
            )

        if not user_input:
            return self._show_login_form()

        errors = self._validate_login_input(user_input)

        if errors:
            return self._show_login_form(errors=errors)

        try:
            auth = await self._authenticate(user_input)
        except AuthException:
            return await self._handle_authentication_failure(reason="invalid_auth")
        except ConnectionException:
            return await self._handle_authentication_failure(reason="connection_error")
        except Exception:
            return await self._handle_authentication_failure(reason="unknown_error")

        return await self._handle_authentication_success(user_input, auth)

    async def _get_available_sites(self, username: str) -> list[dict[str, str]]:
        """Retrieve and normalize available delivery sites for the user."""
        try:
            user_sites = await self._fetch_user_sites_with_retry()

            if not user_sites or not user_sites.deliverySites:
                raise NoDeliverySitesError("No delivery sites found for this account")

            suitable_sites = self._filter_suitable_sites(user_sites.deliverySites)

            if not suitable_sites:
                available_statuses = [
                    getattr(site, "status", "NO_STATUS")
                    for site in user_sites.deliverySites
                ]
                raise NoSitesFoundError(
                    "No suitable sites found. Available: %d, statuses: %s"
                    % (len(user_sites.deliverySites), set(available_statuses))
                )

            return [
                {
                    "value": site.reference,
                    "label": self.create_title(site),
                }
                for site in suitable_sites
            ]

        except NoDeliverySitesError as err:
            _LOGGER.error(
                "No delivery sites found for user %s: %s",
                username,
                err,
            )
            return []

        except NoSitesFoundError as err:
            _LOGGER.error(
                "No suitable sites found for user %s: %s",
                username,
                err,
            )
            return []

        except Exception as err:  # noqa: BLE001
            _LOGGER.exception(
                "Unexpected error fetching sites for user %s: %s",
                username,
                err,
            )
            return []

    async def _fetch_user_sites_with_retry(self) -> object:
        """Fetch user sites with retry logic."""
        async with FrankEnergie(
            auth_token=self.sign_in_data.get(CONF_ACCESS_TOKEN),
            refresh_token=self.sign_in_data.get(CONF_TOKEN),
        ) as api:
            last_err: Exception | None = None

            for attempt in range(2):
                try:
                    return await api.UserSites()
                except ConnectionException as err:
                    last_err = err
                    _LOGGER.warning(
                        "UserSites fetch failed (attempt %d): %s",
                        attempt + 1,
                        err,
                    )
                    await asyncio.sleep(1)

            raise ConnectionException(
                "Failed to fetch user sites after retries"
            ) from last_err

    # not in use yet, but can be used to filter sites in async_step_site
    def _filter_suitable_sites(self, delivery_sites: list[Any]) -> list[Any]:
        """Filter sites for valid delivery or active status."""
        all_sites = [s for s in delivery_sites if hasattr(s, "status")]

        in_delivery = [s for s in all_sites if s.status == "IN_DELIVERY"]
        if in_delivery:
            return in_delivery

        for status in ["ACTIVE", "CONNECTED", "ENABLED", "OPERATIONAL"]:
            sites = [s for s in all_sites if s.status == status]
            if sites:
                return sites

        # fallback: sites with address
        sites_with_address = [
            s for s in all_sites if hasattr(s, "address") and s.address
        ]
        if sites_with_address:
            return sites_with_address

        # fallback: sites with required attributes
        valid_sites = []
        for site in delivery_sites:
            try:
                self.create_title(site)
                valid_sites.append(site)
            except Exception:
                continue
        return valid_sites

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle configuration by re-auth."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        if self._reauth_entry is None:
            _LOGGER.warning(
                "Reauthentication entry with ID %s not found, aborting reauth flow.",
                self.context["entry_id"],
            )
            raise ValueError(
                "Reauthentication entry not found. Cannot continue reauthentication flow."
            )
        return await self.async_step_login()

    async def _async_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create a configuration entry, or update the existing one during reconfigure."""
        if self.source == config_entries.SOURCE_RECONFIGURE:
            reconfigure_entry = self._get_reconfigure_entry()
            updated_data = {**reconfigure_entry.data, **data}
            return self.async_update_reload_and_abort(
                reconfigure_entry,
                data=updated_data,
            )

        # Construct options dictionary with credentials
        options = {
            CONF_USERNAME: data.get(CONF_USERNAME),
            CONF_PASSWORD: data.get(
                CONF_PASSWORD
            ),  # Encrypted in _handle_authentication_success
        }
        # Clean data from credentials
        entry_data = {
            k: v for k, v in data.items() if k not in (CONF_USERNAME, CONF_PASSWORD)
        }

        unique_id = str(
            entry_data.get(CONF_SITE) or options.get(CONF_USERNAME) or DOMAIN
        )
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        site_title = entry_data.pop("site_title", None)
        entry_data.pop("site_titles", None)
        title = site_title or options.get(CONF_USERNAME) or "Frank Energie"

        return self.async_create_entry(
            title=title,
            data=entry_data,
            options=options,
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
        return vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME,
                    default=user_input.get(CONF_USERNAME, "") if user_input else "",
                ): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )

    def _show_login_form(
        self,
        errors: Optional[dict[str, str]] = None,
        user_input: Optional[dict[str, Any]] = None,
    ) -> ConfigFlowResult:
        """Show the login form with optional errors and pre-filled username."""
        # Try to pre-fill username from reauth entry or last user input
        username = None
        if self._reauth_entry:
            username = self._reauth_entry.data.get(CONF_USERNAME)
        elif user_input:
            username = user_input.get(CONF_USERNAME)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=username or ""): str,
                vol.Required(CONF_PASSWORD): str,
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
                return await api.login(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except AuthException:
                _LOGGER.warning(
                    "Authentication failed for user %s", user_input[CONF_USERNAME]
                )
                # Trigger login flow with friendly error
                raise AuthException("invalid_auth")
            except ConnectionException:
                _LOGGER.error("Connection error for user %s", user_input[CONF_USERNAME])
                raise ConnectionException("connection_error")
            except Exception as ex:
                _LOGGER.exception(
                    "Unexpected error for user %s: %s", user_input[CONF_USERNAME], ex
                )
                raise RuntimeError("unknown_error") from ex

    async def _handle_authentication_success(
        self, user_input: dict[str, Any], auth: Authentication
    ) -> ConfigFlowResult:
        """Handle successful authentication."""
        encrypted_password = encrypt_password(self.hass, user_input[CONF_PASSWORD])
        self.sign_in_data = {
            CONF_USERNAME: user_input[CONF_USERNAME],
            CONF_PASSWORD: encrypted_password,
            CONF_ACCESS_TOKEN: auth.authToken,
            CONF_TOKEN: auth.refreshToken,
        }
        if self._reauth_entry:
            # Preserve existing entry data/options
            updated_options = {
                **self._reauth_entry.options,
                CONF_USERNAME: self.sign_in_data[CONF_USERNAME],
                CONF_PASSWORD: encrypted_password,
            }
            updated_data = {
                **self._reauth_entry.data,
                CONF_ACCESS_TOKEN: self.sign_in_data[CONF_ACCESS_TOKEN],
                CONF_TOKEN: self.sign_in_data[CONF_TOKEN],
            }
            # Remove plaintext credentials from data if any
            updated_data.pop(CONF_USERNAME, None)
            updated_data.pop(CONF_PASSWORD, None)

            self.hass.config_entries.async_update_entry(
                self._reauth_entry, data=updated_data, options=updated_options
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            )
            return self.async_abort(reason="reauth_successful")
        return await self.async_step_site(self.sign_in_data)

    async def _handle_authentication_failure(
        self, reason: str = "invalid_auth"
    ) -> ConfigFlowResult:
        """Handle authentication failure with a nice message."""
        return await self.async_step_login(errors={"base": reason})

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> Optional[config_entries.OptionsFlow]:
        """Get options flow handler only if a site is selected."""
        _LOGGER.debug("config_entry for %s", config_entry)
        entry_data = config_entry.data
        if CONF_SITE in entry_data:
            _LOGGER.debug(
                "Site %s is selected, providing options flow: %s",
                entry_data[CONF_SITE],
                entry_data,
            )
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

    def __init__(self, entry_data: dict[str, Any]) -> None:
        """Initialize Frank Energie options flow."""
        self.entry_data = entry_data

    async def async_step_init(
        self, user_input: Optional[dict[str, Any]] = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        return await self.async_step_user(user_input)

    def _get_password_to_validate(
        self, user_input: dict[str, Any], current_password: str
    ) -> str:
        """Resolve the password to use for validation."""
        password_to_validate = user_input.get(CONF_PASSWORD)
        if not password_to_validate and current_password:
            try:
                return decrypt_password(self.hass, current_password)
            except Exception:
                pass
        return password_to_validate or ""

    def _build_options_and_log_changes(
        self, user_input: dict[str, Any], encrypted_password: str
    ) -> dict[str, Any]:
        """Build the new options dictionary and log any changes in polling intervals."""
        entry = self.config_entry
        options = {
            CONF_USERNAME: user_input[CONF_USERNAME],
            CONF_PASSWORD: encrypted_password,
            CONF_INTERVAL_SETTINGS: int(
                user_input.get(CONF_INTERVAL_SETTINGS, DEFAULT_INTERVAL_SETTINGS)
            ),
            CONF_INTERVAL_STATISTICS: int(
                user_input.get(CONF_INTERVAL_STATISTICS, DEFAULT_INTERVAL_STATISTICS)
            ),
            CONF_INTERVAL_BATTERIES: int(
                user_input.get(CONF_INTERVAL_BATTERIES, DEFAULT_INTERVAL_BATTERIES)
            ),
            CONF_INTERVAL_BATTERY_SESSIONS: int(
                user_input.get(
                    CONF_INTERVAL_BATTERY_SESSIONS, DEFAULT_INTERVAL_BATTERY_SESSIONS
                )
            ),
            CONF_INTERVAL_CHARGERS: int(
                user_input.get(CONF_INTERVAL_CHARGERS, DEFAULT_INTERVAL_CHARGERS)
            ),
            CONF_INTERVAL_VEHICLES: int(
                user_input.get(CONF_INTERVAL_VEHICLES, DEFAULT_INTERVAL_VEHICLES)
            ),
            CONF_INTERVAL_PV: int(
                user_input.get(CONF_INTERVAL_PV, DEFAULT_INTERVAL_PV)
            ),
        }

        defaults = {
            CONF_INTERVAL_SETTINGS: DEFAULT_INTERVAL_SETTINGS,
            CONF_INTERVAL_STATISTICS: DEFAULT_INTERVAL_STATISTICS,
            CONF_INTERVAL_BATTERIES: DEFAULT_INTERVAL_BATTERIES,
            CONF_INTERVAL_BATTERY_SESSIONS: DEFAULT_INTERVAL_BATTERY_SESSIONS,
            CONF_INTERVAL_CHARGERS: DEFAULT_INTERVAL_CHARGERS,
            CONF_INTERVAL_VEHICLES: DEFAULT_INTERVAL_VEHICLES,
            CONF_INTERVAL_PV: DEFAULT_INTERVAL_PV,
        }
        changes = []
        for key, default_val in defaults.items():
            old_val = entry.options.get(key, default_val)
            new_val = options.get(key)
            if old_val != new_val:
                changes.append(f"{key}: {old_val} -> {new_val}")

        if changes:
            _LOGGER.debug(
                "Frank Energie polling intervals updated: %s",
                ", ".join(changes),
            )

        _LOGGER.debug(
            "Current Frank Energie polling intervals: %s",
            ", ".join(f"{key}: {options.get(key)}" for key in defaults),
        )
        return options

    async def async_step_user(
        self,
        user_input: Optional[dict[str, Any]] = None,
        errors: Optional[dict[str, str]] = None,
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        entry = self.config_entry

        current_username = (
            entry.options.get(CONF_USERNAME) or entry.data.get(CONF_USERNAME) or ""
        )
        current_password = (
            entry.options.get(CONF_PASSWORD) or entry.data.get(CONF_PASSWORD) or ""
        )

        if user_input is not None:
            password_to_validate = self._get_password_to_validate(
                user_input, current_password
            )

            try:
                async with FrankEnergie() as api:
                    auth = await api.login(
                        user_input[CONF_USERNAME], password_to_validate
                    )

                # Encrypt password before storing in options
                encrypted_password = encrypt_password(self.hass, password_to_validate)
                options = self._build_options_and_log_changes(
                    user_input, encrypted_password
                )

                # Update tokens in data
                updated_data = {
                    **entry.data,
                    CONF_ACCESS_TOKEN: auth.authToken,
                    CONF_TOKEN: auth.refreshToken,
                }
                # Clean up plaintext credentials from data if any
                updated_data.pop(CONF_USERNAME, None)
                updated_data.pop(CONF_PASSWORD, None)

                self.hass.config_entries.async_update_entry(entry, data=updated_data)

                return self.async_create_entry(title=entry.title, data=options)

            except AuthException:
                errors = {"base": "invalid_auth"}
            except ConnectionException:
                errors = {"base": "connection_error"}
            except Exception as ex:
                _LOGGER.exception("Unexpected error in options flow: %s", ex)
                errors = {"base": "unknown_error"}

        data_schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=current_username): str,
                # Leave password blank to avoid exposing it in the UI
                vol.Optional(CONF_PASSWORD, default=""): str,
                vol.Required(
                    CONF_INTERVAL_SETTINGS,
                    default=entry.options.get(
                        CONF_INTERVAL_SETTINGS, DEFAULT_INTERVAL_SETTINGS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=1, max=72, mode=NumberSelectorMode.SLIDER)
                ),
                vol.Required(
                    CONF_INTERVAL_STATISTICS,
                    default=entry.options.get(
                        CONF_INTERVAL_STATISTICS, DEFAULT_INTERVAL_STATISTICS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=15, max=1440, mode=NumberSelectorMode.SLIDER
                    )
                ),
                vol.Required(
                    CONF_INTERVAL_BATTERIES,
                    default=entry.options.get(
                        CONF_INTERVAL_BATTERIES, DEFAULT_INTERVAL_BATTERIES
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=5, max=60, mode=NumberSelectorMode.SLIDER)
                ),
                vol.Required(
                    CONF_INTERVAL_BATTERY_SESSIONS,
                    default=entry.options.get(
                        CONF_INTERVAL_BATTERY_SESSIONS,
                        DEFAULT_INTERVAL_BATTERY_SESSIONS,
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=5, max=1440, mode=NumberSelectorMode.SLIDER
                    )
                ),
                vol.Required(
                    CONF_INTERVAL_CHARGERS,
                    default=entry.options.get(
                        CONF_INTERVAL_CHARGERS, DEFAULT_INTERVAL_CHARGERS
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=5, max=60, mode=NumberSelectorMode.SLIDER)
                ),
                vol.Required(
                    CONF_INTERVAL_VEHICLES,
                    default=entry.options.get(
                        CONF_INTERVAL_VEHICLES, DEFAULT_INTERVAL_VEHICLES
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(min=5, max=60, mode=NumberSelectorMode.SLIDER)
                ),
                vol.Required(
                    CONF_INTERVAL_PV,
                    default=entry.options.get(CONF_INTERVAL_PV, DEFAULT_INTERVAL_PV),
                ): NumberSelector(
                    NumberSelectorConfig(min=5, max=60, mode=NumberSelectorMode.SLIDER)
                ),
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
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
