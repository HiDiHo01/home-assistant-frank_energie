"""Tests for Frank Energie config flow."""

from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import TextSelector, TextSelectorType
from pytest_homeassistant.common import MockConfigEntry
from python_frank_energie.exceptions import AuthException

from custom_components.frank_energie.const import (
    CONF_SITE,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "mock_setup_entry", "mock_setup_entry_success"
)

USER_INPUT = {
    CONF_USERNAME: "user@example.com",
    CONF_PASSWORD: "secure_password",
}


def _assert_credential_selectors(schema: vol.Schema) -> None:
    """Verify that Home Assistant renders semantic email and password inputs."""
    fields = {
        getattr(marker, "schema", marker): validator
        for marker, validator in schema.schema.items()
    }

    username = fields[CONF_USERNAME]
    assert isinstance(username, TextSelector)
    assert username.config["type"] == TextSelectorType.EMAIL
    assert username.config["autocomplete"] == "username"

    password = fields[CONF_PASSWORD]
    assert isinstance(password, TextSelector)
    assert password.config["type"] == TextSelectorType.PASSWORD
    assert password.config["autocomplete"] == "current-password"


async def test_show_login_form(hass: HomeAssistant) -> None:
    """Test that the login form is shown."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"authentication": True},
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "login"
    _assert_credential_selectors(result2["data_schema"])


async def test_invalid_authentication(hass: HomeAssistant, mock_auth_failure) -> None:
    """Test showing error when authentication fails."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"authentication": True},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input=USER_INPUT,
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "login"
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_connection_error(hass: HomeAssistant, mock_auth_exception) -> None:
    """Test handling of connection errors."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"authentication": True},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input=USER_INPUT,
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "login"
    assert result2["errors"] == {"base": "connection_error"}


async def test_successful_login_flow(hass: HomeAssistant, mock_auth_success) -> None:
    """Test a successful login and redirect to site step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"authentication": True},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input=USER_INPUT,
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "site"


async def test_reauth_flow_success(
    hass: HomeAssistant, mock_auth_success, config_entry
) -> None:
    """Test successful reauthentication."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": config_entry.entry_id,
        },
        data={CONF_USERNAME: config_entry.data[CONF_USERNAME]},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "login"
    _assert_credential_selectors(result["data_schema"])

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={**USER_INPUT},
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    await hass.async_block_till_done()


async def test_options_flow_with_site(
    hass: HomeAssistant, config_entry_with_site
) -> None:
    """Test options flow when site is available."""
    result = await hass.config_entries.options.async_init(
        config_entry_with_site.entry_id
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] is None
    _assert_credential_selectors(result["data_schema"])


async def test_options_flow_without_site(hass: HomeAssistant, config_entry) -> None:
    """Test options flow fallback when site is not set."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "You do not have to login for this entry."}


async def test_login_validation_errors(hass: HomeAssistant) -> None:
    """Test validation errors for empty username/password."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"authentication": True},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_USERNAME: "", CONF_PASSWORD: ""},
    )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {
        CONF_USERNAME: "Username is required and cannot be empty.",
        CONF_PASSWORD: "Password is required and cannot be empty.",
    }


async def test_successful_flow_creates_entry(
    hass: HomeAssistant, mock_auth_success
) -> None:
    """Test that the flow creates an entry with the site address as title and encrypted credentials."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={"authentication": True},
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input=USER_INPUT,
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "site"

    # Now complete the site selection step
    result3 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_SITE: "site_ref_123"},
    )
    assert result3["type"] == FlowResultType.CREATE_ENTRY
    # The title must be the address: "Main Street 123"
    assert result3["title"] == "Main Street 123"
    assert result3["data"] == {
        "site_reference": "site_ref_123",
        "access_token": "mock_auth_token",
        "token": "mock_refresh_token",
    }
    # Check that credentials are saved in options and the password is encrypted
    from custom_components.frank_energie.helpers import decrypt_password

    assert result3["options"]["username"] == "user@example.com"
    assert decrypt_password(hass, result3["options"]["password"]) == "secure_password"


async def test_options_flow_submit_blank_password(
    hass: HomeAssistant, config_entry_with_site, mock_auth_success
) -> None:
    """Test options flow submission with a blank password preserves existing password."""
    hass.data["core.uuid"] = "test_uuid_123"

    from custom_components.frank_energie.helpers import (
        decrypt_password,
        encrypt_password,
    )

    hashed_pwd = encrypt_password(hass, "original_secure_pwd")
    hass.config_entries.async_update_entry(
        config_entry_with_site,
        options={
            "username": "user@example.com",
            "password": hashed_pwd,
        },
    )

    result = await hass.config_entries.options.async_init(
        config_entry_with_site.entry_id
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Submit options flow with blank password
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "username": "user@example.com",
            "password": "",
        },
    )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    # It should preserve the existing password (which decrypts to the original value)
    assert result2["data"]["username"] == "user@example.com"
    assert decrypt_password(hass, result2["data"]["password"]) == "original_secure_pwd"


async def test_password_field_uses_password_text_selector(
    hass: HomeAssistant, config_entry_with_site
) -> None:
    """Test that login and options flow schemas configure the password field with a password TextSelector."""
    from homeassistant.helpers.selector import TextSelector, TextSelectorType

    # 1. Check login form schema
    init_res = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    login_form = await hass.config_entries.flow.async_configure(
        init_res["flow_id"],
        user_input={"authentication": True},
    )
    password_validator = login_form["data_schema"].schema[CONF_PASSWORD]
    assert isinstance(password_validator, TextSelector)
    assert password_validator.config["type"] == TextSelectorType.PASSWORD
    assert password_validator.config["autocomplete"] == "current-password"

    # 2. Check options flow schema
    options_form = await hass.config_entries.options.async_init(
        config_entry_with_site.entry_id
    )
    options_password_validator = options_form["data_schema"].schema[CONF_PASSWORD]
    assert isinstance(options_password_validator, TextSelector)
    assert options_password_validator.config["type"] == TextSelectorType.PASSWORD
    assert options_password_validator.config["autocomplete"] == "current-password"


async def test_reconfigure_uses_email_username_selector(
    hass: HomeAssistant, config_entry_with_site: MockConfigEntry
) -> None:
    """Keep current account and password semantics on reconfigure."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": config_entry_with_site.entry_id,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "login"
    _assert_credential_selectors(result["data_schema"])


@pytest.mark.parametrize(
    ("site_failure", "expected_step"),
    [
        ("auth_failure", "login"),
        ("no_delivery_sites", "site"),
    ],
)
async def test_site_failure_keeps_credential_selectors(
    hass: HomeAssistant,
    mock_auth_success: MagicMock,
    site_failure: str,
    expected_step: str,
) -> None:
    """Use semantic credentials in both site lookup fallback forms."""
    api = mock_auth_success.return_value.__aenter__.return_value
    if site_failure == "auth_failure":
        api.UserSites.side_effect = AuthException("synthetic authentication failure")
    else:
        api.UserSites.return_value.deliverySites = []

    initial = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    login_form = await hass.config_entries.flow.async_configure(
        initial["flow_id"], user_input={"authentication": True}
    )
    assert login_form["step_id"] == "login"

    fallback_form = await hass.config_entries.flow.async_configure(
        initial["flow_id"], user_input=USER_INPUT
    )

    assert fallback_form["type"] == FlowResultType.FORM
    assert fallback_form["step_id"] == expected_step
    _assert_credential_selectors(fallback_form["data_schema"])

