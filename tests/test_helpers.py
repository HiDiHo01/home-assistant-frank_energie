from custom_components.frank_energie.helpers import device_translation_key


def test_device_translation_key():
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
