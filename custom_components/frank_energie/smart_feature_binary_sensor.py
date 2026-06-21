# smart_feature_binary_sensor.py
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import callback

from .smart_feature_description import SmartFeatureBinarySensorDescription
from .smart_feature_keys import build_registry_key

_LOGGER = logging.getLogger(__name__)


class SmartFeatureBinarySensor(BinarySensorEntity):
    """HA 2026 stable binary sensor for smart features."""

    def __init__(
        self,
        coordinator,
        description: SmartFeatureBinarySensorDescription,
    ) -> None:
        """Initialize sensor."""
        self._coordinator = coordinator
        self.entity_description = description

        self._attr_unique_id = build_registry_key(
            domain="frank_energie",
            feature_id=description.feature_id,
            context=description.context,
        )

        self._attr_has_entity_name = True

        _LOGGER.debug(
            "initialized smart feature sensor feature_id=%s unique_id=%s",
            description.feature_id,
            self._attr_unique_id,
        )

    @property
    def name(self) -> str:
        """Return entity name."""
        return (
            self.entity_description.name
            or f"Feature {self.entity_description.feature_id}"
        )

    @property
    def is_on(self) -> bool:
        """Return state."""
        data = self._coordinator.data or {}
        return bool(data.get(self.entity_description.feature_id, False))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updates from coordinator."""
        self.async_write_ha_state()
