"""Exercise Smart Heating API isolation and the Home Assistant entity lifecycle."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, call, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant.common import MockConfigEntry
from python_frank_energie.exceptions import (
    AuthRequiredException,
    RateLimitException,
    RefreshTokenInvalidException,
    RequestException,
)
from python_frank_energie.models import (
    Me,
    SmartHvacConnectRequest,
    SmartHvacDevice,
    SmartHvacSummary,
)

from custom_components.frank_energie import smart_hvac
from custom_components.frank_energie.const import (
    DOMAIN,
    SERVICE_NAME_SMART_HVAC,
    SERVICE_NAME_USER,
)
from custom_components.frank_energie.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.frank_energie.smart_hvac import FrankEnergieSmartHvacCoordinator

from .test_contract_support import _api_for_contracts

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def api(freezer: FrozenDateTimeFactory) -> AsyncMock:
    """Mock the provider boundary for an authenticated entry with a selected site."""
    freezer.move_to(datetime(2026, 7, 20, 10, 15, tzinfo=UTC))
    client = _api_for_contracts(("ELECTRICITY",), 15)
    client.me.return_value = _profile(available=True)
    client.smart_hvacs.return_value = []
    client.smart_hvac_connect_requests.return_value = []
    return client


def _profile(*, available: bool, activated: bool = False) -> Me:
    """Build the released client's parsed site-level feature response."""
    return Me.from_dict(
        {
            "data": {
                "me": {"id": "fixture-user"},
                "userSmartHvac": {
                    "hasAcceptedTerms": False,
                    "isActivated": activated,
                    "isAvailableInCountry": True,
                    "isAvailableOnSite": available,
                    "userCreatedAt": "2026-07-20T10:15:00+02:00",
                    "userId": "private-fixture-user-id",
                },
            }
        }
    )


def _entry(hass: HomeAssistant, site_reference: str = "test-site") -> MockConfigEntry:
    """Add an authenticated config entry scoped to a fixture site."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Fixture site",
        data={
            "site_reference": site_reference,
            "access_token": "fixture-access",
            "token": "fixture-refresh",
        },
        unique_id=site_reference,
    )
    entry.add_to_hass(hass)
    return entry


def _equipment(device_id: str, model: str) -> SmartHvacDevice:
    """Build a parsed inventory record including private identifiers."""
    return SmartHvacDevice.from_dict(
        {
            "id": device_id,
            "brand": "Fixture Heating",
            "connectionEAN": "private-fixture-ean",
            "createdAt": "2026-07-20T11:20:00+02:00",
            "deletedAt": None,
            "deviceType": "HEAT_PUMP",
            "displayName": f"Fixture {model}",
            "needsReauthentication": False,
            "externalReference": "private-fixture-reference",
            "model": model,
            "provider": "Fixture Provider",
            "updatedAt": "2026-07-20T11:45:00+02:00",
        }
    )


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, client: AsyncMock
) -> None:
    """Load the real integration and its platforms with a mocked API client."""
    with patch("custom_components.frank_energie.FrankEnergie", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _service_entities(
    hass: HomeAssistant, entry: MockConfigEntry, service: str = SERVICE_NAME_SMART_HVAC
) -> list[er.RegistryEntry]:
    """Find the entities registered under this entry's service device."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_{service}"), config_entry_id=entry.entry_id
    )
    if device is None:
        return []
    assert device.entry_type == dr.DeviceEntryType.SERVICE
    return er.async_entries_for_device(er.async_get(hass), device.id)


def _state(hass: HomeAssistant, entity: er.RegistryEntry) -> State:
    """Read the state of a real Home Assistant registry entry."""
    state = hass.states.get(entity.entity_id)
    assert state is not None
    return state


