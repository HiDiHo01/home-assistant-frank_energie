"""Exercise config-entry migrations through Home Assistant's setup lifecycle."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.frank_energie.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture(name="entry_setup", autouse=True)
async def _entry_setup(
    hass: HomeAssistant, enable_custom_integrations: None
) -> AsyncGenerator[AsyncMock]:
    """Load the domain before entries so setup migrates only the requested entry."""
    with (
        patch(
            "custom_components.frank_energie.async_setup_entry", return_value=True
        ) as setup,
        patch("custom_components.frank_energie.async_unload_entry", return_value=True),
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
        yield setup


def _stored_entry(
    *,
    version: int = 1,
    minor_version: int = 1,
    options: dict[str, object] | None = None,
    authenticated: bool = True,
) -> MockConfigEntry:
    """Build a stored entry with unrelated settings that migration must retain."""
    data = (
        {
            "site_reference": "site-alpha",
            "username": "legacy@example.com",
            "password": "legacy-password",
            "access_token": "saved-access-token",
            "token": "saved-refresh-token",
        }
        if authenticated
        else {}
    )
    return MockConfigEntry(
        domain=DOMAIN,
        source=config_entries.SOURCE_USER,
        title="Existing Frank Energie entry",
        unique_id="site-alpha" if authenticated else DOMAIN,
        version=version,
        minor_version=minor_version,
        data=data,
        options={"interval_settings": 12, "custom": {"keep": True}, **(options or {})},
    )


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize(
    "saved_resolution", [{}, {"resolution": "PT15M"}, {"resolution": "PT60M"}]
)
async def test_legacy_entry_migrates_during_setup(
    hass: HomeAssistant,
    entry_setup: AsyncMock,
    authenticated: bool,
    saved_resolution: dict[str, object],
) -> None:
    """Upgrade v1 entries, retaining credentials, identity and selected options."""
    entry = _stored_entry(options=saved_resolution, authenticated=authenticated)
    entry.add_to_hass(hass)
    original_data = dict(entry.data)
    original_options = dict(entry.options)
    original_identity = (entry.entry_id, entry.unique_id, entry.title)
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="site-beta",
        version=1,
        minor_version=1,
        data={"site_reference": "site-beta"},
        options={"resolution": "PT60M"},
    )
    other_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (entry.version, entry.minor_version) == (2, 1)
    assert entry.state is ConfigEntryState.LOADED
    assert dict(entry.data) == original_data
    assert dict(entry.options) == {
        "resolution": "PT15M",
        **original_options,
    }
    assert (entry.entry_id, entry.unique_id, entry.title) == original_identity
    assert (other_entry.version, other_entry.minor_version) == (1, 1)
    assert dict(other_entry.data) == {"site_reference": "site-beta"}
    assert dict(other_entry.options) == {"resolution": "PT60M"}
    entry_setup.assert_awaited_once_with(hass, entry)


async def test_migration_preserves_an_explicit_null_resolution(
    hass: HomeAssistant,
) -> None:
    """Leave existing values untouched instead of treating them as missing keys."""
    entry = _stored_entry(options={"resolution": None})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert (entry.version, entry.minor_version) == (2, 1)
    assert "resolution" in entry.options
    assert entry.options["resolution"] is None


@pytest.mark.parametrize("options", [{}, {"resolution": "PT60M"}])
async def test_current_entry_does_not_change_during_setup(
    hass: HomeAssistant, options: dict[str, object]
) -> None:
    """Avoid rewriting entries that already use the current schema."""
    entry = _stored_entry(version=2, options=options)
    entry.add_to_hass(hass)
    original_data = dict(entry.data)
    original_options = dict(entry.options)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert (entry.version, entry.minor_version) == (2, 1)
    assert dict(entry.data) == original_data
    assert dict(entry.options) == original_options


async def test_previous_minor_version_preserves_options(
    hass: HomeAssistant,
) -> None:
    """Advance v2.0 metadata without reapplying the v1 default-option migration."""
    entry = _stored_entry(version=2, minor_version=0)
    entry.add_to_hass(hass)
    original_options = dict(entry.options)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert (entry.version, entry.minor_version) == (2, 1)
    assert dict(entry.options) == original_options


async def test_newer_minor_version_is_not_downgraded(
    hass: HomeAssistant,
) -> None:
    """Keep forward-compatible minor versions and their unknown options intact."""
    entry = _stored_entry(version=2, minor_version=2, options={"future_option": True})
    entry.add_to_hass(hass)
    original_options = dict(entry.options)

    assert await hass.config_entries.async_setup(entry.entry_id)

    assert (entry.version, entry.minor_version) == (2, 2)
    assert dict(entry.options) == original_options


async def test_future_major_version_is_rejected_without_mutation(
    hass: HomeAssistant, entry_setup: AsyncMock
) -> None:
    """Refuse an incompatible future major schema before provider setup starts."""
    entry = _stored_entry(version=3, options={"future_option": True})
    entry.add_to_hass(hass)
    original_data = dict(entry.data)
    original_options = dict(entry.options)

    assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert (entry.version, entry.minor_version) == (3, 1)
    assert dict(entry.data) == original_data
    assert dict(entry.options) == original_options
    entry_setup.assert_not_awaited()


async def test_migration_is_idempotent_on_reload(hass: HomeAssistant) -> None:
    """Preserve the migrated entry when Home Assistant unloads and sets it up again."""
    entry = _stored_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    migrated_options = dict(entry.options)
    migrated_data = dict(entry.data)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert (entry.version, entry.minor_version) == (2, 1)
    assert dict(entry.options) == migrated_options
    assert dict(entry.data) == migrated_data


async def test_new_flow_creates_current_schema(hass: HomeAssistant) -> None:
    """Store new entries at the same schema version used by migration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={"authentication": False},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert (entry.version, entry.minor_version) == (2, 1)
    assert dict(entry.data) == {}
