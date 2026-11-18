"""Regression tests for flexible Frank Energie Insight reporting periods."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry

from custom_components.frank_energie.const import (
    CONF_INSIGHT_PERIOD,
    DATA_INSIGHT_PERIOD,
    DATA_INSIGHT_USAGE,
    DATA_USAGE,
    DOMAIN,
)
from custom_components.frank_energie.coordinator import (
    FrankEnergieStatisticsCoordinator,
)
from custom_components.frank_energie.insight_period import (
    default_insight_period,
    validate_insight_period,
)
from custom_components.frank_energie.sensor import (
    SENSOR_TYPES,
    FrankEnergieSensor,
    _INSIGHT_DEFAULT_OBJECT_IDS,
    _async_migrate_insight_entity_ids,
    _sensor_unique_id,
)
from custom_components.frank_energie.text import FrankEnergieInsightPeriod


@pytest.mark.parametrize(
    "period",
    ["2025", "2026", "2026-01", "2026-11", "2026-02-28", "2026-11-18"],
)
def test_accepts_year_month_and_day(period: str) -> None:
    """Accept the three documented provider formats, including the current period."""
    assert validate_insight_period(period, date(2026, 11, 18)) == period


@pytest.mark.parametrize(
    "period",
    [
        "",
        "202",
        "2026-1",
        "2026-11-1",
        "2026-13",
        "2026-00",
        "2026-02-29",
        "2026-11-31",
        "2027",
        "2026-12",
        "2026-11-19",
        "2026-11-18T00:00:00",
        " 2026-11",
        "2026-11 ",
        "2026-11-\n",
    ],
)
def test_rejects_invalid_or_future_periods(period: str) -> None:
    """Reject malformed, nonexistent, or future calendar periods."""
    with pytest.raises(ValueError, match="Insight period"):
        validate_insight_period(period, date(2026, 11, 18))


def test_default_period_crosses_month_and_year() -> None:
    """The default changes at local calendar boundaries."""
    assert default_insight_period(date(2026, 1, 1)) == "2025-12-31"
    assert default_insight_period(date(2026, 11, 18)) == "2026-11-17"


def _coordinator(
    hass: HomeAssistant, entry: MockConfigEntry
) -> FrankEnergieStatisticsCoordinator:
    """Build the real statistics coordinator with an isolated mock API."""
    api = MagicMock()
    api.is_authenticated = True
    settings = MagicMock()
    settings.data = {}
    coordinator = FrankEnergieStatisticsCoordinator(hass, entry, api, settings)
    coordinator._should_skip_api_calls = MagicMock(return_value=False)
    coordinator._fetch_month_summary = AsyncMock(return_value=None)
    return coordinator


def _entry(
    hass: HomeAssistant, options: dict[str, str] | None = None
) -> MockConfigEntry:
    """Register one configured delivery site for the Insight tests."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Insight",
        unique_id="fixture-insight-site",
        data={"site_reference": "fixture-insight-site"},
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize("period", ["2025", "2026-10", "2026-11-15"])
async def test_selected_period_uses_provider_totals_without_changing_recorder_source(
    hass: HomeAssistant, period: str
) -> None:
    """Fetch the selected reporting period apart from the regular yesterday import."""
    entry = _entry(hass, {CONF_INSIGHT_PERIOD: period})
    coordinator = _coordinator(hass, entry)
    yesterday = SimpleNamespace(_id="yesterday")
    chosen = SimpleNamespace(
        _id="selected", electricity=SimpleNamespace(costs_total=12.5)
    )
    coordinator._async_fetch_and_record_usage = AsyncMock(return_value=yesterday)
    coordinator._fetch_period_usage = AsyncMock(return_value=chosen)

    with freeze_time("2026-11-18T12:00:00+00:00"):
        data = await coordinator._async_update_data()

    assert data[DATA_USAGE] is yesterday
    assert data[DATA_INSIGHT_USAGE] is chosen
    assert data[DATA_INSIGHT_PERIOD] == period
    coordinator._fetch_period_usage.assert_awaited_once_with(period)
    assert coordinator._async_fetch_and_record_usage.await_args.args[0] == date(
        2026, 11, 17
    )


