"""Exercise shared HTTP ownership and private-data handling in account flows."""

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from python_frank_energie import Authentication, FrankEnergie
from python_frank_energie.exceptions import (
    AuthException,
    ConnectionException,
    RequestException,
)
from pytest_homeassistant.common import MockConfigEntry

from custom_components.frank_energie import FrankEnergieComponent
from custom_components.frank_energie import (
    async_setup_entry as async_integration_setup_entry,
)
from custom_components.frank_energie.config_flow import (
    ConfigFlow,
    async_handle_auth_failure,
)
from custom_components.frank_energie.const import CONF_SITE, DOMAIN
from custom_components.frank_energie.coordinator import FrankEnergieCoordinator
from custom_components.frank_energie.helpers import encrypt_password

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

USERNAME = "privacy-person@example.invalid"
PASSWORD = "private-test-password"
ACCESS_TOKEN = "private-test-access-token"
REFRESH_TOKEN = "private-test-refresh-token"
SITE_REFERENCE = "private-test-site-reference"
STREET = "Private Test Street"
PRIVATE_VALUES = (
    USERNAME,
    PASSWORD,
    ACCESS_TOKEN,
    REFRESH_TOKEN,
    SITE_REFERENCE,
    STREET,
)


def _user_sites() -> SimpleNamespace:
    """Return synthetic models whose repr contains recognisable private values."""
    site = SimpleNamespace(
        reference=SITE_REFERENCE,
        status="IN_DELIVERY",
        address=SimpleNamespace(
            street=STREET, houseNumber="42", houseNumberAddition=""
        ),
        email=USERNAME,
    )
    return SimpleNamespace(reference=SITE_REFERENCE, deliverySites=[site])


def _entry(hass: HomeAssistant, *, include_site: bool = True) -> MockConfigEntry:
    """Register an account with both legacy data and current credential options."""
    data = {
        CONF_USERNAME: USERNAME,
        CONF_PASSWORD: PASSWORD,
        CONF_ACCESS_TOKEN: ACCESS_TOKEN,
        CONF_TOKEN: REFRESH_TOKEN,
    }
    if include_site:
        data[CONF_SITE] = SITE_REFERENCE
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Private Test Account",
        unique_id=SITE_REFERENCE,
        data=data,
        options={
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: encrypt_password(hass, PASSWORD),
        },
    )
    entry.add_to_hass(hass)
    return entry


def _assert_private_logs(
    caplog: pytest.LogCaptureFixture, *additional_values: str
) -> None:
    """Check rendered integration logs while leaving unrelated HA logs alone."""
    formatter = logging.Formatter("%(message)s")
    rendered = "\n".join(
        formatter.format(record)
        for record in caplog.records
        if record.name.startswith("custom_components.frank_energie")
    )
    for value in (*PRIVATE_VALUES, *additional_values):
        assert value not in rendered


def _assert_shared_session(factory: MagicMock, hass: HomeAssistant) -> None:
    """Verify every real client receives the HA session and leaves it open."""
    session = async_get_clientsession(hass)
    assert factory.call_args_list
    for constructed in factory.call_args_list:
        assert constructed.kwargs.get("clientsession") is session
    assert not session.closed


@pytest.fixture
def account_api() -> Iterator[tuple[MagicMock, AsyncMock, AsyncMock]]:
    """Keep the real client's context manager and mock only account endpoints."""
    authentication = Authentication(ACCESS_TOKEN, REFRESH_TOKEN, None)
    login = AsyncMock(return_value=authentication)
    sites = AsyncMock(return_value=_user_sites())
    with (
        patch.object(FrankEnergie, "login", login),
        patch.object(FrankEnergie, "UserSites", sites),
        patch(
            "custom_components.frank_energie.config_flow.FrankEnergie",
            wraps=FrankEnergie,
        ) as factory,
    ):
        yield factory, login, sites


async def _start_login(hass: HomeAssistant) -> ConfigFlowResult:
    """Start a real user flow and select account authentication."""
    initial = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        initial["flow_id"], user_input={"authentication": True}
    )


