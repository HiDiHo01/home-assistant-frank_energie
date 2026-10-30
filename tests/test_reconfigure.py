"""Regressions for Home Assistant reconfigure flows and credential persistence."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from python_frank_energie.exceptions import AuthException, ConnectionException

from custom_components.frank_energie.const import DOMAIN
from custom_components.frank_energie.helpers import decrypt_password, encrypt_password

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

_CREDENTIALS = {CONF_USERNAME: "updated@example.com", CONF_PASSWORD: "new-password"}


@pytest.fixture(name="reconfigure_entry")
def _reconfigure_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Register an entry with current options and legacy credentials to retain safely."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Existing delivery site",
        unique_id="site-alpha",
        version=2,
        minor_version=1,
        data={
            "site_reference": "site-alpha",
            CONF_USERNAME: "legacy@example.com",
            CONF_PASSWORD: "legacy-password",
            CONF_ACCESS_TOKEN: "old-access-token",
            CONF_TOKEN: "old-refresh-token",
            "custom_data": {"keep": True},
        },
        options={
            CONF_USERNAME: "current@example.com",
            CONF_PASSWORD: encrypt_password(hass, "saved-password"),
            "resolution": "PT60M",
            "interval_settings": 12,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture(name="provider_api")
def _provider_api() -> Iterator[MagicMock]:
    """Mock only the external authentication client, preserving real flow handling."""
    with patch(
        "custom_components.frank_energie.config_flow.FrankEnergie", autospec=True
    ) as factory:
        api = factory.return_value
        api.__aenter__.return_value = api
        api.login.return_value = MagicMock(
            authToken="new-access-token", refreshToken="new-refresh-token"
        )
        yield api


@pytest.mark.parametrize("username_in_options", [False, True])
async def test_reconfigure_form_prefills_saved_username(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    username_in_options: bool,
) -> None:
    """Prefer options credentials while supporting legacy data without exposing passwords."""
    expected_username = (
        "current@example.com" if username_in_options else "legacy@example.com"
    )
    if not username_in_options:
        changed_options = dict(reconfigure_entry.options)
        changed_options.pop(CONF_USERNAME)
        hass.config_entries.async_update_entry(
            reconfigure_entry, options=changed_options
        )
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": reconfigure_entry.entry_id,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    assert result["data_schema"]({CONF_PASSWORD: "supplied-password"}) == {
        CONF_USERNAME: expected_username,
        CONF_PASSWORD: "supplied-password",
    }
    password_field = next(
        field for field in result["data_schema"].schema if field == CONF_PASSWORD
    )
    assert password_field.default is vol.UNDEFINED
    assert dict(reconfigure_entry.data) == original_data
    assert dict(reconfigure_entry.options) == original_options
    provider_api.login.assert_not_awaited()


@pytest.mark.parametrize(
    ("source", "expected_reason"),
    [
        (config_entries.SOURCE_RECONFIGURE, "reconfigure_successful"),
        (config_entries.SOURCE_REAUTH, "reauth_successful"),
    ],
)
async def test_credentials_update_only_the_selected_entry(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    source: str,
    expected_reason: str,
) -> None:
    """Replace credentials and reload once, retaining site identity and other settings."""
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="site-beta",
        data={"site_reference": "site-beta", CONF_ACCESS_TOKEN: "other-token"},
        options={"resolution": "PT15M"},
    )
    other_entry.add_to_hass(hass)
    original_options = dict(reconfigure_entry.options)
    original_identity = (
        reconfigure_entry.entry_id,
        reconfigure_entry.unique_id,
        reconfigure_entry.title,
    )

    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload_entry:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": source, "entry_id": reconfigure_entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=dict(_CREDENTIALS)
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == expected_reason
    reload_entry.assert_awaited_once_with(reconfigure_entry.entry_id)
    provider_api.login.assert_awaited_once_with("updated@example.com", "new-password")
    provider_api.UserSites.assert_not_awaited()
    assert dict(reconfigure_entry.data) == {
        "site_reference": "site-alpha",
        CONF_ACCESS_TOKEN: "new-access-token",
        CONF_TOKEN: "new-refresh-token",
        "custom_data": {"keep": True},
    }
    assert dict(reconfigure_entry.options) == {
        **original_options,
        CONF_USERNAME: "updated@example.com",
        CONF_PASSWORD: reconfigure_entry.options[CONF_PASSWORD],
    }
    assert reconfigure_entry.options[CONF_PASSWORD] != "new-password"
    assert (
        decrypt_password(hass, reconfigure_entry.options[CONF_PASSWORD])
        == "new-password"
    )
    assert (
        reconfigure_entry.entry_id,
        reconfigure_entry.unique_id,
        reconfigure_entry.title,
    ) == original_identity
    assert dict(other_entry.data) == {
        "site_reference": "site-beta",
        CONF_ACCESS_TOKEN: "other-token",
    }
    assert dict(other_entry.options) == {"resolution": "PT15M"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


@pytest.mark.parametrize(
    ("failure", "error_key"),
    [
        (AuthException("rejected"), "invalid_auth"),
        (ConnectionException("offline"), "connection_error"),
        (RuntimeError("unexpected provider failure"), "unknown_error"),
    ],
)
async def test_reconfigure_failure_preserves_entry_and_allows_retry(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    failure: Exception,
    error_key: str,
) -> None:
    """Retain working tokens/options on errors, then commit only a successful retry."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)
    provider_api.login.side_effect = failure

    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload_entry:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": reconfigure_entry.entry_id,
            },
        )
        failed_result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=dict(_CREDENTIALS)
        )
        await hass.async_block_till_done()

        assert failed_result["type"] is FlowResultType.FORM
        assert failed_result["step_id"] == "login"
        assert failed_result["errors"] == {"base": error_key}
        assert dict(reconfigure_entry.data) == original_data
        assert dict(reconfigure_entry.options) == original_options
        reload_entry.assert_not_awaited()

        provider_api.login.side_effect = None
        recovered_result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=dict(_CREDENTIALS)
        )
        await hass.async_block_till_done()

    assert recovered_result["type"] is FlowResultType.ABORT
    assert recovered_result["reason"] == "reconfigure_successful"
    assert reconfigure_entry.data[CONF_ACCESS_TOKEN] == "new-access-token"
    assert (
        decrypt_password(hass, reconfigure_entry.options[CONF_PASSWORD])
        == "new-password"
    )
    reload_entry.assert_awaited_once_with(reconfigure_entry.entry_id)


