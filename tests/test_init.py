"""Test the Frank Energie integration setup and teardown logic."""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime
import zoneinfo

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from custom_components.frank_energie.const import DOMAIN, CONF_COORDINATOR
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tests.utils import ResponseMocks

pytestmark = pytest.mark.asyncio


async def test_setup_entry_success(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer,
    enable_custom_integrations,
) -> None:
    """Test successful setup of a config entry."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = zoneinfo.ZoneInfo("Europe/Amsterdam")
    now = datetime.now(tz).replace(hour=10, minute=15, second=0, microsecond=0)
    freezer.move_to(now)

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    aioclient_responses.add(
        start_of_day,
        [0.2] * 24,
        [1.23] * 24,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "username": "test@example.com",
        },
        entry_id="1234abcd",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert entry.state is ConfigEntryState.LOADED
    assert hass.data[DOMAIN][entry.entry_id][CONF_COORDINATOR]


async def test_setup_entry_auth_failure(
    hass: HomeAssistant,
    enable_custom_integrations,
) -> None:
    """Test setup fails if authentication fails."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "username": "test@example.com",
            "access_token": "expired_token",
            "token": "expired_refresh_token",
        },
        entry_id="1234abcd",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.frank_energie.FrankEnergie") as mock_api:
        api_instance = mock_api.return_value
        api_instance.is_authenticated = True
        api_instance.UserSites = AsyncMock(side_effect=Exception("Not authorized"))
        api_instance.login = AsyncMock(side_effect=Exception("Token renewal failed"))

        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert result is False
        assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_unload_entry(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer,
    enable_custom_integrations,
) -> None:
    """Test successful unload of a config entry."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    tz = zoneinfo.ZoneInfo("Europe/Amsterdam")
    now = datetime.now(tz).replace(hour=10, minute=15, second=0, microsecond=0)
    freezer.move_to(now)

    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    aioclient_responses.add(
        start_of_day,
        [0.2] * 24,
        [1.23] * 24,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "username": "test@example.com",
        },
        entry_id="1234abcd",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert result is True

    unload_result = await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert unload_result is True
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.entry_id not in hass.data[DOMAIN]
