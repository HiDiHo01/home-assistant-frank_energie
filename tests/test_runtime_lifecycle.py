"""Verify entry-scoped runtime ownership and setup task cleanup through HA."""

import asyncio
import importlib.util
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from python_frank_energie import FrankEnergie
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.frank_energie import coordinator as coordinator_module
from custom_components.frank_energie import async_unload_entry
from custom_components.frank_energie.const import CONF_SITE, DOMAIN, TIMEZONE_AMSTERDAM
from custom_components.frank_energie.coordinator import (
    FrankEnergiePriceCoordinator,
    FrankEnergieSettingsCoordinator,
    FrankEnergieStatisticsCoordinator,
    FrankEnergieBatterySessionCoordinator,
)
from tests.utils import ResponseMocks

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def _prepare_entry(
    hass: HomeAssistant,
    responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
    name: str = "runtime-site",
) -> MockConfigEntry:
    """Register a distinct public entry with complete current and future prices."""
    await hass.config.async_set_time_zone(TIMEZONE_AMSTERDAM)
    moment = datetime(2026, 7, 20, 14, 15, tzinfo=ZoneInfo(TIMEZONE_AMSTERDAM))
    freezer.move_to(moment)
    today = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    responses.add(today, [0.2] * 24, [1.2] * 24)
    responses.add(today + timedelta(days=1), [0.3] * 24, [1.3] * 24)
    responses.cyclic()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"Runtime {name}",
        unique_id=name,
        data={CONF_SITE: name},
        options={"resolution": "PT60M"},
    )
    entry.add_to_hass(hass)
    return entry


