"""Regression coverage for selected-site electricity connection IDs."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import RequestException
from python_frank_energie.models import (
    ContractPriceResolutionChangeResult,
    ContractPriceResolutionState,
    User,
)

from custom_components.frank_energie.const import DATA_USER, DOMAIN
from custom_components.frank_energie.coordinator import (
    FrankEnergiePriceCoordinator,
    FrankEnergieSettingsCoordinator,
    _dict_to_market_prices,
    _empty_data,
    _extract_electricity_connection_id,
)

pytestmark = pytest.mark.freeze_time("2026-10-29T08:00:00+00:00")


@dataclass(slots=True)
class _ConnectionContext:
    """Keep each test's entry, API, and coordinators together."""

    entry: MockConfigEntry
    api: MagicMock
    settings: FrankEnergieSettingsCoordinator
    prices: FrankEnergiePriceCoordinator
    store: MagicMock


def _make_user(
    electricity_id: str | None = "electricity-alpha", *, include_gas: bool = True
) -> User:
    """Parse realistic user data with distinct row and connection identifiers."""
    connections: list[dict[str, str]] = []
    if include_gas:
        connections.append(
            {"id": "gas-row", "connectionId": "gas-alpha", "segment": "GAS"}
        )
    if electricity_id is not None:
        connections.append(
            {
                "id": "electricity-row",
                "connectionId": electricity_id,
                "segment": "ELECTRICITY",
            }
        )
    user = User.from_dict(
        {
            "data": {
                "me": {
                    "id": "test-user",
                    "email": "test@example.com",
                    "countryCode": "NL",
                    "connections": connections,
                }
            }
        }
    )
    assert user is not None
    return user


def _make_api(user_data: User | None) -> MagicMock:
    """Mock transport while preserving the published library's response models."""
    api = MagicMock(spec=FrankEnergie)
    api.is_authenticated = True
    api.user = AsyncMock(return_value=user_data)
    api.smart_hvac_status = AsyncMock(return_value=None)
    api.contract_price_resolution_state = AsyncMock(
        return_value=ContractPriceResolutionState(
            active_option="PT15M",
            available_options=["PT15M", "PT60M"],
            is_change_request_possible=True,
        )
    )
    api.contract_price_resolution_request_change = AsyncMock(
        return_value=ContractPriceResolutionChangeResult(success=True)
    )
    return api


def _cached_payload() -> dict[str, object]:
    """Provide a complete quarter-hourly day for a genuinely fresh disk cache."""
    day_start = datetime(2026, 10, 29, tzinfo=ZoneInfo("Europe/Amsterdam")).astimezone(
        UTC
    )
    prices: list[dict[str, object]] = []
    for slot in range(96):
        interval_start = day_start + timedelta(minutes=slot * 15)
        prices.append(
            {
                "from": interval_start.isoformat(),
                "till": (interval_start + timedelta(minutes=15)).isoformat(),
                "marketPrice": 0.15,
                "marketPriceTax": 0.03,
                "sourcingMarkupPrice": 0.01,
                "energyTaxPrice": 0.12,
                "perUnit": "kwh",
            }
        )
    return {
        "prices_today": {
            "electricity": prices,
            "gas": [],
            "energy_country": "NL",
            "energy_type": "electricity",
            "electricity_resolution_minutes": 15,
            "gas_resolution_minutes": 60,
        },
        "prices_tomorrow": None,
        "contract_price_resolution_state": {
            "activeOption": "PT15M",
            "availableOptions": ["PT15M", "PT60M"],
            "isChangeRequestPossible": True,
        },
        "last_fetch_today": "2026-10-29T08:00:00+00:00",
        "last_fetch_tomorrow": None,
    }


