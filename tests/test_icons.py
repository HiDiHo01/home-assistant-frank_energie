"""Regression coverage for every Frank Energie sensor and binary-sensor icon."""

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from custom_components.frank_energie.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    ENODE_CHARGER_BINARY_SENSOR_DESCRIPTIONS,
    ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS,
)
from custom_components.frank_energie.sensor import (
    ENODE_CHARGER_SENSOR_TYPES,
    ENODE_VEHICLE_SENSOR_TYPES,
    SENSOR_TYPES,
)
from custom_components.frank_energie.smart_feature_factory import (
    build_smart_feature_sensors,
)

_INTEGRATION_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "frank_energie"
)
_DESCRIPTION_PLATFORM = {
    "FrankEnergieEntityDescription": "sensor",
    "ChargerSensorDescription": "sensor",
    "EnodeVehicleEntityDescription": "sensor",
    "_DeviceSensorDescription": "sensor",
    "FrankEnergieBinarySensorDescription": "binary_sensor",
    "EnodeVehicleBinarySensorDescription": "binary_sensor",
    "EnodeChargerBinarySensorDescription": "binary_sensor",
}


def _read_icons() -> dict:
    """Read the Home Assistant entity icon translation resource."""
    return json.loads((_INTEGRATION_DIR / "icons.json").read_text(encoding="utf-8"))


def test_all_sensor_and_binary_descriptions_have_an_icon() -> None:
    """Guard every declared entity against missing icon metadata."""
    icons = _read_icons()["entity"]

    for file_name in ("sensor.py", "binary_sensor.py", "smart_hvac_sensor.py"):
        source_path = _INTEGRATION_DIR / file_name
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=file_name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            platform = _DESCRIPTION_PLATFORM.get(node.func.id)
            if platform is None:
                continue
            keywords = {argument.arg: argument.value for argument in node.keywords}
            if "icon" in keywords:
                continue
            translation = keywords.get("translation_key", keywords.get("key"))
            assert isinstance(translation, ast.Constant) and isinstance(
                translation.value, str
            ), f"{file_name}:{node.lineno} has no icon or static translation key"
            device_class = keywords.get("device_class")
            if (
                isinstance(device_class, ast.Attribute)
                and isinstance(device_class.value, ast.Name)
                and device_class.value.id == "SensorDeviceClass"
                and device_class.attr == "MONETARY"
            ):
                icon_entry = icons.get(platform, {}).get(translation.value)
                # Smart HVAC already had a currency icon before this change.
                legacy_hvac_icon = (
                    platform == "sensor"
                    and translation.value == "smart_hvac_total_result"
                    and icon_entry == {"default": "mdi:currency-eur"}
                )
                assert icon_entry is None or legacy_hvac_icon, (
                    f"{file_name}:{node.lineno} overrides the existing "
                    f"monetary icon for {translation.value}"
                )
                continue
            assert icons.get(platform, {}).get(translation.value, {}).get("default"), (
                f"{file_name}:{node.lineno} ({translation.value}) has no icon"
            )


def test_icon_translation_keys_and_mdi_values_are_valid() -> None:
    """Keep icon keys synchronized with entity names in strings.json."""
    icons = _read_icons()["entity"]
    strings = json.loads(
        (_INTEGRATION_DIR / "strings.json").read_text(encoding="utf-8")
    )["entity"]

    for platform in ("sensor", "binary_sensor"):
        for translation_key, icon_options in icons[platform].items():
            assert translation_key in strings[platform], (
                f"Orphaned {platform} icon key: {translation_key}"
            )
            assert isinstance(icon_options["default"], str) and icon_options[
                "default"
            ].startswith("mdi:"), f"Invalid MDI icon for {platform}.{translation_key}"


def test_every_vehicle_and_charger_description_preserves_its_icon() -> None:
    """Reject constructors that silently discard the icon they receive."""
    for description in (
        *ENODE_VEHICLE_SENSOR_TYPES,
        *ENODE_CHARGER_SENSOR_TYPES,
        *ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS,
        *ENODE_CHARGER_BINARY_SENSOR_DESCRIPTIONS,
    ):
        assert description.icon is not None, description.key
        assert description.icon.startswith("mdi:"), description.key

    vehicle_icons = {
        description.key: description.icon
        for description in (
            *ENODE_VEHICLE_SENSOR_TYPES,
            *ENODE_VEHICLE_BINARY_SENSOR_DESCRIPTIONS,
        )
    }
    assert vehicle_icons["charge_limit"] == "mdi:battery-charging-70"
    assert vehicle_icons["last_seen"] == "mdi:clock-outline"


@pytest.mark.parametrize(
    "translation_key",
    (
        "current_electricity_price",
        "gas_market",
        "elec_min",
        "elec_max",
        "invoice_current_period",
        "costs_this_year",
    ),
)
def test_monetary_sensor_keeps_its_existing_icon(translation_key: str) -> None:
    """Do not replace existing monetary device-class icons with MDI overrides."""
    from homeassistant.components.sensor import SensorDeviceClass

    from custom_components.frank_energie.const import ICON

    descriptions = {
        description.translation_key: description for description in SENSOR_TYPES
    }
    description = descriptions[translation_key]

    assert ICON == "mdi:currency-eur"
    assert description.device_class is SensorDeviceClass.MONETARY
    assert description.icon is None
    assert translation_key not in _read_icons()["entity"]["sensor"]


def test_existing_energy_flow_and_binary_sensor_icons_are_preserved() -> None:
    """Protect existing electricity-flow and binary-sensor icon assignments."""
    sensor_icons = {description.key: description.icon for description in SENSOR_TYPES}
    assert sensor_icons["usage_electricity_yesterday"] == (
        "mdi:transmission-tower-export"
    )
    assert sensor_icons["delivered_feed_in_yesterday"] == (
        "mdi:transmission-tower-import"
    )

    binary_icons = {
        description.key: description.icon for description in BINARY_SENSOR_DESCRIPTIONS
    }
    assert binary_icons["electricity_price_lowest_today"] == "mdi:arrow-down-bold"
    assert binary_icons["tomorrow_prices_available"] == "mdi:currency-eur"
    assert _read_icons()["entity"]["binary_sensor"]["smart_hvac"]["default"] == (
        "mdi:heat-pump"
    )


def test_dynamic_smart_feature_binary_has_an_icon() -> None:
    """Give provider-defined binary features a meaningful generic switch icon."""
    sensors = build_smart_feature_sensors(
        MagicMock(), [{"id": "feature-example", "type": "switch", "name": "Demo"}]
    )
    assert len(sensors) == 1
    assert sensors[0].entity_description.icon == "mdi:toggle-switch-outline"