async def test_default_yesterday_avoids_second_api_request(hass: HomeAssistant) -> None:
    """Do not double fetch yesterday when no explicit period is configured."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry)
    yesterday = SimpleNamespace(_id="yesterday")
    coordinator._async_fetch_and_record_usage = AsyncMock(return_value=yesterday)
    coordinator._fetch_period_usage = AsyncMock()

    with freeze_time("2026-11-18T12:00:00+00:00"):
        data = await coordinator._async_update_data()

    assert data[DATA_INSIGHT_PERIOD] == "2026-11-17"
    assert data[DATA_INSIGHT_USAGE] is yesterday
    coordinator._fetch_period_usage.assert_not_awaited()


async def test_selected_period_persists_without_reloading_or_refetching_prices(
    hass: HomeAssistant,
) -> None:
    """Persist the entry's chosen period and refresh only statistics."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()

    with freeze_time("2026-11-18T12:00:00+00:00"):
        assert coordinator.insight_period == "2026-11-17"
        await coordinator.async_set_insight_period("2026-10")
        assert entry.options[CONF_INSIGHT_PERIOD] == "2026-10"
        coordinator.async_request_refresh.assert_awaited_once()
        restored = _coordinator(hass, entry)
        assert restored.insight_period == "2026-10"
        await coordinator.async_set_insight_period("2026-10")
        coordinator.async_request_refresh.assert_awaited_once()


async def test_invalid_selector_never_writes_config_entry(hass: HomeAssistant) -> None:
    """Reject invalid periods before writing or contacting the API."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry)
    coordinator.async_request_refresh = AsyncMock()
    with freeze_time("2026-11-18T12:00:00+00:00"):
        with pytest.raises(ValueError, match="future"):
            await coordinator.async_set_insight_period("2027")
    assert CONF_INSIGHT_PERIOD not in entry.options
    coordinator.async_request_refresh.assert_not_awaited()


async def test_selected_period_is_sent_unchanged_to_provider(
    hass: HomeAssistant,
) -> None:
    """Pass a month or year string to the existing client without inserting a day."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry)
    coordinator.async_api_call = AsyncMock(return_value=None)

    await coordinator._fetch_period_usage("2026-10")
    coordinator.async_api_call.assert_awaited_once_with(
        "period_usage_and_costs",
        coordinator.api.period_usage_and_costs,
        "fixture-insight-site",
        "2026-10",
    )


