"""Regressions for Home Assistant reconfigure flows and credential persistence."""

import asyncio
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
from aiohttp import ClientConnectionError
from homeassistant import config_entries
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from python_frank_energie import Authentication, FrankEnergie
from python_frank_energie.exceptions import (
    AuthException,
    AuthRequiredException,
    ConnectionException,
    NetworkError,
)
from python_frank_energie.models import UserSites

from custom_components.frank_energie.const import DOMAIN
from custom_components.frank_energie.helpers import decrypt_password, encrypt_password

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

_CREDENTIALS = {CONF_USERNAME: "updated@example.com", CONF_PASSWORD: "new-password"}


def _user_sites(*references: str) -> UserSites:
    """Build real SDK models for synthetic delivery sites, including inactive sites."""
    return UserSites.from_dict(
        {
            "data": {
                "userSites": [
                    {"reference": reference, "status": "DELIVERY_ENDED"}
                    for reference in references
                ]
            }
        }
    )


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
    """Mock provider requests while retaining real SDK clients and Home Assistant flows."""
    with (
        patch.object(
            FrankEnergie,
            "login",
            return_value=Authentication("new-access-token", "new-refresh-token", None),
        ) as login,
        patch.object(
            FrankEnergie,
            "UserSites",
            return_value=_user_sites("other-site", "site-alpha"),
        ) as user_sites,
    ):
        yield MagicMock(login=login, UserSites=user_sites)


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
    actual_form_data = result["data_schema"]({CONF_PASSWORD: "supplied-password"})
    assert actual_form_data == {
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
    provider_api.UserSites.assert_awaited_once_with()
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
    actual_identity = (
        reconfigure_entry.entry_id,
        reconfigure_entry.unique_id,
        reconfigure_entry.title,
    )
    assert actual_identity == original_identity
    assert dict(other_entry.data) == {
        "site_reference": "site-beta",
        CONF_ACCESS_TOKEN: "other-token",
    }
    assert dict(other_entry.options) == {"resolution": "PT15M"}
    actual_entry_count = len(hass.config_entries.async_entries(DOMAIN))
    assert actual_entry_count == 2


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
async def test_unexpected_login_failure_does_not_log_private_details(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    caplog: pytest.LogCaptureFixture,
    source: str,
) -> None:
    """Keep provider messages, chained causes and account names out of login logs."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)
    login_error = RuntimeError("synthetic-private-login-payload")
    login_error.__cause__ = ValueError("synthetic-private-login-cause")
    provider_api.login.side_effect = login_error

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source, "entry_id": reconfigure_entry.entry_id}
    )
    failed = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(_CREDENTIALS)
    )
    await hass.async_block_till_done()

    assert failed["type"] is FlowResultType.FORM
    assert failed["errors"] == {"base": "unknown_error"}
    assert "synthetic-private-login-payload" not in caplog.text
    assert "synthetic-private-login-cause" not in caplog.text
    assert _CREDENTIALS[CONF_USERNAME] not in caplog.text
    assert dict(reconfigure_entry.data) == original_data
    assert dict(reconfigure_entry.options) == original_options
    provider_api.UserSites.assert_not_awaited()


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


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
@pytest.mark.parametrize("username", ["updated@example.com", "current@example.com"])
async def test_unrelated_account_is_rejected_and_correct_account_can_retry(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    source: str,
    username: str,
) -> None:
    """Require the configured site even when a login uses the previously saved email."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)
    original_identity = (
        reconfigure_entry.entry_id,
        reconfigure_entry.unique_id,
        reconfigure_entry.title,
    )
    provider_api.UserSites.return_value = _user_sites("unrelated-site")

    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload_entry:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source, "entry_id": reconfigure_entry.entry_id}
        )
        rejected = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**_CREDENTIALS, CONF_USERNAME: username}
        )
        await hass.async_block_till_done()

        assert rejected["type"] is FlowResultType.FORM
        assert rejected["step_id"] == "login"
        assert rejected["errors"] == {"base": "wrong_account"}
        assert dict(reconfigure_entry.data) == original_data
        assert dict(reconfigure_entry.options) == original_options
        actual_identity = (
            reconfigure_entry.entry_id,
            reconfigure_entry.unique_id,
            reconfigure_entry.title,
        )
        assert actual_identity == original_identity
        reload_entry.assert_not_awaited()

        provider_api.UserSites.return_value = _user_sites("other-site", "site-alpha")
        recovered = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(_CREDENTIALS)
        )
        await hass.async_block_till_done()

    assert recovered["type"] is FlowResultType.ABORT
    expected_reason = (
        "reauth_successful"
        if source == config_entries.SOURCE_REAUTH
        else "reconfigure_successful"
    )
    assert recovered["reason"] == expected_reason
    assert reconfigure_entry.data[CONF_ACCESS_TOKEN] == "new-access-token"
    assert reconfigure_entry.data["site_reference"] == "site-alpha"
    assert (
        decrypt_password(hass, reconfigure_entry.options[CONF_PASSWORD])
        == "new-password"
    )
    reload_entry.assert_awaited_once_with(reconfigure_entry.entry_id)


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
@pytest.mark.parametrize(
    ("failure", "error_key"),
    [
        (AuthException("rejected"), "invalid_auth"),
        (AuthRequiredException("expired"), "invalid_auth"),
        (ConnectionException("offline"), "connection_error"),
        (NetworkError("offline"), "connection_error"),
        (ClientConnectionError("offline"), "connection_error"),
        (TimeoutError("timeout"), "connection_error"),
        (RuntimeError("synthetic-private-provider-message"), "unknown_error"),
    ],
)
async def test_site_verification_failure_keeps_credentials_and_allows_retry(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    caplog: pytest.LogCaptureFixture,
    source: str,
    failure: Exception,
    error_key: str,
) -> None:
    """Commit no credentials or reload until the selected site's ownership is verified."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)
    failure.__cause__ = RuntimeError("synthetic-private-provider-cause")
    provider_api.UserSites.side_effect = failure

    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload_entry:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source, "entry_id": reconfigure_entry.entry_id}
        )
        rejected = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(_CREDENTIALS)
        )
        await hass.async_block_till_done()

        assert rejected["type"] is FlowResultType.FORM
        assert rejected["errors"] == {"base": error_key}
        assert dict(reconfigure_entry.data) == original_data
        assert dict(reconfigure_entry.options) == original_options
        assert "synthetic-private-provider-message" not in caplog.text
        assert "synthetic-private-provider-cause" not in caplog.text
        reload_entry.assert_not_awaited()

        provider_api.UserSites.side_effect = None
        recovered = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(_CREDENTIALS)
        )
        await hass.async_block_till_done()

    assert recovered["type"] is FlowResultType.ABORT
    expected_reason = (
        "reauth_successful"
        if source == config_entries.SOURCE_REAUTH
        else "reconfigure_successful"
    )
    assert recovered["reason"] == expected_reason
    reload_entry.assert_awaited_once_with(reconfigure_entry.entry_id)


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
@pytest.mark.parametrize("response", [None, []])
async def test_missing_site_inventory_does_not_replace_credentials(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    source: str,
    response: list[object] | None,
) -> None:
    """Fail closed when the provider offers no evidence that the selected site belongs to it."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)
    inventory = _user_sites("site-alpha")
    inventory.deliverySites = response or []
    provider_api.UserSites.return_value = inventory if response is not None else None
    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload_entry:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source, "entry_id": reconfigure_entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(_CREDENTIALS)
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_account"}
    assert dict(reconfigure_entry.data) == original_data
    assert dict(reconfigure_entry.options) == original_options
    reload_entry.assert_not_awaited()