@pytest.fixture(name="connection_context")
async def _connection_context(
    hass: HomeAssistant,
) -> AsyncGenerator[_ConnectionContext]:
    """Create real coordinators with selected-site data and mocked API/storage."""
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    hass.config.country = "NL"
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="connection-alpha",
        data={"site_reference": "site-alpha"},
        options={"resolution": "PT15M"},
    )
    entry.add_to_hass(hass)
    user_data = _make_user()
    api = _make_api(user_data)
    payload = _cached_payload()
    store = MagicMock()
    store.async_load = AsyncMock(return_value=payload)
    store.async_save = AsyncMock()
    with patch("custom_components.frank_energie.helpers.Store", return_value=store):
        settings = FrankEnergieSettingsCoordinator(hass, entry, api)
        settings.data = _empty_data()
        settings.data[DATA_USER] = user_data
        prices = FrankEnergiePriceCoordinator(hass, entry, api, settings)
        prices.async_request_refresh = AsyncMock()
        cached_prices_payload = payload["prices_today"]
        assert isinstance(cached_prices_payload, dict)
        parsed_prices = _dict_to_market_prices(cached_prices_payload)
        assert parsed_prices is not None
        prices._fetch_prices_with_fallback = AsyncMock(return_value=parsed_prices)
        prices._refresh_tomorrow_cache = AsyncMock(return_value=None)
        yield _ConnectionContext(entry, api, settings, prices, store)


async def test_extracts_electricity_connection_id() -> None:
    """Choose the electricity connectionId instead of gas, row ID, or EAN."""
    assert _extract_electricity_connection_id(_make_user()) == "electricity-alpha"


async def test_user_fetch_replaces_previous_connection_id(
    connection_context: _ConnectionContext,
) -> None:
    """Update an already cached ID when the selected site's connection changes."""
    connection_context.settings._connection_id = "old-electricity"
    connection_context.api.user.return_value = _make_user("new-electricity")

    await connection_context.settings._fetch_user_data()

    assert connection_context.settings._connection_id == "new-electricity"
    connection_context.api.user.assert_awaited_once_with("site-alpha")


@pytest.mark.parametrize("include_gas", [False, True])
async def test_user_fetch_clears_removed_electricity_connection(
    connection_context: _ConnectionContext, include_gas: bool
) -> None:
    """Clear a stale ID after a valid empty or gas-only user response."""
    connection_context.settings._connection_id = "old-electricity"
    connection_context.api.user.return_value = _make_user(None, include_gas=include_gas)

    await connection_context.settings._fetch_user_data()

    assert connection_context.settings._connection_id is None


@pytest.mark.parametrize("request_fails", [False, True])
async def test_missing_user_response_preserves_known_connection(
    connection_context: _ConnectionContext, request_fails: bool
) -> None:
    """Keep the known ID when user data is absent or retrieval fails."""
    connection_context.settings._connection_id = "known-electricity"
    connection_context.api.user.return_value = None
    if request_fails:
        connection_context.api.user.side_effect = RequestException("temporary failure")

    result = await connection_context.settings._fetch_user_data()

    assert result is None
    assert connection_context.settings._connection_id == "known-electricity"


async def test_fresh_price_cache_initializes_connection_without_fetching(
    connection_context: _ConnectionContext,
) -> None:
    """Restore the connection even when fresh cached prices skip the first poll."""
    assert connection_context.prices._connection_id is None

    await connection_context.prices.async_config_entry_first_refresh()

    assert connection_context.prices._connection_id == "electricity-alpha"
    connection_context.prices._fetch_prices_with_fallback.assert_not_awaited()
    connection_context.api.contract_price_resolution_state.assert_not_awaited()
    connection_context.api.user.assert_not_awaited()

    await connection_context.prices.async_set_resolution("PT60M")

    connection_context.api.contract_price_resolution_request_change.assert_awaited_once_with(
        "electricity-alpha", "PT60M"
    )


