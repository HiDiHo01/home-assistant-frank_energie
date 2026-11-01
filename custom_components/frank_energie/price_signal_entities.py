"""Read-only HA entities for full-day, DST-safe market-hour signals.

The source is the market component from Frank Energie electricity slots.
These never claim to represent a Belgian MODEL customer's billing rate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_MARKET_CHEAP_HOURS,
    DATA_ELECTRICITY,
    DEFAULT_MARKET_CHEAP_HOURS,
    SERVICE_NAME_PRICES,
)
from .helpers import service_device_info
from .price_signals import cheap_hour_count, current_market_hour


class _MarketHourEntity(CoordinatorEntity):
    """Shared stable identity and market-hour evidence."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = service_device_info(
            entry.entry_id, SERVICE_NAME_PRICES
        )

    def _current(self):
        data = self.coordinator.data
        prices = data.get(DATA_ELECTRICITY) if isinstance(data, dict) else None
        return current_market_hour(prices, datetime.now(UTC))

    @property
    def available(self) -> bool:
        """No automation decision until the entire local day is validated."""
        return self._current() is not None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Provide price-source semantics without exposing customer data."""
        current = self._current()
        attrs: dict[str, object] = {
            "price_component": "market_price",
            "customer_billing_rate_verified": False,
            "requires_complete_local_day": True,
            "basis": "time_weighted_physical_hours",
        }
        if current is not None:
            hour, length = current
            attrs.update(
                {
                    "rank": hour.rank,
                    "hours_in_local_day": length,
                    "market_price_eur_per_kwh": round(hour.price, 6),
                    "hour_starts_at_utc": hour.starts_at_utc.isoformat(),
                    "hour_ends_at_utc": hour.ends_at_utc.isoformat(),
                }
            )
        return attrs


class FrankEnergieMarketHourRankSensor(_MarketHourEntity, SensorEntity):
    """Rank of the current physical hour, 1 meaning cheapest."""

    _attr_name = "Electricity market price hour rank"
    _attr_icon = "mdi:podium"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_electricity_market_hour_rank"

    @property
    def native_value(self) -> int | None:
        current = self._current()
        return current[0].rank if current is not None else None


class FrankEnergieCheapestMarketHoursBinarySensor(
    _MarketHourEntity, BinarySensorEntity
):
    """ON if the current hour is in the cheapest configured X market hours."""

    _attr_name = "Electricity market in cheapest hours"
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_electricity_market_cheapest_hours"

    def _limit(self) -> int:
        return cheap_hour_count(
            self._entry.options.get(CONF_MARKET_CHEAP_HOURS, DEFAULT_MARKET_CHEAP_HOURS)
        )

    @property
    def is_on(self) -> bool | None:
        current = self._current()
        return current[0].rank <= self._limit() if current is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {**super().extra_state_attributes, "cheapest_hours_count": self._limit()}