@pytest.mark.parametrize(
    ("username", "password", "invalid_fields"),
    [
        ("", "password", {CONF_USERNAME}),
        ("user@example.com", "", {CONF_PASSWORD}),
        ("", "", {CONF_USERNAME, CONF_PASSWORD}),
        ("  ", "  ", {CONF_USERNAME, CONF_PASSWORD}),
    ],
)
async def test_reconfigure_rejects_empty_credentials_without_changes(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    username: str,
    password: str,
    invalid_fields: set[str],
) -> None:
    """Validate blank input locally before API calls or persistent updates."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)

    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload_entry:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": reconfigure_entry.entry_id,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: username, CONF_PASSWORD: password},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    assert set(result["errors"]) == invalid_fields
    assert dict(reconfigure_entry.data) == original_data
    assert dict(reconfigure_entry.options) == original_options
    provider_api.login.assert_not_awaited()
    reload_entry.assert_not_awaited()


async def test_cancelled_reconfigure_keeps_saved_configuration(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
) -> None:
    """Leave configuration intact when the user closes the reconfigure form."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)

    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload_entry:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_RECONFIGURE,
                "entry_id": reconfigure_entry.entry_id,
            },
        )
        hass.config_entries.flow.async_abort(result["flow_id"])
        await hass.async_block_till_done()

    assert dict(reconfigure_entry.data) == original_data
    assert dict(reconfigure_entry.options) == original_options
    provider_api.login.assert_not_awaited()
    reload_entry.assert_not_awaited()
    assert not hass.config_entries.flow.async_progress()