def _sensor(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> er.RegistryEntry:
    """Find one sensor by its translation key under the Smart Heating service."""
    return next(
        sensor
        for sensor in _service_entities(hass, entry)
        if sensor.translation_key == key
    )


async def test_hvac_site_device_and_account_diagnostics(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Expose verified device and account fields with their distinct scopes."""
    entry = _entry(hass)
    api.smart_hvacs.return_value = [_equipment("private-fixture-device", "Model A")]
    api.smart_hvac_summary.return_value = SmartHvacSummary.from_dict(
        {
            "operationalStatus": "FUTURE_PROVIDER_STATUS",
            "operationalStatusTimestamp": "2026-07-20T12:00:00+02:00",
            "steeringStatus": "ACTIVE",
            "firstSessionDate": "2026-07-18",
            "lastSessionDate": "2026-07-19",
            "totalResult": 0,
        }
    )
    api.smart_hvac_connect_requests.return_value = [
        SmartHvacConnectRequest(
            "private-fixture-request",
            "Fixture Heating",
            "private-fixture-error",
            "PENDING",
        )
    ]
    await _setup(hass, entry, api)

    states = {
        sensor.translation_key: _state(hass, sensor)
        for sensor in _service_entities(hass, entry)
    }
    assert {key: state.state for key, state in states.items()} == {
        "smart_hvac_device_count": "1",
        "smart_hvac_profile_created_at": "2026-07-20T08:15:00+00:00",
        "smart_hvac_equipment": "Fixture Model A",
        "smart_hvac_operational_status": "FUTURE_PROVIDER_STATUS",
        "smart_hvac_operational_status_timestamp": "2026-07-20T10:00:00+00:00",
        "smart_hvac_steering_status": "ACTIVE",
        "smart_hvac_first_session_date": "2026-07-18",
        "smart_hvac_last_session_date": "2026-07-19",
        "smart_hvac_total_result": "0.0",
    }
    equipment = states["smart_hvac_equipment"].attributes
    assert equipment["created_at"] == "2026-07-20T09:20:00+00:00"
    assert equipment["updated_at"] == "2026-07-20T09:45:00+00:00"
    assert equipment["brand"] == "Fixture Heating"
    assert equipment["device_type"] == "HEAT_PUMP"
    assert equipment["model"] == "Model A"
    assert equipment["provider"] == "Fixture Provider"
    assert equipment["needs_reauthentication"] is False
    profile = states["smart_hvac_profile_created_at"].attributes
    assert profile["available_on_site"] is True
    assert profile["available_in_country"] is True
    assert profile["has_accepted_terms"] is False
    assert "unit_of_measurement" not in states["smart_hvac_total_result"].attributes
    assert "state_class" not in states["smart_hvac_total_result"].attributes

    account_states = {
        sensor.translation_key: _state(hass, sensor)
        for sensor in _service_entities(hass, entry, SERVICE_NAME_USER)
        if sensor.translation_key in {"smart_hvac", "smart_hvac_connection_requests"}
    }
    assert account_states["smart_hvac"].state == "off"
    assert account_states["smart_hvac_connection_requests"].state == "1"
    assert account_states["smart_hvac_connection_requests"].attributes["requests"] == [
        {"brand": "Fixture Heating", "status": "PENDING", "has_reported_error": True}
    ]
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["smart_hvac"] == {
        "last_update_success": True,
        "device_count": 1,
        "connection_request_count": 1,
    }
    assert "private-fixture-" not in repr((states, account_states, diagnostics))
    api.me.assert_awaited_once_with("test-site")
    api.smart_hvacs.assert_awaited_once_with("test-site")
    api.smart_hvac_summary.assert_awaited_once_with("private-fixture-device")
    api.smart_hvac_connect_requests.assert_awaited_once()
    api.smart_hvac.assert_not_awaited()
    api.smart_hvac_status.assert_not_awaited()


async def test_hvac_inventory_changes_and_recovery(
    hass: HomeAssistant, api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Discover devices after setup and remove them only on valid inventory."""
    entry = _entry(hass)
    caplog.set_level(logging.DEBUG, logger=smart_hvac.__name__)
    await _setup(hass, entry, api)
    coordinator = entry.runtime_data.smart_hvac_coordinator
    count = _sensor(hass, entry, "smart_hvac_device_count")
    assert _state(hass, count).state == "0"

    first = _equipment("private-fixture-first", "Model A")
    second = _equipment("private-fixture-second", "Model B")
    api.smart_hvacs.return_value = [first, second]
    api.smart_hvac_summary.side_effect = [
        RequestException("private-fixture-first"),
        None,
    ]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, count).state == "2"
    summaries = [
        _state(hass, sensor).state
        for sensor in _service_entities(hass, entry)
        if sensor.translation_key == "smart_hvac_operational_status"
    ]
    assert sorted(summaries) == sorted([STATE_UNAVAILABLE, STATE_UNKNOWN])
    assert "private-fixture-first" not in caplog.text
    first_sensor = next(
        sensor
        for sensor in _service_entities(hass, entry)
        if sensor.translation_key == "smart_hvac_equipment"
        and _state(hass, sensor).state == "Fixture Model A"
    )

    api.smart_hvacs.side_effect = RequestException("private-fixture-inventory")
    await coordinator.async_refresh()
    assert _state(hass, count).state == STATE_UNAVAILABLE
    assert len(_service_entities(hass, entry)) == 16
    assert entry.runtime_data.price_coordinator.last_update_success
    assert entry.runtime_data.settings_coordinator.last_update_success
    assert "private-fixture-inventory" not in caplog.text

    api.smart_hvacs.side_effect = None
    api.smart_hvacs.return_value = [
        replace(first, deleted_at=datetime.now(UTC)),
        second,
    ]
    api.smart_hvac_summary.side_effect = None
    api.smart_hvac_summary.return_value = SmartHvacSummary(operational_status="ACTIVE")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, count).state == "1"
    assert len(_service_entities(hass, entry)) == 9
    assert (
        _state(hass, _sensor(hass, entry, "smart_hvac_equipment")).state
        == "Fixture Model B"
    )
    assert (
        _state(hass, _sensor(hass, entry, "smart_hvac_operational_status")).state
        == "ACTIVE"
    )
    assert api.smart_hvac_summary.await_args_list == [
        call("private-fixture-first"),
        call("private-fixture-second"),
        call("private-fixture-second"),
    ]
    assert hass.states.get(first_sensor.entity_id) is None

    api.smart_hvacs.return_value = [first, second]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, first_sensor).state == "Fixture Model A"