@pytest.mark.parametrize("legacy_password_is_encrypted", [False, True])
async def test_offline_options_preserve_legacy_credentials_after_token_renewal(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    legacy_password_is_encrypted: bool,
) -> None:
    """Carry data-only credentials into options before token renewal clears data."""
    saved_options = dict(reconfigure_entry.options)
    saved_options.pop(CONF_USERNAME)
    saved_options.pop(CONF_PASSWORD)
    hass.config_entries.async_update_entry(reconfigure_entry, options=saved_options)
    legacy_password = (
        encrypt_password(hass, "legacy-password")
        if legacy_password_is_encrypted
        else "legacy-password"
    )
    legacy_data = {**reconfigure_entry.data, CONF_PASSWORD: legacy_password}
    hass.config_entries.async_update_entry(reconfigure_entry, data=legacy_data)
    provider_api.login.side_effect = ConnectionException("login offline")
    provider_api.UserSites.side_effect = NetworkError("site discovery offline")

    result = await hass.config_entries.options.async_init(reconfigure_entry.entry_id)
    saved = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "legacy@example.com",
            CONF_PASSWORD: "",
            "interval_settings": 24,
        },
    )
    await hass.async_block_till_done()

    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert reconfigure_entry.options["interval_settings"] == 24
    assert reconfigure_entry.options["resolution"] == "PT60M"
    assert reconfigure_entry.options[CONF_USERNAME] == "legacy@example.com"
    assert decrypt_password(hass, reconfigure_entry.options[CONF_PASSWORD]) == (
        "legacy-password"
    )
    assert dict(reconfigure_entry.data) == legacy_data
    provider_api.login.assert_not_awaited()
    provider_api.UserSites.assert_not_awaited()

    # Token renewal later removes credentials from data. Options must retain
    # a usable encrypted copy for a subsequent silent login.
    renewed_data = dict(reconfigure_entry.data)
    renewed_data.pop(CONF_USERNAME, None)
    renewed_data.pop(CONF_PASSWORD, None)
    hass.config_entries.async_update_entry(reconfigure_entry, data=renewed_data)
    assert reconfigure_entry.options[CONF_USERNAME] == "legacy@example.com"
    assert decrypt_password(hass, reconfigure_entry.options[CONF_PASSWORD]) == (
        "legacy-password"
    )