@pytest.mark.usefixtures("mock_setup_entry")
async def test_account_flow_reuses_session_without_logging_private_models(
    hass: HomeAssistant,
    account_api: tuple[MagicMock, AsyncMock, AsyncMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Login and site selection share HA's session without exposing account data."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    factory, login, sites = account_api
    result = await _start_login(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    assert result["step_id"] == "site"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_SITE: SITE_REFERENCE}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == f"{STREET} 42"
    login.assert_awaited_once_with(USERNAME, PASSWORD)
    sites.assert_awaited_once()
    assert factory.call_count == 2
    _assert_shared_session(factory, hass)
    _assert_private_logs(caplog, result["options"][CONF_PASSWORD])
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (config_entries.SOURCE_REAUTH, "reauth_successful"),
        (config_entries.SOURCE_RECONFIGURE, "reconfigure_successful"),
    ],
)
async def test_credential_update_reuses_shared_session(
    hass: HomeAssistant,
    account_api: tuple[MagicMock, AsyncMock, AsyncMock],
    caplog: pytest.LogCaptureFixture,
    source: str,
    reason: str,
) -> None:
    """Credential updates verify site access without closing the shared session."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    entry = _entry(hass)
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": source, "entry_id": entry.entry_id},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "login"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        )
        await hass.async_block_till_done()
    assert result["reason"] == reason
    factory, login, sites = account_api
    login.assert_awaited_once_with(USERNAME, PASSWORD)
    sites.assert_awaited_once()
    assert factory.call_count == 2
    _assert_shared_session(factory, hass)
    _assert_private_logs(caplog, entry.options[CONF_PASSWORD])


async def test_options_flow_uses_shared_session_without_logging_entry_data(
    hass: HomeAssistant,
    account_api: tuple[MagicMock, AsyncMock, AsyncMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Opening and submitting options must not print stored tokens or credentials."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    _assert_shared_session(account_api[0], hass)
    _assert_private_logs(caplog, entry.options[CONF_PASSWORD])


@pytest.mark.parametrize("path", ["login", "reauth", "site", "options"])
@pytest.mark.parametrize(
    "error_type", [AuthException, ConnectionException, RuntimeError]
)
async def test_flow_errors_keep_shared_session_and_private_values(
    hass: HomeAssistant,
    account_api: tuple[MagicMock, AsyncMock, AsyncMock],
    caplog: pytest.LogCaptureFixture,
    path: str,
    error_type: type[Exception],
) -> None:
    """Failure paths leave the HA session usable and do not echo private errors."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    factory, login, sites = account_api
    failure = error_type(f"Provider echoed {USERNAME} {PASSWORD} {ACCESS_TOKEN}")
    if path == "site":
        sites.side_effect = failure
    else:
        login.side_effect = failure
    if path == "options":
        entry = _entry(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        )
    else:
        if path == "reauth":
            entry = _entry(hass)
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={
                    "source": config_entries.SOURCE_REAUTH,
                    "entry_id": entry.entry_id,
                },
                data=dict(entry.data),
            )
        else:
            result = await _start_login(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
        )
    expected_error = {
        AuthException: "invalid_auth",
        ConnectionException: "connection_error",
        RuntimeError: "site_retrieval_failed" if path == "site" else "unknown_error",
    }[error_type]
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}
    _assert_shared_session(factory, hass)
    _assert_private_logs(caplog)


@pytest.mark.parametrize("recovers", [True, False])
async def test_site_retries_keep_the_same_shared_session(
    hass: HomeAssistant,
    account_api: tuple[MagicMock, AsyncMock, AsyncMock],
    caplog: pytest.LogCaptureFixture,
    recovers: bool,
) -> None:
    """Retries reuse one real client and never take ownership of HA's session."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    factory, _, sites = account_api
    sites.side_effect = [
        ConnectionException(f"Provider echoed {ACCESS_TOKEN}"),
        _user_sites() if recovers else ConnectionException("still unavailable"),
    ]
    flow = ConfigFlow()
    flow.hass = hass
    flow.sign_in_data = {CONF_ACCESS_TOKEN: ACCESS_TOKEN, CONF_TOKEN: REFRESH_TOKEN}
    if recovers:
        result = await flow._fetch_user_sites_with_retry()
        assert result.deliverySites[0].reference == SITE_REFERENCE
    else:
        with pytest.raises(ConnectionException, match="after retries"):
            await flow._fetch_user_sites_with_retry()
    assert sites.await_count == 2
    assert factory.call_count == 1
    _assert_shared_session(factory, hass)
    _assert_private_logs(caplog)


@pytest.mark.parametrize("registered", [False, True])
async def test_auth_failure_uses_the_current_entry_reauth_callback(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    registered: bool,
) -> None:
    """Start HA's real reauth flow only when its config entry still exists."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    entry = (
        _entry(hass)
        if registered
        else MockConfigEntry(domain=DOMAIN, title=USERNAME, data={})
    )
    async_handle_auth_failure(hass, entry)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    if registered:
        assert len(flows) == 1
        assert flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH
        assert flows[0]["context"]["entry_id"] == entry.entry_id
    else:
        assert not flows
    _assert_private_logs(caplog, entry.title)