async def test_text_entity_is_on_existing_insight_service_device(
    hass: HomeAssistant,
) -> None:
    """Use a stable per-entry ID and the shared Insight parent device."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry)
    selector = FrankEnergieInsightPeriod(coordinator, entry)

    with freeze_time("2026-11-18T12:00:00+00:00"):
        assert selector.native_value == "2026-11-17"
    assert selector.unique_id == f"{entry.entry_id}_insight_period"
    assert selector.device_info is not None
    assert (DOMAIN, f"{entry.entry_id}_Usage") in selector.device_info["identifiers"]

    coordinator.async_set_insight_period = AsyncMock()
    with patch.object(selector, "async_write_ha_state"):
        await selector.async_set_value("2026-10")
    coordinator.async_set_insight_period.assert_awaited_once_with("2026-10")


def test_selected_insight_sensors_use_provider_totals_including_zero() -> None:
    """Read the selected period's API aggregate instead of yesterday's total."""
    chosen = SimpleNamespace(
        electricity=SimpleNamespace(costs_total=0.0, usage_total=15.0),
        gas=SimpleNamespace(costs_total=2.5, usage_total=4.0),
        feed_in=SimpleNamespace(costs_total=-0.5, usage_total=1.0),
    )
    data = {
        DATA_INSIGHT_USAGE: chosen,
        DATA_INSIGHT_PERIOD: "2026",
        DATA_USAGE: SimpleNamespace(
            electricity=SimpleNamespace(costs_total=99.0, usage_total=99.0),
        ),
    }
    expected = {
        "costs_electricity_yesterday": 0.0,
        "usage_electricity_yesterday": 15.0,
        "costs_gas_yesterday": 2.5,
        "usage_gas_yesterday": 4.0,
        "gains_feed_in_yesterday": -0.5,
        "delivered_feed_in_yesterday": 1.0,
    }
    by_key = {description.key: description for description in SENSOR_TYPES}
    for key, value in expected.items():
        assert by_key[key].value_fn(data) == value
        assert by_key[key].state_class is None


@pytest.mark.parametrize("period", ["2025", "2026-10", "2026-11-17"])
@pytest.mark.parametrize(
    ("sensor_key", "source_key", "attribute_key"),
    [
        ("costs_electricity_yesterday", "electricity", "electricity"),
        ("usage_electricity_yesterday", "electricity", "electricity"),
        ("costs_gas_yesterday", "gas", "gas"),
        ("usage_gas_yesterday", "gas", "gas"),
        ("gains_feed_in_yesterday", "feed_in", "feed_in"),
        ("delivered_feed_in_yesterday", "feed_in", "feed_in"),
    ],
)
async def test_insight_period_attributes_preserve_all_provider_items(
    hass: HomeAssistant,
    period: str,
    sensor_key: str,
    source_key: str,
    attribute_key: str,
) -> None:
    """Expose the entire provider category and items for every period granularity."""
    entry = _entry(hass)
    coordinator = _coordinator(hass, entry)
    # Regression: month/year must not be truncated even for large item lists.
    item_count = 5000 if len(period) < 10 else 2
    items = [{"date": "2025-01-01", "usage": 1.0}] * item_count
    source = SimpleNamespace(costs_total=12.0, usage_total=25.0, items=items)
    coordinator.data = {
        DATA_INSIGHT_USAGE: SimpleNamespace(**{source_key: source}),
        DATA_INSIGHT_PERIOD: period,
    }
    description = next(item for item in SENSOR_TYPES if item.key == sensor_key)
    # Coordinator setup can replace config-entry runtime data in this fixture.
    entry.runtime_data = SimpleNamespace(settings_coordinator=SimpleNamespace(data={}))
    sensor = FrankEnergieSensor(coordinator, description, entry)

    attributes = sensor.extra_state_attributes
    assert attributes == {
        attribute_key: source,
        "selected_period": period,
    }
    assert attributes[attribute_key].items is items
    assert len(attributes[attribute_key].items) == item_count


async def test_changed_delivery_site_never_publishes_old_insight_totals(
    hass: HomeAssistant,
) -> None:
    """Never attach the former delivery site's totals after reconfiguration."""
    entry = _entry(hass, {CONF_INSIGHT_PERIOD: "2025"})
    coordinator = _coordinator(hass, entry)

    async def _switch_site(_day: date, _now: object) -> object:
        """Simulate a site change while the two provider reads are in flight."""
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "site_reference": "another-site"},
        )
        return SimpleNamespace(_id="previous-site-usage")

    coordinator._async_fetch_and_record_usage = AsyncMock(side_effect=_switch_site)
    coordinator._fetch_period_usage = AsyncMock(
        return_value=SimpleNamespace(_id="selected-old-site")
    )
    with freeze_time("2026-11-18T12:00:00+00:00"):
        data = await coordinator._async_update_data()

    assert data[DATA_INSIGHT_PERIOD] is None
    assert data[DATA_INSIGHT_USAGE] is None