@pytest.mark.parametrize("include_blank_password", [False, True])
async def test_polling_settings_update_without_credential_verification(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    include_blank_password: bool,
) -> None:
    """Save polling changes offline without changing credentials or site identity."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)
    provider_api.login.side_effect = ConnectionException("login unavailable")
    provider_api.UserSites.side_effect = NetworkError("site inventory unavailable")
    user_input = {
        CONF_USERNAME: original_options[CONF_USERNAME],
        "interval_settings": 24,
    }
    if include_blank_password:
        user_input[CONF_PASSWORD] = ""

    initial = await hass.config_entries.options.async_init(reconfigure_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        initial["flow_id"], user_input
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert reconfigure_entry.options["interval_settings"] == 24
    for key, value in original_options.items():
        if key != "interval_settings":
            assert reconfigure_entry.options[key] == value
    assert dict(reconfigure_entry.data) == original_data
    provider_api.login.assert_not_awaited()
    provider_api.UserSites.assert_not_awaited()


@pytest.mark.parametrize("username", ["updated@example.com", "current@example.com"])
async def test_options_credentials_cannot_bypass_site_ownership(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    username: str,
) -> None:
    """Require the same configured site when the options form changes credentials."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)
    provider_api.UserSites.return_value = _user_sites("unrelated-site")
    result = await hass.config_entries.options.async_init(reconfigure_entry.entry_id)
    rejected = await hass.config_entries.options.async_configure(
        result["flow_id"], {**_CREDENTIALS, CONF_USERNAME: username}
    )
    await hass.async_block_till_done()

    assert rejected["type"] is FlowResultType.FORM
    assert rejected["errors"] == {"base": "wrong_account"}
    assert dict(reconfigure_entry.data) == original_data
    assert dict(reconfigure_entry.options) == original_options

    provider_api.UserSites.return_value = _user_sites("site-alpha")
    recovered = await hass.config_entries.options.async_configure(
        result["flow_id"], {**_CREDENTIALS, CONF_USERNAME: username}
    )
    await hass.async_block_till_done()
    assert recovered["type"] is FlowResultType.CREATE_ENTRY
    assert reconfigure_entry.data["site_reference"] == "site-alpha"
    assert reconfigure_entry.data[CONF_ACCESS_TOKEN] == "new-access-token"
    assert (
        decrypt_password(hass, reconfigure_entry.options[CONF_PASSWORD])
        == "new-password"
    )


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
async def test_cancelled_site_check_never_commits_credentials(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    source: str,
) -> None:
    """Propagate cancellation without changing working tokens or scheduling a reload."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)
    provider_api.UserSites.side_effect = asyncio.CancelledError
    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload_entry:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source, "entry_id": reconfigure_entry.entry_id}
        )
        with pytest.raises(asyncio.CancelledError):
            await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(_CREDENTIALS)
            )
        await hass.async_block_till_done()
    assert dict(reconfigure_entry.data) == original_data
    assert dict(reconfigure_entry.options) == original_options
    reload_entry.assert_not_awaited()


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
@pytest.mark.parametrize(
    "username", ["current@example.com", "CURRENT@example.com", "other@example.com"]
)
async def test_legacy_entry_without_site_preserves_its_existing_account(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    source: str,
    username: str,
) -> None:
    """Permit legacy password updates for the existing email without guessing a delivery site."""
    legacy_data = dict(reconfigure_entry.data)
    legacy_data.pop("site_reference")
    hass.config_entries.async_update_entry(reconfigure_entry, data=legacy_data)
    original_options = dict(reconfigure_entry.options)
    with patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as reload_entry:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source, "entry_id": reconfigure_entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**_CREDENTIALS, CONF_USERNAME: username}
        )
        await hass.async_block_till_done()
    if username.casefold() == "current@example.com":
        assert result["type"] is FlowResultType.ABORT
        assert reconfigure_entry.data[CONF_ACCESS_TOKEN] == "new-access-token"
        assert "site_reference" not in reconfigure_entry.data
        reload_entry.assert_awaited_once_with(reconfigure_entry.entry_id)
    else:
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "wrong_account"}
        assert dict(reconfigure_entry.data) == legacy_data
        assert dict(reconfigure_entry.options) == original_options
        reload_entry.assert_not_awaited()
    provider_api.UserSites.assert_not_awaited()


@pytest.mark.parametrize("site_reference", [None, "", 42])
async def test_invalid_saved_site_is_not_replaced_by_another_account(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    site_reference: object,
) -> None:
    """Reject malformed bound identities instead of treating them as unbound legacy accounts."""
    original_data = {**reconfigure_entry.data, "site_reference": site_reference}
    original_options = dict(reconfigure_entry.options)
    hass.config_entries.async_update_entry(reconfigure_entry, data=original_data)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": reconfigure_entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(_CREDENTIALS)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_account"}
    assert dict(reconfigure_entry.data) == original_data
    assert dict(reconfigure_entry.options) == original_options
    provider_api.UserSites.assert_not_awaited()


@pytest.mark.parametrize(
    ("failure", "error_key"),
    [
        (AuthException("rejected"), "invalid_auth"),
        (NetworkError("offline"), "connection_error"),
        (RuntimeError("synthetic-private-provider-message"), "unknown_error"),
    ],
)
async def test_options_site_check_failure_preserves_credentials_and_allows_retry(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
    error_key: str,
) -> None:
    """Keep options and tokens unchanged on verification failure, then recover normally."""
    original_data = dict(reconfigure_entry.data)
    original_options = dict(reconfigure_entry.options)
    failure.__cause__ = RuntimeError("synthetic-private-provider-cause")
    provider_api.UserSites.side_effect = failure
    result = await hass.config_entries.options.async_init(reconfigure_entry.entry_id)
    failed = await hass.config_entries.options.async_configure(
        result["flow_id"], dict(_CREDENTIALS)
    )
    await hass.async_block_till_done()
    assert failed["type"] is FlowResultType.FORM
    assert failed["errors"] == {"base": error_key}
    assert dict(reconfigure_entry.data) == original_data
    assert dict(reconfigure_entry.options) == original_options
    assert "synthetic-private-provider-message" not in caplog.text
    assert "synthetic-private-provider-cause" not in caplog.text
    provider_api.UserSites.side_effect = None
    recovered = await hass.config_entries.options.async_configure(
        result["flow_id"], dict(_CREDENTIALS)
    )
    await hass.async_block_till_done()
    assert recovered["type"] is FlowResultType.CREATE_ENTRY
    assert reconfigure_entry.data[CONF_ACCESS_TOKEN] == "new-access-token"


@pytest.mark.parametrize(
    "source",
    [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE, "options"],
)
async def test_site_check_uses_new_tokens_and_leaves_the_host_session_open(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    source: str,
) -> None:
    """Verify ownership using new tokens without closing Home Assistant's shared session."""
    shared_session = async_get_clientsession(hass)

    async def read_sites(client: FrankEnergie) -> UserSites:
        """Observe the real SDK context at the provider-request boundary."""
        assert client.auth is not None
        assert client.auth.authToken == "new-access-token"
        assert client.auth.refreshToken == "new-refresh-token"
        assert client._session is shared_session
        return _user_sites("site-alpha")

    with (
        patch.object(
            FrankEnergie,
            "login",
            return_value=Authentication("new-access-token", "new-refresh-token", None),
        ),
        patch.object(
            FrankEnergie, "UserSites", autospec=True, side_effect=read_sites
        ) as site_request,
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        if source == "options":
            result = await hass.config_entries.options.async_init(
                reconfigure_entry.entry_id
            )
            completed = await hass.config_entries.options.async_configure(
                result["flow_id"], dict(_CREDENTIALS)
            )
            assert completed["type"] is FlowResultType.CREATE_ENTRY
        else:
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": source, "entry_id": reconfigure_entry.entry_id},
            )
            completed = await hass.config_entries.flow.async_configure(
                result["flow_id"], dict(_CREDENTIALS)
            )
            assert completed["type"] is FlowResultType.ABORT
        await hass.async_block_till_done()
    site_request.assert_awaited_once()
    assert not shared_session.closed


