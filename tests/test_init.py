"""Test the Frank Energie integration setup and teardown logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import zoneinfo

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from custom_components.frank_energie.const import DOMAIN, CONF_COORDINATOR
from custom_components.frank_energie.helpers import encrypt_password
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
            "token": "expired_refresh_token",  # NOSONAR
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


async def test_encryption_decryption(hass: HomeAssistant) -> None:
    """Test encrypting and decrypting passwords."""
    from custom_components.frank_energie.helpers import (
        encrypt_password,
        decrypt_password,
    )

    # Ensure UUID exists
    hass.data["core.uuid"] = "test_uuid_123"

    password = "secret_password_123"
    encrypted = encrypt_password(hass, password)
    assert encrypted != password
    assert encrypted.startswith("gAAAA")

    decrypted = decrypt_password(hass, encrypted)
    assert decrypted == password

    # Test plaintext fallback
    assert decrypt_password(hass, "my_plain_password") == "my_plain_password"
    assert decrypt_password(hass, "") == ""


async def test_setup_entry_recovery_via_renew(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer,
    enable_custom_integrations,
) -> None:
    """Test setup recovers using token renewal when AuthException is raised."""
    from python_frank_energie.exceptions import AuthException

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
            "access_token": "expired_token",
            "token": "expired_refresh_token",
        },
        options={
            "username": "user@example.com",
            "password": encrypt_password(hass, "correct_password"),
        },
        entry_id="setup_renew_test",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.frank_energie.FrankEnergie") as mock_api:
        api_instance = mock_api.return_value
        api_instance.__aenter__.return_value = api_instance
        api_instance.is_authenticated = True

        # First call to UserSites fails, subsequent calls succeed
        mock_user_sites = MagicMock()
        mock_address = MagicMock()
        mock_address.street = "Main Street"
        mock_address.houseNumber = "123"
        mock_address.houseNumberAddition = ""
        mock_site = MagicMock()
        mock_site.reference = "site_ref_123"
        mock_site.address = mock_address
        mock_user_sites.deliverySites = [mock_site]

        calls = []

        def user_sites_side_effect(*args, **kwargs):
            if not calls:
                calls.append(True)
                raise AuthException("Expired")
            return mock_user_sites

        api_instance.UserSites = AsyncMock(side_effect=user_sites_side_effect)

        # Mock successful token renewal
        mock_tokens = MagicMock()
        mock_tokens.authToken = "new_auth_token"
        mock_tokens.refreshToken = "new_refresh_token"
        api_instance.renew_token = AsyncMock(return_value=mock_tokens)

        with patch(
            "custom_components.frank_energie.FrankEnergieCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            result = await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        assert result is True
        assert entry.state is ConfigEntryState.LOADED
        assert entry.data["access_token"] == "new_auth_token"


async def test_setup_entry_recovery_via_login(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer,
    enable_custom_integrations,
) -> None:
    """Test setup recovers using silent login when token renewal fails."""
    from python_frank_energie.exceptions import AuthException

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
            "access_token": "expired_token",
            "token": "expired_refresh_token",
        },
        options={
            "username": "user@example.com",
            "password": encrypt_password(hass, "correct_password"),
        },
        entry_id="setup_login_test",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.frank_energie.FrankEnergie") as mock_api:
        api_instance = mock_api.return_value
        api_instance.__aenter__.return_value = api_instance
        api_instance.is_authenticated = True

        # UserSites fails with AuthException on first call
        mock_user_sites = MagicMock()
        mock_address = MagicMock()
        mock_address.street = "Main Street"
        mock_address.houseNumber = "123"
        mock_address.houseNumberAddition = ""
        mock_site = MagicMock()
        mock_site.reference = "site_ref_123"
        mock_site.address = mock_address
        mock_user_sites.deliverySites = [mock_site]

        calls_login = []

        def user_sites_side_effect_login(*args, **kwargs):
            if not calls_login:
                calls_login.append(True)
                raise AuthException("Expired")
            return mock_user_sites

        api_instance.UserSites = AsyncMock(side_effect=user_sites_side_effect_login)

        # Mock token renewal failure
        api_instance.renew_token = AsyncMock(
            side_effect=AuthException("Renewal failed")
        )

        # Mock successful login
        mock_auth = MagicMock()
        mock_auth.authToken = "logged_in_auth_token"
        mock_auth.refreshToken = "logged_in_refresh_token"
        api_instance.login = AsyncMock(return_value=mock_auth)

        with patch(
            "custom_components.frank_energie.FrankEnergieCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            result = await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        assert result is True
        assert entry.state is ConfigEntryState.LOADED
        assert entry.data["access_token"] == "logged_in_auth_token"


async def test_setup_entry_restores_title(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer,
    enable_custom_integrations,
) -> None:
    """Test setup restores the address-based title if overwritten by email."""

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

    # Entry title is set to user email address
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="user@example.com",
        data={
            "access_token": "valid_token",
            "token": "valid_refresh_token",
            "site_reference": "site_ref_123",
        },
        entry_id="setup_title_test",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.frank_energie.FrankEnergie") as mock_api:
        api_instance = mock_api.return_value
        api_instance.__aenter__.return_value = api_instance
        api_instance.is_authenticated = True

        mock_user_sites = MagicMock()
        mock_address = MagicMock()
        mock_address.street = "Main Street"
        mock_address.houseNumber = "123"
        mock_address.houseNumberAddition = ""
        mock_site = MagicMock()
        mock_site.reference = "site_ref_123"
        mock_site.address = mock_address
        mock_user_sites.deliverySites = [mock_site]

        api_instance.UserSites = AsyncMock(return_value=mock_user_sites)

        with patch(
            "custom_components.frank_energie.FrankEnergieCoordinator.async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ):
            result = await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

        assert result is True
        assert entry.state is ConfigEntryState.LOADED
        # Title should be healed back to the address
        assert entry.title == "Main Street 123"
