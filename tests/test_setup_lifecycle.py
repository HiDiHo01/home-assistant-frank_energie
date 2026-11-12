"""Exercise config-entry startup failure, cancellation and platform rollback."""

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from pytest_homeassistant.common import MockConfigEntry

from custom_components.frank_energie import (
    FrankEnergieComponent,
    async_unload_entry,
    coordinator as coordinator_module,
)
from custom_components.frank_energie.const import DOMAIN, TIMEZONE_AMSTERDAM
from custom_components.frank_energie.coordinator import (
    FrankEnergiePriceCoordinator,
    FrankEnergieSettingsCoordinator,
    FrankEnergieStatisticsCoordinator,
)
from tests.utils import ResponseMocks

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def _prepare_entry(
    hass: HomeAssistant,
    responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> MockConfigEntry:
    """Register a public entry backed by synthetic hourly prices."""
    await hass.config.async_set_time_zone(TIMEZONE_AMSTERDAM)
    moment = datetime(2026, 7, 20, 14, 15, tzinfo=ZoneInfo(TIMEZONE_AMSTERDAM))
    freezer.move_to(moment)
    today = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    responses.add(today, [0.2] * 24, [1.2] * 24)
    responses.add(today + timedelta(days=1), [0.3] * 24, [1.3] * 24)
    responses.cyclic()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Lifecycle test",
        unique_id="lifecycle-site",
        data={},
        options={"resolution": "PT60M"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize(
    "error_type", [ConfigEntryNotReady, ConfigEntryAuthFailed, RuntimeError]
)
@pytest.mark.parametrize(
    "coordinator_type",
    [FrankEnergieSettingsCoordinator, FrankEnergiePriceCoordinator],
)
async def test_failed_setup_discards_runtime(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
    error_type: type[Exception],
    coordinator_type: type[
        FrankEnergieSettingsCoordinator | FrankEnergiePriceCoordinator
    ],
) -> None:
    """No entry runtime or global coordinator may survive initial-refresh errors."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    with patch.object(
        coordinator_type,
        "async_config_entry_first_refresh",
        side_effect=error_type("synthetic initial refresh failure"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    try:
        assert not hasattr(entry, "runtime_data")
        assert entry.entry_id not in hass.data.get(DOMAIN, {})
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_first_refresh_failure_cancels_sibling_tasks(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A failed price refresh cancels another pending resource request."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    released = asyncio.Event()

    async def failed_refresh() -> None:
        """Fail after the sibling starts to exercise gather's failure behavior."""
        await sibling_started.wait()
        raise ConfigEntryNotReady("synthetic refresh failure")

    async def blocked_refresh() -> None:
        """Record cancellation of the concurrent statistics request."""
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
            new=AsyncMock(side_effect=blocked_refresh),
        ),
    ):
        try:
            async with asyncio.timeout(5):
                assert not await hass.config_entries.async_setup(entry.entry_id)
            assert sibling_cancelled.is_set()
            assert not hasattr(entry, "runtime_data")
        finally:
            released.set()
            await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()


async def test_cancelled_initial_refresh_discards_runtime(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Cancelling entry setup also finishes its outstanding refresh requests."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    started = asyncio.Event()
    released = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_refresh() -> None:
        """Keep price startup pending until the enclosing setup is cancelled."""
        started.set()
        try:
            await released.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with patch.object(
        FrankEnergiePriceCoordinator,
        "async_config_entry_first_refresh",
        new=AsyncMock(side_effect=blocked_refresh),
    ):
        setup_task = hass.async_create_task(
            hass.config_entries.async_setup(entry.entry_id)
        )
        try:
            async with asyncio.timeout(5):
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


@pytest.mark.parametrize("rollback_result", [True, False])
async def test_partial_platform_setup_rolls_back(
    hass: HomeAssistant, rollback_result: bool
) -> None:
    """Platform forwarding failure unloads previously initialized sensors."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    component = FrankEnergieComponent(hass, entry)
    forwarded: list[list[Platform | str]] = []

    async def forward(_entry: MockConfigEntry, platforms: list[Platform | str]) -> None:
        """Allow the sensor platform, then raise on dependent platforms."""
        forwarded.append(platforms)
        if platforms != [Platform.SENSOR]:
            raise RuntimeError("synthetic dependent platform failure")

    with (
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=forward
        ),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=rollback_result),
        ) as unload,
    ):
        with pytest.raises(RuntimeError, match="dependent platform failure"):
            await component._async_forward_entry_setups()
        assert forwarded[0] == [Platform.SENSOR]
        assert len(forwarded) == 2
        unload.assert_awaited_once()


async def test_cancelled_platform_setup_rolls_back(hass: HomeAssistant) -> None:
    """Cancellation during platform forwarding still unloads partial setup."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    component = FrankEnergieComponent(hass, entry)
    with (
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ) as unload,
    ):
        with pytest.raises(asyncio.CancelledError):
            await component._async_forward_entry_setups()
        unload.assert_awaited_once()


async def test_failed_unload_preserves_runtime(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Refused unload does not discard data for an active entry."""
    entry = await _prepare_entry(hass, aioclient_responses, freezer)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime = entry.runtime_data
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=False),
    ):
        assert not await async_unload_entry(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is runtime
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def test_coordinator_import_preserves_windows_event_loop_policy() -> None:
    """Import on Windows leaves Home Assistant's process-wide loop policy alone."""
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