async def test_resolution_change_uses_latest_settings_connection(
    connection_context: _ConnectionContext,
) -> None:
    """Use changed settings immediately without waiting for a price refresh."""
    connection_context.prices._connection_id = "old-electricity"
    connection_context.settings.data[DATA_USER] = _make_user("new-electricity")

    await connection_context.prices.async_set_resolution("PT60M")

    assert connection_context.prices._connection_id == "new-electricity"
    connection_context.api.contract_price_resolution_request_change.assert_awaited_once_with(
        "new-electricity", "PT60M"
    )
    connection_context.api.user.assert_not_awaited()


@pytest.mark.parametrize("include_gas", [False, True])
async def test_resolution_change_rejects_removed_connection(
    connection_context: _ConnectionContext, include_gas: bool
) -> None:
    """Never mutate a connection removed by a valid settings response."""
    connection_context.prices._connection_id = "old-electricity"
    connection_context.settings.data[DATA_USER] = _make_user(
        None, include_gas=include_gas
    )

    await connection_context.prices.async_set_resolution("PT60M")

    assert connection_context.prices._connection_id is None
    connection_context.api.contract_price_resolution_request_change.assert_not_awaited()
    connection_context.prices.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("include_gas", [False, True])
async def test_price_refresh_clears_removed_connection(
    connection_context: _ConnectionContext, include_gas: bool
) -> None:
    """Stop querying an old ID after an empty or gas-only settings response."""
    connection_context.prices._connection_id = "old-electricity"
    connection_context.settings.data[DATA_USER] = _make_user(
        None, include_gas=include_gas
    )

    await connection_context.prices._async_update_data()

    assert connection_context.prices._connection_id is None
    connection_context.api.contract_price_resolution_state.assert_not_awaited()


@pytest.mark.parametrize("missing_payload", [False, True])
async def test_resolution_change_preserves_id_without_settings_payload(
    connection_context: _ConnectionContext, missing_payload: bool
) -> None:
    """Retain a known same-entry ID when no authoritative user response exists."""
    connection_context.prices._connection_id = "known-electricity"
    connection_context.settings.data = None if missing_payload else _empty_data()

    await connection_context.prices.async_set_resolution("PT60M")

    assert connection_context.prices._connection_id == "known-electricity"
    connection_context.api.contract_price_resolution_request_change.assert_awaited_once_with(
        "known-electricity", "PT60M"
    )


@pytest.mark.parametrize("missing_payload", [False, True])
async def test_price_refresh_preserves_id_without_settings_payload(
    connection_context: _ConnectionContext, missing_payload: bool
) -> None:
    """Preserve the same-entry ID during temporary settings unavailability."""
    connection_context.prices._connection_id = "known-electricity"
    connection_context.settings.data = None if missing_payload else _empty_data()

    await connection_context.prices._async_update_data()

    assert connection_context.prices._connection_id == "known-electricity"
    connection_context.api.contract_price_resolution_state.assert_awaited_once_with(
        "known-electricity"
    )


async def test_resolution_connections_remain_entry_scoped(
    hass: HomeAssistant, connection_context: _ConnectionContext
) -> None:
    """Send each entry's resolution request to its own electricity connection."""
    other_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="connection-beta",
        data={"site_reference": "site-beta"},
        options={"resolution": "PT15M"},
    )
    other_entry.add_to_hass(hass)
    other_user = _make_user("electricity-beta")
    other_api = _make_api(other_user)
    other_settings = FrankEnergieSettingsCoordinator(hass, other_entry, other_api)
    other_settings.data = _empty_data()
    other_settings.data[DATA_USER] = other_user
    other_prices = FrankEnergiePriceCoordinator(
        hass, other_entry, other_api, other_settings
    )
    other_prices.async_request_refresh = AsyncMock()

    await connection_context.prices.async_set_resolution("PT60M")
    await other_prices.async_set_resolution("PT60M")

    connection_context.api.contract_price_resolution_request_change.assert_awaited_once_with(
        "electricity-alpha", "PT60M"
    )
    other_api.contract_price_resolution_request_change.assert_awaited_once_with(
        "electricity-beta", "PT60M"
    )
