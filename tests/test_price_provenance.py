"""Price-source truth for Belgian MODEL contracts and other sites.

These regressions change diagnostic provenance only, not prices, HA entity IDs
or Recorder histories. Synthetic values deliberately contain no account data.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from python_frank_energie.models import MarketPrices, PriceData

from custom_components.frank_energie.coordinator import (
    FrankEnergiePriceCoordinator,
    _price_selection_outcome,
    _site_segment_flags,
)


def _prices(electricity: bool = False, gas: bool = False) -> MarketPrices:
    point = {
        "from": "2026-10-31T12:00:00+00:00",
        "till": "2026-10-31T13:00:00+00:00",
        "marketPrice": 0.12,
        "marketPriceTax": 0.02,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.10,
    }
    return MarketPrices(
        electricity=PriceData(
            [point] if electricity else [], energy_type="electricity"
        ),
        gas=PriceData([point] if gas else [], energy_type="gas"),
        energy_country="BE",
    )


@pytest.mark.parametrize(
    (
        "selected_electricity",
        "selected_gas",
        "customer_electricity",
        "customer_gas",
        "authenticated",
        "cached",
        "expected",
    ),
    [
        (False, False, False, False, True, False, "not_available"),
        (False, False, False, False, False, False, "not_available"),
        (True, True, False, False, True, False, "public_fallback"),
        (True, True, False, False, True, True, "cached_fallback"),
        (True, False, True, False, True, False, "customer"),
        (True, True, True, False, True, False, "mixed"),
        (True, True, True, True, True, False, "customer"),
        (True, True, False, False, False, False, "public"),
        (True, True, False, False, False, True, "cached_fallback"),
    ],
)
def test_selected_slot_provenance_is_not_a_success_flag(
    selected_electricity,
    selected_gas,
    customer_electricity,
    customer_gas,
    authenticated,
    cached,
    expected,
):
    selected = _prices(selected_electricity, selected_gas)
    assert (
        _price_selection_outcome(
            selected,
            authenticated=authenticated,
            customer_electricity=customer_electricity,
            customer_gas=customer_gas,
            fallback_from_cache=cached,
        )
        == expected
    )


def _coordinator(authenticated: bool = True) -> FrankEnergiePriceCoordinator:
    hass = MagicMock()
    hass.config.country = "BE"
    entry = MagicMock()
    entry.options = {"resolution": "PT15M"}
    entry.data = {"site_reference": "synthetic-reference"}
    api = MagicMock()
    api.is_authenticated = authenticated
    coordinator = FrankEnergiePriceCoordinator(hass, entry, api, MagicMock())
    coordinator.user_electricity_enabled = True
    coordinator.user_gas_enabled = True
    coordinator._record_price_fetch = MagicMock()
    return coordinator


@pytest.mark.asyncio
async def test_model_public_prices_do_not_become_customer_provenance():
    """A variable MODEL tariff must not claim spot prices as its customer rate."""
    coordinator = _coordinator()
    public = _prices(True, True)
    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=public)
    coordinator._fetch_user_prices_for_range = AsyncMock(return_value=_prices())
    selected = await coordinator._fetch_prices_with_fallback(
        date(2026, 10, 31), date(2026, 11, 1)
    )
    assert selected is not None
    assert len(selected.electricity.all) == 1
    assert len(selected.gas.all) == 1
    assert coordinator._record_price_fetch.call_args.args[1] == "public_fallback"


@pytest.mark.asyncio
async def test_both_sources_empty_cannot_be_mislabeled_as_fallback():
    coordinator = _coordinator()
    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=None)
    coordinator._fetch_user_prices_for_range = AsyncMock(return_value=None)
    selected = await coordinator._fetch_prices_with_fallback(
        date(2026, 10, 31), date(2026, 11, 1)
    )
    assert selected is not None
    assert not selected.electricity.all
    assert not selected.gas.all
    assert coordinator._record_price_fetch.call_args.args[1] == "not_available"


@pytest.mark.asyncio
async def test_partial_customer_no_public_slots_stays_customer_not_mixed():
    coordinator = _coordinator()
    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=_prices())
    coordinator._fetch_user_prices_for_range = AsyncMock(
        return_value=_prices(electricity=True)
    )
    selected = await coordinator._fetch_prices_with_fallback(
        date(2026, 10, 31), date(2026, 11, 1)
    )
    assert selected is not None
    assert len(selected.electricity.all) == 1
    assert not selected.gas.all
    assert coordinator._record_price_fetch.call_args.args[1] == "customer"


@pytest.mark.asyncio
async def test_cache_is_never_misreported_as_fresh_public_feed():
    coordinator = _coordinator()
    coordinator._cached_prices = _prices(electricity=True)
    coordinator._fetch_public_prices_for_range = AsyncMock(return_value=None)
    coordinator._fetch_user_prices_for_range = AsyncMock(return_value=None)
    selected = await coordinator._fetch_prices_with_fallback(
        date(2026, 10, 31), date(2026, 11, 1)
    )
    assert selected is coordinator._cached_prices
    assert coordinator._record_price_fetch.call_args.args[1] == "cached_fallback"


def test_selected_delivery_site_controls_price_fallback_not_coordinator_defaults():
    """Settings coordinator owns the sites; price coordinator must read them."""
    sites = SimpleNamespace(
        reference="site-a",
        segments=["ELECTRICITY", "GAS"],
        deliverySites=[
            SimpleNamespace(reference="site-a", segments=["ELECTRICITY", "GAS"]),
            SimpleNamespace(reference="site-b", segments=["ELECTRICITY"]),
        ],
    )
    assert _site_segment_flags(sites, "site-a") == (True, True)
    assert _site_segment_flags(sites, "site-b") == (True, False)
    assert _site_segment_flags(sites, "unknown-site") == (False, False)
    assert _site_segment_flags(sites, None) == (False, False)
    assert _site_segment_flags(None, "site-a") == (False, False)


def test_top_level_first_site_fallback_only_when_reference_matches():
    """Pinned UserSites parser exposes first-site segments at its root."""
    sites = SimpleNamespace(
        reference="site-a",
        segments=["ELECTRICITY", "GAS"],
        deliverySites=[],
    )
    assert _site_segment_flags(sites, "site-a") == (True, True)
    assert _site_segment_flags(sites, "site-b") == (False, False)


def test_selected_site_flags_are_case_normalized_and_fail_closed_on_missing_data():
    sites = SimpleNamespace(
        reference="site-a",
        segments=["ELECTRICITY"],
        deliverySites=[
            SimpleNamespace(reference="site-a", segments=["gas", "eLeCtRiCiTy"]),
        ],
    )
    assert _site_segment_flags(sites, "site-a") == (True, True)
    sites.deliverySites[0].segments = None
    assert _site_segment_flags(sites, "site-a") == (True, False)


@pytest.mark.asyncio
async def test_price_refresh_reconciles_flags_from_selected_site_before_fetching():
    """Real price coordinator previously kept both flags permanently False."""
    coordinator = _coordinator()
    coordinator.settings_coordinator.data = {
        "user": None,
        "user_sites": SimpleNamespace(
            reference="synthetic-reference",
            segments=["ELECTRICITY", "GAS"],
            deliverySites=[
                SimpleNamespace(
                    reference="synthetic-reference",
                    segments=["ELECTRICITY", "GAS"],
                )
            ],
        ),
    }
    coordinator._should_skip_api_calls = MagicMock(return_value=False)
    coordinator._reconcile_resolution = MagicMock()
    coordinator._adjust_update_interval = MagicMock()
    coordinator._update_today_prices = MagicMock()
    coordinator._aggregate_data = MagicMock(return_value={})
    coordinator._maybe_fire_lowest_price_event = MagicMock()
    coordinator._maybe_fire_lowest_4p_event = MagicMock()
    coordinator._maybe_fire_lowest_16p_event = MagicMock()
    coordinator._fetch_contract_price_resolution_state = AsyncMock(return_value=None)
    coordinator._refresh_tomorrow_cache = AsyncMock(return_value=None)
    coordinator.store.async_save = AsyncMock()

    async def assert_flags_on_fetch(*args, **kwargs):
        assert coordinator.user_electricity_enabled is True
        assert coordinator.user_gas_enabled is True
        return _prices(electricity=True, gas=True)

    coordinator._fetch_prices_with_fallback = AsyncMock(
        side_effect=assert_flags_on_fetch
    )
    await coordinator._async_update_data()
    coordinator._fetch_prices_with_fallback.assert_awaited_once()


def test_actual_pinned_user_sites_parser_preserves_selected_site_isolation():
    """The client exposes first-site segments at root even with multiple sites."""
    from python_frank_energie.models import UserSites

    sites = UserSites.from_dict(
        {
            "data": {
                "userSites": [
                    {"reference": "site-a", "segments": ["ELECTRICITY", "GAS"]},
                    {"reference": "site-b", "segments": ["GAS"]},
                ]
            }
        }
    )
    assert _site_segment_flags(sites, "site-a") == (True, True)
    assert _site_segment_flags(sites, "site-b") == (False, True)
    assert _site_segment_flags(sites, "missing-site") == (False, False)


@pytest.mark.asyncio
async def test_cached_first_refresh_checks_selected_site_before_skipping_api() -> None:
    """A partial cache cannot hide a subscribed gas segment at startup."""
    coordinator = _coordinator()
    coordinator.user_electricity_enabled = False
    coordinator.user_gas_enabled = False
    coordinator.settings_coordinator.data = {
        "user_sites": SimpleNamespace(
            deliverySites=[
                SimpleNamespace(
                    reference="synthetic-reference",
                    segments=["ELECTRICITY", "GAS"],
                )
            ]
        )
    }
    coordinator._async_setup = AsyncMock(return_value=True)
    coordinator._is_cache_fresh = MagicMock(return_value=True)

    await coordinator.async_config_entry_first_refresh()

    assert coordinator.user_electricity_enabled
    assert coordinator.user_gas_enabled
    coordinator._is_cache_fresh.assert_called_once()
