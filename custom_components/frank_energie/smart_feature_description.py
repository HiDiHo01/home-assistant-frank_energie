# smart_feature_description.py
from __future__ import annotations

from dataclasses import dataclass, field

from homeassistant.components.binary_sensor import BinarySensorEntityDescription


@dataclass(frozen=True, kw_only=True)
class SmartFeatureBinarySensorDescription(BinarySensorEntityDescription):
    """Stable HA 2026 binary sensor description."""

    feature_id: str
    feature_type: str

    # HA 2025+ requirement safety
    translation_placeholders: dict[str, str] = field(default_factory=dict)

    # optional future-proof metadata
    context: str | None = None
    version: int = 1
