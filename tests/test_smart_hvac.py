"""Exercise Smart Heating API isolation and the Home Assistant entity lifecycle."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from unittest.mock import AsyncMock, call, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CURRENCY_EURO, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.icon import async_get_icons
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
from custom_components.frank_energie.helpers import service_device_info
from custom_components.frank_energie.smart_hvac import FrankEnergieSmartHvacCoordinator
from custom_components.frank_energie.smart_hvac_sensor import _DEVICE_SENSORS

from .test_contract_support import _api_for_contracts

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def api(freezer: FrozenDateTimeFactory) -> AsyncMock:
    """Mock the provider boundary for an authenticated entry with a selected site."""
    freezer.move_to(datetime(2026, 7, 20, 10, 15, tzinfo=UTC))
    client = _api_for_contracts(("ELECTRICITY",), 15)
    client.me.return_value = _profile(available=True, activated=True)
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


def _equipment_devices(
    hass: HomeAssistant, entry: MockConfigEntry
) -> list[dr.DeviceEntry]:
    """Find this site's equipment registrations, including their entry types."""
    prefix = f"{entry.entry_id}_smart_hvac_device_"
    return [
        device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        if any(
            domain == DOMAIN and identifier.startswith(prefix)
            for domain, identifier in device.identifiers
        )
    ]


def _state(hass: HomeAssistant, entity: er.RegistryEntry) -> State:
    """Read the state of a real Home Assistant registry entry."""
    state = hass.states.get(entity.entity_id)
    assert state is not None
    return state


