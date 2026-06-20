"""Tests for translation files alignment."""

import json
from pathlib import Path

INTEGRATION_DIR = (
    Path(__file__).parent.parent
    / "custom_components"
    / "frank_energie"
)
STRINGS_FILE = INTEGRATION_DIR / "strings.json"
TRANSLATIONS_DIR = INTEGRATION_DIR / "translations"


def get_key_paths(d: dict, prefix: str = "") -> set[str]:
    """Recursively find all key paths in a dictionary."""
    paths = set()
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        paths.add(path)
        if isinstance(v, dict):
            paths.update(get_key_paths(v, path))
        else:
            paths.add(path)
    return paths


def test_translation_keys_alignment():
    """Verify that strings.json and all translation files have the exact same keys."""
    json_files = list(TRANSLATIONS_DIR.glob("*.json"))
    json_files.append(STRINGS_FILE)

    assert len(json_files) >= 3, "There should be strings.json and at least two translation files (en.json and nl.json)"

    translations = {}
    for file_path in json_files:
        with open(file_path, "r", encoding="utf-8") as f:
            translations[file_path.name] = json.load(f)

    # Get key paths for each file
    key_paths = {
        name: get_key_paths(content)
        for name, content in translations.items()
    }

    # Compare each pair of translation files
    file_names = list(key_paths.keys())
    for i in range(len(file_names)):
        for j in range(i + 1, len(file_names)):
            file_a = file_names[i]
            file_b = file_names[j]
            keys_a = key_paths[file_a]
            keys_b = key_paths[file_b]

            only_in_a = keys_a - keys_b
            only_in_b = keys_b - keys_a

            assert not only_in_a, (
                f"Keys found in {file_a} but missing in {file_b}: {sorted(only_in_a)}"
            )
            assert not only_in_b, (
                f"Keys found in {file_b} but missing in {file_a}: {sorted(only_in_b)}"
            )
