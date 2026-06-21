# smart_feature_factory.py
from __future__ import annotations

from collections.abc import Iterable

from .smart_feature_binary_sensor import SmartFeatureBinarySensor
from .smart_feature_description import SmartFeatureBinarySensorDescription


def build_smart_feature_sensors(
    coordinator,
    features: Iterable[dict],
) -> list[SmartFeatureBinarySensor]:
    """Generate HA-safe binary sensors from feature list."""

    sensors: list[SmartFeatureBinarySensor] = []

    for feature in features:
        feature_id = str(feature["id"])
        feature_type = str(feature.get("type", "unknown"))

        description = SmartFeatureBinarySensorDescription(
            key=feature_id,
            name=feature.get("name", f"Feature {feature_id}"),
            feature_id=feature_id,
            feature_type=feature_type,
            context=feature.get("context"),
            translation_placeholders={},
        )

        sensors.append(
            SmartFeatureBinarySensor(
                coordinator=coordinator,
                description=description,
            )
        )

    return sensors