@pytest.mark.parametrize(
    "source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE]
)
@pytest.mark.parametrize("site_access", [False, True])
async def test_credential_changes_preserve_registered_devices_and_entities(
    hass: HomeAssistant,
    reconfigure_entry: MockConfigEntry,
    provider_api: MagicMock,
    source: str,
    site_access: bool,
) -> None:
    """Retain registry IDs and user customization after rejected and accepted logins."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=reconfigure_entry.entry_id,
        identifiers={(DOMAIN, "site-alpha")},
        name="Synthetic delivery site",
    )
    entity = entities.async_get_or_create(
        "sensor",
        DOMAIN,
        "site-alpha-meter",
        config_entry=reconfigure_entry,
        device_id=device.id,
    )
    entities.async_update_entity(entity.entity_id, name="Customized meter name")
    provider_api.UserSites.return_value = _user_sites(
        "site-alpha" if site_access else "other-site"
    )
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source, "entry_id": reconfigure_entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], dict(_CREDENTIALS)
        )
        await hass.async_block_till_done()
    expected_type = FlowResultType.ABORT if site_access else FlowResultType.FORM
    assert result["type"] is expected_type
    observed_entity = entities.async_get(entity.entity_id)
    assert observed_entity is not None
    assert observed_entity.unique_id == "site-alpha-meter"
    assert observed_entity.config_entry_id == reconfigure_entry.entry_id
    assert observed_entity.device_id == device.id
    assert observed_entity.name == "Customized meter name"
    assert devices.async_get(device.id) is device