async def test_hvac_entries_isolate_site_data_and_reload(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Keep two sites isolated even when their inventories reuse an equipment ID."""
    other_api = _api_for_contracts(("ELECTRICITY",), 15)
    other_api.me.return_value = _profile(available=False)
    other_api.smart_hvacs.return_value = []
    other_api.smart_hvac_connect_requests.return_value = []
    primary = _entry(hass)
    await _setup(hass, primary, api)
    other = _entry(hass, "other-site")
    await _setup(hass, other, other_api)
    assert _service_entities(hass, other) == []
    api.smart_hvacs.return_value = [_equipment("shared-fixture-id", "Model A")]
    other_api.smart_hvacs.return_value = [_equipment("shared-fixture-id", "Model B")]
    old_coordinator = primary.runtime_data.smart_hvac_coordinator
    await old_coordinator.async_refresh()
    await other.runtime_data.smart_hvac_coordinator.async_refresh()
    await hass.async_block_till_done()

    primary_sensor = _sensor(hass, primary, "smart_hvac_equipment")
    other_sensor = _sensor(hass, other, "smart_hvac_equipment")
    assert primary_sensor.device_id != other_sensor.device_id
    assert primary_sensor.unique_id != other_sensor.unique_id
    assert _state(hass, primary_sensor).state == "Fixture Model A"
    assert _state(hass, other_sensor).state == "Fixture Model B"
    api.smart_hvacs.assert_awaited_with("test-site")
    other_api.smart_hvacs.assert_awaited_with("other-site")

    with patch("custom_components.frank_energie.FrankEnergie", return_value=api):
        assert await hass.config_entries.async_reload(primary.entry_id)
        await hass.async_block_till_done()
    assert (
        _sensor(hass, primary, "smart_hvac_equipment").entity_id
        == primary_sensor.entity_id
    )
    assert (
        _sensor(hass, other, "smart_hvac_equipment").entity_id == other_sensor.entity_id
    )
    api.smart_hvacs.reset_mock()
    await old_coordinator.async_refresh()
    api.smart_hvacs.assert_not_awaited()


async def test_hvac_anonymous_entry(
    hass: HomeAssistant,
    frank_energie_setup: Callable[[], Awaitable[MockConfigEntry]],
) -> None:
    """Anonymous price entries never set up HVAC polling or a service device."""
    entry = await frank_energie_setup()
    assert entry.runtime_data.smart_hvac_coordinator is None
    assert _service_entities(hass, entry) == []


async def test_hvac_rate_limit_preserves_other_reads(
    hass: HomeAssistant, api: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop further calls on rate limiting while preserving successful reads."""
    coordinator = FrankEnergieSmartHvacCoordinator(hass, _entry(hass), api, "test-site")
    monkeypatch.setattr(smart_hvac, "monotonic", lambda: 0.0)
    api.smart_hvacs.return_value = [
        _equipment("first", "Model A"),
        _equipment("second", "Model B"),
        _equipment("third", "Model C"),
    ]
    summary = SmartHvacSummary(steering_status="ACTIVE")
    api.smart_hvac_summary.side_effect = [
        summary,
        RateLimitException("SmartHvacSummary", retry_after=1800),
        summary,
        summary,
        summary,
    ]
    await coordinator.async_refresh()
    assert coordinator.data.summaries == {"first": summary}
    assert coordinator.data.requests == ()
    assert api.smart_hvac_summary.await_args_list == [call("first"), call("second")]
    monkeypatch.setattr(smart_hvac, "monotonic", lambda: 1799.0)
    await coordinator.async_refresh()
    api.smart_hvacs.assert_awaited_once()
    monkeypatch.setattr(smart_hvac, "monotonic", lambda: 1800.0)
    await coordinator.async_refresh()
    assert coordinator.data.summaries == dict.fromkeys(
        ("first", "second", "third"), summary
    )


async def test_hvac_maintenance_window(
    hass: HomeAssistant, api: AsyncMock, freezer: FrozenDateTimeFactory
) -> None:
    """Preserve the last snapshot when maintenance prevents an API refresh."""
    coordinator = FrankEnergieSmartHvacCoordinator(hass, _entry(hass), api, "test-site")
    await coordinator.async_refresh()
    snapshot = coordinator.data

    freezer.move_to(datetime(2026, 7, 21, 0, 15, tzinfo=UTC))
    await coordinator.async_refresh()

    assert coordinator.data is snapshot
    api.smart_hvacs.assert_awaited_once()
    api.smart_hvac_connect_requests.assert_awaited_once()


@pytest.mark.parametrize("error", [AuthRequiredException, RefreshTokenInvalidException])
async def test_hvac_authentication_failure(
    hass: HomeAssistant,
    api: AsyncMock,
    caplog: pytest.LogCaptureFixture,
    error: type[Exception],
) -> None:
    """Send actual auth failures through Home Assistant's reauthentication path."""
    entry = _entry(hass)
    api.smart_hvacs.side_effect = error("private-fixture-authentication")
    with patch("custom_components.frank_energie.FrankEnergie", return_value=api):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    )
    assert "private-fixture-authentication" not in caplog.text
    api.smart_hvac_connect_requests.assert_not_awaited()
