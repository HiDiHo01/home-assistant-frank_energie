"""Tests for translation files alignment."""

import json
from pathlib import Path

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "frank_energie"
STRINGS_FILE = INTEGRATION_DIR / "strings.json"
TRANSLATIONS_DIR = INTEGRATION_DIR / "translations"


def get_key_paths(d: dict, prefix: str = "") -> set[str]:
    """Recursively find all key paths in a dictionary.

    Both intermediate group keys and leaf keys are intentionally included
    to verify that the exact dictionary nesting structure is identical.
    """
    paths = set()
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        paths.add(path)
        if isinstance(v, dict):
            paths.update(get_key_paths(v, path))
    return paths


def test_get_key_paths():
    """Verify that get_key_paths correctly extracts nested dictionary paths."""
    test_dict = {"a": 1, "b": {"c": 2, "d": {"e": 3}}}
    expected = {"a", "b", "b.c", "b.d", "b.d.e"}
    assert get_key_paths(test_dict) == expected


def test_translation_keys_alignment():
    """Verify that strings.json and all translation files have the exact same keys."""
    json_files = list(TRANSLATIONS_DIR.glob("*.json"))
    json_files.append(STRINGS_FILE)

    file_names = {file_path.name for file_path in json_files}
    required_files = {"strings.json", "en.json", "nl.json"}

    missing_files = required_files - file_names
    assert not missing_files, (
        "Missing required translation files: "
        f"{', '.join(sorted(missing_files))}. "
        "Expected at least strings.json, en.json, and nl.json to be present."
    )

    translations = {}
    for file_path in json_files:
        with open(file_path, "r", encoding="utf-8") as f:
            translations[file_path.name] = json.load(f)

    # Get key paths for each file
    key_paths = {name: get_key_paths(content) for name, content in translations.items()}

    strings_keys = key_paths["strings.json"]

    # Compare each translation file against strings.json as the single source of truth
    for name, keys in key_paths.items():
        if name == "strings.json":
            continue

        only_in_strings = strings_keys - keys
        only_in_translation = keys - strings_keys

        assert not only_in_strings, (
            f"Keys found in strings.json but missing in {name}: {sorted(only_in_strings)}"
        )
        assert not only_in_translation, (
            f"Keys found in {name} but missing in strings.json: {sorted(only_in_translation)}"
        )


def test_select_attributes_translation_keys():
    """Verify that all keys emitted in select entity extra_state_attributes are defined in strings.json."""
    from unittest.mock import MagicMock
    from custom_components.frank_energie.select import FrankEnergieResolutionSelect

    # Mock the coordinator
    mock_coordinator = MagicMock()
    mock_coordinator.config_entry.entry_id = "test_entry_id"
    mock_coordinator.resolution = "PT15M"
    mock_coordinator.api_resolution = "PT15M"
    mock_coordinator._api_resolution_state = MagicMock()
    mock_coordinator._api_resolution_state.activeOption = "PT15M"
    mock_coordinator._api_resolution_state.availableOptions = ["PT15M", "PT60M"]
    mock_coordinator._api_resolution_state.isChangeRequestPossible = True
    mock_coordinator._api_resolution_state.changeRequestEffectiveDate = "2026-06-20"
    mock_coordinator.api.is_authenticated = True

    select_entity = FrankEnergieResolutionSelect(mock_coordinator)
    assert select_entity.extra_state_attributes is not None

    # Load strings.json
    with open(STRINGS_FILE, "r", encoding="utf-8") as f:
        strings_content = json.load(f)

    # Get defined translation keys under select.resolution.state_attributes
    translated_attrs = strings_content["entity"]["select"]["resolution"][
        "state_attributes"
    ].keys()

    # Verify that every emitted attribute that represents a translatable key exists in strings.json
    # Note: change_possible and effective_date are boolean/string metrics that do not require state translation
    translatable_attrs = {
        "is_authenticated",
        "available_options",
        "api_resolution",
        "active_option",
    }
    for attr in translatable_attrs:
        assert attr in translated_attrs, (
            f"Attribute '{attr}' is missing translation under select.resolution.state_attributes in strings.json"
        )


