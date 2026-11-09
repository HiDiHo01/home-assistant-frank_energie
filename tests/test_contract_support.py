"""Exercise electricity-only, gas-only and dual-contract integration lifecycles."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from inspect import iscoroutinefunction
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import RequestException
from python_frank_energie.models import MarketPrices, PriceData, User, UserSites
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.frank_energie.const import (
    DATA_DELIVERY_SITE,
    DATA_ELECTRICITY,
    DATA_GAS,
    DOMAIN,
    TIMEZONE_AMSTERDAM,
)
from custom_components.frank_energie.coordinator import (
    FrankEnergiePriceCoordinator,
    FrankEnergieSettingsCoordinator,
)
from custom_components.frank_energie.helpers import async_sync_contract_entities
from custom_components.frank_energie.sensor import SENSOR_TYPES, FrankEnergieSensor

CONTRACT_CASES = [("ELECTRICITY",), ("GAS",), ("ELECTRICITY", "GAS")]
AMSTERDAM = ZoneInfo(TIMEZONE_AMSTERDAM)


def _market_prices(
    day: date, contracts: tuple[str, ...], resolution: int = 15
) -> MarketPrices:
    """Create real provider models with electricity and gas on independent grids."""
    start = datetime.combine(day, datetime.min.time(), AMSTERDAM).astimezone(UTC)
    end = datetime.combine(
        day + timedelta(days=1), datetime.min.time(), AMSTERDAM
    ).astimezone(UTC)

    def commodity(kind: str, minutes: int, total: float) -> PriceData:
        """Build a full local day only for a commodity present in this response."""
        interval = timedelta(minutes=minutes)
        count = int((end - start) / interval) if kind in contracts else 0
        return PriceData(
            [
                {
                    "from": (start + index * interval).isoformat(),
                    "till": (start + (index + 1) * interval).isoformat(),
                    "marketPrice": total,
                    "marketPriceTax": 0.0,
                    "sourcingMarkupPrice": 0.0,
                    "energyTaxPrice": 0.0,
                    "perUnit": "kwh" if kind == "ELECTRICITY" else "m3",
                }
                for index in range(count)
            ],
            energy_type=kind.lower(),
            resolution_minutes=minutes,
        )

    return MarketPrices(
        electricity=commodity("ELECTRICITY", resolution, 0.25),
        gas=commodity("GAS", 60, 1.2),
        energy_country="NL",
    )


def _api_for_contracts(contracts: tuple[str, ...], resolution: int) -> AsyncMock:
    """Mock external account endpoints while keeping HA and coordinators real."""
    api = AsyncMock(spec=FrankEnergie)
    for name in dir(FrankEnergie):
        if iscoroutinefunction(getattr(FrankEnergie, name, None)):
            setattr(api, name, AsyncMock(return_value=None))
    api.is_authenticated = True
    api.user.return_value = User.from_dict({"data": {"me": {"countryCode": "NL"}}})
    api.UserSites.return_value = UserSites.from_dict(
        {
            "data": {
                "userSites": [
                    {"reference": "other-site", "segments": ["ELECTRICITY", "GAS"]},
                    {"reference": "test-site", "segments": list(reversed(contracts))},
                ]
            }
        }
    )

    async def public_prices(start: date, **kwargs: object) -> MarketPrices:
        """Public prices include both commodities regardless of this site's contracts."""
        return _market_prices(start, ("ELECTRICITY", "GAS"), resolution)

    async def user_prices(
        site_reference: str, country: str, start: date, end: date
    ) -> MarketPrices:
        """Return only the selected site's commodities from the real fetch path."""
        return _market_prices(start, contracts, resolution)

    api.prices.side_effect = public_prices
    api.user_prices.side_effect = user_prices
    return api


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("contracts", CONTRACT_CASES)
@pytest.mark.parametrize("resolution", [15, 60])
async def test_contract_setup_reload_and_rollover(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    contracts: tuple[str, ...],
    resolution: int,
) -> None:
    """Load actual HA platforms, reuse disk cache and keep prices valid at midnight."""
    await hass.config.async_set_time_zone(TIMEZONE_AMSTERDAM)
    freezer.move_to(datetime(2026, 7, 20, 14, 15, tzinfo=AMSTERDAM))
    api = _api_for_contracts(contracts, resolution)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Contract test site",
        data={
            "site_reference": "test-site",
            "access_token": "test-token",
            "token": "test-refresh",
        },
        options={"resolution": f"PT{resolution}M"},
        unique_id="test-site",
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registered_segments = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.unique_id}.segments",
        config_entry=entry,
        suggested_object_id="frank_energie_gebruiker_segmenten",
    )
    with patch("custom_components.frank_energie.FrankEnergie", return_value=api):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        def assert_prices() -> None:
            """Check native prices and contract-bound entities through HA's registry."""
            segments_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{entry.unique_id}.segments"
            )
            assert segments_id == registered_segments.entity_id
            segments_state = hass.states.get(registered_segments.entity_id)
            assert segments_state is not None
            assert segments_state.state == ", ".join(sorted(contracts))
            for kind, key, expected in [
                ("ELECTRICITY", "elec_markup", 0.25),
                ("GAS", "gas_markup", 1.2),
            ]:
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, f"{entry.unique_id}.{key}"
                )
                if kind in contracts:
                    assert entity_id is not None
                    state = hass.states.get(entity_id)
                    assert state is not None
                    assert float(state.state) == pytest.approx(expected)
                else:
                    assert entity_id is None
            assert bool(
                registry.async_get_entity_id(
                    "binary_sensor",
                    DOMAIN,
                    f"{entry.unique_id}_electricity_price_lowest_today",
                )
            ) is ("ELECTRICITY" in contracts)
            assert bool(
                registry.async_get_entity_id(
                    "select", DOMAIN, f"{entry.entry_id}_resolution"
                )
            ) is ("ELECTRICITY" in contracts)

        assert_prices()
        prices = entry.runtime_data.price_coordinator
        assert prices.last_fetch_tomorrow is not None
        assert prices._is_cache_fresh(datetime.now(UTC))
        calls_before_reload = api.user_prices.await_count
        registry_before_reload = {
            item.entity_id: item.disabled_by
            for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        }

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert api.user_prices.await_count == calls_before_reload
        assert {
            item.entity_id: item.disabled_by
            for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        } == registry_before_reload
        assert_prices()

        midnight = datetime(2026, 7, 21, tzinfo=AMSTERDAM)
        freezer.move_to(midnight)
        async_fire_time_changed(hass, midnight)
        await hass.async_block_till_done()
        assert_prices()

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_segments_recover_when_selected_site_is_returned(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Keep the sensor unavailable until the API includes the configured site."""
    freezer.move_to("2026-07-20T08:15:00Z")
    api = _api_for_contracts(("GAS",), 60)
    sites = api.UserSites.return_value
    api.UserSites.return_value = replace(sites, deliverySites=sites.deliverySites[:1])
    entry = MockConfigEntry(domain=DOMAIN, data={"site_reference": "test-site"})
    entry.add_to_hass(hass)
    settings = FrankEnergieSettingsCoordinator(hass, entry, api)
    description = next(item for item in SENSOR_TYPES if item.key == "segments")
    sensor = FrankEnergieSensor(settings, description, entry)

    await settings.async_refresh()
    assert sensor.native_value is None
    assert not sensor.available

    api.UserSites.return_value = sites
    await settings.async_refresh()
    assert sensor.native_value == "GAS"
    assert sensor.available


def _coordinator(
    hass: HomeAssistant, contracts: tuple[str, ...], resolution: int = 15
) -> FrankEnergiePriceCoordinator:
    """Create real scoped coordinators for focused cache and outage regressions."""
    api = _api_for_contracts(contracts, resolution)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={"resolution": f"PT{resolution}M"}
    )
    entry.add_to_hass(hass)
    settings = FrankEnergieSettingsCoordinator(hass, entry, api)
    settings.data[DATA_DELIVERY_SITE] = api.UserSites.return_value.deliverySites[1]
    prices = FrankEnergiePriceCoordinator(hass, entry, api, settings)
    prices.store.async_save = AsyncMock()
    return prices


@pytest.mark.parametrize("contracts", CONTRACT_CASES)
async def test_required_contract_cache_survives_fetch_exception(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, contracts: tuple[str, ...]
) -> None:
    """A live-fetch exception must not require an uncontracted commodity in cache."""
    freezer.move_to("2026-07-20T08:15:00Z")
    prices = _coordinator(hass, contracts)
    prices._static_prices_today = _market_prices(date(2026, 7, 20), contracts)
    prices._fetch_prices_with_fallback = AsyncMock(
        side_effect=RequestException("temporary outage")
    )
    result = await prices._async_update_data()
    for kind, key in [("ELECTRICITY", DATA_ELECTRICITY), ("GAS", DATA_GAS)]:
        if kind in contracts:
            assert result[key].current is not None
    assert prices.last_fetch_today is None


@pytest.mark.parametrize("missing", ["ELECTRICITY", "GAS"])
async def test_dual_contract_outage_rejects_missing_required_cache(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, missing: str
) -> None:
    """Partial dual-contract cache must not become a successful fallback."""
    freezer.move_to("2026-07-20T08:15:00Z")
    prices = _coordinator(hass, ("ELECTRICITY", "GAS"))
    prices._static_prices_today = _market_prices(
        date(2026, 7, 20),
        tuple(kind for kind in ("ELECTRICITY", "GAS") if kind != missing),
    )
    prices._fetch_prices_with_fallback = AsyncMock(
        side_effect=RequestException("temporary outage")
    )
    with pytest.raises(UpdateFailed):
        await prices._async_update_data()


@pytest.mark.parametrize("first", ["ELECTRICITY", "GAS"])
@pytest.mark.parametrize("second_is_complete", [False, True])
async def test_dual_contract_partial_tomorrow_keeps_retrying(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    first: str,
    second_is_complete: bool,
) -> None:
    """Keep the published commodity and retry until both required series arrive."""
    freezer.move_to("2026-07-20T12:15:00Z")
    prices = _coordinator(hass, ("ELECTRICITY", "GAS"))
    today = date(2026, 7, 20)
    tomorrow = today + timedelta(days=1)
    partial = _market_prices(tomorrow, (first,))
    complete = _market_prices(
        tomorrow,
        ("ELECTRICITY", "GAS")
        if second_is_complete
        else tuple(kind for kind in ("ELECTRICITY", "GAS") if kind != first),
    )
    prices._fetch_tomorrow_data = AsyncMock(side_effect=[partial, None, complete])
    now = datetime.now(UTC)
    first_result = await prices._refresh_tomorrow_cache(today, tomorrow, now)
    assert first_result is not None
    assert prices.last_fetch_tomorrow is None
    prices._adjust_update_interval(now)
    assert prices.update_interval == timedelta(minutes=5)

    assert await prices._refresh_tomorrow_cache(today, tomorrow, now) is first_result
    result = await prices._refresh_tomorrow_cache(today, tomorrow, now)
    assert result.electricity.all
    assert result.gas.all
    assert prices.last_fetch_tomorrow == now
    await prices._refresh_tomorrow_cache(today, tomorrow, now)
    assert prices._fetch_tomorrow_data.await_count == 3


@pytest.mark.parametrize("contracts", CONTRACT_CASES)
@pytest.mark.parametrize("resolution", [15, 60])
async def test_cache_resolution_applies_only_to_electricity(
    hass: HomeAssistant, contracts: tuple[str, ...], resolution: int
) -> None:
    """Hourly gas must not be invalidated by the electricity resolution option."""
    prices = _coordinator(hass, contracts, resolution)
    prices._static_prices_today = _market_prices(
        date(2026, 7, 20), contracts, resolution
    )
    assert prices._is_cache_resolution_valid()


@pytest.mark.parametrize("published", ["ELECTRICITY", "GAS"])
@pytest.mark.parametrize("source", ["live", "cache"])
async def test_tomorrow_keeps_valid_commodity_when_other_is_stale(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    published: str,
    source: str,
) -> None:
    """Discard an echoed commodity independently in both live and stored prices."""
    freezer.move_to("2026-07-20T12:15:00Z")
    prices = _coordinator(hass, ("ELECTRICITY", "GAS"))
    today = date(2026, 7, 20)
    tomorrow = today + timedelta(days=1)
    remaining = "GAS" if published == "ELECTRICITY" else "ELECTRICITY"
    valid = _market_prices(tomorrow, (published,))
    stale = _market_prices(today, (remaining,))
    mixed = replace(valid, **{remaining.lower(): getattr(stale, remaining.lower())})
    now = datetime.now(UTC)
    if source == "cache":
        prices.cached_prices_tomorrow = mixed
        prices.last_fetch_tomorrow = now
    prices._fetch_tomorrow_data = AsyncMock(
        side_effect=[
            mixed if source == "live" else None,
            _market_prices(tomorrow, (remaining,)),
        ]
    )

    result = await prices._refresh_tomorrow_cache(today, tomorrow, now)
    assert result is not None
    assert (
        getattr(result, published.lower()).all == getattr(valid, published.lower()).all
    )
    other = getattr(result, remaining.lower())
    assert other is None or not other.all
    assert prices.last_fetch_tomorrow is None
    prices._adjust_update_interval(now)
    assert prices.update_interval == timedelta(minutes=5)

    result = await prices._refresh_tomorrow_cache(today, tomorrow, now)
    assert result.electricity.all
    assert result.gas.all
    assert prices.last_fetch_tomorrow == now
    assert prices._fetch_tomorrow_data.await_count == 2


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("entity_domain", ["sensor", "binary_sensor", "select"])
@pytest.mark.parametrize("default_enabled", [False, True])
@pytest.mark.parametrize(
    "disabled",
    [None, er.RegistryEntryDisabler.USER, er.RegistryEntryDisabler.INTEGRATION],
)
async def test_contract_registry_sync_preserves_user_choices(
    hass: HomeAssistant,
    supported: bool,
    disabled: er.RegistryEntryDisabler | None,
    entity_domain: str,
    default_enabled: bool,
) -> None:
    """Only integration-owned disables may follow a site's changing contracts."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registered = registry.async_get_or_create(
        entity_domain,
        DOMAIN,
        "contract-price",
        config_entry=entry,
        disabled_by=disabled,
    )
    other_entry = MockConfigEntry(domain=DOMAIN, data={})
    other_entry.add_to_hass(hass)
    other = registry.async_get_or_create(
        entity_domain, DOMAIN, "other-contract-price", config_entry=other_entry
    )
    async_sync_contract_entities(
        hass,
        entry,
        entity_domain,
        {"contract-price": supported, "other-contract-price": False},
        enabled_by_default={"contract-price"} if default_enabled else set(),
    )
    expected = disabled
    if (
        supported
        and disabled == er.RegistryEntryDisabler.INTEGRATION
        and default_enabled
    ):
        expected = None
    elif not supported and disabled is None:
        expected = er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(registered.entity_id).disabled_by == expected
    assert registry.async_get(other.entity_id).disabled_by is None


async def test_gas_only_skips_electricity_resolution_api(hass: HomeAssistant) -> None:
    """A gas connection must never be used for electricity resolution operations."""
    prices = _coordinator(hass, ("GAS",), resolution=15)
    prices._connection_id = "gas-connection"
    assert prices.resolution == "PT60M"
    assert await prices._fetch_contract_price_resolution_state("gas-connection") is None
    await prices.async_set_resolution("PT15M")
    prices.api.contract_price_resolution_state.assert_not_awaited()
    prices.api.contract_price_resolution_request_change.assert_not_awaited()


async def test_daily_gas_cache_ignores_electricity_resolution(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A single daily gas interval must remain reusable with the default option."""
    freezer.move_to("2026-07-20T08:00:00Z")
    prices = _coordinator(hass, ("GAS",), resolution=15)
    cached = _market_prices(date(2026, 7, 20), ("GAS",))
    cached.gas = replace(
        cached.gas,
        prices=[
            {**cached.gas.prices[0], "till": cached.gas.all[-1].date_till.isoformat()}
        ],
    )
    assert cached.gas.resolution_minutes == 1440
    prices._static_prices_today = cached
    prices.last_fetch_today = datetime.now(UTC)
    assert prices._is_cache_fresh(datetime.now(UTC))
