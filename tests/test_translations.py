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