def _sensor(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> er.RegistryEntry:
    """Find a sensor with a unique translation key in this config entry."""
    return next(
        sensor
        for sensor in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if sensor.translation_key == key
    )


async def test_hvac_steering_status_enum_and_unknown_provider_values(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Localize known HVAC status keys without changing unknown upstream states."""
    entry = _entry(hass)
    api.smart_hvacs.return_value = [_equipment("fixture-device", "Model A")]
    api.smart_hvac_summary.return_value = SmartHvacSummary(steering_status="INACTIVE")
    await _setup(hass, entry, api)

    steering_sensor = _sensor(hass, entry, "smart_hvac_steering_status")
    unique_id = steering_sensor.unique_id
    state = _state(hass, steering_sensor)
    assert state.state == "inactive"
    assert state.attributes["options"] == ["active", "inactive"]

    coordinator = entry.runtime_data.smart_hvac_coordinator
    assert coordinator.data.summaries["fixture-device"].steering_status == "INACTIVE"

    api.smart_hvac_summary.return_value = SmartHvacSummary(
        steering_status="NEW_PROVIDER_STATUS"
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = _state(hass, steering_sensor)
    assert state.state == "NEW_PROVIDER_STATUS"
    assert state.attributes["options"] == [
        "active",
        "inactive",
        "NEW_PROVIDER_STATUS",
    ]
    assert _sensor(hass, entry, "smart_hvac_steering_status").unique_id == unique_id

    api.smart_hvac_summary.return_value = SmartHvacSummary(steering_status="ACTIVE")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = _state(hass, steering_sensor)
    assert state.state == "active"
    assert state.attributes["options"] == ["active", "inactive"]


@pytest.mark.parametrize(
    ("total_result", "expected_state"),
    [
        (12.3456, "12.3456"),
        (-8.25, "-8.25"),
        (0.0, "0.0"),
        (None, STATE_UNKNOWN),
    ],
)
async def test_hvac_total_result_eur_display_metadata(
    hass: HomeAssistant,
    api: AsyncMock,
    total_result: float | None,
    expected_state: str,
) -> None:
    """Present HVAC results in EUR while retaining raw numeric precision."""
    description = next(
        description
        for description in _DEVICE_SENSORS
        if description.key == "smart_hvac_total_result"
    )
    assert description.device_class is SensorDeviceClass.MONETARY
    assert description.native_unit_of_measurement == CURRENCY_EURO
    assert description.suggested_display_precision == 2
    assert description.state_class is None

    summary = SmartHvacSummary.from_dict({"totalResult": total_result})
    assert description.summary_value is not None
    assert description.summary_value(summary) == total_result

    entry = _entry(hass)
    api.smart_hvacs.return_value = [_equipment("fixture-device", "Model A")]
    api.smart_hvac_summary.return_value = summary
    await _setup(hass, entry, api)

    result_sensor = _sensor(hass, entry, "smart_hvac_total_result")
    initial_unique_id = result_sensor.unique_id
    state = _state(hass, result_sensor)
    assert state.state == expected_state
    assert state.attributes["unit_of_measurement"] == CURRENCY_EURO
    assert state.attributes["device_class"] == "monetary"
    assert "state_class" not in state.attributes
    assert (
        entry.runtime_data.smart_hvac_coordinator.data.summaries[
            "fixture-device"
        ].total_result
        == total_result
    )

    await entry.runtime_data.smart_hvac_coordinator.async_refresh()
    await hass.async_block_till_done()
    assert (
        _sensor(hass, entry, "smart_hvac_total_result").unique_id == initial_unique_id
    )


async def test_hvac_site_device_and_account_diagnostics(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Expose device and account fields and icons with their distinct scopes."""
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

    (device,) = _equipment_devices(hass, entry)
    assert device.entry_type is None
    assert device.name == "Fixture Model A"
    assert device.manufacturer == "Fixture Heating"
    assert device.model == "Model A"
    assert "private-fixture-" not in repr(device.identifiers)
    service_sensors = _service_entities(hass, entry)
    assert {sensor.translation_key for sensor in service_sensors} == {
        "smart_hvac_device_count",
        "smart_hvac_profile_created_at",
    }
    device_sensors = er.async_entries_for_device(er.async_get(hass), device.id)
    states = {
        sensor.translation_key: _state(hass, sensor)
        for sensor in (*service_sensors, *device_sensors)
    }
    assert {key: state.state for key, state in states.items()} == {
        "smart_hvac_device_count": "1",
        "smart_hvac_profile_created_at": "2026-07-20T08:15:00+00:00",
        "smart_hvac_equipment": "Fixture Model A",
        "smart_hvac_operational_status": "FUTURE_PROVIDER_STATUS",
        "smart_hvac_operational_status_timestamp": "2026-07-20T10:00:00+00:00",
        "smart_hvac_steering_status": "active",
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
    assert (
        states["smart_hvac_total_result"].attributes["unit_of_measurement"]
        == CURRENCY_EURO
    )
    assert states["smart_hvac_total_result"].attributes["device_class"] == "monetary"
    assert "state_class" not in states["smart_hvac_total_result"].attributes

    account_states = {
        sensor.translation_key: _state(hass, sensor)
        for sensor in _service_entities(hass, entry, SERVICE_NAME_USER)
        if sensor.translation_key in {"smart_hvac", "smart_hvac_connection_requests"}
    }
    assert account_states["smart_hvac"].state == "on"
    assert account_states["smart_hvac"].attributes["icon"] == "mdi:heat-pump"
    assert account_states["smart_hvac_connection_requests"].state == "1"
    assert account_states["smart_hvac_connection_requests"].attributes["requests"] == [
        {"brand": "Fixture Heating", "status": "PENDING", "has_reported_error": True}
    ]
    hvac_icons = (await async_get_icons(hass, "entity", (DOMAIN,)))[DOMAIN]["sensor"]
    for key in states.keys() | {"smart_hvac_connection_requests"}:
        assert hvac_icons[key]["default"].startswith("mdi:")

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
    assert _equipment_devices(hass, entry) == []

    first = _equipment("private-fixture-first", "Model A")
    second = _equipment("private-fixture-second", "Model B")
    api.smart_hvacs.return_value = [first, second, first]
    api.smart_hvac_summary.side_effect = [
        RequestException("private-fixture-first"),
        None,
    ]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, count).state == "2"
    devices = _equipment_devices(hass, entry)
    assert len(devices) == 2
    assert {device.name for device in devices} == {"Fixture Model A", "Fixture Model B"}
    assert all(device.entry_type is None for device in devices)
    registry = er.async_get(hass)
    equipment_sensors = [
        sensor
        for device in devices
        for sensor in er.async_entries_for_device(registry, device.id)
    ]
    summaries = [
        _state(hass, sensor).state
        for sensor in equipment_sensors
        if sensor.translation_key == "smart_hvac_operational_status"
    ]
    assert sorted(summaries) == sorted([STATE_UNAVAILABLE, STATE_UNKNOWN])
    assert "private-fixture-first" not in caplog.text
    first_sensor = next(
        sensor
        for sensor in equipment_sensors
        if sensor.translation_key == "smart_hvac_equipment"
        and _state(hass, sensor).state == "Fixture Model A"
    )

    api.smart_hvacs.side_effect = RequestException("private-fixture-inventory")
    await coordinator.async_refresh()
    assert _state(hass, count).state == STATE_UNAVAILABLE
    assert {device.id for device in _equipment_devices(hass, entry)} == {
        device.id for device in devices
    }
    assert all(
        registry.async_get(sensor.entity_id) == sensor for sensor in equipment_sensors
    )
    assert _state(hass, first_sensor).state == STATE_UNAVAILABLE
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
    (remaining_device,) = _equipment_devices(hass, entry)
    assert remaining_device.name == "Fixture Model B"
    assert remaining_device in devices
    assert len(_service_entities(hass, entry)) == 2
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


async def test_hvac_deactivation_prunes_only_missing_equipment(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Remove stale devices while inactive, but discover new devices on reactivation."""
    first = _equipment("existing-device", "Model A")
    removed = _equipment("removed-device", "Model B")
    newcomer = _equipment("new-device", "Model C")
    api.smart_hvacs.return_value = [first, removed]
    entry = _entry(hass)
    await _setup(hass, entry, api)
    original = next(
        sensor
        for sensor in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if sensor.translation_key == "smart_hvac_equipment"
        and _state(hass, sensor).state == "Fixture Model A"
    )
    assert len(_equipment_devices(hass, entry)) == 2

    api.me.return_value = _profile(available=True, activated=False)
    await entry.runtime_data.settings_coordinator.async_refresh()
    api.smart_hvacs.return_value = [first, newcomer]
    await entry.runtime_data.smart_hvac_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(_equipment_devices(hass, entry)) == 1
    assert _state(hass, original).state == "Fixture Model A"
    assert {device.name for device in _equipment_devices(hass, entry)} == {
        "Fixture Model A"
    }
    assert {
        sensor.translation_key
        for sensor in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if sensor.translation_key == "smart_hvac_equipment"
    } == {"smart_hvac_equipment"}

    api.me.return_value = _profile(available=True, activated=True)
    await entry.runtime_data.settings_coordinator.async_refresh()
    await hass.async_block_till_done()
    assert {device.name for device in _equipment_devices(hass, entry)} == {
        "Fixture Model A",
        "Fixture Model C",
    }
    assert er.async_get(hass).async_get(original.entity_id).id == original.id


async def test_hvac_inactive_reload_prunes_missing_equipment(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Clean registrations after reload when activation is false and inventory is empty."""
    api.smart_hvacs.return_value = [_equipment("existing-device", "Model A")]
    entry = _entry(hass)
    await _setup(hass, entry, api)
    previous = _sensor(hass, entry, "smart_hvac_equipment")
    assert len(_equipment_devices(hass, entry)) == 1

    api.me.return_value = _profile(available=True, activated=False)
    api.smart_hvacs.return_value = []
    with patch("custom_components.frank_energie.FrankEnergie", return_value=api):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert _equipment_devices(hass, entry) == []
    assert er.async_get(hass).async_get(previous.entity_id) is None


async def test_hvac_null_collections_differ_from_failed_reads(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Expose null responses as unknown and unsuccessful reads as unavailable."""
    api.smart_hvacs.return_value = None
    api.smart_hvac_connect_requests.return_value = None
    entry = _entry(hass)
    await _setup(hass, entry, api)

    coordinator = entry.runtime_data.smart_hvac_coordinator
    count = _sensor(hass, entry, "smart_hvac_device_count")
    assert _state(hass, count).state == STATE_UNKNOWN
    assert coordinator.data.devices is None
    assert coordinator.data.requests is None
    assert coordinator.data.devices_fetched
    assert coordinator.data.requests_fetched

    api.smart_hvacs.side_effect = RequestException("private-inventory-error")
    api.smart_hvac_connect_requests.side_effect = RequestException(
        "private-requests-error"
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, count).state == STATE_UNAVAILABLE
    assert not coordinator.data.devices_fetched
    assert not coordinator.data.requests_fetched

    api.smart_hvacs.side_effect = None
    api.smart_hvac_connect_requests.side_effect = None
    api.smart_hvacs.return_value = []
    api.smart_hvac_connect_requests.return_value = []
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, count).state == "0"
    assert coordinator.data.devices == {}
    assert coordinator.data.requests == ()
    assert coordinator.data.devices_fetched
    assert coordinator.data.requests_fetched
    assert (
        _state(hass, _sensor(hass, entry, "smart_hvac_connection_requests")).state
        == "0"
    )


async def test_hvac_updates_device_metadata_without_duplicates(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Update a physical device in place and reuse it on repeated refreshes."""
    entry = _entry(hass)
    api.smart_hvacs.return_value = [SmartHvacDevice(id="fixture-device")]
    await _setup(hass, entry, api)
    (original_device,) = _equipment_devices(hass, entry)
    assert original_device.name == "HVAC"
    assert original_device.manufacturer is None
    assert original_device.model is None
    original_sensor = _sensor(hass, entry, "smart_hvac_equipment")

    api.smart_hvacs.return_value = [_equipment("fixture-device", "Model A")]
    coordinator = entry.runtime_data.smart_hvac_coordinator
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    (updated_device,) = _equipment_devices(hass, entry)
    assert updated_device.id == original_device.id
    assert updated_device.identifiers == original_device.identifiers
    assert updated_device.name == "Fixture Model A"
    assert updated_device.manufacturer == "Fixture Heating"
    assert updated_device.model == "Model A"
    assert _sensor(hass, entry, "smart_hvac_equipment") == original_sensor
    assert _state(hass, original_sensor).state == "Fixture Model A"


async def test_hvac_existing_sensors_move_to_equipment_device(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Reattach sensors from the earlier service grouping with their IDs intact."""
    entry = _entry(hass)
    entry_id = entry.entry_id
    api.smart_hvacs.return_value = [_equipment("fixture-device", "Model A")]
    device_registry = dr.async_get(hass)
    service_info = service_device_info(entry_id, SERVICE_NAME_SMART_HVAC)
    service = device_registry.async_get_or_create(
        config_entry_id=entry_id, **service_info
    )
    registry = er.async_get(hass)
    fingerprint = sha256(f"{entry_id}:fixture-device".encode()).hexdigest()
    device_keys = (
        "smart_hvac_equipment",
        "smart_hvac_operational_status",
        "smart_hvac_steering_status",
        "smart_hvac_operational_status_timestamp",
        "smart_hvac_first_session_date",
        "smart_hvac_last_session_date",
        "smart_hvac_total_result",
    )
    service_keys = ("smart_hvac_device_count", "smart_hvac_profile_created_at")
    unique_ids = {key: f"{entry_id}_{key}" for key in service_keys} | {
        key: f"{entry_id}_smart_hvac_equipment_{fingerprint}_{key}"
        for key in device_keys
    }
    previous_sensors = {
        key: registry.async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            config_entry=entry,
            device_id=service.id,
            suggested_object_id=f"previous_{key}",
        )
        for key, unique_id in unique_ids.items()
    }

    await _setup(hass, entry, api)

    (device,) = _equipment_devices(hass, entry)
    assert device.id != service.id
    for key, previous in previous_sensors.items():
        current = _sensor(hass, entry, key)
        assert current.id == previous.id
        assert current.entity_id == previous.entity_id
        assert current.unique_id == previous.unique_id
        assert current.device_id == (device.id if key in device_keys else service.id)
    assert (
        device_registry.async_get(service.id).entry_type == dr.DeviceEntryType.SERVICE
    )


async def test_hvac_waits_for_confirmed_site_activation(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Register HVAC sensors when Frank confirms activation during a refresh."""
    api.me.return_value = _profile(available=True, activated=False)
    api.smart_hvacs.return_value = [_equipment("fixture-device", "Model A")]
    entry = _entry(hass)
    await _setup(hass, entry, api)

    registry = dr.async_get(hass)
    service_identifier = (DOMAIN, f"{entry.entry_id}_{SERVICE_NAME_SMART_HVAC}")
    assert (
        registry.async_get_device_by_identifier(
            service_identifier, config_entry_id=entry.entry_id
        )
        is None
    )
    assert _equipment_devices(hass, entry) == []
    user_keys = {
        sensor.translation_key
        for sensor in _service_entities(hass, entry, SERVICE_NAME_USER)
    }
    assert "smart_hvac" in user_keys
    assert "smart_hvac_connection_requests" not in user_keys

    api.me.return_value = _profile(available=False, activated=True)
    await entry.runtime_data.settings_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(_equipment_devices(hass, entry)) == 1
    assert _state(hass, _sensor(hass, entry, "smart_hvac_device_count")).state == "1"
    assert (
        _state(hass, _sensor(hass, entry, "smart_hvac_equipment")).state
        == "Fixture Model A"
    )
    assert "smart_hvac_connection_requests" in {
        sensor.translation_key
        for sensor in _service_entities(hass, entry, SERVICE_NAME_USER)
    }


async def test_hvac_entries_isolate_site_data_and_reload(
    hass: HomeAssistant, api: AsyncMock
) -> None:
    """Keep two sites isolated even when their inventories reuse an equipment ID."""
    other_api = _api_for_contracts(("ELECTRICITY",), 15)
    other_api.me.return_value = _profile(available=False, activated=True)
    other_api.smart_hvacs.return_value = []
    other_api.smart_hvac_connect_requests.return_value = []
    primary = _entry(hass)
    await _setup(hass, primary, api)
    other = _entry(hass, "other-site")
    await _setup(hass, other, other_api)
    assert _state(hass, _sensor(hass, other, "smart_hvac_device_count")).state == "0"
    api.smart_hvacs.return_value = [_equipment("shared-fixture-id", "Model A")]
    other_api.smart_hvacs.return_value = [_equipment("shared-fixture-id", "Model B")]
    old_coordinator = primary.runtime_data.smart_hvac_coordinator
    await old_coordinator.async_refresh()
    await other.runtime_data.smart_hvac_coordinator.async_refresh()
    await hass.async_block_till_done()

    primary_sensor = _sensor(hass, primary, "smart_hvac_equipment")
    other_sensor = _sensor(hass, other, "smart_hvac_equipment")
    (primary_device,) = _equipment_devices(hass, primary)
    (other_device,) = _equipment_devices(hass, other)
    assert primary_sensor.device_id == primary_device.id
    assert other_sensor.device_id == other_device.id
    assert primary_device.identifiers.isdisjoint(other_device.identifiers)
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
    assert _equipment_devices(hass, primary)[0].id == primary_device.id
    assert (
        _sensor(hass, other, "smart_hvac_equipment").entity_id == other_sensor.entity_id
    )
    api.smart_hvacs.reset_mock()
    await old_coordinator.async_refresh()
    api.smart_hvacs.assert_not_awaited()

    api.smart_hvacs.return_value = []
    with patch("custom_components.frank_energie.FrankEnergie", return_value=api):
        assert await hass.config_entries.async_reload(primary.entry_id)
        await hass.async_block_till_done()
    assert er.async_get(hass).async_get(primary_sensor.entity_id) is None
    assert _equipment_devices(hass, primary) == []
    assert _equipment_devices(hass, other)[0].id == other_device.id
    assert _state(hass, _sensor(hass, primary, "smart_hvac_device_count")).state == "0"
    assert _state(hass, other_sensor).state == "Fixture Model B"


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


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [
        (None, 900),
        (60, 900),
        (1800, 1800),
        (172800, 86400),
        (float("nan"), 900),
        (float("inf"), 900),
        (-1, 900),
    ],
)
async def test_hvac_retry_after_is_bounded(
    hass: HomeAssistant,
    api: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    retry_after: float | None,
    expected_delay: int,
) -> None:
    """Limit malformed and excessive retry hints without blocking recovery."""
    clock = [0.0]
    monkeypatch.setattr(smart_hvac, "monotonic", lambda: clock[0])
    coordinator = FrankEnergieSmartHvacCoordinator(hass, _entry(hass), api, "test-site")
    api.smart_hvacs.side_effect = RateLimitException(
        "SmartHvacs", retry_after=retry_after
    )

    await coordinator.async_refresh()
    assert coordinator._retry_at == expected_delay
    clock[0] = expected_delay - 1
    await coordinator.async_refresh()
    api.smart_hvacs.assert_awaited_once()

    clock[0] = float(expected_delay)
    api.smart_hvacs.side_effect = None
    await coordinator.async_refresh()
    assert api.smart_hvacs.await_count == 2
    assert coordinator.data.devices == {}


async def test_hvac_rate_limited_requests_preserve_stale_summaries(
    hass: HomeAssistant, api: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep skipped summaries cached, but never expose them as fresh readings."""
    monkeypatch.setattr(smart_hvac, "monotonic", lambda: 0.0)
    api.smart_hvacs.return_value = [_equipment("fixture-device", "Model A")]
    initial_summary = SmartHvacSummary(steering_status="ACTIVE")
    api.smart_hvac_summary.return_value = initial_summary
    entry = _entry(hass)
    await _setup(hass, entry, api)

    coordinator = entry.runtime_data.smart_hvac_coordinator
    steering_sensor = _sensor(hass, entry, "smart_hvac_steering_status")
    assert _state(hass, steering_sensor).state == "active"

    api.smart_hvac_summary.reset_mock()
    api.smart_hvac_connect_requests.side_effect = RateLimitException(
        "SmartHvacConnectRequests", retry_after=1800
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.data.summaries == {"fixture-device": initial_summary}
    assert coordinator.data.stale_summaries == frozenset({"fixture-device"})
    assert _state(hass, steering_sensor).state == STATE_UNAVAILABLE
    api.smart_hvac_summary.assert_not_awaited()

    monkeypatch.setattr(smart_hvac, "monotonic", lambda: 1800.0)
    api.smart_hvac_connect_requests.side_effect = None
    api.smart_hvac_summary.side_effect = RequestException(
        "private-fixture-summary-failure"
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.data.summaries == {}
    assert not coordinator.data.stale_summaries
    assert _state(hass, steering_sensor).state == STATE_UNAVAILABLE
    api.smart_hvac_summary.assert_awaited_once_with("fixture-device")

    api.smart_hvac_summary.side_effect = None
    api.smart_hvac_summary.return_value = SmartHvacSummary(steering_status="RECOVERED")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.data.summaries["fixture-device"].steering_status == "RECOVERED"
    assert not coordinator.data.stale_summaries
    assert _state(hass, steering_sensor).state == "RECOVERED"


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
