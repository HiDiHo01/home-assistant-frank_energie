"""Set the release manifest version and fail when the update cannot complete."""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_PATH = Path("custom_components") / "frank_energie" / "manifest.json"


def update_manifest(version: str, manifest_path: Path = MANIFEST_PATH) -> str:
    """Write the requested version, propagating invalid data and file errors."""
    normalized_version = version.strip().removeprefix("v")
    if not normalized_version:
        raise ValueError("The release version must not be empty after removing 'v'.")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Manifest '{manifest_path}' contains invalid JSON: {error.msg}."
        ) from error

    if not isinstance(manifest, dict):
        raise TypeError(f"Manifest '{manifest_path}' must contain a JSON object.")

    manifest["version"] = normalized_version
    manifest_path.write_text(
        json.dumps(manifest, indent=4, sort_keys=True), encoding="utf-8"
    )
    return normalized_version


def main(arguments: Sequence[str] | None = None) -> int:
    """Update the manifest and return a failing exit status for expected errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        "-V",
        default=datetime.now(timezone.utc).strftime("%Y.%m.%d"),
        help="Release version, optionally prefixed with v; defaults to the UTC date.",
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        updated_version = update_manifest(parsed_arguments.version)
    except (OSError, TypeError, ValueError) as error:
        print(f"Unable to update integration manifest: {error}", file=sys.stderr)
        return 1

    print(f"Manifest updated with version: {updated_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