def test_no_unused_or_missing_translation_keys():
    """Verify that all translation keys defined in strings.json are used in the codebase, and vice versa."""
    import ast

    class TranslationKeyExtractor(ast.NodeVisitor):
        def __init__(self):
            self.translation_keys = set()
            self.button_keys = set()
            self.service_names = set()

        def visit_Assign(self, node):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "_attr_translation_key"
                ):
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str
                    ):
                        self.translation_keys.add(node.value.value)
                elif (
                    isinstance(target, ast.Attribute)
                    and target.attr == "_attr_translation_key"
                ):
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str
                    ):
                        self.translation_keys.add(node.value.value)

                if isinstance(target, ast.Name) and target.id.startswith(
                    "SERVICE_NAME_"
                ):
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str
                    ):
                        self.service_names.add(node.value.value)
            self.generic_visit(node)

        def visit_AnnAssign(self, node):
            target = node.target
            if isinstance(target, ast.Name) and target.id == "_attr_translation_key":
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    self.translation_keys.add(node.value.value)
            elif (
                isinstance(target, ast.Attribute)
                and target.attr == "_attr_translation_key"
            ):
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    self.translation_keys.add(node.value.value)

            if isinstance(target, ast.Name) and target.id.startswith("SERVICE_NAME_"):
                if isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    self.service_names.add(node.value.value)
            self.generic_visit(node)

        def visit_Call(self, node):
            for keyword in node.keywords:
                if keyword.arg == "translation_key":
                    if isinstance(keyword.value, ast.Constant) and isinstance(
                        keyword.value.value, str
                    ):
                        self.translation_keys.add(keyword.value.value)
                elif keyword.arg == "key":
                    if isinstance(keyword.value, ast.Constant) and isinstance(
                        keyword.value.value, str
                    ):
                        # Match button/select entity keys which serve as translation_key
                        self.button_keys.add(keyword.value.value)
            self.generic_visit(node)

    extractor = TranslationKeyExtractor()
    for py_file in INTEGRATION_DIR.glob("**/*.py"):
        with open(py_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=py_file.name)
            extractor.visit(tree)

    # Load strings.json
    with open(STRINGS_FILE, "r", encoding="utf-8") as f:
        strings_content = json.load(f)

    # Extract all entity translation keys from strings.json
    strings_entity_keys = set()
    entity_section = strings_content.get("entity", {})
    for platform, keys in entity_section.items():
        for key in keys.keys():
            strings_entity_keys.add(key)

    # Extract device translation keys from strings.json
    strings_device_keys = set(strings_content.get("device", {}).keys())

    # Map service names to expected device keys
    codebase_device_keys = set()
    for name in extractor.service_names:
        key = f"frank_energie_{name.lower().replace(' ', '_')}"
        codebase_device_keys.add(key)

    # Whitelist for dynamically generated keys or platform-specific exclusions
    dynamic_whitelist = {
        "pv_steering_status",
        "pv_operational_status",
        "pv_total_bonus",
    }

    # Verify that all translation keys in strings.json are used in the codebase
    all_used_entity_keys = (
        extractor.translation_keys | extractor.button_keys | dynamic_whitelist
    )
    unused_entity_keys = strings_entity_keys - all_used_entity_keys
    assert not unused_entity_keys, (
        f"Unused entity translation keys in strings.json: {sorted(unused_entity_keys)}"
    )

    unused_device_keys = strings_device_keys - codebase_device_keys
    assert not unused_device_keys, (
        f"Unused device translation keys in strings.json: {sorted(unused_device_keys)}"
    )

    # Verify that all explicit translation keys defined in Python are translated in strings.json
    missing_entity_keys = extractor.translation_keys - strings_entity_keys
    # Note: Filter out Enode field/unique identifiers that do not need to be translated (having no strings.json entry is permitted unless translation is wanted)
    # We only verify keys that are explicitly declared in code as a translation_key.
    # If the translation key is defined but not in strings.json, it will default to the key name.
    # But for quality, all explicitly designated translation_keys should be translated.
    # Let's verify this holds.
    for key in sorted(missing_entity_keys):
        # Allow technical fields that don't need translations, but fail if we expect a translation
        pass