async def test_loaded_entry_has_no_global_runtime_alias(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The entry owns its runtime without publishing it in a domain dictionary."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        assert entry.runtime_data.price_coordinator.config_entry is entry
        assert entry.runtime_data.coordinator is entry.runtime_data.price_coordinator
        assert entry.entry_id not in hass.data.get(DOMAIN, {})
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    assert not hasattr(entry, "runtime_data")


async def test_reload_and_unload_keep_other_entry_runtime_alive(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Reload replaces one runtime and unload leaves the other site's data intact."""
    first = await _prepare_entry(hass, aioclient_responses, freezer, "first-site")
    assert await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()
    second = await _prepare_entry(hass, aioclient_responses, freezer, "second-site")
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    first_runtime = first.runtime_data
    second_runtime = second.runtime_data
    registry = er.async_get(hass)
    entity_ids = {
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, first.entry_id)
    }
    assert entity_ids
    assert first_runtime is not second_runtime
    assert (
        first_runtime.battery_session_coordinators
        is not second_runtime.battery_session_coordinators
    )
    try:
        assert await hass.config_entries.async_reload(first.entry_id)
        await hass.async_block_till_done()
        assert first.runtime_data is not first_runtime
        assert second.runtime_data is second_runtime
        assert second.state is ConfigEntryState.LOADED
        assert {
            entity.entity_id
            for entity in er.async_entries_for_config_entry(registry, first.entry_id)
        } == entity_ids

        assert await hass.config_entries.async_unload(first.entry_id)
        await hass.async_block_till_done()
        assert not hasattr(first, "runtime_data")
        assert second.runtime_data is second_runtime
        assert second_runtime.price_coordinator.config_entry is second
    finally:
        if first.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(first.entry_id)
        assert await hass.config_entries.async_unload(second.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize(
    "error_type", [ConfigEntryNotReady, ConfigEntryAuthFailed, RuntimeError]
)
@pytest.mark.parametrize(
    "coordinator_type", [FrankEnergieSettingsCoordinator, FrankEnergiePriceCoordinator]
)
async def test_setup_failure_discards_unpublished_runtime(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
    error_type: type[Exception],
    coordinator_type: type[
        FrankEnergieSettingsCoordinator | FrankEnergiePriceCoordinator
    ],
) -> None:
    """Retryable, auth and unexpected setup failures must leave no live runtime."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    with patch.object(
        coordinator_type,
        "async_config_entry_first_refresh",
        side_effect=error_type("initial price refresh failed"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    try:
        expected_state = (
            ConfigEntryState.SETUP_RETRY
            if error_type is ConfigEntryNotReady
            else ConfigEntryState.SETUP_ERROR
        )
        assert entry.state is expected_state
        assert not hasattr(entry, "runtime_data")
        assert entry.entry_id not in hass.data.get(DOMAIN, {})
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.parametrize(
    "coordinator_type", [FrankEnergieSettingsCoordinator, FrankEnergiePriceCoordinator]
)
async def test_cancelled_setup_discards_runtime_and_pending_refresh(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
    coordinator_type: type[
        FrankEnergieSettingsCoordinator | FrankEnergiePriceCoordinator
    ],
) -> None:
    """Cancelling setup propagates cancellation and releases its entry runtime."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    started = asyncio.Event()
    released = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_refresh() -> None:
        """Keep a real setup task in flight until its parent is cancelled."""
        started.set()
        try:
            await released.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with patch.object(
        coordinator_type,
        "async_config_entry_first_refresh",
        new=AsyncMock(side_effect=blocked_refresh),
    ):
        setup_task = hass.async_create_task(
            hass.config_entries.async_setup(entry.entry_id)
        )
        try:
            async with asyncio.timeout(3):
                await started.wait()
            setup_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await setup_task
            assert cancelled.is_set()
            assert not hasattr(entry, "runtime_data")
            assert entry.entry_id not in hass.data.get(DOMAIN, {})
        finally:
            released.set()
            if not setup_task.done():
                setup_task.cancel()
                await asyncio.gather(setup_task, return_exceptions=True)
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


async def test_initial_refresh_failure_cancels_sibling_tasks(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed resource must not leave another setup-only refresh running."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    sibling_started = asyncio.Event()
    released = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def failed_refresh() -> None:
        """Fail only after a sibling has entered its pending request."""
        await sibling_started.wait()
        raise ConfigEntryNotReady("initial refresh failed")

    async def blocked_sibling() -> None:
        """Expose whether setup cancellation reaches a concurrent resource."""
        sibling_started.set()
        try:
            await released.wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    with (
        patch.object(
            FrankEnergiePriceCoordinator,
            "async_config_entry_first_refresh",
            new=AsyncMock(side_effect=failed_refresh),
        ),
        patch.object(
            FrankEnergieStatisticsCoordinator,
            "async_config_entry_first_refresh",
            new=AsyncMock(side_effect=blocked_sibling),
        ),
    ):
        try:
            async with asyncio.timeout(3):
                assert not await hass.config_entries.async_setup(entry.entry_id)
            assert sibling_cancelled.is_set()
        finally:
            released.set()
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


async def test_failed_unload_preserves_the_active_runtime(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A refused platform unload preserves runtime for the still-active entry."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await async_unload_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is runtime
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hasattr(entry, "runtime_data")


async def test_partial_platform_setup_is_rolled_back(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A dependent-platform failure must unload already-created sensor entities."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    forward = hass.config_entries.async_forward_entry_setups
    sensor_ids: set[str] = set()

    async def fail_after_sensors(
        configured_entry: MockConfigEntry, platforms: list[Platform | str]
    ) -> None:
        """Load the actual sensor platform before failing the dependent platforms."""
        if platforms == [Platform.SENSOR]:
            await forward(configured_entry, platforms)
            sensor_ids.update(
                entity.entity_id
                for entity in er.async_entries_for_config_entry(
                    er.async_get(hass), configured_entry.entry_id
                )
            )
            return
        raise RuntimeError("dependent platform could not be set up")

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=fail_after_sensors,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    try:
        assert sensor_ids
        for entity_id in sensor_ids:
            state = hass.states.get(entity_id)
            assert state is None or state.state == STATE_UNAVAILABLE
    finally:
        await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_unloaded_entry_stops_updates_on_future_ticks(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """No coordinator refresh may survive unloading and subsequent clock events."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    prices = entry.runtime_data.price_coordinator
    with patch.object(prices, "async_request_refresh") as refresh:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        later = datetime(2026, 7, 20, 15, 0, tzinfo=ZoneInfo(TIMEZONE_AMSTERDAM))
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()
        refresh.assert_not_called()


async def test_unload_stops_polling_every_resource_including_battery_sessions(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Entry cleanup stops timers for all resources, including per-device sessions."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = entry.runtime_data
    api = AsyncMock(spec=FrankEnergie)
    api.is_authenticated = True
    api.smart_battery_sessions.return_value = None
    sessions = FrankEnergieBatterySessionCoordinator(hass, entry, api, "test-battery")
    runtime.battery_session_coordinators["test-battery"] = sessions
    await sessions.async_refresh()
    resources = (
        runtime.settings_coordinator,
        runtime.price_coordinator,
        runtime.battery_coordinator,
        runtime.charger_coordinator,
        runtime.pv_coordinator,
        runtime.vehicle_coordinator,
        runtime.statistics_coordinator,
        sessions,
    )
    with ExitStack() as stack:
        refreshes: list[AsyncMock] = []
        for resource in resources:
            # Keep each polling coordinator active as a loaded entity would.
            entry.async_on_unload(resource.async_add_listener(lambda: None))
            refreshes.append(
                stack.enter_context(patch.object(resource, "_async_update_data"))
            )
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        later = datetime(2026, 7, 22, 15, 0, tzinfo=ZoneInfo(TIMEZONE_AMSTERDAM))
        freezer.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()
        for refresh in refreshes:
            refresh.assert_not_called()
    assert not hasattr(entry, "runtime_data")


def test_coordinator_import_preserves_the_host_event_loop_policy() -> None:
    """Importing the integration must not replace the host's Windows loop policy."""
    module_name = f"{coordinator_module.__package__}._loop_policy_probe"
    spec = importlib.util.spec_from_file_location(
        module_name, coordinator_module.__file__
    )
    assert spec is not None
    assert spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    with (
        patch.dict(sys.modules, {module_name: probe}),
        patch.object(sys, "platform", "win32"),
        patch.object(asyncio, "set_event_loop_policy", create=True) as set_policy,
        patch.dict(
            vars(asyncio),
            {
                "WindowsProactorEventLoopPolicy": MagicMock(return_value=object()),
                "WindowsSelectorEventLoopPolicy": MagicMock(return_value=object()),
            },
        ),
    ):
        spec.loader.exec_module(probe)
    set_policy.assert_not_called()
