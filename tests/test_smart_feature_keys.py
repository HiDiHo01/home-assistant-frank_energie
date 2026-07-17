from custom_components.frank_energie.smart_feature_keys import build_registry_key
import hashlib


def test_build_registry_key_with_context():
    key = build_registry_key("sensor", "battery_level", "home")
    expected = hashlib.sha1("sensor:battery_level:home".encode("utf-8")).hexdigest()[
        :16
    ]
    assert key == expected


def test_build_registry_key_without_context():
    key = build_registry_key("sensor", "battery_level")
    expected = hashlib.sha1("sensor:battery_level:".encode("utf-8")).hexdigest()[:16]
    assert key == expected


def test_build_registry_key_stability():
    key1 = build_registry_key("domain1", "feature1", "context1")
    key2 = build_registry_key("domain1", "feature1", "context1")
    assert key1 == key2


def test_build_registry_key_length():
    key = build_registry_key("domain1", "feature1", "context1")
    assert len(key) == 16
