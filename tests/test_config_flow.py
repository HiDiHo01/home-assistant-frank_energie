"""Tests for Frank Energie config flow."""

import pytest

from homeassistant import config_entries
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

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


async def test_options_flow_without_site(hass: HomeAssistant, config_entry) -> None:
    """Test options flow fallback when site is not set."""
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] is None

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"market_cheap_hours": 5}
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert config_entry.options["market_cheap_hours"] == 5


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


async def test_reconfigure_anonymous_entry_to_authenticated_site(
    hass: HomeAssistant, mock_auth_success
) -> None:
    """Upgrade a public-price entry in place after validating the site."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.frank_energie.helpers import decrypt_password

    anonymous = MockConfigEntry(
        domain=DOMAIN,
        title="Frank Energie",
        data={},
        options={CONF_USERNAME: None, CONF_PASSWORD: None, "resolution": "PT15M"},
        unique_id=DOMAIN,
    )
    anonymous.add_to_hass(hass)
    original_id = anonymous.entry_id

    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": anonymous.entry_id,
        },
    )
    assert flow["type"] == FlowResultType.FORM

    site_form = await hass.config_entries.flow.async_configure(
        flow["flow_id"], user_input=USER_INPUT
    )
    assert site_form["type"] == FlowResultType.FORM
    assert site_form["step_id"] == "site"
    # An incomplete reconfigure must not damage the working public entry.
    assert anonymous.data == {}
    assert anonymous.options["resolution"] == "PT15M"

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], user_input={CONF_SITE: "site_ref_123"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert anonymous.entry_id == original_id
    assert anonymous.unique_id == DOMAIN
    assert anonymous.title == "Main Street 123"
    assert anonymous.data[CONF_SITE] == "site_ref_123"
    assert anonymous.data["access_token"] == "mock_auth_token"
    assert anonymous.data["token"] == "mock_refresh_token"
    assert "username" not in anonymous.data
    assert "password" not in anonymous.data
    assert anonymous.options["username"] == USER_INPUT["username"]
    assert (
        decrypt_password(hass, anonymous.options["password"]) == USER_INPUT["password"]
    )
    assert anonymous.options["resolution"] == "PT15M"
    await hass.async_block_till_done()


async def test_reconfigure_anonymous_invalid_login_preserves_entry(
    hass: HomeAssistant, mock_auth_failure
) -> None:
    """Failed credentials must not mutate an anonymous configuration."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    anonymous = MockConfigEntry(
        domain=DOMAIN, title="Frank Energie", data={}, options={}, unique_id=DOMAIN
    )
    anonymous.add_to_hass(hass)
    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": anonymous.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], user_input=USER_INPUT
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert anonymous.data == {}
    assert anonymous.options == {}


async def test_reconfigure_anonymous_no_delivery_sites_preserves_entry(
    hass: HomeAssistant, mock_auth_success
) -> None:
    """A valid login without usable sites must not replace the public entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from unittest.mock import MagicMock

    api = mock_auth_success.return_value.__aenter__.return_value
    api.UserSites.return_value = MagicMock(deliverySites=[])
    anonymous = MockConfigEntry(
        domain=DOMAIN, title="Frank Energie", data={}, options={}, unique_id=DOMAIN
    )
    anonymous.add_to_hass(hass)
    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": anonymous.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], user_input=USER_INPUT
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "site"
    assert result["errors"] == {"base": "no_delivery_sites"}
    assert anonymous.data == {}
    assert anonymous.options == {}


async def test_reconfigure_anonymous_multiple_sites_selects_explicit_site(
    hass: HomeAssistant, mock_auth_success
) -> None:
    """Never default silently to the first site when adding authentication."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from unittest.mock import MagicMock

    api = mock_auth_success.return_value.__aenter__.return_value
    first_site = api.UserSites.return_value.deliverySites[0]
    second = MagicMock()
    second.status = "IN_DELIVERY"
    second.reference = "site_ref_456"
    second.address.street = "Second Street"
    second.address.houseNumber = "45"
    second.address.houseNumberAddition = ""
    api.UserSites.return_value.deliverySites = [first_site, second]
    anonymous = MockConfigEntry(
        domain=DOMAIN, title="Frank Energie", data={}, options={}, unique_id=DOMAIN
    )
    anonymous.add_to_hass(hass)

    flow = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": anonymous.entry_id,
        },
    )
    site_form = await hass.config_entries.flow.async_configure(
        flow["flow_id"], user_input=USER_INPUT
    )
    assert site_form["step_id"] == "site"

    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], user_input={CONF_SITE: "site_ref_456"}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert anonymous.data[CONF_SITE] == "site_ref_456"
    assert anonymous.title == "Second Street 45"
    await hass.async_block_till_done()
