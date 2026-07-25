"""Test the Frank Energie integration setup and teardown logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import zoneinfo

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from custom_components.frank_energie import FrankEnergieComponent
from custom_components.frank_energie.const import (
    DOMAIN,
    CONF_COORDINATOR,
    TIMEZONE_AMSTERDAM,
)
from custom_components.frank_energie.helpers import encrypt_password
from custom_components.frank_energie.helpers import (
    _get_fernet_key as real_get_fernet_key,
)
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


async def test_remove_entry_deletes_price_cache_store(
    hass: HomeAssistant,
    aioclient_responses: ResponseMocks,
    freezer,
    enable_custom_integrations,
) -> None:
    """Removing a config entry must delete its persisted price cache file.

    Regression test: only async_unload_entry existed, which HA calls on
    reload/disable, not on full removal. Without async_remove_entry, the
    per-entry Store file under .storage/ was never cleaned up, so
    add-then-delete-then-readd (e.g. testing with a throwaway unauthenticated
    entry) left orphaned cache files behind indefinitely.
    """
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

    with patch(
        "custom_components.frank_energie.helpers.Store", autospec=True
    ) as mock_store_cls:
        mock_store = mock_store_cls.return_value
        mock_store.async_remove = AsyncMock()

        remove_result = await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

        assert remove_result["require_restart"] is False
        mock_store_cls.assert_called_once_with(hass, 1, f"{DOMAIN}_prices_{entry.entry_id}")
        mock_store.async_remove.assert_awaited_once()


async def test_aligned_refresh_covers_whole_price_release_window(
    hass: HomeAssistant,
) -> None:
    """The 13:00 trigger must not be a single exact-tick point of failure.

    Regression test: only the exact hour==13, minute==0 tick used to call
    async_request_refresh(); every other quarter-hour tick in the window
    just re-broadcast cached data via async_update_listeners() without
    fetching anything. If this listener's registration (the last step of
    setup) lost the race against that one exact boundary — plausible on a
    slow full HA restart competing with every other integration for the
    event loop — nothing else was scheduled to try again until the next
    day's 13:00. Now every tick across 13:00-15:00 local retries, so no
    single tick is load-bearing.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="test_aligned")
    entry.add_to_hass(hass)
    component = FrankEnergieComponent(hass, entry)

    price_coordinator = MagicMock()
    price_coordinator.async_request_refresh = AsyncMock()
    price_coordinator.async_update_listeners = MagicMock()
    price_coordinator.resolution = "PT60M"

    captured_callback = None

    def fake_track_utc_time_change(hass_arg, action, **kwargs):
        nonlocal captured_callback
        captured_callback = action
        return MagicMock()

    with patch(
        "custom_components.frank_energie.async_track_utc_time_change",
        side_effect=fake_track_utc_time_change,
    ):
        await component._schedule_aligned_updates(price_coordinator)

    assert captured_callback is not None

    tz = zoneinfo.ZoneInfo(TIMEZONE_AMSTERDAM)

    for hour, minute in [(13, 0), (13, 15), (13, 45), (14, 45)]:
        price_coordinator.async_request_refresh.reset_mock()
        with patch(
            "homeassistant.util.dt.now",
            return_value=datetime(2026, 7, 21, hour, minute, tzinfo=tz),
        ):
            await captured_callback(datetime.now(tz))
        price_coordinator.async_request_refresh.assert_awaited_once()

    # Outside the window: no refresh, just a listener update.
    price_coordinator.async_request_refresh.reset_mock()
    price_coordinator.async_update_listeners.reset_mock()
    with patch(
        "homeassistant.util.dt.now",
        return_value=datetime(2026, 7, 21, 15, 0, tzinfo=tz),
    ):
        await captured_callback(datetime.now(tz))
    price_coordinator.async_request_refresh.assert_not_called()
    price_coordinator.async_update_listeners.assert_called_once()


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


async def test_decrypt_password_returns_none_on_corrupted_ciphertext(
    hass: HomeAssistant,
) -> None:
    """A gAAAA-prefixed string that fails to decrypt (wrong/rotated key,
    corruption) must return None with a warning, not raise.

    Realistic scenario: core.uuid changes, or a password encrypted on one
    HA instance gets restored via backup onto another.
    """
    from custom_components.frank_energie.helpers import decrypt_password

    hass.data["core.uuid"] = "test_uuid_123"

    # Looks like a Fernet token (gAAAA prefix) but isn't valid ciphertext.
    corrupted = "gAAAAABnot-a-real-fernet-token" + "x" * 20

    assert decrypt_password(hass, corrupted) is None


async def test_get_fernet_key_raises_when_core_uuid_missing(
    hass: HomeAssistant,
) -> None:
    """_get_fernet_key must raise EncryptionError when core.uuid is unavailable,
    rather than silently deriving a key from a missing/None value.

    Tests the real implementation directly (captured at import time, before
    the autouse mock_core_uuid fixture in conftest.py patches this same name
    for every other test) — that fixture exists specifically so hass-based
    tests don't need a real core.uuid, which means it also hides this
    function's own error path from every test that goes through the mock.
    """
    from custom_components.frank_energie.exceptions import EncryptionError

    hass.data.pop("core.uuid", None)

    with pytest.raises(EncryptionError):
        real_get_fernet_key(hass)


@pytest.mark.skip(reason="Lingering timer false positive during mock teardown")
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


@pytest.mark.skip(reason="Lingering timer false positive during mock teardown")
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


@pytest.mark.skip(reason="Lingering timer false positive during mock teardown")
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
