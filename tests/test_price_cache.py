from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from datetime import datetime, date, timezone
from homeassistant.core import HomeAssistant
from python_frank_energie.models import (
    MarketPrices,
    PriceData,
    Price,
    ContractPriceResolutionState,
)
from custom_components.frank_energie.coordinator import (
    FrankEnergiePriceCoordinator,
    FrankEnergieSettingsCoordinator,
    _price_to_dict,
    _market_prices_to_dict,
    _dict_to_market_prices,
    _resolution_state_to_dict,
    _dict_to_resolution_state,
)


@pytest.fixture
def mock_store():
    """Mock the Store helper."""
    with patch("custom_components.frank_energie.coordinator.Store") as mock_store_class:
        store_instance = mock_store_class.return_value
        store_instance.async_load = AsyncMock(return_value=None)
        store_instance.async_save = AsyncMock()
        yield store_instance


@pytest.mark.asyncio
async def test_price_serialization():
    """Test Price serialization and deserialization helpers."""
    # Create mock Price data
    raw_price = {
        "from": "2026-06-26T07:30:00+00:00",
        "till": "2026-06-26T07:45:00+00:00",
        "marketPrice": 0.15,
        "marketPriceTax": 0.03,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.12,
        "perUnit": "kwh",
    }

    price = Price(raw_price, energy_type="electricity")
    serialized = _price_to_dict(price)

    assert serialized["from"] == "2026-06-26T07:30:00+00:00"
    assert serialized["marketPrice"] == 0.15
    assert serialized["energyTaxPrice"] == 0.12

    # Test MarketPrices serialization and deserialization
    electricity = PriceData([raw_price], energy_type="electricity")
    gas = PriceData([], energy_type="gas")
    market_prices = MarketPrices(electricity=electricity, gas=gas, energy_country="NL")

    serialized_mp = _market_prices_to_dict(market_prices)
    deserialized_mp = _dict_to_market_prices(serialized_mp)

    assert len(deserialized_mp.electricity.all) == 1
    assert deserialized_mp.electricity.all[0].market_price == 0.15
    assert deserialized_mp.energy_country == "NL"


@pytest.mark.asyncio
async def test_resolution_state_serialization():
    """Test ContractPriceResolutionState serialization and deserialization."""
    raw_state = {
        "activeOption": "PT15M",
        "availableOptions": ["PT15M", "PT1H"],
        "changeRequestEffectiveDate": "2026-07-01",
        "isChangeRequestPossible": True,
        "upcomingChange": None,
        "upcomingChangeEffectiveDate": None,
    }
    state = ContractPriceResolutionState.from_dict(raw_state)
    serialized = _resolution_state_to_dict(state)
    deserialized = _dict_to_resolution_state(serialized)

    # Serialized dict should contain ISO strings / None for date-like fields
    assert serialized["changeRequestEffectiveDate"] == "2026-07-01"
    assert "upcomingChange" in serialized
    assert serialized["upcomingChange"] is None
    assert "upcomingChangeEffectiveDate" in serialized
    assert serialized["upcomingChangeEffectiveDate"] is None

    # Deserialized object should preserve values and types
    assert deserialized.active_option == "PT15M"
    assert deserialized.is_change_request_possible is True
    assert deserialized.change_request_effective_date == date(2026, 7, 1)
    assert deserialized.upcoming_change is None
    assert deserialized.upcoming_change_effective_date is None


@pytest.mark.asyncio
async def test_resolution_state_serialization_from_date_types():
    """ContractPriceResolutionState serializes date/datetime fields to ISO strings and round-trips None."""
    change_request_date = date(2026, 7, 1)
    upcoming_change_dt = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)

    state = ContractPriceResolutionState(
        active_option="PT15M",
        available_options=["PT15M", "PT1H"],
        change_request_effective_date=change_request_date,
        is_change_request_possible=True,
        upcoming_change=upcoming_change_dt,
        upcoming_change_effective_date=None,
    )

    serialized = _resolution_state_to_dict(state)

    # Date / datetime instances should be converted to ISO 8601 strings
    assert serialized["changeRequestEffectiveDate"] == "2026-07-01"
    assert serialized["upcomingChange"] == upcoming_change_dt.isoformat()
    # None should be preserved as None
    assert serialized["upcomingChangeEffectiveDate"] is None

    deserialized = _dict_to_resolution_state(serialized)

    # Round-trip should preserve values and types
    assert deserialized.change_request_effective_date == change_request_date
    assert deserialized.upcoming_change == upcoming_change_dt
    assert deserialized.upcoming_change_effective_date is None


@pytest.mark.asyncio
async def test_coordinator_load_cache(mock_store, mock_config_entry):
    """Test that coordinator loads cached data from Store on startup."""
    mock_config_entry.data = {}
    mock_config_entry.options = {}

    raw_price = {
        "from": "2026-06-26T07:30:00+00:00",
        "till": "2026-06-26T07:45:00+00:00",
        "marketPrice": 0.15,
        "marketPriceTax": 0.03,
        "sourcingMarkupPrice": 0.01,
        "energyTaxPrice": 0.12,
        "perUnit": "kwh",
    }

    cached_data = {
        "prices_today": {
            "electricity": [raw_price],
            "gas": [],
            "energy_country": "NL",
            "energy_type": "electricity",
        },
        "prices_tomorrow": None,
        "contract_price_resolution_state": {
            "activeOption": "PT15M",
            "availableOptions": ["PT15M"],
            "changeRequestEffectiveDate": None,
            "isChangeRequestPossible": False,
            "upcomingChange": None,
            "upcomingChangeEffectiveDate": None,
        },
        "last_fetch_today": "2026-06-26T07:30:00+00:00",
        "last_fetch_tomorrow": None,
    }

    mock_store.async_load = AsyncMock(return_value=cached_data)

    hass = MagicMock(spec=HomeAssistant)
    hass.config = MagicMock()
    hass.config.country = "NL"

    mock_api = MagicMock()
    settings_coordinator = FrankEnergieSettingsCoordinator(
        hass, mock_config_entry, mock_api
    )

    price_coordinator = FrankEnergiePriceCoordinator(
        hass, mock_config_entry, mock_api, settings_coordinator
    )

    # We patch super().async_config_entry_first_refresh to avoid actual update call
    with patch(
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator.async_config_entry_first_refresh",
        AsyncMock(),
    ):
        await price_coordinator.async_config_entry_first_refresh()

    assert price_coordinator._static_prices_today is not None
    assert (
        price_coordinator._static_prices_today.electricity.all[0].market_price == 0.15
    )
    assert (
        price_coordinator._static_contract_price_resolution_state.active_option
        == "PT15M"
    )
    assert price_coordinator.last_fetch_today == datetime.fromisoformat(
        "2026-06-26T07:30:00+00:00"
    )
