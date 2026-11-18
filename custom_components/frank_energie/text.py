"""Editable Home Assistant text entity for Frank Energie Insights."""

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import SERVICE_NAME_USAGE
from .coordinator import FrankEnergieStatisticsCoordinator
from .helpers import service_device_info


# Home Assistant awaits this platform hook even though entity registration is sync.
async def async_setup_entry(  # NOSONAR(S7503)
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Expose one period selector for each authenticated Frank Energie entry."""
    coordinator = entry.runtime_data.statistics_coordinator
    if coordinator.api.is_authenticated:
        async_add_entities([FrankEnergieInsightPeriod(coordinator, entry)])


class FrankEnergieInsightPeriod(
    CoordinatorEntity[FrankEnergieStatisticsCoordinator], TextEntity
):
    """Select a year, month, or date for Insight reporting."""

    _attr_has_entity_name = True
    _attr_translation_key = "insight_period"
    _attr_icon = "mdi:calendar-range"
    _attr_native_min = 4
    _attr_native_max = 10
    _attr_pattern = r"[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?$"

    def __init__(
        self, coordinator: FrankEnergieStatisticsCoordinator, entry: ConfigEntry
    ) -> None:
        """Associate the selector with this entry's Insight service device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_insight_period"
        self._attr_device_info = service_device_info(entry.entry_id, SERVICE_NAME_USAGE)

    @property
    def native_value(self) -> str:
        """Read the selected provider period, defaulting to yesterday."""
        return self.coordinator.insight_period

    async def async_set_value(self, value: str) -> None:
        """Save a validated period and refresh its Insight statistics."""
        await self.coordinator.async_set_insight_period(value)
        self.async_write_ha_state()