async def test_error_logs_retain_traceback_without_private_exception_chain(
    hass: HomeAssistant,
    account_api: tuple[MagicMock, AsyncMock, AsyncMock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep useful frames without printing a provider's message or chained cause."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    failure = RuntimeError(f"Provider echoed {USERNAME}")
    failure.__cause__ = ValueError(f"Credentials: {PASSWORD} {ACCESS_TOKEN}")
    account_api[1].side_effect = failure
    result = await _start_login(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD},
    )
    assert result["errors"] == {"base": "unknown_error"}
    error_records = [
        record
        for record in caplog.records
        if record.name.startswith("custom_components.frank_energie") and record.exc_info
    ]
    assert error_records
    rendered = "\n".join(logging.Formatter().format(record) for record in error_records)
    assert "Traceback (most recent call last)" in rendered
    assert "RuntimeError" in rendered
    _assert_private_logs(caplog)


async def test_setup_does_not_log_stored_credentials(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Exercise the real setup entry point without polling unrelated resources."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    entry = _entry(hass)
    with patch.object(FrankEnergieComponent, "setup", return_value=True):
        assert await async_integration_setup_entry(hass, entry)
    assert any(entry.entry_id in record.getMessage() for record in caplog.records)
    _assert_private_logs(caplog, entry.options[CONF_PASSWORD])


async def test_site_title_setup_does_not_log_the_delivery_address(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Keep the address in the entry title without copying it into debug logs."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    entry = _entry(hass, include_site=False)
    coordinator = MagicMock()
    coordinator.api.UserSites = AsyncMock(return_value=_user_sites())
    coordinator.async_api_call = AsyncMock(return_value=_user_sites())
    await FrankEnergieComponent(hass, entry)._select_site_reference(coordinator)
    assert entry.data[CONF_SITE] == SITE_REFERENCE
    assert entry.title == f"{STREET} 42"
    _assert_private_logs(caplog)


async def test_authenticated_fetch_does_not_log_the_response(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Return account models unchanged while logging only the endpoint name."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    api = MagicMock(spec=FrankEnergie)
    api.is_authenticated = True
    coordinator = FrankEnergieCoordinator(hass, _entry(hass), api)
    payload = SimpleNamespace(email=USERNAME, password=PASSWORD, token=ACCESS_TOKEN)

    async def account_endpoint() -> SimpleNamespace:
        """Provide a private model through the coordinator's authenticated path."""
        return payload

    assert await coordinator._fetch_authenticated(account_endpoint) is payload
    _assert_private_logs(caplog)


async def test_authenticated_fetch_failure_omits_private_provider_exception(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Retain useful endpoint frames without printing its message or cause."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    api = MagicMock(spec=FrankEnergie)
    api.is_authenticated = True
    coordinator = FrankEnergieCoordinator(hass, _entry(hass), api)
    failure = RequestException(f"Provider returned {USERNAME} {PASSWORD}")
    failure.__cause__ = ValueError(f"Refresh token: {REFRESH_TOKEN}")

    async def account_endpoint() -> None:
        """Raise a realistic provider error containing synthetic private values."""
        raise failure

    assert await coordinator._fetch_authenticated(account_endpoint) is None
    rendered = "\n".join(
        logging.Formatter().format(record)
        for record in caplog.records
        if record.name.startswith("custom_components.frank_energie")
    )
    assert "account_endpoint" in rendered
    assert "Traceback (most recent call last)" in rendered
    assert "RequestException" in rendered
    _assert_private_logs(caplog)


async def test_vehicle_fetch_does_not_log_the_vehicle_model(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Vehicle coordinates and owner information must remain outside debug logs."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    api = MagicMock(spec=FrankEnergie)
    api.is_authenticated = True
    payload = SimpleNamespace(vehicles=[SimpleNamespace(owner=USERNAME)])
    api.enode_vehicles = AsyncMock(return_value=payload)
    coordinator = FrankEnergieCoordinator(hass, _entry(hass), api)
    assert await coordinator._fetch_enode_vehicles(True) is payload
    _assert_private_logs(caplog)


async def test_battery_details_merge_does_not_log_private_model_fields(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Keep the detailed battery merge while omitting raw provider models."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    api = MagicMock(spec=FrankEnergie)
    api.is_authenticated = True
    settings = SimpleNamespace(owner=USERNAME)
    summary = SimpleNamespace(account=USERNAME)
    details = SimpleNamespace(
        smart_battery=SimpleNamespace(settings=settings),
        smart_battery_summary=summary,
    )
    api.smart_battery_details = AsyncMock(return_value=details)
    coordinator = FrankEnergieCoordinator(hass, _entry(hass), api)
    battery = SimpleNamespace(id="test-battery", settings=None, summary=None)
    assert await coordinator._fetch_battery_details(battery) is details
    assert battery.settings is settings
    assert battery.summary is summary
    _assert_private_logs(caplog)


async def test_public_resolution_change_does_not_log_other_options(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Changing resolution retains saved credentials without printing the options."""
    caplog.set_level(logging.DEBUG, logger="custom_components.frank_energie")
    api = MagicMock(spec=FrankEnergie)
    api.is_authenticated = False
    entry = _entry(hass)
    coordinator = FrankEnergieCoordinator(hass, entry, api)
    with patch.object(coordinator, "async_request_refresh"):
        await coordinator.async_set_resolution("PT60M")
    assert entry.options["resolution"] == "PT60M"
    assert entry.options[CONF_USERNAME] == USERNAME
    _assert_private_logs(caplog, entry.options[CONF_PASSWORD])
