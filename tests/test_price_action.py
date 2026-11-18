"""Test the cached tariff action through Home Assistant's service registry."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_get_all_descriptions
from pytest_homeassistant.common import MockConfigEntry
from python_frank_energie.models import PriceData, UserSites

from custom_components.frank_energie.const import DATA_DELIVERY_SITE, DOMAIN
from custom_components.frank_energie.services import SERVICE_GET_PRICES_FOR_DATE

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _price_data(
    start: datetime, hours: int, *, unit: str, minutes: int = 60, base: float = 0.0
) -> PriceData:
    """Build provider prices with explicit, fixed-duration UTC intervals."""
    interval = timedelta(minutes=minutes)
    return PriceData(
        [
            {
                "from": (start + index * interval).isoformat(),
                "till": (start + (index + 1) * interval).isoformat(),
                "marketPrice": base if index == 1 else base - 0.15,
                "marketPriceTax": 0.0,
                "sourcingMarkupPrice": 0.0,
                "energyTaxPrice": 0.01 if index == 0 else 0.0,
                "perUnit": unit,
            }
            for index in range(hours * 60 // minutes)
        ],
        resolution_minutes=minutes,
    )


async def _load_entry(
    hass: HomeAssistant,
    prices: dict[str, PriceData],
    *,
    contracts: tuple[str, ...] = (),
) -> MockConfigEntry:
    """Load the real domain and action with a deterministic price snapshot."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    api = SimpleNamespace(
        is_authenticated=bool(contracts), prices=AsyncMock(), user_prices=AsyncMock()
    )
    sites = UserSites.from_dict(
        {
            "data": {
                "userSites": [{"reference": "demo-site", "segments": list(contracts)}]
            }
        }
    )
    entry.runtime_data = SimpleNamespace(
        price_coordinator=SimpleNamespace(api=api, data=prices),
        settings_coordinator=SimpleNamespace(
            data={DATA_DELIVERY_SITE: sites.deliverySites[0]} if contracts else {}
        ),
    )
    with patch("custom_components.frank_energie.async_setup_entry", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
    return entry


async def _prices_for_date(
    hass: HomeAssistant, entry: MockConfigEntry, day: str, energy_type: str
) -> ServiceResponse:
    """Invoke the user-facing response-only action."""
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GET_PRICES_FOR_DATE,
        {"config_entry": entry.entry_id, "date": day, "energy_type": energy_type},
        blocking=True,
        return_response=True,
    )
    assert isinstance(response, dict)
    return response


@pytest.mark.parametrize(
    ("day", "start", "hours"),
    [
        ("2026-03-29", datetime(2026, 3, 28, 23, tzinfo=UTC), 23),
        ("2026-10-25", datetime(2026, 10, 24, 22, tzinfo=UTC), 25),
    ],
)
async def test_electricity_day_retains_dst_intervals(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    day: str,
    start: datetime,
    hours: int,
) -> None:
    """Read complete spring and autumn days regardless of HA's time zone."""
    freezer.move_to(start - timedelta(hours=6))
    entry = await _load_entry(
        hass, {"electricity": _price_data(start, hours, unit="kwh", minutes=15)}
    )

    response = await _prices_for_date(hass, entry, day, "electricity")

    assert response["date"] == day
    assert response["unit"] == "€/kWh"
    assert response["coverage"] == {
        "start": start.isoformat(),
        "end": (start + timedelta(hours=hours)).isoformat(),
    }
    periods = response["prices"]
    assert len(periods) == hours * 4
    assert periods[0]["market_price"] == pytest.approx(-0.15)
    assert periods[0]["all_in_price"] == pytest.approx(-0.14)
    assert periods[1]["market_price"] == pytest.approx(0)
    assert periods[1]["all_in_price"] == pytest.approx(0)
    assert periods[-1]["end"] == response["coverage"]["end"]
    entry.runtime_data.price_coordinator.api.prices.assert_not_awaited()


async def test_action_scopes_historical_prices_and_contracts_to_entry(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Read the selected cache even when another entry has the same price date."""
    freezer.move_to(datetime(2026, 7, 20, 12, tzinfo=UTC))
    start = datetime(2026, 7, 18, 22, tzinfo=UTC)
    public = await _load_entry(
        hass, {"electricity": _price_data(start, 24, unit="kwh", base=0.1)}
    )
    contracted = await _load_entry(
        hass,
        {"electricity": _price_data(start, 24, unit="kwh", base=0.5)},
        contracts=("ELECTRICITY",),
    )

    public_prices = await _prices_for_date(hass, public, "2026-07-19", "electricity")
    site_prices = await _prices_for_date(hass, contracted, "2026-07-19", "electricity")
    assert public_prices["prices"][1]["market_price"] == pytest.approx(0.1)
    assert site_prices["prices"][1]["market_price"] == pytest.approx(0.5)

    with pytest.raises(ServiceValidationError) as error:
        await _prices_for_date(hass, contracted, "2026-07-19", "gas")
    assert error.value.translation_key == "price_contract_unavailable"
    contracted.runtime_data.price_coordinator.api.user_prices.assert_not_awaited()
    descriptions = await async_get_all_descriptions(hass)
    fields = descriptions[DOMAIN][SERVICE_GET_PRICES_FOR_DATE]["fields"]
    assert fields["config_entry"]["selector"] == {
        "config_entry": {"integration": DOMAIN}
    }


@pytest.mark.parametrize(
    ("start", "hours", "day", "unit", "price_unit"),
    [
        (datetime(2026, 3, 28, 23, tzinfo=UTC), 5, "2026-03-29", "m3", "€/m³"),
        (datetime(2026, 10, 24, 22, tzinfo=UTC), 7, "2026-10-25", "kwh", "€/kWh"),
    ],
)
async def test_gas_tomorrow_covers_published_window_only(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    start: datetime,
    hours: int,
    day: str,
    unit: str,
    price_unit: str,
) -> None:
    """Accept the 06:00 local cutoff and retain the provider's unit."""
    freezer.move_to(start - timedelta(hours=6))
    entry = await _load_entry(
        hass, {"gas": _price_data(start, hours, unit=unit)}, contracts=("GAS",)
    )

    response = await _prices_for_date(hass, entry, day, "gas")

    assert response["unit"] == price_unit
    assert response["coverage"]["end"] == (start + timedelta(hours=hours)).isoformat()
    assert len(response["prices"]) == hours


async def test_action_rejects_incomplete_prices_and_invalid_entries(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Use translated validation errors for missing periods and unavailable entries."""
    freezer.move_to(datetime(2026, 7, 20, 12, tzinfo=UTC))
    entry = await _load_entry(
        hass,
        {
            "electricity": _price_data(
                datetime(2026, 7, 20, 22, tzinfo=UTC), 23, unit="kwh"
            )
        },
    )
    with pytest.raises(ServiceValidationError) as error:
        await _prices_for_date(hass, entry, "2026-07-21", "electricity")
    assert error.value.translation_key == "prices_for_date_unavailable"

    inactive = MockConfigEntry(domain=DOMAIN)
    inactive.add_to_hass(hass)
    with pytest.raises(ServiceValidationError) as error:
        await _prices_for_date(hass, inactive, "2026-07-21", "electricity")
    assert error.value.translation_key == "service_config_entry_not_loaded"

    foreign = MockConfigEntry(domain="other_integration")
    foreign.add_to_hass(hass)
    with pytest.raises(ServiceValidationError) as error:
        await _prices_for_date(hass, foreign, "2026-07-21", "electricity")
    assert error.value.translation_key == "service_config_entry_wrong_domain"
