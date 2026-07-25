from datetime import UTC, datetime

from custom_components.frank_energie.const import UNIT_GAS_BE, UNIT_GAS_NL
from custom_components.frank_energie.helpers import (
    build_charge_settings_input,
    device_translation_key,
    resolve_gas_unit,
)


def test_resolve_gas_unit_m3_maps_to_nl() -> None:
    assert resolve_gas_unit("M3") == UNIT_GAS_NL


def test_resolve_gas_unit_kwh_maps_to_be() -> None:
    assert resolve_gas_unit("KWH") == UNIT_GAS_BE


def test_resolve_gas_unit_is_case_insensitive() -> None:
    assert resolve_gas_unit("m3") == UNIT_GAS_NL
    assert resolve_gas_unit("kwh") == UNIT_GAS_BE


def test_resolve_gas_unit_none_defaults_to_nl() -> None:
    assert resolve_gas_unit(None) == UNIT_GAS_NL


def test_resolve_gas_unit_unsupported_value_falls_back_to_nl() -> None:
    assert resolve_gas_unit("LITER") == UNIT_GAS_NL


def test_build_charge_settings_input_includes_all_thirteen_fields() -> None:
    from python_frank_energie.models import ChargeSettings

    deadline = datetime(2026, 7, 21, 7, 0, tzinfo=UTC)
    settings = ChargeSettings(
        calculated_deadline=deadline,
        capacity=50.0,
        deadline=deadline,
        hour_friday=1,
        hour_monday=2,
        hour_saturday=3,
        hour_sunday=4,
        hour_thursday=5,
        hour_tuesday=6,
        hour_wednesday=7,
        id="charge-settings-1",
        is_smart_charging_enabled=True,
        is_solar_charging_enabled=False,
        max_charge_limit=80,
        min_charge_limit=20,
    )

    result = build_charge_settings_input(settings)

    assert result == {
        "id": "charge-settings-1",
        "deadline": deadline.isoformat(),
        "isSmartChargingEnabled": True,
        "isSolarChargingEnabled": False,
        "minChargeLimit": 20,
        "maxChargeLimit": 80,
        "hourMonday": 2,
        "hourTuesday": 6,
        "hourWednesday": 7,
        "hourThursday": 5,
        "hourFriday": 1,
        "hourSaturday": 3,
        "hourSunday": 4,
    }


def test_build_charge_settings_input_handles_none_deadline() -> None:
    from python_frank_energie.models import ChargeSettings

    settings = ChargeSettings(
        calculated_deadline=datetime(2026, 7, 21, 7, 0, tzinfo=UTC),
        capacity=50.0,
        deadline=None,
        hour_friday=0,
        hour_monday=0,
        hour_saturday=0,
        hour_sunday=0,
        hour_thursday=0,
        hour_tuesday=0,
        hour_wednesday=0,
        id="charge-settings-2",
        is_smart_charging_enabled=False,
        is_solar_charging_enabled=False,
        max_charge_limit=80,
        min_charge_limit=20,
    )

    result = build_charge_settings_input(settings)

    assert result["deadline"] is None


def test_device_translation_key() -> None:
    """Test generating device translation key from service names."""
    # Simple lowercase string
    assert device_translation_key("test") == "frank_energie_test"

    # Uppercase string
    assert device_translation_key("TEST") == "frank_energie_test"

    # Mixed case string
    assert device_translation_key("Test") == "frank_energie_test"

    # String with spaces
    assert device_translation_key("Test Service") == "frank_energie_test_service"

    # Multiple spaces
    assert device_translation_key("Test  Service") == "frank_energie_test__service"

    # Real world examples
    assert device_translation_key("Frank Energie") == "frank_energie_frank_energie"
    assert device_translation_key("Gas Service") == "frank_energie_gas_service"
    assert (
        device_translation_key("Electricity Service")
        == "frank_energie_electricity_service"
    )