def _authenticated_insight_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create an entry with an authenticated settings coordinator for migration."""
    entry = _entry(hass)
    entry.runtime_data = SimpleNamespace(
        settings_coordinator=SimpleNamespace(api=SimpleNamespace(is_authenticated=True))
    )
    return entry


@pytest.mark.parametrize(
    ("sensor_key", "object_id"),
    [
        ("costs_electricity_yesterday", "frank_energie_inzicht_kosten_elektriciteit"),
        ("usage_electricity_yesterday", "frank_energie_inzicht_verbruik_elektriciteit"),
        ("costs_gas_yesterday", "frank_energie_inzicht_kosten_gas"),
        ("usage_gas_yesterday", "frank_energie_inzicht_verbruik_gas"),
        ("gains_feed_in_yesterday", "frank_energie_inzicht_opbrengst_elektriciteit"),
        (
            "delivered_feed_in_yesterday",
            "frank_energie_inzicht_teruggeleverde_elektriciteit",
        ),
    ],
)
@pytest.mark.parametrize("old_suffix", ["gisteren", "geselecteerde_periode"])
async def test_insight_default_entity_ids_migrate(
    hass: HomeAssistant, sensor_key: str, object_id: str, old_suffix: str
) -> None:
    """Migrate standard registered names without changing the existing identity."""
    entry = _authenticated_insight_entry(hass)
    registry = er.async_get(hass)
    unique_id = f"{entry.unique_id}.{sensor_key}"
    old_entity_id = f"sensor.{object_id}_{old_suffix}"
    original = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        suggested_object_id=f"{object_id}_{old_suffix}",
        config_entry=entry,
    )
    assert original.entity_id == old_entity_id

    _async_migrate_insight_entity_ids(hass, entry)

    new_entity_id = f"sensor.{object_id}"
    migrated = registry.async_get(new_entity_id)
    assert migrated is not None
    assert migrated.id == original.id
    assert migrated.unique_id == original.unique_id
    assert migrated.config_entry_id == entry.entry_id
    assert registry.async_get(old_entity_id) is None
    _async_migrate_insight_entity_ids(hass, entry)
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) == new_entity_id


@pytest.mark.parametrize(
    ("sensor_key", "object_id"),
    [
        ("costs_electricity_yesterday", "frank_energie_inzicht_kosten_elektriciteit"),
        ("usage_electricity_yesterday", "frank_energie_inzicht_verbruik_elektriciteit"),
        ("costs_gas_yesterday", "frank_energie_inzicht_kosten_gas"),
        ("usage_gas_yesterday", "frank_energie_inzicht_verbruik_gas"),
        ("gains_feed_in_yesterday", "frank_energie_inzicht_opbrengst_elektriciteit"),
        (
            "delivered_feed_in_yesterday",
            "frank_energie_inzicht_teruggeleverde_elektriciteit",
        ),
    ],
)
async def test_insight_custom_entity_ids_are_preserved(
    hass: HomeAssistant, sensor_key: str, object_id: str
) -> None:
    """Do not touch an Insight entity ID changed by the Home Assistant user."""
    entry = _authenticated_insight_entry(hass)
    registry = er.async_get(hass)
    original = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.unique_id}.{sensor_key}",
        suggested_object_id=f"my_custom_{object_id}",
        config_entry=entry,
    )
    _async_migrate_insight_entity_ids(hass, entry)
    assert registry.async_get(original.entity_id) == original


async def test_insight_migration_skips_occupied_destination(
    hass: HomeAssistant,
) -> None:
    """Do not rename when another entity owns the requested short ID."""
    entry = _authenticated_insight_entry(hass)
    registry = er.async_get(hass)
    original = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.unique_id}.costs_electricity_yesterday",
        suggested_object_id="frank_energie_inzicht_kosten_elektriciteit_gisteren",
        config_entry=entry,
    )
    occupied = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "occupied_by_another_entity",
        suggested_object_id="frank_energie_inzicht_kosten_elektriciteit",
    )
    _async_migrate_insight_entity_ids(hass, entry)
    assert registry.async_get(original.entity_id) == original
    assert registry.async_get(occupied.entity_id) == occupied


async def test_insight_migration_preserves_description_unique_ids(
    hass: HomeAssistant,
) -> None:
    """Renaming entity IDs must not change the six sensor unique IDs."""
    entry = _authenticated_insight_entry(hass)
    for key in _INSIGHT_DEFAULT_OBJECT_IDS:
        description = next(item for item in SENSOR_TYPES if item.key == key)
        assert _sensor_unique_id(entry, description) == f"{entry.unique_id}.{key}"
