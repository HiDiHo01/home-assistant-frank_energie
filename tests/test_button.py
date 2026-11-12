"""Regression tests for localized refresh buttons and contract routing."""

from unittest.mock import MagicMock

import pytest

from custom_components.frank_energie import button as button_platform
from custom_components.frank_energie.button import (
    FrankEnergieButtonEntityDescription,
    FrankEnergieRefreshButton,
)
from custom_components.frank_energie.const import (
    DATA_DELIVERY_SITE,
    SERVICE_NAME_BATTERIES,
    SERVICE_NAME_GAS_PRICES,
    SERVICE_NAME_PRICES,
)


@pytest.mark.asyncio
async def test_refresh_button(mock_coordinator: MagicMock) -> None:
    """Refresh the price coordinator without overriding localized entity naming."""
    button = FrankEnergieRefreshButton(
        coordinator=mock_coordinator,
        description=FrankEnergieButtonEntityDescription(
            key="refresh_prices",
            translation_key="refresh_prices",
        ),
        entry=MagicMock(entry_id="test_entry"),
    )
    assert button.entity_description.name is None
    assert button.translation_key == "refresh_prices"
    assert button.unique_id == "test_entry_refresh_prices"
    assert button.has_entity_name is True

    await button.async_press()
    mock_coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_battery_sessions_button(mock_coordinator: MagicMock) -> None:
    """Refresh battery sessions using the translated, stable button identity."""
    button = FrankEnergieRefreshButton(
        coordinator=mock_coordinator,
        description=FrankEnergieButtonEntityDescription(
            key="refresh_battery_sessions",
            translation_key="refresh_battery_sessions",
            service_name=SERVICE_NAME_BATTERIES,
        ),
        entry=MagicMock(entry_id="test_entry"),
    )
    assert button.entity_description.name is None
    assert button.translation_key == "refresh_battery_sessions"
    assert button.unique_id == "test_entry_refresh_battery_sessions"
    assert button.has_entity_name is True

    await button.async_press()
    mock_coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contracts", "expected_price_buttons"),
    [
        ({"ELECTRICITY"}, [("refresh_prices", SERVICE_NAME_PRICES)]),
        ({"GAS"}, [("refresh_prices", SERVICE_NAME_GAS_PRICES)]),
        (
            {"ELECTRICITY", "GAS"},
            [
                ("refresh_prices", SERVICE_NAME_PRICES),
                ("refresh_gas_prices", SERVICE_NAME_GAS_PRICES),
            ],
        ),
    ],
)
async def test_setup_buttons_preserves_contract_routing(
    mock_coordinator: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    contracts: set[str],
    expected_price_buttons: list[tuple[str, str]],
) -> None:
    """Keep price/gas buttons distinct and localize the battery refresh button."""
    runtime_data = MagicMock()
    runtime_data.coordinator = mock_coordinator
    runtime_data.settings_coordinator.api.is_authenticated = True
    runtime_data.settings_coordinator.data = {DATA_DELIVERY_SITE: object()}
    runtime_data.battery_session_coordinators = {"battery_1": mock_coordinator}

    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.pref_disable_new_entities = False
    entry.runtime_data = runtime_data

    def _known_contracts(_site: object) -> set[str]:
        """Return the contract types configured for the current test case."""
        return contracts

    monkeypatch.setattr(button_platform, "known_site_contracts", _known_contracts)
    monkeypatch.setattr(
        button_platform, "async_sync_contract_entities", MagicMock()
    )

    added = MagicMock()
    await button_platform.async_setup_entry(MagicMock(), entry, added)
    added.assert_called_once()
    entities = added.call_args.args[0]

    descriptions = [
        (entity.entity_description.key, entity.entity_description.service_name)
        for entity in entities
    ]
    assert descriptions == [
        *expected_price_buttons,
        ("refresh_battery_sessions", SERVICE_NAME_BATTERIES),
    ]
    assert [entity.unique_id for entity in entities] == [
        *(f"test_entry_{key}" for key, _ in expected_price_buttons),
        "test_entry_refresh_battery_sessions",
    ]
    assert all(entity.entity_description.name is None for entity in entities)
    assert all(entity.has_entity_name for entity in entities)
    assert [entity.translation_key for entity in entities] == [
        *("refresh_prices" for _ in expected_price_buttons),
        "refresh_battery_sessions",
    ]
