"""Tests for translation files alignment."""

import ast
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
        "pv_operational_status_timestamp",
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
    assert not missing_entity_keys, (
        f"Translation keys defined in code but missing from strings.json: {sorted(missing_entity_keys)}"
    )


def test_all_translation_keys_are_lowercase():
    """Verify that all translation keys (dictionary keys) in strings.json are entirely lowercase."""
    with open(STRINGS_FILE, "r", encoding="utf-8") as f:
        strings_content = json.load(f)

    def check_lowercase_keys(d: dict, path: str = "") -> None:
        if isinstance(d, dict):
            for k, v in d.items():
                current_path = f"{path}.{k}" if path else k
                assert k == k.lower(), (
                    f"Translation key '{current_path}' is not entirely lowercase"
                )
                if isinstance(v, dict):
                    check_lowercase_keys(v, current_path)

    # We specifically check configuration step keys, device keys, and entity keys
    check_lowercase_keys(strings_content.get("config", {}), "config")
    check_lowercase_keys(strings_content.get("device", {}), "device")
    check_lowercase_keys(strings_content.get("entity", {}), "entity")
    check_lowercase_keys(strings_content.get("options", {}), "options")


def _check_for_empty_states(d: dict, path: str = "") -> None:
    if isinstance(d, dict):
        for k, v in d.items():
            current_path = f"{path}.{k}" if path else k
            if k == "state":
                assert v != {}, (
                    f"Empty 'state' dictionary found at '{current_path}'. "
                    "State translations must be populated with lowercase keys, "
                    "or the 'state' key must be removed entirely if no translations are needed."
                )
            if isinstance(v, dict):
                _check_for_empty_states(v, current_path)


def test_no_empty_state_translations():
    """Verify that there are no empty state translation dictionaries in strings.json."""
    with open(STRINGS_FILE, "r", encoding="utf-8") as f:
        strings_content = json.load(f)

    _check_for_empty_states(strings_content)


class _DescriptionValidator(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
        self.failures = []

    def _get_call_names(self, node):
        if isinstance(node.func, ast.Name):
            return node.func.id, node.func.id
        if isinstance(node.func, ast.Attribute):
            full_name = (
                f"{node.func.value.id}.{node.func.attr}"
                if isinstance(node.func.value, ast.Name)
                else node.func.attr
            )
            return node.func.attr, full_name
        return None, None

    def visit_Call(self, node):
        func_name, full_name = self._get_call_names(node)

        if func_name and (
            "Description" in func_name or "EntityDescription" in func_name
        ):
            has_key = any(k.arg in ("translation_key", "key") for k in node.keywords)
            if not has_key:
                self.failures.append(
                    f"Line {node.lineno}: Entity description '{full_name}' "
                    f"does not specify 'translation_key' or 'key'."
                )
        self.generic_visit(node)


def test_all_descriptions_have_translation_key():
    """Verify that all entity descriptions have translation_key or key set.

    This ensures that all entity descriptions can be translated (with
    translation_key defaulting to key). We exclude smart_feature_factory.py
    as it builds descriptions dynamically.
    """
    failures = []
    for py_file in INTEGRATION_DIR.glob("**/*.py"):
        if py_file.name == "smart_feature_factory.py":
            continue
        with open(py_file, "r", encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read(), filename=py_file.name)
                validator = _DescriptionValidator(py_file.name)
                validator.visit(tree)
                for failure in validator.failures:
                    failures.append(f"{py_file.name}: {failure}")
            except SyntaxError as exc:
                import pytest

                pytest.fail(f"Failed to parse {py_file}: {exc}")

    assert not failures, (
        f"Entity descriptions found without 'translation_key' or 'key':\n"
        f"{'\n'.join(failures)}"
    )


class _EnumStateValidator(ast.NodeVisitor):
    def __init__(self):
        self.keys_with_options = set()
        self.keys_with_enum = set()
        self.all_keys = set()

    def _get_key_from_kwarg(self, kw) -> str | None:
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
        return None

    def _parse_keywords(
        self, keywords: list[ast.keyword]
    ) -> tuple[str | None, bool, bool]:
        key = None
        has_options = False
        has_enum = False
        for kw in keywords:
            if kw.arg in ("translation_key", "key") and not key:
                key = self._get_key_from_kwarg(kw)
            elif kw.arg == "options":
                has_options = True
            elif kw.arg == "device_class" and isinstance(kw.value, ast.Attribute):
                has_enum = kw.value.attr == "ENUM"
        return key, has_options, has_enum

    def visit_Call(self, node):
        func_name = getattr(node.func, "id", getattr(node.func, "attr", None))

        if not func_name or (
            "Description" not in func_name and "EntityDescription" not in func_name
        ):
            self.generic_visit(node)
            return

        key, has_options, has_enum = self._parse_keywords(node.keywords)
        if key:
            self.all_keys.add(key)
            if has_options:
                self.keys_with_options.add(key)
            if has_enum:
                self.keys_with_enum.add(key)

        self.generic_visit(node)


def _get_state_translation_keys() -> set[str]:
    with open(STRINGS_FILE, "r", encoding="utf-8") as f:
        strings_content = json.load(f)

    keys = set()
    sensor_entities = strings_content.get("entity", {}).get("sensor", {})
    for key, value in sensor_entities.items():
        if "state" in value:
            keys.add(key)
    return keys


def test_sensors_with_state_translations_are_enums():
    """Verify that sensors with state translations use SensorDeviceClass.ENUM and define options."""
    state_translation_keys = _get_state_translation_keys()

    validator = _EnumStateValidator()
    for py_file in INTEGRATION_DIR.glob("**/*.py"):
        if py_file.name == "smart_feature_factory.py":
            continue
        with open(py_file, "r", encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read(), filename=py_file.name)
                validator.visit(tree)
            except SyntaxError as exc:
                import pytest

                pytest.fail(f"Failed to parse {py_file}: {exc}")

    failures = []
    for key in state_translation_keys:
        if key in validator.all_keys:
            if key not in validator.keys_with_enum:
                failures.append(
                    f"'{key}' has state translations but missing device_class=SensorDeviceClass.ENUM"
                )
            if key not in validator.keys_with_options:
                failures.append(
                    f"'{key}' has state translations but missing options=[...]"
                )

    assert not failures, (
        "Sensors with state translations must be ENUMs with options:\n"
        + "\n".join(failures)
    )
