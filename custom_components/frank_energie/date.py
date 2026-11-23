"""Editable start and end dates for the entry's Smart Battery period sensors."""

from dataclasses import replace
from datetime import date

from homeassistant.components.date import DateEntity, DateEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import FrankEnergieEntryData
from .battery_results import (
    BatteryPeriod,
    BatteryPeriodEntity,
    async_watch_battery_inventory,
)

PARALLEL_UPDATES = 0

_DATES = (
    DateEntityDescription(
        key="start_date",
        translation_key="battery_period_start",
        icon="mdi:calendar-start",
    ),
    DateEntityDescription(
        key="end_date", translation_key="battery_period_end", icon="mdi:calendar-end"
    ),
)


async def async_setup_entry(  # NOSONAR(S7503)
    _hass: HomeAssistant,
    entry: ConfigEntry[FrankEnergieEntryData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add one pair of date controls when this entry has a Smart Battery."""

    @callback
    def _async_add_dates(_ids: list[str], first: bool) -> None:
        """Attach the selectors to the Batteries service on first discovery."""
        if first:
            async_add_entities(FrankEnergieBatteryDate(entry, item) for item in _DATES)

    async_watch_battery_inventory(entry, _async_add_dates)


class FrankEnergieBatteryDate(BatteryPeriodEntity, DateEntity):
    """Edit one bound of the shared, persistent battery period."""

    def __init__(
        self,
        entry: ConfigEntry[FrankEnergieEntryData],
        description: DateEntityDescription,
    ) -> None:
        """Associate this bound with the entry's Batteries service."""
        super().__init__(entry, f"battery_period_{description.key}")
        self.entity_description = description

    @property
    def native_value(self) -> date:
        """Read the selected bound as a calendar date."""
        period = BatteryPeriod.for_entry(self._entry)
        return (
            period.start_date
            if self.entity_description.key == "start_date"
            else period.end_date
        )

    async def async_set_value(self, value: date) -> None:  # NOSONAR(S7503)
        """Save both bounds atomically and notify the entry's period listeners."""
        previous = BatteryPeriod.for_entry(self._entry)
        period = replace(previous, **{self.entity_description.key: value})
        self._async_save_selection(period)
